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
RECIPIENT = "sujayprakash24@gmail.com"


class DailyReportScheduler:
    def __init__(self):
        self._timer: threading.Timer | None = None
        self._stopped = False

    def start(self):
        self._stopped = False
        self._schedule_next()
        print(f"[REPORT] Daily report scheduler started — emails {RECIPIENT} at {SEND_HOUR}:{SEND_MINUTE:02d} IST")

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

        db: DBSession = Session()
        try:
            today_str = now.strftime("%Y-%m-%d")
            today_trades = (
                db.query(Trade)
                .filter(
                    Trade.env == TradeEnv.PAPER,
                    Trade.entered_at >= f"{today_str}T00:00:00",
                    Trade.entered_at <= f"{today_str}T23:59:59",
                )
                .all()
            )
            if not today_trades:
                print(f"[REPORT] Skipping — no trades today ({today_str})")
                return
            print(f"[REPORT] {len(today_trades)} trades today — sending report to {RECIPIENT}...")
        finally:
            db.close()

        self._send_email(smtp_user, smtp_pass, today_str)

    def _send_email(self, smtp_user: str, smtp_pass: str, today_str: str):
        import smtplib
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText
        from datetime import date, timedelta
        from backend.database import Session, Trade, TradeEnv, TradeStatus
        from sqlalchemy.orm import Session as DBSession

        smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
        smtp_port = int(os.getenv("SMTP_PORT", "587"))

        db: DBSession = Session()
        try:
            rows = (
                db.query(Trade)
                .filter(
                    Trade.env == TradeEnv.PAPER,
                    Trade.entered_at >= f"{today_str}T00:00:00",
                    Trade.entered_at <= f"{today_str}T23:59:59",
                    Trade.status != TradeStatus.OPEN,
                )
                .order_by(Trade.entered_at)
                .all()
            )
            trades = [
                {
                    "symbol": t.symbol,
                    "direction": t.direction,
                    "strike": t.strike,
                    "qty": t.quantity,
                    "lot_size": t.lot_size,
                    "entry": t.entry_price,
                    "exit": t.exit_price,
                    "pnl": t.pnl or 0,
                    "pnl_pct": t.pnl_pct or 0,
                    "entered_at": str(t.entered_at) if t.entered_at else "",
                    "exited_at": str(t.exited_at) if t.exited_at else "",
                    "logic": t.entry_logic or "",
                }
                for t in rows
            ]
        finally:
            db.close()

        if not trades:
            print(f"[REPORT] No closed trades to report for {today_str}")
            return

        def is_es(t): return t["logic"].startswith("[ES]")
        def is_ob(t): return t["logic"].startswith("[OB]")
        def capital(t):
            e = t.get("entry") or 0
            q = t.get("qty") or 0
            l = t.get("lot_size") or 1
            return e * q * l

        def stats(ts):
            net = sum(t["pnl"] for t in ts)
            wins = sum(1 for t in ts if t["pnl"] > 0)
            caps = [capital(t) for t in ts if capital(t)]
            gp = sum(t["pnl"] for t in ts if t["pnl"] > 0)
            gl = abs(sum(t["pnl"] for t in ts if t["pnl"] < 0))
            pf = round(gp / gl, 2) if gl else ("∞" if gp > 0 else 0)
            return {
                "n": len(ts),
                "net": net,
                "wins": wins,
                "win_pct": round(wins / len(ts) * 100) if ts else 0,
                "pf": pf,
                "avg_cap": round(sum(caps) / len(caps)) if caps else 0,
            }

        es_trades = [t for t in trades if is_es(t)]
        ob_trades = [t for t in trades if is_ob(t)]
        all_stats = stats(trades)
        es_stats = stats(es_trades)
        ob_stats = stats(ob_trades)

        def fmt_pnl(v):
            sign = "+" if v >= 0 else "−"
            color = "#22c55e" if v >= 0 else "#ef4444"
            return f'<span style="color:{color};font-weight:600">{sign}₹{abs(int(v)):,}</span>'

        def trade_rows(ts):
            if not ts:
                return '<tr><td colspan="9" style="color:#888;text-align:center;padding:12px">No trades</td></tr>'
            html = ""
            for t in ts:
                cap = capital(t)
                pnl_color = "#22c55e" if t["pnl"] >= 0 else "#ef4444"
                entered = (t["entered_at"] or "")[-8:][:5] or "—"
                exited = (t["exited_at"] or "")[-8:][:5] or "—"
                html += f"""<tr>
                  <td>{t['symbol']}</td>
                  <td>{(t['direction'] or '').upper()}</td>
                  <td>{t['strike'] or '—'}</td>
                  <td>{t['qty'] or '—'}</td>
                  <td>₹{t['entry']:.2f}</td>
                  <td>{f"₹{t['exit']:.2f}" if t['exit'] else '—'}</td>
                  <td>{f"₹{int(cap):,}" if cap else '—'}</td>
                  <td style="color:{pnl_color};font-weight:600">
                    {"+" if t['pnl'] >= 0 else "−"}₹{abs(int(t['pnl'])):,}
                  </td>
                  <td style="color:{pnl_color}">{t['pnl_pct']:.1f}%</td>
                  <td style="color:#888">{entered}</td>
                  <td style="color:#888">{exited}</td>
                </tr>"""
            return html

        def section_html(title, ts, st, color):
            return f"""
            <div style="margin-bottom:32px">
              <div style="background:{color}15;border-top:3px solid {color};padding:14px 18px;margin-bottom:12px">
                <span style="color:{color};font-weight:700;font-size:16px">{title}</span>
                <div style="display:flex;gap:28px;margin-top:10px;flex-wrap:wrap">
                  <div><div style="font-size:10px;text-transform:uppercase;color:#888">Trades</div>
                       <div style="font-size:20px;font-weight:700">{st['n']}</div></div>
                  <div><div style="font-size:10px;text-transform:uppercase;color:#888">Win Rate</div>
                       <div style="font-size:20px;font-weight:700">{st['win_pct']}%</div></div>
                  <div><div style="font-size:10px;text-transform:uppercase;color:#888">Net PnL</div>
                       <div style="font-size:20px">{fmt_pnl(st['net'])}</div></div>
                  <div><div style="font-size:10px;text-transform:uppercase;color:#888">Profit Factor</div>
                       <div style="font-size:20px;font-weight:700">{st['pf']}</div></div>
                  <div><div style="font-size:10px;text-transform:uppercase;color:#888">Avg Capital</div>
                       <div style="font-size:20px;font-weight:700">{"₹" + f"{st['avg_cap']:,}" if st['avg_cap'] else "—"}</div></div>
                </div>
              </div>
              <table style="width:100%;border-collapse:collapse;font-size:12px">
                <thead><tr style="background:#f3f4f6;font-size:10px;text-transform:uppercase;letter-spacing:.05em">
                  <th style="padding:6px 8px;text-align:left">Symbol</th>
                  <th style="padding:6px 8px;text-align:left">Dir</th>
                  <th style="padding:6px 8px;text-align:left">Strike</th>
                  <th style="padding:6px 8px;text-align:left">Qty</th>
                  <th style="padding:6px 8px;text-align:left">Entry</th>
                  <th style="padding:6px 8px;text-align:left">Exit</th>
                  <th style="padding:6px 8px;text-align:left">Capital</th>
                  <th style="padding:6px 8px;text-align:left">PnL</th>
                  <th style="padding:6px 8px;text-align:left">%</th>
                  <th style="padding:6px 8px;text-align:left">In</th>
                  <th style="padding:6px 8px;text-align:left">Out</th>
                </tr></thead>
                <tbody>{trade_rows(ts)}</tbody>
              </table>
            </div>"""

        html_body = f"""<!DOCTYPE html><html><head><meta charset="utf-8"/></head>
        <body style="font-family:'Segoe UI',Arial,sans-serif;color:#111;max-width:900px;margin:0 auto;padding:24px">
          <h1 style="font-size:22px;margin-bottom:4px">SPVH AMC — Daily Trading Report</h1>
          <p style="color:#666;font-size:12px;margin-bottom:24px">
            Date: {today_str} &nbsp;|&nbsp; Mode: PAPER &nbsp;|&nbsp; Auto-generated at 15:15 IST
          </p>

          <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-bottom:28px">
            <div style="font-weight:700;font-size:14px;margin-bottom:10px">Today's Summary</div>
            <div style="display:flex;gap:28px;flex-wrap:wrap">
              <div><div style="font-size:10px;text-transform:uppercase;color:#888">Total Trades</div>
                   <div style="font-size:24px;font-weight:700">{all_stats['n']}</div></div>
              <div><div style="font-size:10px;text-transform:uppercase;color:#888">Win Rate</div>
                   <div style="font-size:24px;font-weight:700">{all_stats['win_pct']}%</div></div>
              <div><div style="font-size:10px;text-transform:uppercase;color:#888">Net PnL</div>
                   <div style="font-size:24px">{fmt_pnl(all_stats['net'])}</div></div>
              <div><div style="font-size:10px;text-transform:uppercase;color:#888">Profit Factor</div>
                   <div style="font-size:24px;font-weight:700">{all_stats['pf']}</div></div>
              <div><div style="font-size:10px;text-transform:uppercase;color:#888">Avg Capital</div>
                   <div style="font-size:24px;font-weight:700">{"₹" + f"{all_stats['avg_cap']:,}" if all_stats['avg_cap'] else "—"}</div></div>
            </div>
          </div>

          {section_html("Early Scalp [ES]", es_trades, es_stats, "#a855f7")}
          <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0"/>
          {section_html("Opening Breakout [OB]", ob_trades, ob_stats, "#3b82f6")}

          <p style="color:#aaa;font-size:10px;margin-top:32px;text-align:center">
            Auto-generated by SPVH AMC Trading Platform. Do not reply to this email.
          </p>
        </body></html>"""

        msg = MIMEMultipart("alternative")
        net = all_stats['net']
        subject_pnl = f"{'+'if net>=0 else '−'}₹{abs(int(net)):,}"
        msg["Subject"] = f"SPVH AMC Daily Report — {today_str} — {subject_pnl}"
        msg["From"] = smtp_user
        msg["To"] = RECIPIENT
        msg.attach(MIMEText(html_body, "html"))

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, RECIPIENT, msg.as_string())

        print(f"[REPORT] Daily report sent to {RECIPIENT} — {all_stats['n']} trades, net {all_stats['net']:+,.0f}")


daily_report_scheduler = DailyReportScheduler()
