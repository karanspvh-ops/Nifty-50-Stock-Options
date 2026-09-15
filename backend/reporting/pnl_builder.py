"""pnl_builder.py — PnL & Trade Report Generator.

Runs as a background thread. Polls every 30s; regenerates the daily
report whenever the closed-trade count changes.

Edit this file to change:
  - What fields are included per trade in the report
  - The session summary metrics
  - How the trade statement (human-readable sentence) is built
"""

import os
import json
import threading
import time
from datetime import date, datetime
from typing import List

from backend.database import (
    Session as DBSession, Trade, TradingSession, Report,
    TradeStatus, TradeEnv,
)

_ROOT       = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR = os.path.join(_ROOT, "reports", "pnl")
os.makedirs(REPORTS_DIR, exist_ok=True)

CHECK_INTERVAL = 30   # seconds between report generation cycles


class PnLAgent:

    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._last_trade_count = {"paper": 0, "live": 0}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="PnLAgent")
        self._thread.start()
        print("[PnL AGENT] Started.")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                for env in (TradeEnv.PAPER, TradeEnv.LIVE):
                    self._check_and_generate(env)
            except Exception as e:
                print(f"[PnL AGENT] Error: {e}")
            time.sleep(CHECK_INTERVAL)

    def _check_and_generate(self, env: TradeEnv):
        db = DBSession()
        try:
            today_str = date.today().isoformat()
            closed_trades = (
                db.query(Trade)
                .join(TradingSession)
                .filter(
                    TradingSession.date == today_str,
                    Trade.env == env,
                    Trade.status != TradeStatus.OPEN,
                )
                .all()
            )
            key = env.value
            if len(closed_trades) != self._last_trade_count.get(key, 0):
                self._last_trade_count[key] = len(closed_trades)
                if closed_trades:
                    self.generate_daily_report(env, today_str)
        finally:
            db.close()

    # ── Core report builder ───────────────────────────────────────────────────

    def generate_daily_report(self, env: TradeEnv, date_str: str = None) -> dict:
        date_str = date_str or date.today().isoformat()
        db = DBSession()
        try:
            session = (
                db.query(TradingSession)
                .filter(TradingSession.date == date_str, TradingSession.env == env)
                .first()
            )
            trades = (
                db.query(Trade)
                .join(TradingSession)
                .filter(
                    TradingSession.date == date_str,
                    Trade.env == env,
                    Trade.status != TradeStatus.OPEN,
                )
                .order_by(Trade.entered_at)
                .all()
            )

            if not trades:
                return {}

            trade_rows = []
            for t in trades:
                peak     = t.highest_price
                peak_pct = (
                    round((peak - t.entry_price) / t.entry_price * 100, 2)
                    if peak and t.entry_price and peak > t.entry_price else None
                )
                trade_rows.append({
                    "id":              t.id,
                    "symbol":          t.symbol,
                    "option":          t.option_symbol,
                    "strike":          t.strike,
                    "expiry":          t.expiry,
                    "option_type":     t.option_type,
                    "direction":       t.direction,
                    "entry":           t.entry_price,
                    "exit":            t.exit_price,
                    "peak":            peak,
                    "peak_pct":        peak_pct,
                    "qty":             t.quantity,
                    "lot_size":        t.lot_size,
                    "pnl":             t.pnl,
                    "pnl_pct":         t.pnl_pct,
                    "status":          t.status,
                    "entered_at":      str(t.entered_at),
                    "exited_at":       str(t.exited_at) if t.exited_at else None,
                    "entry_logic":     t.entry_logic or "",
                    "exit_logic":      t.exit_logic or "",
                    "trade_statement": self._build_trade_statement(t),
                    "indicators":      t.indicators_snapshot or {},
                })

            total_pnl     = sum(t.pnl or 0 for t in trades)
            wins          = [t for t in trades if (t.pnl or 0) > 0]
            losses        = [t for t in trades if (t.pnl or 0) <= 0]
            win_rate      = round(len(wins) / len(trades) * 100, 1) if trades else 0
            avg_win       = round(sum(t.pnl for t in wins)   / len(wins),   2) if wins   else 0
            avg_loss      = round(sum(t.pnl for t in losses) / len(losses), 2) if losses else 0
            profit_factor = abs(avg_win / avg_loss) if avg_loss else 0

            report = {
                "date":         date_str,
                "env":          env.value,
                "generated_at": datetime.utcnow().isoformat(),
                "report_type":  "pnl",
                "session": {
                    "sector": session.selected_sector if session else None,
                    "stocks": session.selected_stocks if session else [],
                    "status": session.status if session else None,
                },
                "summary": {
                    "total_trades":  len(trades),
                    "wins":          len(wins),
                    "losses":        len(losses),
                    "win_rate_pct":  win_rate,
                    "total_pnl":     round(total_pnl, 2),
                    "avg_win":       avg_win,
                    "avg_loss":      avg_loss,
                    "profit_factor": round(profit_factor, 2),
                    "best_trade":    max((t.pnl or 0 for t in trades), default=0),
                    "worst_trade":   min((t.pnl or 0 for t in trades), default=0),
                },
                "trades": trade_rows,
            }

            content_str = json.dumps(report, indent=2)
            existing = (
                db.query(Report)
                .filter(Report.date == date_str, Report.env == env,
                        Report.report_type == "pnl")
                .first()
            )
            if existing:
                existing.content      = content_str
                existing.generated_at = datetime.utcnow()
            else:
                db.add(Report(report_type="pnl", date=date_str, env=env, content=content_str))
            db.commit()

            fname = os.path.join(REPORTS_DIR, f"{date_str}-{env.value}.json")
            with open(fname, "w") as f:
                json.dump(report, f, indent=2)

            print(f"[PnL AGENT] Report generated: {fname} | "
                  f"Trades: {len(trades)} | PnL: {total_pnl:.2f} | Win rate: {win_rate}%")
            return report
        finally:
            db.close()

    def _build_trade_statement(self, t: Trade) -> str:
        direction    = "CALL" if t.direction == "call" else "PUT"
        ind          = t.indicators_snapshot or {}
        entry_reason = t.entry_logic or "Entry triggered by indicator alignment"
        exit_reason  = t.exit_logic  or "Exit triggered by risk engine"
        pnl_sign     = "profit" if (t.pnl or 0) > 0 else "loss"
        pnl_val      = abs(t.pnl or 0)

        contract = ""
        if t.strike and t.option_type:
            contract = f" — strike {t.strike:g} {t.option_type}"
            if t.expiry:
                contract += f" (expiry {t.expiry})"

        def _t(s):
            if not s: return "—"
            s = str(s)
            return s.split(" ")[1].split(".")[0] if " " in s else s

        peak       = t.highest_price
        peak_blurb = ""
        if peak and t.entry_price and peak > t.entry_price:
            pp         = (peak - t.entry_price) / t.entry_price * 100
            peak_blurb = f" Peak premium ₹{peak:.2f} ({pp:+.1f}%)."

        stmt = (
            f"Bought {direction} on {t.symbol}{contract} at ₹{t.entry_price:.2f} "
            f"[{_t(t.entered_at)} IST]. "
            f"Entry reason: {entry_reason[:140]}. "
            f"Exited at ₹{(t.exit_price if t.exit_price else 0):.2f} "
            f"[{_t(t.exited_at)} IST]. "
            f"Exit reason: {exit_reason[:140]}.{peak_blurb} "
            f"Result: ₹{pnl_val:.2f} {pnl_sign} ({t.pnl_pct:.1f}%)."
        )
        if ind.get("rsi"):
            stmt += f" RSI at entry: {ind['rsi']:.1f}."
        if ind.get("adx"):
            stmt += f" ADX: {ind['adx']:.1f}."
        return stmt

    # ── Public read API ───────────────────────────────────────────────────────

    def get_report(self, env: TradeEnv, date_str: str = None) -> dict:
        date_str = date_str or date.today().isoformat()
        db = DBSession()
        try:
            row = (
                db.query(Report)
                .filter(Report.date == date_str, Report.env == env,
                        Report.report_type == "pnl")
                .first()
            )
            return json.loads(row.content) if row else {}
        finally:
            db.close()

    def list_reports(self, env: TradeEnv) -> List[str]:
        db = DBSession()
        try:
            rows = (
                db.query(Report.date)
                .filter(Report.env == env, Report.report_type == "pnl")
                .order_by(Report.date.desc())
                .all()
            )
            return [r.date for r in rows]
        finally:
            db.close()


# ── Singleton ──────────────────────────────────────────────────────────────────
pnl_agent = PnLAgent()
