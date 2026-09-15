"""email_sender.py — SMTP email delivery for trading reports.

Edit this file to change:
  - Email template subject line
  - Date-range query logic (today / 7d / 30d / custom / all)
  - SMTP connection parameters

This module has NO FastAPI imports — it is pure Python so it can be
called from the scheduler or from the REST endpoint without coupling.
"""

import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from typing               import Optional

from sqlalchemy.orm import Session as DBSession

from backend.database      import Session, Trade, TradeStatus, TradeEnv
from backend.reporting.stats       import build_stats
from backend.reporting.html_builder import build_report_html


def _query_trades(env: TradeEnv, from_dt: datetime, to_dt: datetime) -> list:
    db: DBSession = Session()
    try:
        rows = (
            db.query(Trade)
            .filter(
                Trade.env == env,
                Trade.entered_at >= from_dt,
                Trade.entered_at <= to_dt,
                Trade.status != TradeStatus.OPEN,
            )
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
                "peak_pct":    (
                    round((t.highest_price - t.entry_price) / t.entry_price * 100, 1)
                    if (t.highest_price and t.entry_price and t.highest_price > t.entry_price)
                    else None
                ),
                "pnl":         t.pnl or 0,
                "pnl_pct":     t.pnl_pct or 0,
                "entered_at":  str(t.entered_at) if t.entered_at else "",
                "exited_at":   str(t.exited_at)  if t.exited_at  else "",
                "logic":       t.entry_logic or "",
            }
            for t in rows
        ]

        # Opening balance for ES capital chart = ₹5L + prior ES PnL
        prior_es = (
            db.query(Trade)
            .filter(
                Trade.env == env,
                Trade.entered_at < from_dt,
                Trade.status != TradeStatus.OPEN,
                Trade.entry_logic.like("[ES]%"),
            )
            .all()
        )
        es_start_balance = 500_000 + sum((t.pnl or 0) for t in prior_es)
        return trades, es_start_balance
    finally:
        db.close()


def _date_range(period: str, custom_from: Optional[str], custom_to: Optional[str]):
    today = date.today()
    if period == "today":
        return today, today
    if period == "7d":
        return today - timedelta(days=6), today
    if period == "30d":
        return today - timedelta(days=29), today
    if period == "custom":
        return date.fromisoformat(custom_from), date.fromisoformat(custom_to)
    return date(2000, 1, 1), today   # "all"


def send_report(
    env:         TradeEnv,
    to_email:    str,
    period:      str,
    custom_from: Optional[str] = None,
    custom_to:   Optional[str] = None,
) -> dict:
    """
    Build and send an HTML report email.
    Returns {"status": "sent", "to": ..., "period": ..., "trades": N}.
    Raises ValueError for bad config, RuntimeError for SMTP failure.
    """
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        raise ValueError("SMTP not configured. Set SMTP_USER and SMTP_PASS environment variables.")

    from_date, to_date = _date_range(period, custom_from, custom_to)
    from_dt = datetime(from_date.year, from_date.month, from_date.day,  0,  0,  0)
    to_dt   = datetime(to_date.year,   to_date.month,   to_date.day,  23, 59, 59)

    trades, es_start_balance = _query_trades(env, from_dt, to_dt)

    es_trades = [t for t in trades if t["logic"].startswith("[ES]")]
    ob_trades = [t for t in trades if t["logic"].startswith("[OB]")]
    all_s, es_s, ob_s = build_stats(trades), build_stats(es_trades), build_stats(ob_trades)

    period_label = {
        "today":  str(date.today()),
        "7d":     f"Last 7 days (to {date.today()})",
        "30d":    f"Last 30 days (to {date.today()})",
        "custom": f"{from_date} to {to_date}",
        "all":    "All Time",
    }.get(period, period)

    html_body = build_report_html(
        period_label, env.value.upper(), str(date.today()),
        all_s, es_s, ob_s, es_trades, ob_trades, es_start_balance,
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SPVH AMC Report — {period_label}"
    msg["From"]    = smtp_user
    msg["To"]      = to_email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, to_email, msg.as_string())
    except Exception as exc:
        raise RuntimeError(f"SMTP error: {exc}") from exc

    return {"status": "sent", "to": to_email, "period": period_label, "trades": all_s["n"]}
