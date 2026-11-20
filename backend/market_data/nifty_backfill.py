"""nifty_backfill.py — End-of-day completion of the NIFTY option chain data.

The Cloudflare collector records quote snapshots (no minute OHLC); cloud_sync
runs this right after each full sync. It fills everything Zerodha still has
for a day from its minute history: per-minute OHLC, volume and OI for every strike within ±10 of ATM
(ATM re-evaluated every minute from the index's own minute close), plus NIFTY
spot candles. Running it at 15:35 means the backend no longer has to stay up
until 15:30 for price/volume/OI.

What can NOT be backfilled: bid/ask, spread, 5-level depth and pending
buy/sell quantities. Zerodha keeps no history of the order book; those exist
only in the live collector's snapshots taken at the time.

Rules
  - Fill, never overwrite: a minute the live collector already recorded keeps
    its values (including its order-book fields); only empty fields are filled.
  - Same contract the live collector would use: the nearest weekly expiry on
    or after that day. If that contract has already expired, Zerodha has
    dropped its history and the day is reported as not repairable.
  - snapshot_time follows the live convention: the minute the bar closes
    (Kite's 09:15 bar -> 09:16 snapshot). Spot candles keep the bar's own minute.
"""

import threading
import time as _time
from datetime import date, datetime, time as dtime, timedelta

from backend.core.broker import broker
from backend.core import rate_gate
from backend.core.clock import now_ist
from backend.storage.nifty_options_engine import NiftySession
from backend.storage.nifty_options_models import NiftyOptionSnapshot, NiftySpotCandle

LADDER = 10                 # strikes each side of ATM
HIST_GAP_SEC = 0.36         # Kite historical API: 3 requests/second
MARKET_OPEN, MARKET_CLOSE = dtime(9, 15), dtime(15, 30)

_FILL_FIELDS = ("nifty_spot", "open", "high", "low", "close", "volume",
                "oi", "oi_day_high", "oi_day_low", "day_volume")

_state_lock = threading.Lock()
_state = {"running": False, "started_at": None, "results": []}


def get_status() -> dict:
    with _state_lock:
        return {**_state, "results": list(_state["results"])}


def _hist(kite, token, day: date, oi: bool):
    out = kite.historical_data(int(token), datetime.combine(day, MARKET_OPEN),
                               datetime.combine(day, MARKET_CLOSE), "minute", oi=oi)
    _time.sleep(HIST_GAP_SEC)
    # Kite returns tz-aware IST timestamps; the DB stores naive IST.
    for b in out:
        b["date"] = b["date"].replace(tzinfo=None)
    return out


def _expiry_for(day: date, chain_map: dict, db) -> tuple:
    """(expiry_iso, None) or (None, reason)."""
    listed = sorted({m["expiry"] for m in chain_map.values() if str(m.get("expiry", "")) >= day.isoformat()})
    used = [r[0] for r in db.query(NiftyOptionSnapshot.expiry)
            .filter(NiftyOptionSnapshot.date == day.isoformat()).distinct().all()]
    if used:
        exp = min(used)
        if exp in listed:
            return exp, None
        return None, f"its {exp} contracts have expired; Zerodha no longer has their minute history"
    if not listed:
        return None, "no listed NIFTY expiry on or after this day"
    exp = listed[0]
    # A weekly expiry more than 7 days out means the real near-week contract
    # for that day has already expired and been delisted.
    if (date.fromisoformat(exp) - day).days > 7:
        return None, f"its near-week contract has expired (nearest still listed is {exp})"
    return exp, None


def backfill_day(day: date) -> dict:
    from backend.universe.instrument_cache import get_nifty_option_chain, get_nifty_index_token

    res = {"date": day.isoformat(), "status": "ok", "inserted": 0, "filled": 0,
           "spot_candles": 0, "contracts": 0, "minutes": 0, "expiry": None, "note": ""}
    if not broker.has_token():
        return {**res, "status": "skipped", "note": "Zerodha not signed in"}
    idx_token = get_nifty_index_token()
    chain_map = get_nifty_option_chain()
    if not idx_token or not chain_map:
        return {**res, "status": "skipped", "note": "instrument cache not loaded yet"}

    kite = broker.kite()
    spot_bars = _hist(kite, idx_token, day, oi=False)
    if not spot_bars:
        return {**res, "status": "skipped", "note": "no market data (holiday or weekend)"}

    # Mid-session run: leave the last two minutes to the live collector so the
    # two writers never both insert the same minute.
    now = now_ist().replace(tzinfo=None)
    if day == now.date() and now.time() < dtime(15, 32):
        cutoff = now.replace(second=0, microsecond=0) - timedelta(minutes=2)
        spot_bars = [b for b in spot_bars if b["date"] < cutoff]
    spot_at = {b["date"]: b["close"] for b in spot_bars}
    res["minutes"] = len(spot_bars)

    db = NiftySession()
    try:
        expiry, why = _expiry_for(day, chain_map, db)

        # Spot candles (independent of whether options can be repaired)
        have = {r[0] for r in db.query(NiftySpotCandle.candle_time)
                .filter(NiftySpotCandle.date == day.isoformat()).all()}
        new_candles = [dict(date=day.isoformat(), candle_time=b["date"], open=b["open"], high=b["high"],
                            low=b["low"], close=b["close"], volume=b.get("volume"))
                       for b in spot_bars if b["date"] not in have]
        if new_candles:
            db.bulk_insert_mappings(NiftySpotCandle, new_candles)
            db.commit()
        res["spot_candles"] = len(new_candles)

        if not expiry:
            return {**res, "status": "not_repairable", "note": f"Option data can't be repaired: {why}"}
        res["expiry"] = expiry

        chain = {tok: m for tok, m in chain_map.items() if m["expiry"] == expiry}
        strikes = sorted({float(m["strike"]) for m in chain.values()})
        idx_of = {s: i for i, s in enumerate(strikes)}

        # Existing rows for the day, keyed by (close minute, strike, type)
        existing = {}
        live_atm = {}     # close minute -> ATM index the live collector used then
        for r in db.query(NiftyOptionSnapshot).filter(NiftyOptionSnapshot.date == day.isoformat()).all():
            snap = r.snapshot_time.replace(second=0, microsecond=0)
            existing[(snap, r.strike, r.option_type)] = r
            if r.expiry == expiry and r.strike in idx_of:
                live_atm[snap] = idx_of[r.strike] - r.moneyness_rank

        # ATM per bar: where the live collector recorded that minute, keep its
        # ATM so every row of the minute ranks against the same centre (it
        # re-centres hourly); otherwise the strike nearest that minute's spot.
        atm_idx = {}
        for t, px in spot_at.items():
            snap = t + timedelta(minutes=1)
            atm_idx[t] = live_atm.get(snap, min(range(len(strikes)), key=lambda k: abs(strikes[k] - px)))
        lo = max(0, min(atm_idx.values()) - LADDER)
        hi = min(len(strikes) - 1, max(atm_idx.values()) + LADDER)
        wanted = set(strikes[lo:hi + 1])
        contracts = [(tok, float(m["strike"]), m["instrument_type"]) for tok, m in chain.items()
                     if float(m["strike"]) in wanted and m["instrument_type"] in ("CE", "PE")]
        res["contracts"] = len(contracts)

        inserts, updates = [], []
        for tok, strike, otype in contracts:
            try:
                bars = _hist(kite, tok, day, oi=True)
            except Exception as e:
                print(f"[NIFTY-BACKFILL] {day} {strike} {otype}: {e}")
                continue
            day_vol, oi_hi, oi_lo = 0, None, None
            for b in bars:
                t = b["date"]
                day_vol += int(b.get("volume") or 0)
                oi = b.get("oi")
                if oi:
                    oi_hi = oi if oi_hi is None else max(oi_hi, oi)
                    oi_lo = oi if oi_lo is None else min(oi_lo, oi)
                if t not in atm_idx:
                    continue
                rank = idx_of[strike] - atm_idx[t]
                if abs(rank) > LADDER:
                    continue
                snap = t + timedelta(minutes=1)
                vals = dict(nifty_spot=spot_at[t], open=b["open"], high=b["high"], low=b["low"],
                            close=b["close"], volume=int(b.get("volume") or 0), oi=oi,
                            oi_day_high=oi_hi, oi_day_low=oi_lo, day_volume=day_vol)
                row = existing.get((snap, strike, otype))
                if row is None:
                    inserts.append(dict(date=day.isoformat(), snapshot_time=snap, expiry=expiry,
                                        strike=strike, option_type=otype, moneyness_rank=rank, **vals))
                else:
                    fill = {k: v for k, v in vals.items() if getattr(row, k) is None and v is not None}
                    if fill:
                        updates.append({"id": row.id, **fill})

        if inserts:
            db.bulk_insert_mappings(NiftyOptionSnapshot, inserts)
        if updates:
            db.bulk_update_mappings(NiftyOptionSnapshot, updates)
        db.commit()
        res["inserted"], res["filled"] = len(inserts), len(updates)
        return res
    finally:
        db.close()


def recent_trading_days(n: int = 7) -> list:
    today = now_ist().date()
    return [today - timedelta(days=k) for k in range(n) if (today - timedelta(days=k)).weekday() < 5]


def run_async(days: list) -> bool:
    """Backfill the given days in a background thread. False if already running."""
    with _state_lock:
        if _state["running"]:
            return False
        _state.update(running=True, started_at=now_ist().isoformat(), results=[])

    def _run():
        try:
            for d in days:
                try:
                    with rate_gate.background():      # yields to live trading calls
                        r = backfill_day(d)
                except Exception as e:
                    r = {"date": d.isoformat(), "status": "error", "note": str(e)}
                print(f"[NIFTY-BACKFILL] {r}")
                with _state_lock:
                    _state["results"].append(r)
        finally:
            with _state_lock:
                _state["running"] = False

    threading.Thread(target=_run, daemon=True, name="NiftyBackfill").start()
    return True
