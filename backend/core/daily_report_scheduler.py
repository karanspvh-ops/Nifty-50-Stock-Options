"""
daily_report_scheduler.py — Sends end-of-day report email in two ways:

  1. Event-driven: OB and ES each call notify_session_done() when their
     session ends. Once both have called in, a background thread polls
     every 10 s until all open trades are closed, then sends the email.

  2. Fallback: a fixed 15:15 IST timer fires in case either strategy
     didn't notify (backend restart, holiday, etc.). Skips if the
     event-driven send already happened today.
"""

import os
import threading
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SEND_HOUR = 15
SEND_MINUTE = 15
RECIPIENTS = ["sujayprakash24@gmail.com", "saurav.prakash@bpaconsulting.in"]

EXPECTED_STRATEGIES = {"OB", "ES"}
POLL_INTERVAL_S = 10


class DailyReportScheduler:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._stopped = False

        # Event-driven tracking — reset each trading day
        self._lock = threading.Lock()
        self._done_strategies: set[str] = set()
        self._report_sent_date: str | None = None  # "YYYY-MM-DD" when sent
        self._poll_thread: threading.Thread | None = None

    # ── Fixed 15:15 fallback timer ────────────────────────────────────────────

    def start(self):
        self._stopped = False
        self._schedule_next()
        print(f"[REPORT] Scheduler started — event-driven + fallback at "
              f"{SEND_HOUR}:{SEND_MINUTE:02d} IST → {', '.join(RECIPIENTS)}")

    def stop(self):
        self._stopped = True
        if self._timer:
            self._timer.cancel()
            self._timer = None
        print("[REPORT] Scheduler stopped.")

    def _schedule_next(self):
        if self._stopped:
            return
        now = datetime.now(IST)
        target = now.replace(hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        self._timer = threading.Timer(delay, self._fire_fallback)
        self._timer.daemon = True
        self._timer.start()
        print(f"[REPORT] Fallback timer set for {target.strftime('%Y-%m-%d %H:%M IST')} "
              f"({delay/3600:.1f}h from now)")

    def _fire_fallback(self):
        try:
            self._send_if_applicable(source="fallback-timer")
        except Exception as e:
            print(f"[REPORT] Fallback send error: {e}")
        finally:
            self._schedule_next()

    # ── Event-driven: strategies call this when their session ends ────────────

    def notify_session_done(self, strategy: str):
        """
        Called by OB/ES when their square-off phase completes.
        Once all expected strategies have notified, waits for open trades
        to clear then sends the report.
        """
        today_str = datetime.now(IST).strftime("%Y-%m-%d")
        with self._lock:
            # Reset on a new day
            if self._report_sent_date and self._report_sent_date != today_str:
                self._done_strategies.clear()
                self._report_sent_date = None

            self._done_strategies.add(strategy)
            all_done = EXPECTED_STRATEGIES.issubset(self._done_strategies)
            already_sent = self._report_sent_date == today_str
            poll_running = self._poll_thread and self._poll_thread.is_alive()

        print(f"[REPORT] {strategy} session done. "
              f"Strategies done today: {self._done_strategies}")

        if all_done and not already_sent and not poll_running:
            self._start_poll_thread()

    def _start_poll_thread(self):
        t = threading.Thread(target=self._poll_until_clear, daemon=True)
        with self._lock:
            self._poll_thread = t
        t.start()
        print("[REPORT] Polling for open trades to clear before sending report…")

    def _poll_until_clear(self):
        while True:
            try:
                open_count = self._count_open_trades_today()
            except Exception as e:
                print(f"[REPORT] Poll error: {e}")
                open_count = -1

            if open_count == 0:
                print("[REPORT] All trades closed — sending report.")
                try:
                    self._send_if_applicable(source="event-driven")
                except Exception as e:
                    print(f"[REPORT] Event-driven send error: {e}")
                return

            print(f"[REPORT] {open_count} trade(s) still open — "
                  f"retrying in {POLL_INTERVAL_S}s…")
            threading.Event().wait(POLL_INTERVAL_S)

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

    # ── Core send logic (shared by both paths) ────────────────────────────────

    def _send_if_applicable(self, source: str = ""):
        now = datetime.now(IST)
        today_str = now.strftime("%Y-%m-%d")

        with self._lock:
            if self._report_sent_date == today_str:
                print(f"[REPORT] Already sent today — skipping ({source})")
                return

        if now.weekday() >= 5:
            print(f"[REPORT] Skipping — weekend ({now.strftime('%A')})")
            return

        smtp_user = os.getenv("SMTP_USER", "")
        smtp_pass = os.getenv("SMTP_PASS", "")
        if not smtp_user or not smtp_pass:
            print("[REPORT] Skipping — SMTP_USER/SMTP_PASS not configured")
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
                    "symbol":     t.symbol,
                    "direction":  t.direction,
                    "strike":     t.strike,
                    "qty":        t.quantity,
                    "lot_size":   t.lot_size,
                    "entry":      t.entry_price,
                    "exit":       t.exit_price,
                    "pnl":        t.pnl or 0,
                    "pnl_pct":    t.pnl_pct or 0,
                    "entered_at": str(t.entered_at) if t.entered_at else "",
                    "exited_at":  str(t.exited_at) if t.exited_at else "",
                    "logic":      t.entry_logic or "",
                }
                for t in closed
            ]
        finally:
            db.close()

        if not trades:
            print(f"[REPORT] No closed trades for {today_str}")
            return

        with self._lock:
            if self._report_sent_date == today_str:
                print(f"[REPORT] Already sent today (race) — skipping ({source})")
                return
            self._report_sent_date = today_str

        self._send_email(smtp_user, smtp_pass, today_str, trades)

    def _send_email(self, smtp_user: str, smtp_pass: str, today_str: str, trades: list):
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
            today_str, "PAPER", today_str, all_s, es_s, ob_s, es_trades, ob_trades
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
