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


def _calc_brokerage() -> float:
    return 47.2  # 2 legs × ₹20 + 18% GST


def _calc_statutory(t: dict, capital_fn) -> int:
    entry    = t.get("entry") or 0
    exit_p   = t.get("exit")  or 0
    qty      = t.get("qty")   or 0
    lot_size = t.get("lot_size") or 1
    entry_val = entry  * qty * lot_size
    exit_val  = exit_p * qty * lot_size
    stt         = 0.00125 * exit_val
    exchange    = 0.00053 * (entry_val + exit_val)
    gst_on_exch = 0.18    * exchange
    sebi        = (entry_val + exit_val) * 10 / 10_000_000
    stamp       = 0.00003 * entry_val
    return round(stt + exchange + gst_on_exch + sebi + stamp)


def _trade_table_html(trades, capital_fn):
    if not trades:
        return '<p style="color:#888;padding:8px;font-size:12px">No trades in this period.</p>'

    from collections import defaultdict
    from datetime import datetime as _dt

    def _day_key(t):
        s = t.get("entered_at") or ""
        try:
            return str(s)[:10]
        except Exception:
            return "Unknown"

    def _fmt_day(d):
        try:
            return _dt.strptime(d, "%Y-%m-%d").strftime("%A, %d %B %Y")
        except Exception:
            return d

    by_date = defaultdict(list)
    for t in trades:
        by_date[_day_key(t)].append(t)
    sorted_dates = sorted(by_date.keys())

    th = 'padding:7px 8px;text-align:left;font-size:10px;text-transform:uppercase;color:#1e293b;font-weight:700;letter-spacing:.5px;border-bottom:2px solid #cbd5e1;background:#dde4ee'
    col_headers = (
        f'<th style="{th}">Symbol</th>'
        f'<th style="{th}">Entry</th><th style="{th}">Exit</th>'
        f'<th style="{th}">Capital</th><th style="{th}">PnL</th><th style="{th}">%</th>'
        f'<th style="{th}">Peak</th>'
        f'<th style="{th}">Brokerage</th><th style="{th}">Statutory</th>'
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
        html += f"""<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:20px">
          <thead>
            <tr style="background:{day_bg}">
              <td colspan="9" style="padding:7px 12px;font-size:12px;font-weight:700;color:#1e293b">{_fmt_day(date_key)}</td>
              <td colspan="2" style="padding:7px 12px;text-align:right;font-size:12px;font-weight:700;color:{day_color}">{day_sign}₹{abs(int(day_pnl)):,} &nbsp;·&nbsp; {len(day_trades)} trade{"s" if len(day_trades)!=1 else ""}</td>
            </tr>
            <tr>{col_headers}</tr>
          </thead><tbody>"""
        for i, t in enumerate(day_trades):
            cap        = capital_fn(t)
            broker     = _calc_brokerage()
            statutory  = _calc_statutory(t, capital_fn)
            pnl_color  = "#15803d" if t["pnl"] >= 0 else "#b91c1c"
            peak       = t.get("peak")
            peak_pct   = t.get("peak_pct")
            peak_color = "#15803d" if (peak_pct or 0) >= 0 else "#b91c1c"
            if peak is not None and peak_pct is not None:
                sign = "+" if peak_pct >= 0 else ""
                peak_cell = f'₹{peak:.2f} <span style="font-size:10px">({sign}{peak_pct:.1f}%)</span>'
            else:
                peak_cell = "—"
            bg       = "#ffffff" if i % 2 == 0 else "#f8fafc"
            # Extract HH:MM:SS — [-8:] breaks for microsecond-format timestamps
            # ("2026-06-05 10:17:33.295082"[-8:] = "3.295082"). Use [11:19] instead.
            entered  = ((t.get("entered_at") or "")[11:19]) or "—"
            exited   = ((t.get("exited_at")  or "")[11:19]) or "—"
            direction = (t.get("direction") or "").lower()
            opt_type  = t.get("option_type") or ("CE" if direction == "call" else ("PE" if direction == "put" else ""))
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
              <td style="{td};color:{peak_color}">{peak_cell}</td>
              <td style="{td};color:#64748b">₹{int(broker)}</td>
              <td style="{td};color:#64748b">₹{statutory}</td>
              <td style="{td};color:#475569">{entered}</td>
              <td style="{td};color:#475569">{exited}</td>
            </tr>"""
        html += "</tbody></table>"

    return html


def _capital_chart_svg(days_data: list, start_balance: float) -> str:
    """Inline SVG line chart of running balance trajectory."""
    if not days_data:
        return ""
    from datetime import datetime as _dt2

    balances = [start_balance]
    r = start_balance
    for d in days_data:
        r += d.get("day_pnl", 0)
        balances.append(r)

    W, H = 740, 130
    PL, PR, PT, PB = 60, 20, 24, 28
    PW, PH = W - PL - PR, H - PT - PB
    N = len(balances) - 1

    mn = min(balances); mx = max(balances)
    span = max(mx - mn, 10_000)
    mn -= span * 0.1;  mx += span * 0.1

    def xi(i): return PL + (i / max(N, 1)) * PW
    def yi(v): return PT + (1 - (v - mn) / (mx - mn)) * PH

    svg_grid = ""
    for k in range(5):
        v = mn + (mx - mn) * k / 4
        yv = yi(v)
        if PT - 2 <= yv <= H - PB + 2:
            svg_grid += (
                f'<line x1="{PL}" y1="{yv:.1f}" x2="{W-PR}" y2="{yv:.1f}" '
                f'stroke="#e5e7eb" stroke-width="0.5"/>'
                f'<text x="{PL-4}" y="{yv:.1f}" text-anchor="end" '
                f'dominant-baseline="middle" font-size="8" fill="#9ca3af">₹{v/100000:.1f}L</text>'
            )

    svg_ref = ""
    b5 = yi(500_000)
    if PT - 2 <= b5 <= H - PB + 2:
        svg_ref = (
            f'<line x1="{PL}" y1="{b5:.1f}" x2="{W-PR}" y2="{b5:.1f}" '
            f'stroke="#94a3b8" stroke-width="1" stroke-dasharray="4,3" opacity="0.6"/>'
            f'<text x="{W-PR+3}" y="{b5:.1f}" dominant-baseline="middle" '
            f'font-size="7" fill="#94a3b8">₹5L</text>'
        )

    pts = " ".join(f"{xi(i):.1f},{yi(v):.1f}" for i, v in enumerate(balances))
    lc = "#15803d" if balances[-1] >= start_balance else "#b91c1c"

    svg_pts = (f'<circle cx="{xi(0):.1f}" cy="{yi(start_balance):.1f}" r="3" '
               f'fill="#9ca3af" stroke="white" stroke-width="1.5"/>')
    for i, (v, d) in enumerate(zip(balances[1:], days_data), 1):
        cx, cy = xi(i), yi(v)
        mc = "#15803d" if v >= start_balance else "#b91c1c"
        pnl = d.get("day_pnl", 0)
        pc  = "#15803d" if pnl >= 0 else "#b91c1c"
        ps  = "+" if pnl >= 0 else "−"
        try:
            dl = _dt2.strptime(d["date"], "%Y-%m-%d").strftime("%d %b")
        except Exception:
            dl = d["date"][-5:]
        ly = cy - 10 if i % 2 == 1 else cy + 16
        if ly < PT + 2: ly = cy + 16
        if ly > H - PB - 2: ly = cy - 10
        svg_pts += (
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="{mc}" stroke="white" stroke-width="1.5"/>'
            f'<text x="{cx:.1f}" y="{ly:.1f}" text-anchor="middle" font-size="8" fill="{pc}">{ps}₹{abs(int(pnl)):,}</text>'
            f'<text x="{cx:.1f}" y="{H-4}" text-anchor="middle" font-size="8" fill="#64748b">{dl}</text>'
        )

    return (
        f'<div style="margin-bottom:20px">'
        f'<div style="font-size:11px;font-weight:600;color:#475569;margin-bottom:6px">Capital Trajectory</div>'
        f'<svg width="{W}" height="{H}" xmlns="http://www.w3.org/2000/svg" style="display:block;overflow:visible">'
        f'{svg_grid}{svg_ref}'
        f'<polyline points="{pts}" fill="none" stroke="{lc}" stroke-width="2.5" '
        f'stroke-linejoin="round" stroke-linecap="round"/>'
        f'{svg_pts}'
        f'</svg></div>'
    )


def _es_capital_section(es_trades: list, start_balance: float = 500_000) -> str:
    """Concurrent capital usage for ES trades, simulated against ₹5L base capital."""
    if not es_trades:
        return ""

    from datetime import datetime as _dt
    from collections import defaultdict

    def _pts(s):
        if not s:
            return None
        try:
            return _dt.fromisoformat(str(s).replace(" ", "T")[:23])
        except Exception:
            return None

    def _cap(t):
        return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)

    by_day: dict = defaultdict(list)
    for t in es_trades:
        day = str(t.get("entered_at") or "")[:10]
        if day:
            by_day[day].append(t)
    if not by_day:
        return ""

    running_bal = float(start_balance)
    days_data = []

    for day in sorted(by_day):
        raw = sorted(by_day[day], key=lambda t: str(t.get("entered_at") or ""))
        opening = running_bal

        enriched = []
        for t in raw:
            t_e = _pts(t.get("entered_at"))
            conc = 0.0
            if t_e:
                for u in raw:
                    u_e = _pts(u.get("entered_at"))
                    u_x = _pts(u.get("exited_at"))
                    if u_e and u_e <= t_e and (u_x is None or t_e < u_x):
                        conc += _cap(u)
            enriched.append({**t, "_conc": int(round(conc))})

        events = []
        for t in raw:
            c = _cap(t)
            if c > 0:
                if _pts(t.get("entered_at")):
                    events.append((_pts(t.get("entered_at")), +c))
                if _pts(t.get("exited_at")):
                    events.append((_pts(t.get("exited_at")), -c))
        events.sort(key=lambda x: x[0])
        rc = 0.0
        peak = 0.0
        for _, delta in events:
            rc += delta
            if rc > peak:
                peak = rc

        day_pnl = sum(t.get("pnl") or 0 for t in raw)
        running_bal += day_pnl
        days_data.append({
            "date":     day,
            "opening":  int(opening),
            "peak":     int(round(peak)),
            "headroom": int(opening - peak),
            "breached": peak > opening,
            "day_pnl":  day_pnl,
            "trades":   enriched,
        })

    th = ("padding:6px 8px;text-align:left;font-size:9px;text-transform:uppercase;"
          "color:#1e293b;font-weight:700;letter-spacing:.5px;background:#ede9fe;"
          "border-bottom:2px solid #c4b5fd")
    tds = "padding:5px 8px;border-bottom:1px solid #e2e8f0;font-size:11px"

    summary_rows = ""
    for d in days_data:
        pct = f"{round(d['peak'] / d['opening'] * 100, 1)}%" if d["opening"] else "0%"
        sc  = "#b91c1c" if d["breached"] else "#15803d"
        hc  = "#b91c1c" if d["headroom"] < 0 else "#15803d"
        pc  = "#15803d" if d["day_pnl"] >= 0 else "#b91c1c"
        ps  = "+" if d["day_pnl"] >= 0 else "−"
        try:
            dl = _dt.strptime(d["date"], "%Y-%m-%d").strftime("%d %b")
        except Exception:
            dl = d["date"]
        summary_rows += (
            f'<tr>'
            f'<td style="{tds};color:#0f172a;font-weight:600">{dl}</td>'
            f'<td style="{tds}">₹{d["opening"]:,}</td>'
            f'<td style="{tds};color:#b45309;font-weight:600">₹{d["peak"]:,}</td>'
            f'<td style="{tds};color:{hc}">₹{abs(d["headroom"]):,}</td>'
            f'<td style="{tds}">{pct}</td>'
            f'<td style="{tds};color:{sc};font-weight:600">{"⚠ OVER LIMIT" if d["breached"] else "✓ Within limit"}</td>'
            f'<td style="{tds};color:{pc};font-weight:600">{ps}₹{abs(int(d["day_pnl"])):,}</td>'
            f'</tr>'
        )

    summary_tbl = (
        f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse;margin-bottom:20px">'
        f'<thead><tr>'
        f'<th style="{th}">Date</th><th style="{th}">Opening</th>'
        f'<th style="{th}">Peak Concurrent</th><th style="{th}">Headroom</th>'
        f'<th style="{th}">% Used</th><th style="{th}">Status</th><th style="{th}">Day PnL</th>'
        f'</tr></thead><tbody>{summary_rows}</tbody></table>'
    )

    detail_html = ""
    for d in days_data:
        try:
            day_label = _dt.strptime(d["date"], "%Y-%m-%d").strftime("%A, %d %b %Y")
        except Exception:
            day_label = d["date"]
        trade_rows = ""
        for i, t in enumerate(d["trades"]):
            cap  = _cap(t)
            pnl  = t.get("pnl") or 0
            conc = t.get("_conc", 0)
            pc   = "#15803d" if pnl >= 0 else "#b91c1c"
            cc   = "#b91c1c" if conc > d["opening"] else "#0f172a"
            bg   = "#ffffff" if i % 2 == 0 else "#faf5ff"
            entered = (str(t.get("entered_at") or "")[11:19]) or "—"
            exited  = (str(t.get("exited_at")  or "")[11:19]) or "—"
            dur_s = ""
            e_ts = _pts(t.get("entered_at"))
            x_ts = _pts(t.get("exited_at"))
            if e_ts and x_ts:
                secs = int((x_ts - e_ts).total_seconds())
                dur_s = f"{secs // 60}m {secs % 60}s"
            sym = " ".join(filter(None, [
                t.get("symbol"),
                str(t.get("strike")) if t.get("strike") else "",
                t.get("option_type") or "",
            ]))
            trade_rows += (
                f'<tr style="background:{bg}">'
                f'<td style="{tds};font-weight:600;color:#0f172a">{sym}</td>'
                f'<td style="{tds};color:#475569;font-family:monospace">{entered}</td>'
                f'<td style="{tds};color:#475569;font-family:monospace">{exited}</td>'
                f'<td style="{tds};color:#475569">{dur_s}</td>'
                f'<td style="{tds}">₹{int(cap):,}</td>'
                f'<td style="{tds};color:{cc};font-weight:600">₹{conc:,}</td>'
                f'<td style="{tds};color:{pc};font-weight:700">{"+" if pnl>=0 else "−"}₹{abs(int(pnl)):,}</td>'
                f'</tr>'
            )
        detail_html += (
            f'<div style="page-break-inside:avoid;margin-bottom:16px">'
            f'<table cellpadding="0" cellspacing="0" style="width:100%;border-collapse:collapse">'
            f'<thead>'
            f'<tr style="background:#f5f0ff">'
            f'<td colspan="5" style="padding:6px 8px;font-size:11px;font-weight:700;color:#7c3aed">{day_label}</td>'
            f'<td colspan="2" style="padding:6px 8px;text-align:right;font-size:11px;color:#555">'
            f'Peak ₹{d["peak"]:,} / Opening ₹{d["opening"]:,} &nbsp;·&nbsp; '
            f'{"⚠ OVER LIMIT" if d["breached"] else "✓ Within limit"}</td>'
            f'</tr>'
            f'<tr>'
            f'<th style="{th}">Symbol</th><th style="{th}">In</th><th style="{th}">Out</th>'
            f'<th style="{th}">Duration</th><th style="{th}">Capital</th>'
            f'<th style="{th}">Concurrent at Entry</th><th style="{th}">PnL</th>'
            f'</tr>'
            f'</thead><tbody>{trade_rows}</tbody></table>'
            f'</div>'
        )

    closing_bal = int(round(running_bal))
    bal_color = "#15803d" if closing_bal >= start_balance else "#b91c1c"
    bal_sign  = "+" if closing_bal >= int(start_balance) else "−"
    bal_diff  = abs(closing_bal - int(start_balance))

    return (
        f'<div style="margin-top:32px;padding-top:16px;border-top:2px solid #c4b5fd">'
        f'<div style="font-weight:700;font-size:14px;color:#7c3aed;margin-bottom:4px">'
        f'ES — Capital Usage (₹5L Base)</div>'
        f'<div style="font-size:11px;color:#555;margin-bottom:4px">'
        f'Base ₹5,00,000 &nbsp;·&nbsp; ₹1L/trade budget &nbsp;·&nbsp; '
        f'Period opening ₹{int(start_balance):,} &nbsp;·&nbsp; '
        f'Period closing <span style="color:{bal_color};font-weight:600">₹{closing_bal:,}</span> '
        f'(<span style="color:{bal_color}">{bal_sign}₹{bal_diff:,}</span>)</div>'
        f'{_capital_chart_svg(days_data, start_balance)}'
        f'{summary_tbl}{detail_html}'
        f'</div>'
    )


def _build_report_html(period_label, mode, generated_date, all_s, es_s, ob_s,
                       es_trades, ob_trades, es_start_balance: float = 500_000):
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
      {_es_capital_section(es_trades, es_start_balance)}

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
            for t in rows
        ]
        # Cumulative ES PnL from all days before this period → correct opening balance
        prior_es = (
            db.query(Trade)
            .filter(Trade.env == trade_env,
                    Trade.entered_at < from_dt,
                    Trade.status != TradeStatus.OPEN,
                    Trade.entry_logic.like("[ES]%"))
            .all()
        )
        es_start_balance = 500_000 + sum((t.pnl or 0) for t in prior_es)
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
                                   all_s, es_s, ob_s, es_trades, ob_trades, es_start_balance)

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
