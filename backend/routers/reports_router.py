"""reports_router.py — REST endpoints for PnL and ML reports."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional
from backend.database import TradeEnv
from backend.agents.pnl_agent import pnl_agent
from backend.agents.ml_agent  import ml_agent
from backend.core.tradable_tracker import tradable_tracker

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/tradable/{env}")
def get_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = tradable_tracker.get_report(trade_env)
    if not report:
        return {"status": "no_report", "env": env}
    return report


@router.post("/tradable/{env}/generate")
def generate_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return tradable_tracker.generate_report(trade_env)


@router.get("/pnl/{env}")
def get_pnl_report(env: str, date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = pnl_agent.get_report(trade_env, date)
    if not report:
        return {"status": "no_report", "env": env, "date": date}
    return report


@router.post("/pnl/{env}/generate")
def generate_pnl_report(env: str, date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = pnl_agent.generate_daily_report(trade_env, date)
    return report or {"status": "no_trades"}


@router.get("/pnl/{env}/list")
def list_pnl_reports(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return {"dates": pnl_agent.list_reports(trade_env)}


@router.get("/ml/{env}")
def get_ml_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = ml_agent.get_latest_report(trade_env)
    if not report:
        return {"status": "no_report", "env": env}
    return report


@router.post("/ml/{env}/trigger")
def trigger_ml_analysis(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return ml_agent.trigger_now(trade_env)


@router.get("/ml/weights")
def get_ml_weights():
    return ml_agent.get_weights()


# ── Shared email-safe HTML builder ─────────────────────────────────────────────

def _fmt_pnl_html(v):
    sign = "+" if v >= 0 else "−"
    color = "#22c55e" if v >= 0 else "#ef4444"
    return f'<span style="color:{color};font-weight:600">{sign}₹{abs(int(v)):,}</span>'


def _stats_table(st, label="Summary"):
    pnl_color = "#22c55e" if st["net"] >= 0 else "#ef4444"
    return f"""
    <table cellpadding="0" cellspacing="0" style="width:100%;margin-bottom:8px">
      <tr>
        <td style="padding:4px 16px 4px 0;vertical-align:top">
          <div style="font-size:10px;text-transform:uppercase;color:#888;letter-spacing:.5px">Total Trades</div>
          <div style="font-size:22px;font-weight:700;color:#111">{st['n']}</div>
        </td>
        <td style="padding:4px 16px 4px 0;vertical-align:top">
          <div style="font-size:10px;text-transform:uppercase;color:#888;letter-spacing:.5px">Win Rate</div>
          <div style="font-size:22px;font-weight:700;color:#111">{st['win_pct']}%</div>
        </td>
        <td style="padding:4px 16px 4px 0;vertical-align:top">
          <div style="font-size:10px;text-transform:uppercase;color:#888;letter-spacing:.5px">Net PnL</div>
          <div style="font-size:22px;font-weight:700;color:{pnl_color}">
            {"+" if st['net']>=0 else "−"}₹{abs(int(st['net'])):,}
          </div>
        </td>
        <td style="padding:4px 16px 4px 0;vertical-align:top">
          <div style="font-size:10px;text-transform:uppercase;color:#888;letter-spacing:.5px">Profit Factor</div>
          <div style="font-size:22px;font-weight:700;color:#111">{st['pf']}</div>
        </td>
        <td style="padding:4px 0;vertical-align:top">
          <div style="font-size:10px;text-transform:uppercase;color:#888;letter-spacing:.5px">Avg Capital</div>
          <div style="font-size:22px;font-weight:700;color:#111">{"₹"+f"{st['avg_cap']:,}" if st['avg_cap'] else "—"}</div>
        </td>
      </tr>
    </table>"""


def _trade_table_html(trades, capital_fn):
    if not trades:
        return '<p style="color:#888;padding:8px;font-size:12px">No trades in this period.</p>'

    from collections import defaultdict
    from datetime import datetime as _dt

    def _day_key(t):
        s = t.get("entered_at") or ""
        try:
            return str(s)[:10]  # YYYY-MM-DD
        except Exception:
            return "Unknown"

    def _fmt_day(d):
        try:
            return _dt.strptime(d, "%Y-%m-%d").strftime("%A, %d %B %Y")
        except Exception:
            return d

    # Group and sort by date
    by_date = defaultdict(list)
    for t in trades:
        by_date[_day_key(t)].append(t)
    sorted_dates = sorted(by_date.keys())

    th = 'padding:7px 8px;text-align:left;font-size:10px;text-transform:uppercase;color:#1e293b;font-weight:700;letter-spacing:.5px;border-bottom:2px solid #cbd5e1;background:#dde4ee'
    col_headers = (
        f'<th style="{th}">Symbol</th>'
        f'<th style="{th}">Entry</th><th style="{th}">Exit</th>'
        f'<th style="{th}">Capital</th><th style="{th}">PnL</th><th style="{th}">%</th>'
        f'<th style="{th}">In</th><th style="{th}">Out</th>'
    )
    td = "padding:6px 8px;border-bottom:1px solid #e2e8f0;color:#111;font-size:12px"

    html = ""
    for date_key in sorted_dates:
        day_trades = by_date[date_key]
        day_pnl = sum(t["pnl"] for t in day_trades)
        day_bg    = "#dcfce7" if day_pnl >= 0 else "#fee2e2"
        day_color = "#15803d" if day_pnl >= 0 else "#b91c1c"
        day_sign  = "+" if day_pnl >= 0 else "−"
        # Single unified table: date header row + column headers both in <thead>
        html += f"""<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:20px">
          <thead>
            <tr style="background:{day_bg}">
              <td colspan="6" style="padding:7px 12px;font-size:12px;font-weight:700;color:#1e293b">{_fmt_day(date_key)}</td>
              <td colspan="2" style="padding:7px 12px;text-align:right;font-size:12px;font-weight:700;color:{day_color}">{day_sign}₹{abs(int(day_pnl)):,} &nbsp;·&nbsp; {len(day_trades)} trade{"s" if len(day_trades)!=1 else ""}</td>
            </tr>
            <tr>{col_headers}</tr>
          </thead><tbody>"""
        for i, t in enumerate(day_trades):
            cap = capital_fn(t)
            pnl_color = "#15803d" if t["pnl"] >= 0 else "#b91c1c"
            bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
            entered = (t.get("entered_at") or "")[-8:] or "—"
            exited  = (t.get("exited_at")  or "")[-8:] or "—"
            direction = (t.get("direction") or "").lower()
            opt_type  = "CE" if direction == "call" else ("PE" if direction == "put" else "")
            sym_label = " ".join(filter(None, [
                t["symbol"],
                str(t["strike"]) if t.get("strike") else "",
                opt_type,
                f"{t['qty']} QTY" if t.get("qty") else "",
            ]))
            html += f"""<tr style="background:{bg}">
              <td style="{td};font-weight:600;color:#0f172a">{sym_label}</td>
              <td style="{td}">₹{t['entry']:.2f}</td>
              <td style="{td}">{f"₹{t['exit']:.2f}" if t.get('exit') else '—'}</td>
              <td style="{td}">{f"₹{int(cap):,}" if cap else '—'}</td>
              <td style="{td};color:{pnl_color};font-weight:700">{"+" if t['pnl']>=0 else "−"}₹{abs(int(t['pnl'])):,}</td>
              <td style="{td};color:{pnl_color};font-weight:600">{t['pnl_pct']:.1f}%</td>
              <td style="{td};color:#475569">{entered}</td>
              <td style="{td};color:#475569">{exited}</td>
            </tr>"""
        html += "</tbody></table>"

    return html


def _build_report_html(period_label, mode, generated_date, all_s, es_s, ob_s,
                       es_trades, ob_trades):
    def capital(t):
        return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"/></head>
    <body style="font-family:'Segoe UI',Arial,sans-serif;color:#111;max-width:900px;margin:0 auto;padding:24px;font-size:13px">

      <h1 style="font-size:22px;margin:0 0 4px 0">SPVH AMC — Trading Report</h1>
      <p style="color:#666;font-size:12px;margin:0 0 24px 0">
        Period: {period_label} &nbsp;&bull;&nbsp; Mode: {mode}
        &nbsp;&bull;&nbsp; Generated: {generated_date}
      </p>

      <!-- Combined Summary -->
      <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-bottom:28px">
        <div style="font-weight:700;font-size:14px;margin-bottom:12px">Combined Summary</div>
        {_stats_table(all_s)}
      </div>

      <!-- Early Scalp -->
      <div style="border-top:3px solid #a855f7;background:#faf5ff;padding:16px 18px;margin-bottom:12px">
        <div style="font-weight:700;font-size:16px;color:#a855f7;margin-bottom:10px">Early Scalp [ES]</div>
        {_stats_table(es_s)}
      </div>
      {_trade_table_html(es_trades, capital)}

      <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0"/>

      <!-- Opening Breakout -->
      <div style="border-top:3px solid #3b82f6;background:#eff6ff;padding:16px 18px;margin-bottom:12px">
        <div style="font-weight:700;font-size:16px;color:#3b82f6;margin-bottom:10px">Opening Breakout [OB]</div>
        {_stats_table(ob_s)}
      </div>
      {_trade_table_html(ob_trades, capital)}

      <p style="color:#aaa;font-size:10px;margin-top:32px;text-align:center">
        Auto-generated by SPVH AMC Trading Platform. Do not reply.
      </p>
    </body></html>"""


# ── Email report ───────────────────────────────────────────────────────────────

class EmailReportPayload(BaseModel):
    env:         str
    email:       str
    period:      str                   # today | 7d | 30d | custom | all
    custom_from: Optional[str] = None  # YYYY-MM-DD
    custom_to:   Optional[str] = None  # YYYY-MM-DD


@router.post("/email")
def email_report(payload: EmailReportPayload):
    import os, smtplib
    from email.mime.multipart import MIMEMultipart
    from email.mime.text      import MIMEText
    from datetime             import date, datetime, timedelta
    from sqlalchemy.orm       import Session as DBSession
    from backend.database     import Session, Trade, TradeStatus

    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        raise HTTPException(
            status_code=503,
            detail="SMTP not configured. Set SMTP_USER and SMTP_PASS environment variables."
        )

    try:
        trade_env = TradeEnv(payload.env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {payload.env}")

    today = date.today()
    if payload.period == "today":
        from_date, to_date = today, today
    elif payload.period == "7d":
        from_date, to_date = today - timedelta(days=6), today
    elif payload.period == "30d":
        from_date, to_date = today - timedelta(days=29), today
    elif payload.period == "custom":
        if not payload.custom_from or not payload.custom_to:
            raise HTTPException(status_code=400, detail="custom_from and custom_to required")
        from_date = date.fromisoformat(payload.custom_from)
        to_date   = date.fromisoformat(payload.custom_to)
    else:
        from_date, to_date = date(2000, 1, 1), today

    from_dt = datetime(from_date.year, from_date.month, from_date.day, 0, 0, 0)
    to_dt   = datetime(to_date.year,   to_date.month,   to_date.day,  23, 59, 59)

    db: DBSession = Session()
    try:
        rows = (
            db.query(Trade)
            .filter(Trade.env == trade_env, Trade.entered_at >= from_dt,
                    Trade.entered_at <= to_dt, Trade.status != TradeStatus.OPEN)
            .order_by(Trade.entered_at)
            .all()
        )
        trades = [
            {
                "symbol":    t.symbol,
                "direction": t.direction,
                "strike":    t.strike,
                "qty":       t.quantity,
                "lot_size":  t.lot_size,
                "entry":     t.entry_price,
                "exit":      t.exit_price,
                "pnl":       t.pnl or 0,
                "pnl_pct":   t.pnl_pct or 0,
                "entered_at": str(t.entered_at) if t.entered_at else "",
                "exited_at":  str(t.exited_at)  if t.exited_at  else "",
                "logic":     t.entry_logic or "",
            }
            for t in rows
        ]
    finally:
        db.close()

    def capital(t):
        return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)

    def stats(ts):
        net  = sum(t["pnl"] for t in ts)
        wins = sum(1 for t in ts if t["pnl"] > 0)
        caps = [capital(t) for t in ts if capital(t)]
        gp   = sum(t["pnl"] for t in ts if t["pnl"] > 0)
        gl   = abs(sum(t["pnl"] for t in ts if t["pnl"] < 0))
        pf   = round(gp / gl, 2) if gl else ("&infin;" if gp > 0 else 0)
        return {"n": len(ts), "net": net, "wins": wins,
                "win_pct": round(wins / len(ts) * 100) if ts else 0,
                "pf": pf,
                "avg_cap": round(sum(caps) / len(caps)) if caps else 0}

    es_trades = [t for t in trades if t["logic"].startswith("[ES]")]
    ob_trades = [t for t in trades if t["logic"].startswith("[OB]")]
    all_s, es_s, ob_s = stats(trades), stats(es_trades), stats(ob_trades)

    period_label = {"today": str(today), "7d": f"Last 7 days (to {today})",
                    "30d": f"Last 30 days (to {today})",
                    "custom": f"{from_date} to {to_date}", "all": "All Time"
                    }.get(payload.period, payload.period)

    html_body = _build_report_html(period_label, payload.env.upper(), str(today),
                                   all_s, es_s, ob_s, es_trades, ob_trades)

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"SPVH AMC Report — {period_label}"
    msg["From"]    = smtp_user
    msg["To"]      = payload.email
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(smtp_user, payload.email, msg.as_string())
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"SMTP error: {exc}")

    return {"status": "sent", "to": payload.email, "period": period_label,
            "trades": all_s["n"]}


@router.post("/send-now")
def send_daily_report_now():
    """Immediately send today's end-of-day report to all recipients."""
    from backend.core.daily_report_scheduler import daily_report_scheduler
    return daily_report_scheduler.send_now()
