"""
tradable_tracker.py — Watches every stock in the active index and records
"tradable windows": the span of time a stock satisfies our entry criteria.

For each stock with enough data:
  direction = call (up) / put (down) based on its move
  signal    = entry_engine.check_entry(...)
  - should_enter True  -> open a window (if not already open)
  - should_enter False -> close the open window (if any)

Active windows are held in memory and exposed for the dashboard's
"flashing" tradable panel. Closed windows are persisted to SQLite
and rolled up into a daily report (separate from PnL).

Runs as a background thread, every SCAN_INTERVAL seconds.
"""

import os, sys, threading, time, json
from datetime import datetime, date
from typing import Dict, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state     import market
from backend.core.entry_engine     import check_entry, ENTRY_SCORE_THRESHOLD
from backend.core.stock_universe   import get_stocks_for_index, get_meta
from backend.core.settings_manager import get_active_index, is_live_mode
from backend.database import (
    Session as DBSession, TradableSignal, Report,
    TradeEnv, TradeDirection,
)

SCAN_INTERVAL   = 12      # seconds between sweeps
MAX_EVALUATE    = 80      # cap stocks evaluated per sweep (top movers)
MIN_ABS_MOVE    = 0.3     # ignore near-flat stocks
REPORTS_DIR     = os.path.join(ROOT, "reports", "tradable")
os.makedirs(REPORTS_DIR, exist_ok=True)


class TradableTracker:
    def __init__(self):
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # token -> active window dict
        self._active: Dict[str, dict] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="TradableTracker")
        self._thread.start()
        print("[TRADABLE] Tracker started.")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._sweep()
            except Exception as e:
                print(f"[TRADABLE] Sweep error: {e}")
            time.sleep(SCAN_INTERVAL)

    # ── One sweep over the universe ───────────────────────────────────────────
    def _sweep(self):
        if not market.is_feed_connected():
            return

        env          = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER
        stocks       = get_stocks_for_index("FNO", fno_only=True)       # combined F&O universe (~211)
        moves        = market.get_all_stock_moves()
        sector_moves = market.get_sector_moves()

        # Rank by absolute move, evaluate only the top movers (efficiency)
        ranked = sorted(
            [s for s in stocks if moves.get(s["token"])],
            key=lambda s: abs(moves[s["token"]].get("pct_change", 0)),
            reverse=True,
        )[:MAX_EVALUATE]

        seen_tradable = set()

        for s in ranked:
            token = s["token"]
            move  = moves.get(token, {})
            pct   = move.get("pct_change", 0) or 0
            if abs(pct) < MIN_ABS_MOVE:
                continue

            meta = get_meta(token)
            if meta.get("lot_size", 0) <= 0:
                continue   # not an options stock — can't trade it

            direction = "call" if pct > 0 else "put"
            signal    = check_entry(token, direction)

            if signal.should_enter:
                seen_tradable.add(token)
                if token not in self._active:
                    self._open_window(env, s, move, signal, sector_moves)
            # if not tradable, it will be closed below if it was active

        # Close any active windows no longer tradable
        for token in list(self._active.keys()):
            if token not in seen_tradable:
                self._close_window(token)

    # ── Window open / close ───────────────────────────────────────────────────
    def _open_window(self, env, stock, move, signal, sector_moves):
        token  = stock["token"]
        sector = stock.get("sector", "")
        now    = datetime.now()
        self._active[token] = {
            "env":        env,
            "symbol":     stock["symbol"],
            "token":      token,
            "sector":     sector,
            "direction":  signal.direction,
            "opened_at":  now,
            "entry_score":signal.score,
            "max_score":  signal.max_score,
            "ltp_at_open":move.get("ltp"),
            "sector_pct": sector_moves.get(sector, {}).get("pct_change", 0),
            "reason":     signal.reasoning,
        }
        print(f"[TRADABLE] + {stock['symbol']} {signal.direction.upper()} "
              f"score {signal.score}/8 @ {now.strftime('%H:%M:%S')}")

    def _close_window(self, token: str):
        w = self._active.pop(token, None)
        if not w:
            return
        now      = datetime.now()
        duration = int((now - w["opened_at"]).total_seconds())
        ltp_now  = market.get_ltp(token)

        db = DBSession()
        try:
            db.add(TradableSignal(
                date         = date.today().isoformat(),
                env          = w["env"],
                symbol       = w["symbol"],
                token        = token,
                sector       = w["sector"],
                direction    = TradeDirection(w["direction"]),
                opened_at    = w["opened_at"],
                closed_at    = now,
                duration_sec = duration,
                entry_score  = w["entry_score"],
                max_score    = w["max_score"],
                ltp_at_open  = w["ltp_at_open"],
                ltp_at_close = ltp_now,
                sector_pct   = w["sector_pct"],
                reason       = w["reason"],
                was_traded   = w.get("_was_traded", False),
            ))
            db.commit()
        finally:
            db.close()
        print(f"[TRADABLE] - {w['symbol']} closed after {duration}s")

    # ── Public read API ───────────────────────────────────────────────────────
    def get_active(self) -> list:
        """Currently tradable stocks (for the flashing panel)."""
        out = []
        for w in self._active.values():
            ltp_now = market.get_ltp(w["token"])
            out.append({
                "symbol":      w["symbol"],
                "token":       w["token"],
                "sector":      w["sector"],
                "direction":   w["direction"],
                "opened_at":   w["opened_at"].isoformat(),
                "live_secs":   int((datetime.now() - w["opened_at"]).total_seconds()),
                "entry_score": w["entry_score"],
                "max_score":   w["max_score"],
                "ltp":         ltp_now,
                "sector_pct":  w["sector_pct"],
                "reason":      w["reason"],
            })
        return sorted(out, key=lambda x: x["entry_score"], reverse=True)

    def get_history(self, env: TradeEnv, date_str: str = None) -> list:
        """Closed (and active) tradable windows for the day."""
        date_str = date_str or date.today().isoformat()
        db = DBSession()
        try:
            rows = (
                db.query(TradableSignal)
                .filter(TradableSignal.date == date_str, TradableSignal.env == env)
                .order_by(TradableSignal.opened_at.desc())
                .all()
            )
            history = [self._row_to_dict(r) for r in rows]
        finally:
            db.close()
        # Prepend still-active windows
        active = [{**a, "closed_at": None, "duration_sec": a["live_secs"], "status": "active"}
                  for a in self.get_active()]
        return active + history

    # ── Report generation (separate from PnL) ─────────────────────────────────
    def generate_report(self, env: TradeEnv, date_str: str = None) -> dict:
        date_str = date_str or date.today().isoformat()
        db = DBSession()
        try:
            rows = (
                db.query(TradableSignal)
                .filter(TradableSignal.date == date_str, TradableSignal.env == env)
                .order_by(TradableSignal.opened_at)
                .all()
            )
            signals = [self._row_to_dict(r) for r in rows]

            total      = len(signals)
            traded     = sum(1 for s in signals if s["was_traded"])
            avg_dur    = round(sum(s["duration_sec"] or 0 for s in signals) / total, 1) if total else 0
            by_sector  = {}
            for s in signals:
                by_sector[s["sector"]] = by_sector.get(s["sector"], 0) + 1

            report = {
                "report_type":  "tradable_signals",
                "date":         date_str,
                "env":          env.value,
                "generated_at": datetime.utcnow().isoformat(),
                "summary": {
                    "total_windows":      total,
                    "windows_traded":     traded,
                    "windows_missed":     total - traded,
                    "avg_window_seconds": avg_dur,
                    "by_sector":          by_sector,
                },
                "signals": signals,
            }

            content = json.dumps(report, indent=2)
            existing = (
                db.query(Report)
                .filter(Report.date == date_str, Report.env == env,
                        Report.report_type == "tradable_signals")
                .first()
            )
            if existing:
                existing.content = content
                existing.generated_at = datetime.utcnow()
            else:
                db.add(Report(report_type="tradable_signals", date=date_str,
                              env=env, content=content))
            db.commit()
        finally:
            db.close()

        with open(os.path.join(REPORTS_DIR, f"{date_str}-{env.value}.json"), "w") as f:
            json.dump(report, f, indent=2)
        print(f"[TRADABLE] Report generated | {date_str} {env.value} | windows: {total}")
        return report

    def get_report(self, env: TradeEnv, date_str: str = None) -> dict:
        date_str = date_str or date.today().isoformat()
        db = DBSession()
        try:
            row = (
                db.query(Report)
                .filter(Report.date == date_str, Report.env == env,
                        Report.report_type == "tradable_signals")
                .first()
            )
            return json.loads(row.content) if row else {}
        finally:
            db.close()

    # ── Helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _row_to_dict(r: TradableSignal) -> dict:
        return {
            "id":           r.id,
            "symbol":       r.symbol,
            "token":        r.token,
            "sector":       r.sector,
            "direction":    r.direction,
            "opened_at":    str(r.opened_at),
            "closed_at":    str(r.closed_at) if r.closed_at else None,
            "duration_sec": r.duration_sec,
            "entry_score":  r.entry_score,
            "max_score":    r.max_score,
            "ltp_at_open":  r.ltp_at_open,
            "ltp_at_close": r.ltp_at_close,
            "sector_pct":   r.sector_pct,
            "reason":       r.reason,
            "was_traded":   r.was_traded,
            "status":       "closed" if r.closed_at else "active",
        }

    def mark_traded(self, symbol: str):
        """Called by trading engine when it actually enters a trade on a symbol."""
        for w in self._active.values():
            if w["symbol"] == symbol:
                w["_was_traded"] = True


tradable_tracker = TradableTracker()
