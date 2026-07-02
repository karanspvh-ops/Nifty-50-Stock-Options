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
    from datetime             import date, timedelta
    from sqlalchemy.orm       import Session as DBSession
    from backend.database     import Session, Trade, TradeStatus

    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.getenv("SMTP_PORT", "587"))

    if not smtp_user or not smtp_pass:
        raise HTTPException(
            status_code=503,
            detail=(
                "SMTP not configured. Set SMTP_USER and SMTP_PASS environment variables "
                "(for Gmail use an App Password)."
            )
        )

    try:
        trade_env = TradeEnv(payload.env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {payload.env}")

    # ── Resolve date range ────────────────────────────────────────────────────
    today = date.today()
    if payload.period == "today":
        from_date, to_date = today, today
    elif payload.period == "7d":
        from_date, to_date = today - timedelta(days=6), today
    elif payload.period == "30d":
        from_date, to_date = today - timedelta(days=29), today
    elif payload.period == "custom":
        if not payload.custom_from or not payload.custom_to:
            raise HTTPException(status_code=400, detail="custom_from and custom_to required for custom period")
        from_date = date.fromisoformat(payload.custom_from)
        to_date   = date.fromisoformat(payload.custom_to)
    else:  # all
        from_date, to_date = date(2000, 1, 1), today

    # ── Fetch trades ──────────────────────────────────────────────────────────
    db: DBSession = Session()
    try:
        rows = (
            db.query(Trade)
            .filter(
                Trade.env        == trade_env,
                Trade.entered_at >= f"{from_date}T00:00:00",
                Trade.entered_at <= f"{to_date}T23:59:59",
                Trade.status     != TradeStatus.OPEN,
            )
            .order_by(Trade.entered_at)
            .all()
        )
        trades = [
            {
                "id":        t.id,
                "symbol":    t.symbol,
                "direction": t.direction,
                "strike":    t.strike,
                "qty":       t.qty,
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

    # ── Compute stats ─────────────────────────────────────────────────────────
    def is_es(t): return t["logic"].startswith("[ES]")
    def is_ob(t): return t["logic"].startswith("[OB]")
    def capital(t):
        e, q, l = t.get("entry") or 0, t.get("qty") or 0, t.get("lot_size") or 1
        return e * q * l

    def stats(ts):
        net   = sum(t["pnl"] for t in ts)
        wins  = sum(1 for t in ts if t["pnl"] > 0)
        caps  = [capital(t) for t in ts if capital(t)]
        gp    = sum(t["pnl"] for t in ts if t["pnl"] > 0)
        gl    = abs(sum(t["pnl"] for t in ts if t["pnl"] < 0))
        pf    = round(gp / gl, 2) if gl else ("∞" if gp > 0 else 0)
        return {
            "n":       len(ts),
            "net":     net,
            "wins":    wins,
            "win_pct": round(wins / len(ts) * 100) if ts else 0,
            "pf":      pf,
            "avg_cap": round(sum(caps) / len(caps)) if caps else 0,
        }

    es_trades = [t for t in trades if is_es(t)]
    ob_trades = [t for t in trades if is_ob(t)]
    all_stats = stats(trades)
    es_stats  = stats(es_trades)
    ob_stats  = stats(ob_trades)

    period_label = {
        "today": str(today),
        "7d":    f"Last 7 days (to {today})",
        "30d":   f"Last 30 days (to {today})",
        "custom": f"{from_date} to {to_date}",
        "all":   "All Time",
    }.get(payload.period, payload.period)

    # ── Build HTML ────────────────────────────────────────────────────────────
    def fmt_pnl(v):
        sign = "+" if v >= 0 else "−"
        color = "#22c55e" if v >= 0 else "#ef4444"
        return f'<span style="color:{color};font-weight:600">{sign}₹{abs(int(v)):,}</span>'

    def trade_rows(ts):
        if not ts:
            return "<tr><td colspan='9' style='color:#888;text-align:center;padding:12px'>No trades</td></tr>"
        rows_html = ""
        for t in ts:
            cap = capital(t)
            pnl_color = "#22c55e" if t["pnl"] >= 0 else "#ef4444"
            entered = (t["entered_at"] or "")[-8:][:5] or "—"
            exited  = (t["exited_at"]  or "")[-8:][:5] or "—"
            rows_html += f"""<tr>
              <td>{t['symbol']}</td>
              <td>{(t['direction'] or '').upper()}</td>
              <td>{t['strike'] or '—'}</td>
              <td>{t['qty'] or '—'}</td>
              <td>₹{t['entry']:.2f}</td>
              <td>{f"₹{t['exit']:.2f}" if t['exit'] else '—'}</td>
              <td>{f"₹{int(cap):,}" if cap else '—'}</td>
              <td style="color:{pnl_color};font-weight:600">
                {"+" if t['pnl']>=0 else "−"}₹{abs(int(t['pnl'])):,}
              </td>
              <td style="color:{pnl_color}">{t['pnl_pct']:.1f}%</td>
              <td style="color:#888">{entered}</td>
              <td style="color:#888">{exited}</td>
            </tr>"""
        return rows_html

    def section_html(title, ts, st, color):
        return f"""
        <div style="margin-bottom:32px">
          <div style="background:{color}18;border-top:3px solid {color};padding:14px 18px;margin-bottom:12px;border-radius:0">
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
                   <div style="font-size:20px;font-weight:700">{"₹"+f"{st['avg_cap']:,}" if st['avg_cap'] else "—"}</div></div>
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
      <h1 style="font-size:22px;margin-bottom:4px">SPVH AMC — Trading Report</h1>
      <p style="color:#666;font-size:12px;margin-bottom:24px">
        Period: {period_label} &nbsp;|&nbsp; Mode: {payload.env.upper()}
        &nbsp;|&nbsp; Generated: {today}
      </p>

      <div style="background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:18px;margin-bottom:28px">
        <div style="font-weight:700;font-size:14px;margin-bottom:10px">Combined Summary</div>
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
               <div style="font-size:24px;font-weight:700">{"₹"+f"{all_stats['avg_cap']:,}" if all_stats['avg_cap'] else "—"}</div></div>
        </div>
      </div>

      {section_html("Early Scalp [ES]",      es_trades, es_stats, "#a855f7")}
      <hr style="border:none;border-top:1px solid #e5e7eb;margin:28px 0"/>
      {section_html("Opening Breakout [OB]", ob_trades, ob_stats, "#3b82f6")}
    </body></html>"""

    # ── Send via SMTP ─────────────────────────────────────────────────────────
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

    return {"status": "sent", "to": payload.email, "period": period_label}
