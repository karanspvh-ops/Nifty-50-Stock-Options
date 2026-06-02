"""
opening_breakout.py — Opening Breakout positional strategy.

Flow (timed to the market open):
  09:15 – 09:40  SCAN     : track each stock's move from the 09:15 open,
                            aggregate sectors, determine overall trend.
  09:35 – 09:40  PREVIEW  : publish a TRADE PLAN (chosen sector, direction,
                            top-3 stocks) a few minutes before entry, so it
                            can be reviewed manually.
  09:40 – 09:45  ENTER    : for each planned stock that has already moved
                            >= 1.5%, buy the ATM option (CALL if bullish
                            sector / PUT if bearish). Up to 3 positions in
                            the same sector. One lot each.
  after entry    MANAGE   : positional hold. Hard SL 10%, trail the stop as
                            price climbs, target +50% (single lot = full
                            exit; multi-lot = sell half at +50%, trail rest
                            to +100%). No trend-based early exit.

Direction is single: only CALLs (bullish day) OR only PUTs (bearish day),
chosen from the overall market trend.

Trades are tagged with "[OB]" in entry_logic so the generic risk engine
leaves them alone — this engine manages them.
"""

import os, sys, threading, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state     import market
from backend.core.stock_universe   import get_meta, get_stocks_in_sector, get_token
from backend.core.settings_manager import is_live_mode, get_active_index
from backend.core.session_manager  import get_or_create_session, check_portfolio_sl
from backend.core.order_executor   import place_entry_order
from backend.core.risk_engine      import risk_engine
from backend.database import (
    Session as DBSession, Trade, TradeStatus, TradeEnv, SessionStatus,
)
from backend.core.session_manager  import update_session_status, set_selected_stocks

# ── Strategy parameters ───────────────────────────────────────────────────────
SCAN_START      = dtime(9, 15)
PREVIEW_FROM    = dtime(9, 35)
SCAN_END        = dtime(9, 40)     # finalize sector/direction (25 min)
ENTRY_DEADLINE  = dtime(9, 45)     # must enter by 30 min
SQUARE_OFF      = dtime(15, 15)

MOVE_MIN_PCT        = 1.5          # opening breakout trigger
MOVE_TARGET_PCT     = 2.0          # preferred move
MAX_POSITIONS       = 3
RFACTOR_MIN         = 0.8

HARD_SL_PCT         = 10.0         # option premium hard stop
TARGET_PCT          = 50.0         # single-lot full-exit target
SECOND_TARGET_PCT   = 100.0        # multi-lot runner target
TRAIL_ACTIVATE_PCT  = 20.0         # start trailing once +20%
TRAIL_GAP_PCT       = 20.0         # trail 20% off the peak

OB_TAG = "[OB]"
LOOP_SEC = 5


@dataclass
class PlanStock:
    symbol:        str
    token:         str
    opening_move:  float          # % from 09:15 open
    day_move:      float          # % from prev close
    r_factor:      float
    ltp:           float
    est_premium:   float
    eligible:      bool           # has it moved >= MOVE_MIN_PCT?
    entered:       bool = False


@dataclass
class TradePlan:
    status:        str            # "scanning" | "preview" | "final" | "entering" | "done"
    trend:         str            # "bullish" | "bearish" | "neutral"
    sector:        Optional[str]
    direction:     Optional[str]  # "call" | "put"
    sector_pct:    float
    stocks:        List[PlanStock] = field(default_factory=list)
    note:          str = ""
    generated_at:  str = ""


class OpeningBreakout:
    def __init__(self):
        self._running   = False
        self._thread:   Optional[threading.Thread] = None
        self._enabled   = True
        self._today     = None
        self._open_ref: Dict[str, float] = {}      # token -> 09:15 open price
        self._plan:     Optional[TradePlan] = None
        self._phase     = "IDLE"

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="OpeningBreakout")
        self._thread.start()
        print("[OB] Opening Breakout strategy started.")

    def stop(self):
        self._running = False

    def set_enabled(self, on: bool):
        self._enabled = on

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[OB] tick error: {e}")
            time.sleep(LOOP_SEC)

    # ── Per-tick state machine ────────────────────────────────────────────────
    def _tick(self):
        now = datetime.now()
        t   = now.time()

        # daily reset
        if self._today != now.date():
            self._today = now.date()
            self._open_ref.clear()
            self._plan  = None
            self._phase = "IDLE"

        if not self._enabled or not market.is_feed_connected():
            return

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER

        # Portfolio kill-switch still applies
        if check_portfolio_sl(env):
            self._phase = "KILLED"
            return

        # Square off
        if t >= SQUARE_OFF:
            self._square_off(env)
            self._phase = "DONE"
            return

        # Pre-open
        if t < SCAN_START:
            self._phase = "IDLE"
            return

        # ── Capture 09:15 opening reference prices ────────────────────────────
        if SCAN_START <= t <= ENTRY_DEADLINE:
            self._capture_open_refs()

        # ── SCAN / PREVIEW / FINALIZE ─────────────────────────────────────────
        if t < SCAN_END:
            self._phase = "SCANNING" if t < PREVIEW_FROM else "PREVIEW"
            self._plan  = self._build_plan(status="scanning" if t < PREVIEW_FROM else "preview")
            return

        # finalize plan at 09:40
        if self._plan is None or self._plan.status in ("scanning", "preview"):
            self._plan = self._build_plan(status="final")
            self._phase = "PLANNED"
            if self._plan and self._plan.sector:
                set_selected_stocks(env, self._plan.sector,
                                    [s.symbol for s in self._plan.stocks])
                print(f"[OB] PLAN FINAL | {self._plan.trend.upper()} | sector {self._plan.sector} "
                      f"| {self._plan.direction} | stocks: {[s.symbol for s in self._plan.stocks]}")

        # ── ENTER (until 09:45) ───────────────────────────────────────────────
        if t <= ENTRY_DEADLINE and self._plan and self._plan.sector:
            self._plan.status = "entering"
            self._phase = "ENTERING"
            self._enter_positions(env)

        # ── MANAGE open OB trades ─────────────────────────────────────────────
        self._manage(env)
        if self._phase not in ("ENTERING",):
            self._phase = "MANAGING"

    # ── Opening reference capture ─────────────────────────────────────────────
    def _capture_open_refs(self):
        index  = get_active_index()
        moves  = market.get_all_stock_moves()
        for token, mv in moves.items():
            if token not in self._open_ref and mv.get("ltp"):
                self._open_ref[token] = mv["ltp"]

    def _opening_move(self, token: str, ltp: float) -> float:
        ref = self._open_ref.get(token)
        if not ref:
            return 0.0
        return round((ltp - ref) / ref * 100, 2)

    # ── Plan builder (also used by the API for on-demand preview) ─────────────
    def _build_plan(self, status: str) -> Optional[TradePlan]:
        sector_moves = market.get_sector_moves()
        if not sector_moves:
            return TradePlan(status=status, trend="neutral", sector=None,
                             direction=None, sector_pct=0.0,
                             note="Waiting for market data…",
                             generated_at=datetime.now().isoformat())

        # Overall market trend from breadth (avg of all sectors)
        avg = sum(d.get("pct_change", 0) for d in sector_moves.values()) / max(len(sector_moves), 1)
        trend = "bullish" if avg > 0 else ("bearish" if avg < 0 else "neutral")

        if trend == "bullish":
            sector, data = max(sector_moves.items(), key=lambda x: x[1].get("pct_change", 0))
            direction = "call"
        elif trend == "bearish":
            sector, data = min(sector_moves.items(), key=lambda x: x[1].get("pct_change", 0))
            direction = "put"
        else:
            return TradePlan(status=status, trend="neutral", sector=None, direction=None,
                             sector_pct=0.0, note="Market flat — no directional edge.",
                             generated_at=datetime.now().isoformat())

        sector_pct = data.get("pct_change", 0)
        index      = get_active_index()
        candidates: List[PlanStock] = []

        for s in get_stocks_in_sector(sector, index):
            token  = s["token"]
            meta   = get_meta(token)
            if meta.get("lot_size", 0) <= 0:
                continue
            mv  = market.get_stock_move(token)
            if not mv:
                continue
            ltp = mv.get("ltp") or 0
            day_move = mv.get("pct_change", 0)
            # Direction filter — must align with the chosen side
            if direction == "call" and day_move <= 0:   continue
            if direction == "put"  and day_move >= 0:    continue
            opening_move = self._opening_move(token, ltp)
            # Use opening move if we have a ref, else fall back to day move
            move = opening_move if self._open_ref.get(token) else day_move
            sec_avg = abs(sector_pct) or 0.1
            r_factor = round(abs(day_move) / sec_avg, 2)
            if r_factor < RFACTOR_MIN:
                continue
            est_premium = round(ltp * 0.015, 2)
            candidates.append(PlanStock(
                symbol=s["symbol"], token=token,
                opening_move=move, day_move=day_move,
                r_factor=r_factor, ltp=ltp, est_premium=est_premium,
                eligible=abs(move) >= MOVE_MIN_PCT,
            ))

        # Rank: prefer eligible (already moved), then by |move| * r_factor
        candidates.sort(key=lambda c: (c.eligible, abs(c.opening_move) * c.r_factor), reverse=True)
        top = candidates[:MAX_POSITIONS]

        note = (f"Top {len(top)} {sector} stocks; entries trigger once a stock "
                f"has moved ≥ {MOVE_MIN_PCT}% from the open.")
        return TradePlan(
            status=status, trend=trend, sector=sector, direction=direction,
            sector_pct=sector_pct, stocks=top, note=note,
            generated_at=datetime.now().isoformat(),
        )

    # ── Entry ─────────────────────────────────────────────────────────────────
    def _enter_positions(self, env: TradeEnv):
        if not self._plan:
            return
        open_ob = self._count_open_ob(env)
        session = get_or_create_session(env)

        for ps in self._plan.stocks:
            if open_ob >= MAX_POSITIONS:
                break
            if ps.entered or self._already_in(env, ps.symbol):
                continue
            # Must have moved enough to trigger the breakout entry
            mv  = market.get_stock_move(ps.token)
            ltp = mv.get("ltp") if mv else None
            if not ltp:
                continue
            move = self._opening_move(ps.token, ltp) if self._open_ref.get(ps.token) else ps.day_move
            if abs(move) < MOVE_MIN_PCT:
                continue
            if market.is_trading_halted():
                break

            reason = (f"{OB_TAG} Opening breakout | {self._plan.trend} | sector {self._plan.sector} "
                      f"({self._plan.sector_pct:+.2f}%) | {ps.symbol} moved {move:+.2f}% from open "
                      f"| R-factor {ps.r_factor} | ATM {self._plan.direction.upper()}")
            trade = place_entry_order(
                env=env, symbol=ps.symbol, token=ps.token,
                direction=self._plan.direction, session_id=session["id"],
                entry_logic=reason, indicators={"opening_move": move, "r_factor": ps.r_factor},
                sl_pct_override=HARD_SL_PCT, target_pct_override=TARGET_PCT,
            )
            if trade:
                ps.entered = True
                open_ob += 1
                update_session_status(env, SessionStatus.ACTIVE)
                print(f"[OB] ENTERED {ps.symbol} {self._plan.direction.upper()} @ "
                      f"{trade.entry_price:.2f} | SL 10% target 50%")

    # ── Management (positional, trailing) ─────────────────────────────────────
    def _manage(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob_trades = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()

        for t in ob_trades:
            token = get_token(t.symbol)
            ltp   = market.get_ltp(token) if token else None
            if not ltp:
                continue
            entry   = t.entry_price
            pnl_pct = (ltp - entry) / entry * 100

            # update peak
            if t.highest_price is None or ltp > t.highest_price:
                self._set_highest(t.id, ltp)
                t.highest_price = ltp

            hard_sl = entry * (1 - HARD_SL_PCT / 100)
            peak    = t.highest_price or ltp
            peak_pct = (peak - entry) / entry * 100

            # trailing stop becomes active after +20%
            trail_stop = hard_sl
            if peak_pct >= TRAIL_ACTIVATE_PCT:
                trail_stop = max(hard_sl, peak * (1 - TRAIL_GAP_PCT / 100))

            # Target (single lot → full exit at +50%)
            if pnl_pct >= TARGET_PCT and t.quantity <= 1:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Target +{TARGET_PCT:.0f}% hit")
                continue
            # Multi-lot: sell half at +50%, runner trails to +100%
            if pnl_pct >= SECOND_TARGET_PCT:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Runner target +{SECOND_TARGET_PCT:.0f}% hit")
                continue

            # Stop / trail exit
            if ltp <= trail_stop:
                lock = (trail_stop - entry) / entry * 100
                if trail_stop > hard_sl:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Trailing stop hit (locked {lock:+.1f}%)")
                else:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Hard SL -{HARD_SL_PCT:.0f}% hit")

    def _square_off(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()
        for t in ob:
            risk_engine.force_exit_trade(t.id, f"{OB_TAG} Square-off 3:15 PM")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _count_open_ob(self, env: TradeEnv) -> int:
        db = DBSession()
        try:
            trades = db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env).all()
            return sum(1 for t in trades if (t.entry_logic or "").startswith(OB_TAG))
        finally:
            db.close()

    def _already_in(self, env: TradeEnv, symbol: str) -> bool:
        db = DBSession()
        try:
            return db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env,
                Trade.symbol == symbol).count() > 0
        finally:
            db.close()

    def _set_highest(self, trade_id: int, price: float):
        db = DBSession()
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                t.highest_price = price
                db.commit()
        finally:
            db.close()

    # ── Public API ────────────────────────────────────────────────────────────
    def get_plan(self) -> dict:
        """Return the current plan; build an on-demand preview if none yet."""
        plan = self._plan or self._build_plan(status="preview")
        if not plan:
            return {"status": "no_data"}
        d = asdict(plan)
        d["phase"]   = self._phase
        d["enabled"] = self._enabled
        return d

    def get_state(self) -> dict:
        return {
            "enabled":     self._enabled,
            "phase":       self._phase,
            "today":       str(self._today) if self._today else None,
            "open_refs":   len(self._open_ref),
            "params": {
                "scan_window":   "09:15–09:40",
                "entry_by":      "09:45",
                "move_min_pct":  MOVE_MIN_PCT,
                "max_positions": MAX_POSITIONS,
                "hard_sl_pct":   HARD_SL_PCT,
                "target_pct":    TARGET_PCT,
                "trail_gap_pct": TRAIL_GAP_PCT,
            },
        }


opening_breakout = OpeningBreakout()
