"""
trading_engine.py — Master trading orchestrator.

State machine (runs every 5 seconds):

  IDLE
    ↓ market opens (9:00)
  SCANNING   — sector_scanner builds picture, no trades yet
    ↓ 9:45 AM  — scanner finalizes top 2 stocks
  SELECTED   — watching for entry signal on those 2 stocks
    ↓ entry signal score >= 5
  IN_TRADE   — trade placed, risk_engine monitors tick-by-tick
    ↓ exit signal or SL/target hit
  TRADE_CLOSED — log result, loop back to WATCHING if time < 3:00 PM
    ↓ 3:15 PM
  SQUARE_OFF — force-exit all open positions
    ↓
  DAY_DONE

Hard rules enforced here:
  - Feed must be connected at every step
  - Trading halted flag checked before every entry
  - Max 2 concurrent open trades per environment
  - All positions closed by 3:15 PM regardless
"""

import time, threading
from datetime import datetime, date, time as dtime
from typing import Optional, List

from backend.core.market_state    import market
from backend.core.sector_scanner  import sector_scanner, SCAN_END, SQUARE_OFF
from backend.core.entry_engine    import check_entry, ENTRY_SCORE_THRESHOLD
from backend.core.exit_engine     import check_exit
from backend.core.order_executor  import place_entry_order, place_exit_order
from backend.core.risk_engine     import risk_engine
from backend.core.session_manager import (
    get_or_create_session, update_session_status,
    set_selected_stocks, check_portfolio_sl
)
from backend.core.settings_manager import get_settings, is_live_mode
from backend.database import (
    Session as DBSession, Trade, TradeStatus, TradeEnv, SessionStatus
)


ENGINE_TICK = 5       # seconds between main loop iterations
MAX_OPEN_TRADES = 2   # per environment


class TradingEngine:
    def __init__(self):
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._state    = "IDLE"
        self._today    = None

        # Register exit callback with risk engine
        risk_engine.register_exit_callback(self._on_risk_exit)

    # ── State machine ──────────────────────────────────────────────────────────

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[TRADING ENGINE] Tick error: {e}")
            time.sleep(ENGINE_TICK)

    def _tick(self):
        now = datetime.now()
        t   = now.time()

        # ── Daily reset ───────────────────────────────────────────────────────
        if self._today != now.date():
            self._today = now.date()
            self._state = "IDLE"
            sector_scanner.reset()
            print(f"[ENGINE] New trading day: {self._today}")

        # ── Feed guard ────────────────────────────────────────────────────────
        if not market.is_feed_connected():
            if self._state not in ("IDLE", "FEED_DOWN"):
                print("[ENGINE] Feed disconnected — trading halted.")
                self._state = "FEED_DOWN"
            return

        if self._state == "FEED_DOWN":
            print("[ENGINE] Feed restored — resuming.")
            self._state = "IDLE"

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER

        # ── Portfolio SL check ────────────────────────────────────────────────
        if check_portfolio_sl(env):
            if self._state not in ("IDLE", "DAY_DONE", "KILLED"):
                print("[ENGINE] Portfolio SL breached — killing all trades.")
                self._state = "KILLED"
            return

        # ── Square-off time ───────────────────────────────────────────────────
        if t >= SQUARE_OFF:
            if self._state not in ("DAY_DONE", "IDLE"):
                self._square_off_all(env)
                self._state = "DAY_DONE"
            return

        # ── Pre-market / outside trading hours ────────────────────────────────
        if t < dtime(9, 0):
            self._state = "IDLE"
            return

        # ── Ensure session exists ─────────────────────────────────────────────
        session = get_or_create_session(env)

        # ── SCANNING window: 9:00 – 9:45 ─────────────────────────────────────
        if t < SCAN_END:
            self._state = "SCANNING"
            result = sector_scanner.run_scan()
            if result:
                symbols = [c.symbol for c in result.candidates]
                set_selected_stocks(env, result.top_sector, symbols)
            return

        # ── Post-scan: SELECTED / WATCHING ───────────────────────────────────
        if not sector_scanner.is_finalized():
            sector_scanner.run_scan()   # try one more time
            return

        result = sector_scanner.get_result()
        if not result or not result.candidates:
            print("[ENGINE] No tradable stocks found after scan. Sitting out today.")
            self._state = "NO_TRADE"
            return

        # ── Count currently open trades ───────────────────────────────────────
        open_count = self._open_trade_count(env)
        if open_count >= MAX_OPEN_TRADES:
            self._state = "IN_TRADE"
            self._monitor_open_trades(env, result)
            return

        # ── Look for entry signals on selected stocks ─────────────────────────
        self._state = "WATCHING"
        for candidate in result.candidates:
            if open_count >= MAX_OPEN_TRADES:
                break
            if self._already_in_trade(env, candidate.symbol):
                continue
            if market.is_trading_halted():
                break

            signal = check_entry(candidate.token, candidate.direction)
            if signal.should_enter:
                print(f"[ENGINE] ENTRY SIGNAL | {candidate.symbol} {candidate.direction.upper()} "
                      f"| Score: {signal.score}/8 | {signal.reasoning[:80]}")
                trade = place_entry_order(
                    env         = env,
                    symbol      = candidate.symbol,
                    token       = candidate.token,
                    direction   = candidate.direction,
                    session_id  = session["id"],
                    entry_logic = signal.reasoning,
                    indicators  = signal.indicators,
                )
                if trade:
                    open_count += 1
                    self._state = "IN_TRADE"
                    update_session_status(env, SessionStatus.ACTIVE)
            else:
                print(f"[ENGINE] Watching {candidate.symbol} | Score: {signal.score}/8 — waiting")

        # ── Monitor open trades for trend-based exit ──────────────────────────
        if open_count > 0:
            self._monitor_open_trades(env, result)

    def _monitor_open_trades(self, env: TradeEnv, scan_result):
        """Check exit signals for open trades (trend reversal / sideways)."""
        db = DBSession()
        try:
            open_trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            from backend.core.stock_universe import get_token
            for trade in open_trades:
                token = get_token(trade.symbol)
                if not token:
                    continue
                ltp = market.get_ltp(token)
                if not ltp:
                    continue
                exit_sig = check_exit(
                    token       = token,
                    direction   = trade.direction,
                    entry_price = trade.entry_price,
                    current_ltp = ltp,
                )
                if exit_sig.should_exit and exit_sig.urgency == "immediate":
                    print(f"[ENGINE] TREND EXIT | {trade.symbol} | {exit_sig.reason}")
                    risk_engine.force_exit_trade(
                        trade_id = trade.id,
                        reason   = f"Trend exit: {exit_sig.reason}",
                    )
        finally:
            db.close()

    def _square_off_all(self, env: TradeEnv):
        """Force-close all open positions at 3:15 PM."""
        db = DBSession()
        try:
            open_trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            for trade in open_trades:
                print(f"[ENGINE] SQUARE OFF: {trade.symbol} #{trade.id}")
                risk_engine.force_exit_trade(
                    trade_id = trade.id,
                    reason   = "Auto square-off at 3:15 PM",
                )
            update_session_status(env, SessionStatus.STOPPED)
            print(f"[ENGINE] Square-off complete. {len(open_trades)} positions closed.")
        finally:
            db.close()

    def _on_risk_exit(self, trade_id: int, exit_price: float, reason: str, env: TradeEnv):
        """Callback from RiskEngine when SL/target/dynamic SL fires."""
        place_exit_order(trade_id, exit_price, reason, env)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _open_trade_count(self, env: TradeEnv) -> int:
        db = DBSession()
        try:
            return (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .count()
            )
        finally:
            db.close()

    def _already_in_trade(self, env: TradeEnv, symbol: str) -> bool:
        db = DBSession()
        try:
            return (
                db.query(Trade)
                .filter(
                    Trade.status == TradeStatus.OPEN,
                    Trade.env    == env,
                    Trade.symbol == symbol,
                )
                .count() > 0
            )
        finally:
            db.close()

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread  = threading.Thread(
            target=self._loop, daemon=True, name="TradingEngine"
        )
        self._thread.start()
        print("[ENGINE] Trading engine started.")

    def stop(self):
        self._running = False
        print("[ENGINE] Trading engine stopped.")

    def get_state(self) -> dict:
        scan = sector_scanner.get_result()
        return {
            "state":         self._state,
            "env":           "live" if is_live_mode() else "paper",
            "today":         str(self._today) if self._today else None,
            "feed_ok":       market.is_feed_connected(),
            "halted":        market.is_trading_halted(),
            "halt_reason":   market.get_halt_reason(),
            "scan_finalized": sector_scanner.is_finalized(),
            "top_sector":    scan.top_sector if scan else None,
            "selected_stocks": [c.symbol for c in scan.candidates] if scan else [],
            "open_trades":   risk_engine.get_open_risk_snapshot(),
        }


# ── Global singleton ──────────────────────────────────────────────────────────
trading_engine = TradingEngine()
