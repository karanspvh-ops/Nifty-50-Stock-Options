"""
daily_report_scheduler.py — Sends an end-of-day email report at 15:15 IST.

Skips automatically when:
  - It's a weekend (Sat/Sun)
  - No trades were executed today (holiday / backend didn't start in time)
  - SMTP is not configured
"""

import os
import threading
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
SEND_HOUR = 15
SEND_MINUTE = 15
RECIPIENTS = ["sujayprakash24@gmail.com", "saurav.prakash@bpaconsulting.in"]


class DailyReportScheduler:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._stopped = False

    def start(self):
        self._stopped = False
        self._schedule_next()
        print(f"[REPORT] Daily report scheduler started — emails {', '.join(RECIPIENTS)} at {SEND_HOUR}:{SEND_MINUTE:02d} IST")

    def stop(self):
        self._stopped = True
        if self._timer:
            self._timer.cancel()
            self._timer = None
        print("[REPORT] Daily report scheduler stopped.")

    def _schedule_next(self):
        if self._stopped:
            return
        now = datetime.now(IST)
        target = now.replace(hour=SEND_HOUR, minute=SEND_MINUTE, second=0, microsecond=0)
        if target <= now:
            target += timedelta(days=1)
        delay = (target - now).total_seconds()
        self._timer = threading.Timer(delay, self._fire)
        self._timer.daemon = True
        self._timer.start()
        print(f"[REPORT] Next report scheduled at {target.strftime('%Y-%m-%d %H:%M IST')} ({delay/3600:.1f}h from now)")

    def _fire(self):
        try:
            self._send_if_applicable()
        except Exception as e:
            print(f"[REPORT] Error sending daily report: {e}")
        finally:
            self._schedule_next()

    def _send_if_applicable(self):
        now = datetime.now(IST)

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

        today_date = now.date()
        from_dt = datetime(today_date.year, today_date.month, today_date.day, 0, 0, 0)
        to_dt   = datetime(today_date.year, today_date.month, today_date.day, 23, 59, 59)

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
                print(f"[REPORT] Skipping — no trades today ({today_date})")
                return
            print(f"[REPORT] {len(today_trades)} trades today — sending report to {', '.join(RECIPIENTS)}...")

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
                    "symbol": t.symbol, "direction": t.direction, "strike": t.strike,
                    "qty": t.quantity, "lot_size": t.lot_size,
                    "entry": t.entry_price, "exit": t.exit_price,
                    "pnl": t.pnl or 0, "pnl_pct": t.pnl_pct or 0,
                    "entered_at": str(t.entered_at) if t.entered_at else "",
                    "exited_at": str(t.exited_at) if t.exited_at else "",
                    "logic": t.entry_logic or "",
                }
                for t in closed
            ]
        finally:
            db.close()

        if not trades:
            print(f"[REPORT] No closed trades to report for {today_date}")
            return

        self._send_email(smtp_user, smtp_pass, str(today_date), trades)

    def _send_email(self, smtp_user: str, smtp_pass: str, today_str: str, trades: list):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))

        def capital(t):
            return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)

        def stats(ts):
            net = sum(t["pnl"] for t in ts)
            wins = sum(1 for t in ts if t["pnl"] > 0)
            caps = [capital(t) for t in ts if capital(t)]
            gp = sum(t["pnl"] for t in ts if t["pnl"] > 0)
            gl = abs(sum(t["pnl"] for t in ts if t["pnl"] < 0))
            pf = round(gp / gl, 2) if gl else ("&infin;" if gp > 0 else 0)
            return {"n": len(ts), "net": net, "wins": wins,
                    "win_pct": round(wins / len(ts) * 100) if ts else 0,
                    "pf": pf,
                    "avg_cap": round(sum(caps) / len(caps)) if caps else 0}

        es_trades = [t for t in trades if t["logic"].startswith("[ES]")]
        ob_trades = [t for t in trades if t["logic"].startswith("[OB]")]
        all_s, es_s, ob_s = stats(trades), stats(es_trades), stats(ob_trades)

        from backend.routers.reports_router import _build_report_html
        html_body = _build_report_html(
            today_str, "PAPER", today_str, all_s, es_s, ob_s, es_trades, ob_trades
        )

        net = all_s['net']
        subject_pnl = f"{'+'if net>=0 else '−'}₹{abs(int(net)):,}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"SPVH AMC Daily Report — {today_str} — {subject_pnl}"
        msg["From"] = smtp_user
        msg["To"] = ", ".join(RECIPIENTS)
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, RECIPIENTS, msg.as_string())

        print(f"[REPORT] Daily report sent to {', '.join(RECIPIENTS)} — {all_s['n']} trades, net {all_s['net']:+,.0f}")


daily_report_scheduler = DailyReportScheduler()
