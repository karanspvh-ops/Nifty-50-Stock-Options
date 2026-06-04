"""
backtest.py — Replay the Opening Breakout strategy over any date window using
real Zerodha historical stock + option prices. Mirrors the live logic:
  • combined F&O universe, sector picture @9:40
  • qualifying sector (>=MIN_SECTOR_MOVERS breakout movers), strongest absolute
  • top-3 by |move| x R-factor
  • entry needs >=1.5% move + momentum + volume + Volume-Profile VA break
  • gap-acceleration (9:35) ; window extends to 10:00
  • ATM option, nearest monthly expiry ; real option premium
  • 10% SL / target / trailing on the OPTION premium (uses current params)

Runs in a background thread; the UI polls status + result.
"""

import threading, time as _t
from datetime import datetime, date, time as dtime, timedelta
from collections import defaultdict

from backend.core.broker          import broker
from backend.core.stock_universe  import get_tradable_universe, SECTOR_OF, load_instrument_cache
from backend.core.volume_profile  import build_profile
from backend.core.opening_breakout import (
    opening_breakout, MOVE_MIN_PCT, MIN_SECTOR_MOVERS, SECTOR_MIN_PCT, RFACTOR_MIN,
    MAX_POSITIONS, GAP_MIN_PCT, EARLY_SECTOR_MIN_PCT, CLARITY_NET, BREADTH_MIN_PCT,
)

EARLY = dtime(9, 35); FINAL = dtime(9, 40); DEAD = dtime(9, 45); HARD = dtime(10, 30)

_status = {"running": False, "phase": "idle", "done_days": 0, "total_days": 0, "note": ""}
_result: dict = {}


def get_status() -> dict: return dict(_status)
def get_result() -> dict: return dict(_result)


def _trading_days(d0: date, d1: date):
    out, d = [], d0
    while d <= d1:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def run_async(from_str: str, to_str: str):
    if _status["running"]:
        return
    threading.Thread(target=_run, args=(from_str, to_str), daemon=True).start()


def _run(from_str, to_str):
    try:
        d0 = date.fromisoformat(from_str); d1 = date.fromisoformat(to_str)
    except Exception:
        _status.update(running=False, phase="error", note="invalid dates"); return
    if not broker.has_token():
        _status.update(running=False, phase="error", note="not logged in to Zerodha"); return

    kite   = broker.kite()
    uni    = get_tradable_universe()
    cache  = load_instrument_cache()
    params = opening_breakout.get_params()
    trail_gap = params.get("trail_gap_pct", 20.0)
    sl_pct    = params.get("hard_sl_pct", 10.0)
    tgt_pct   = params.get("target_pct", 50.0)
    activate  = params.get("trail_activate_pct", 20.0)

    days = _trading_days(d0, d1)
    _status.update(running=True, phase="fetching stock candles", done_days=0,
                   total_days=len(days), note="")

    # ── Pull all F&O stock candles for the whole range (one call each) ────────
    FROM = datetime.combine(d0 - timedelta(days=6), dtime(9, 0))
    TO   = datetime.combine(d1, dtime(15, 30))
    sdata = {}
    for s in uni:
        try:
            sdata[s["symbol"]] = kite.historical_data(int(s["token"]), FROM, TO, "5minute")
        except Exception:
            pass
        _t.sleep(0.3)

    opt_cache = {}
    def opt_hist(tok, day):
        k = (tok, day)
        if k in opt_cache:
            return opt_cache[k]
        try:
            c = kite.historical_data(int(tok), datetime.combine(day, dtime(9, 15)),
                                     datetime.combine(day, dtime(15, 30)), "5minute")
            _t.sleep(0.3)
        except Exception:
            c = []
        opt_cache[k] = c
        return c

    all_trades, daily = [], []
    _status["phase"] = "simulating days"
    for di, day in enumerate(days):
        trades = _sim_day(day, sdata, cache, opt_hist, trail_gap, sl_pct, tgt_pct, activate)
        all_trades.extend(trades)
        daily.append({
            "date": day.isoformat(), "trades": len(trades),
            "net_pnl_pct": round(sum(t["pnl_pct"] for t in trades), 1),
            "sector": trades[0]["sector"] if trades else None,
            "direction": trades[0]["direction"] if trades else None,
        })
        _status["done_days"] = di + 1

    n    = len(all_trades)
    wins = [t for t in all_trades if t["pnl_pct"] > 0]
    summary = {
        "days_scanned": len(days),
        "days_traded":  sum(1 for d in daily if d["trades"]),
        "total_trades": n, "wins": len(wins), "losses": n - len(wins),
        "win_rate":     round(len(wins) / n * 100, 1) if n else 0,
        "avg_pnl_pct":  round(sum(t["pnl_pct"] for t in all_trades) / n, 1) if n else 0,
        "total_pnl_pct":round(sum(t["pnl_pct"] for t in all_trades), 1),
        "best":  round(max((t["pnl_pct"] for t in all_trades), default=0), 1),
        "worst": round(min((t["pnl_pct"] for t in all_trades), default=0), 1),
        "params": {"trail_gap_pct": trail_gap, "hard_sl_pct": sl_pct, "target_pct": tgt_pct},
    }
    global _result
    _result = {"from": from_str, "to": to_str, "summary": summary,
               "daily": daily, "trades": all_trades}
    _status.update(running=False, phase="done")


# ── One trading day ───────────────────────────────────────────────────────────
def _sim_day(day, sdata, cache, opt_hist, trail_gap, sl_pct, tgt_pct, activate):
    def today(rows):  return [r for r in rows if r["date"].date() == day]
    def prevc(rows):
        p = [r for r in rows if r["date"].date() < day]; return p[-1]["close"] if p else None
    def at(rows, hh, mm):
        cut = dtime(hh, mm); last = None
        for r in today(rows):
            if r["date"].time() <= cut: last = r
        return last

    # sector picture @9:45
    sec = defaultdict(list)
    for sym, rows in sdata.items():
        if sym not in SECTOR_OF: continue
        t = today(rows)
        if not t: continue
        pc = prevc(rows); ref = t[0]["open"]; c = at(rows, 9, 45)
        if not pc or not c: continue
        sec[SECTOR_OF[sym]].append((sym, (c["close"] - pc) / pc * 100, ref, pc, rows))
    if not sec:
        return []
    sec_avg = {k: sum(m for _, m, _, _, _ in v) / len(v) for k, v in sec.items()}

    # SMART DIRECTION — clarity from breadth
    ups   = sum(1 for v in sec.values() for _, m, _, _, _ in v if m >=  BREADTH_MIN_PCT)
    downs = sum(1 for v in sec.values() for _, m, _, _, _ in v if m <= -BREADTH_MIN_PCT)
    tot   = ups + downs; net = (ups - downs) / tot if tot else 0
    if   net >=  CLARITY_NET: allowed = {"call"}
    elif net <= -CLARITY_NET: allowed = {"put"}
    else:                     allowed = {"call", "put"}

    # qualifying sectors in the allowed direction(s)
    qual = {}
    for k, v in sec.items():
        sp = sec_avg[k]
        if abs(sp) < SECTOR_MIN_PCT: continue
        sd = "call" if sp > 0 else "put"
        if sd not in allowed: continue
        movers = sum(1 for _, m, _, _, _ in v
                     if (sd == "call" and m >= MOVE_MIN_PCT) or (sd == "put" and m <= -MOVE_MIN_PCT))
        if movers >= MIN_SECTOR_MOVERS:
            qual[k] = (sp, sd)
    if not qual:
        return []

    # candidates: ALL stocks, each its OWN side, restricted to allowed direction(s)
    cands = []
    for sct, stocks in sec.items():
        own = abs(sec_avg.get(sct, 0)) or 0.1
        for sym, dmove, ref, pc, rows in stocks:
            sd = "call" if dmove > 0 else "put"
            if sd not in allowed: continue
            rf = abs(dmove) / own
            if rf < RFACTOR_MIN: continue
            cands.append((sym, sct, sd, dmove, rf, ref, pc, rows))
    cands.sort(key=lambda c: abs(c[3]) * c[4], reverse=True)
    top = cands[:MAX_POSITIONS]

    # find each candidate's first confirmed entry within 9:35–10:30
    entries = []
    for sym, sct, sd, dmove, rf, ref, pc, rows in top:
        gap = abs((ref - pc) / pc * 100)
        for r in today(rows):
            tt = r["date"].time()
            if tt < EARLY: continue
            if tt > HARD: break
            mv = (r["close"] - ref) / ref * 100
            if (sd == "put" and mv > -MOVE_MIN_PCT) or (sd == "call" and mv < MOVE_MIN_PCT):
                continue
            if tt < FINAL and not (gap >= GAP_MIN_PCT and abs(sec_avg.get(sct, 0)) >= EARLY_SECTOR_MIN_PCT):
                continue
            cc, vr, vb = _confirm(rows, r["date"], sd)
            momentum_ok = (cc >= 2 and vr >= 1.3)
            spike_ok    = (abs(mv) >= 2.0 and vr >= 2.0)
            if vb and (momentum_ok or spike_ok):
                entries.append({"sym": sym, "sct": sct, "sd": sd, "row": r,
                                "cc": cc, "vr": vr, "t": tt}); break

    # window rule: if anything fired by 9:45 → take all primary; else take the
    # single earliest entry found in the 9:45–10:30 extension.
    primary = [e for e in entries if e["t"] <= DEAD]
    selected = primary if primary else sorted(entries, key=lambda e: e["t"])[:1]

    trades = []
    for e in selected:
        r = e["row"]; sd = e["sd"]
        opt = _atm(cache, e["sym"], r["close"], sd, day)
        if not opt: continue
        otok, ostrike, osym = opt
        sim = _sim_option(opt_hist(otok, day), r["date"], sl_pct, tgt_pct, activate, trail_gap)
        if not sim: continue
        trades.append({
            "date": day.isoformat(), "symbol": e["sym"], "sector": e["sct"], "direction": sd,
            "strike": ostrike, "option_symbol": osym,
            "entry_time": r["date"].strftime("%H:%M"), "entry_premium": round(sim["entry"], 1),
            "exit_time": sim["exit_time"], "exit_premium": round(sim["exit"], 1),
            "pnl_pct": round(sim["pnl"], 1), "peak_pct": round(sim["peak"], 1),
            "exit_reason": sim["reason"], "momentum": e["cc"], "vol_ratio": e["vr"],
        })
    return trades


def _confirm(rows, upto, direction):
    s = [r for r in rows if r["date"] <= upto][-50:]
    cc = 0
    for i in range(len(s) - 1, 0, -1):
        a, b = s[i], s[i - 1]
        ok = (a["low"] < b["low"] and a["close"] <= b["close"]) if direction == "put" \
             else (a["high"] > b["high"] and a["close"] >= b["close"])
        if ok: cc += 1
        else: break
    vols = [r["volume"] for r in s[-20:]]; av = sum(vols) / max(len(vols), 1)
    vr = round(s[-1]["volume"] / av, 2) if av else 0
    cd = [{"open": r["open"], "high": r["high"], "low": r["low"],
           "close": r["close"], "volume": r["volume"]} for r in s]
    vp20 = build_profile(cd[-20:]); vp50 = build_profile(cd[-50:]); ltp = s[-1]["close"]
    brk = lambda vp: (ltp < vp.get("val", ltp)) if direction == "put" else (ltp > vp.get("vah", ltp))
    return cc, vr, (brk(vp20) or brk(vp50))


def _atm(cache, sym, px, direction, day):
    ot = "PE" if direction == "put" else "CE"
    opts = [(t, m) for t, m in cache.items() if m["name"] == sym and m["instrument_type"] == ot]
    fut = [(t, m) for t, m in opts if date.fromisoformat(m["expiry"]) >= day]
    if not fut: return None
    near = min(date.fromisoformat(m["expiry"]) for _, m in fut)
    same = [(t, m) for t, m in fut if date.fromisoformat(m["expiry"]) == near]
    tok, m = min(same, key=lambda x: abs(x[1]["strike"] - px))
    return tok, m["strike"], m["tradingsymbol"]


def _sim_option(oc, e_time, sl, tgt, activate, gap):
    ep = None; peak = -1e9; ex = None; last = None
    for c in oc:
        if c["date"] < e_time: continue
        if ep is None: ep = c["close"]
        pnl = (c["close"] - ep) / ep * 100; peak = max(peak, pnl); last = (c["date"], c["close"], pnl)
        if ex: continue
        tstop = -sl if peak < activate else max(-sl, peak - gap)
        if pnl >= tgt:
            ex = (c["date"], c["close"], pnl, f"Target +{tgt:.0f}%")
        elif pnl <= tstop:
            ex = (c["date"], c["close"], pnl, f"SL -{sl:.0f}%" if tstop == -sl else f"Trail (lock {tstop:+.0f}%)")
    if ep is None:
        return None
    if not ex:
        ex = (last[0], last[1], last[2], "Square-off / EOD")
    return {"entry": ep, "exit": ex[1], "exit_time": ex[0].strftime("%H:%M"),
            "pnl": ex[2], "peak": peak, "reason": ex[3]}
