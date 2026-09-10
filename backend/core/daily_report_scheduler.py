"""
daily_report_scheduler.py — Sends end-of-day report email the moment the
last open trade of the day closes.

Trigger: risk_engine calls on_trade_closed() after every exit. Once the open
trade count hits 0 and it is past 10:30 IST (no new entries possible from
either ES or OB), the report is sent immediately.

Manual override: POST /api/reports/send-now
"""

import os
import threading
from datetime import datetime, time as dtime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))

# After this time no new trades can be entered (ES done 09:30, OB hard-deadline 10:30).
# Gate is 10:31 — one minute of margin so an OB entry at exactly 10:30:00 is not missed.
ALL_ENTRIES_DONE = dtime(10, 31)

RECIPIENTS = ["sujayprakash24@gmail.com", "saurav.prakash@bpaconsulting.in"]


class DailyReportScheduler:
    def __init__(self):
        self._lock = threading.Lock()
        self._report_sent_date: str | None = None  # "YYYY-MM-DD" on send
        self._start_eod_fallback()

    def _start_eod_fallback(self):
        """Fire at 15:20 IST every weekday — catches days where the backend restarted
        after all trades closed and on_trade_closed was never re-triggered."""
        import time

        def _loop():
            while True:
                now    = datetime.now(IST)
                target = now.replace(hour=15, minute=20, second=0, microsecond=0)
                if now >= target:
                    target = target + timedelta(days=1)
                time.sleep((target - now).total_seconds())
                print("[REPORT] EOD fallback trigger firing at 15:20 IST…")
                today_str = datetime.now(IST).strftime("%Y-%m-%d")
                self._send_if_applicable(source="eod-fallback", today_str=today_str)

        threading.Thread(target=_loop, daemon=True, name="ReportEODFallback").start()

    # ── Called by risk_engine after every trade exit ──────────────────────────

    def on_trade_closed(self):
        """
        Fires after every trade exit. Sends the daily report as soon as:
          1. No open trades remain today, AND
          2. It is past 10:30 IST (all entry windows have closed).
        """
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        with self._lock:
            if self._report_sent_date == today_str:
                return  # already sent

        if now.time() < ALL_ENTRIES_DONE:
            return  # still within entry window — more trades possible

        try:
            open_count = self._count_open_trades_today()
        except Exception as e:
            print(f"[REPORT] on_trade_closed check error: {e}")
            return

        if open_count > 0:
            return  # other trades still running

        print("[REPORT] Last trade closed — sending report now.")
        threading.Thread(
            target=self._send_if_applicable,
            kwargs={"source": "last-trade-closed", "today_str": today_str},
            daemon=False,
        ).start()

    # ── Manual / API trigger ──────────────────────────────────────────────────

    def send_now(self):
        """Force-send today's report immediately (manual trigger)."""
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        with self._lock:
            if self._report_sent_date == today_str:
                return {"status": "already_sent", "date": today_str}
        threading.Thread(
            target=self._send_if_applicable,
            kwargs={"source": "manual", "today_str": today_str},
            daemon=False,
        ).start()
        return {"status": "triggered"}

    # ── DB helpers ────────────────────────────────────────────────────────────

    def _count_open_trades_today(self) -> int:
        from backend.database import Session, Trade, TradeEnv, TradeStatus
        from sqlalchemy.orm import Session as DBSession

        now = datetime.now(IST)
        today = now.date()
        from_dt = datetime(today.year, today.month, today.day, 0, 0, 0)
        to_dt   = datetime(today.year, today.month, today.day, 23, 59, 59)

        db: DBSession = Session()
        try:
            return (
                db.query(Trade)
                .filter(
                    Trade.env == TradeEnv.PAPER,
                    Trade.entered_at >= from_dt,
                    Trade.entered_at <= to_dt,
                    Trade.status == TradeStatus.OPEN,
                )
                .count()
            )
        finally:
            db.close()

    # ── Core send logic ───────────────────────────────────────────────────────

    def _send_if_applicable(self, source: str = "", today_str: str = ""):
        now = datetime.now(IST)
        if not today_str:
            today_str = now.strftime("%Y-%m-%d")

        with self._lock:
            if self._report_sent_date == today_str:
                print(f"[REPORT] Already sent today — skipping ({source})")
                return
            self._report_sent_date = today_str  # reserve before releasing lock

        if now.weekday() >= 5:
            print(f"[REPORT] Skipping — weekend ({now.strftime('%A')})")
            with self._lock:
                self._report_sent_date = None  # undo reservation on skip
            return

        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        if not smtp_user or not smtp_pass:
            print("[REPORT] Skipping — SMTP_USER/SMTP_PASS not configured")
            with self._lock:
                self._report_sent_date = None
            return

        from backend.database import Session, Trade, TradeEnv, TradeStatus
        from sqlalchemy.orm import Session as DBSession

        today = now.date()
        from_dt = datetime(today.year, today.month, today.day, 0, 0, 0)
        to_dt   = datetime(today.year, today.month, today.day, 23, 59, 59)

        db: DBSession = Session()
        try:
            today_trades = (
                db.query(Trade)
                .filter(Trade.env == TradeEnv.PAPER,
                        Trade.entered_at >= from_dt,
                        Trade.entered_at <= to_dt)
                .all()
            )
            if not today_trades:
                print(f"[REPORT] Skipping — no trades today ({today_str})")
                with self._lock:
                    self._report_sent_date = None
                return

            print(f"[REPORT] {len(today_trades)} trades today — "
                  f"sending to {', '.join(RECIPIENTS)} [{source}]…")

            closed = (
                db.query(Trade)
                .filter(Trade.env == TradeEnv.PAPER,
                        Trade.entered_at >= from_dt,
                        Trade.entered_at <= to_dt,
                        Trade.status != TradeStatus.OPEN)
                .order_by(Trade.entered_at)
                .all()
            )
            trades = [
                {
                    "symbol":      t.symbol,
                    "direction":   t.direction,
                    "option_type": t.option_type,
                    "strike":      t.strike,
                    "qty":         t.quantity,
                    "lot_size":    t.lot_size,
                    "entry":       t.entry_price,
                    "exit":        t.exit_price,
                    "peak":        t.highest_price,
                    "peak_pct":    round((t.highest_price - t.entry_price) / t.entry_price * 100, 1)
                                   if (t.highest_price and t.entry_price
                                       and t.highest_price > t.entry_price) else None,
                    "pnl":         t.pnl or 0,
                    "pnl_pct":     t.pnl_pct or 0,
                    "entered_at":  str(t.entered_at) if t.entered_at else "",
                    "exited_at":   str(t.exited_at)  if t.exited_at  else "",
                    "logic":       t.entry_logic or "",
                }
                for t in closed
            ]
            prior_es = (
                db.query(Trade)
                .filter(Trade.env == TradeEnv.PAPER,
                        Trade.entered_at < from_dt,
                        Trade.status != TradeStatus.OPEN,
                        Trade.entry_logic.like("[ES]%"))
                .all()
            )
            es_start_balance = 500_000 + sum((t.pnl or 0) for t in prior_es)
        finally:
            db.close()

        if not trades:
            print(f"[REPORT] No closed trades for {today_str}")
            with self._lock:
                self._report_sent_date = None
            return

        try:
            self._send_email(smtp_user, smtp_pass, today_str, trades, es_start_balance)
        except Exception as e:
            print(f"[REPORT] SMTP error — resetting reservation so next trigger can retry: {e}")
            with self._lock:
                self._report_sent_date = None

    def _send_email(self, smtp_user: str, smtp_pass: str, today_str: str, trades: list,
                    es_start_balance: float = 500_000):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))

        def capital(t):
            return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)

        def stats(ts):
            net  = sum(t["pnl"] for t in ts)
            wins = sum(1 for t in ts if t["pnl"] > 0)
            caps = [capital(t) for t in ts if capital(t)]
            gp   = sum(t["pnl"] for t in ts if t["pnl"] > 0)
            gl   = abs(sum(t["pnl"] for t in ts if t["pnl"] < 0))
            pf   = round(gp / gl, 2) if gl else ("&infin;" if gp > 0 else 0)
            return {
                "n": len(ts), "net": net, "wins": wins,
                "win_pct": round(wins / len(ts) * 100) if ts else 0,
                "pf": pf,
                "avg_cap": round(sum(caps) / len(caps)) if caps else 0,
            }

        es_trades = [t for t in trades if t["logic"].startswith("[ES]")]
        ob_trades = [t for t in trades if t["logic"].startswith("[OB]")]
        all_s, es_s, ob_s = stats(trades), stats(es_trades), stats(ob_trades)

        from backend.routers.reports_router import _build_report_html
        html_body = _build_report_html(
            today_str, "PAPER", today_str, all_s, es_s, ob_s, es_trades, ob_trades,
            es_start_balance
        )

        net = all_s["net"]
        subject_pnl = f"{'+'if net>=0 else '−'}₹{abs(int(net)):,}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"SPVH AMC Daily Report — {today_str} — {subject_pnl}"
        msg["From"]    = smtp_user
        msg["To"]      = ", ".join(RECIPIENTS)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, RECIPIENTS, msg.as_string())

        print(f"[REPORT] Sent to {', '.join(RECIPIENTS)} — "
              f"{all_s['n']} trades, net {all_s['net']:+,.0f}")


daily_report_scheduler = DailyReportScheduler()
