"""stop_orders.py — A resting stop-loss SELL at Zerodha for every live trade.

As soon as a live entry is filled, a SELL stop-loss (SL, stop-limit) order is
placed at the trade's stop, so an exit never has to be chased:

  - trigger = the stop (hard SL, later the dynamic SL); limit = trigger less
    LIMIT_BUFFER_PCT, so once triggered it sells into the bid immediately
    (NSE doesn't allow SL-M on options, so it has to be a stop-limit)
  - the dynamic SL ratchet modifies the same order upward, throttled to stay
    inside Zerodha's per-order modification cap (replaced when near it)
  - target / time-stop / manual / kill-switch exits convert this same order to
    an immediate LIMIT sell -- never a second SELL order
  - if the stop triggers but the price gaps through the limit, the takeover
    loop re-prices it each second under the bid until it fills; if it still
    can't, a critical alert is raised for that trade
  - when Zerodha fills the stop, the trade is closed in the DB at the actual
    fill price (driven by Zerodha's pushed order update)

Trade-off: Zerodha triggers stops on the last traded price, so a stray print
will trigger this order. The limit then sells at the bid, not at the stray
print.
"""

import threading
import time
from typing import Optional

from backend.core.broker import broker
from backend.core import alerts
from backend.execution import live_orders
from backend.execution.live_orders import LiveOrderError, Fill, _round_tick, _touch, TERMINAL

LIMIT_BUFFER_PCT  = 4.0     # stop-limit price this far under the trigger
EXIT_BUFFERS_PCT  = (3.0, 6.0, 10.0, 15.0)   # immediate exits: successive limits under the bid
MIN_STEP_PCT      = 0.3     # don't modify for moves smaller than this ...
MIN_STEP_TICKS    = 2       # ... or than 2 ticks
MIN_MOD_GAP_SEC   = 1.5     # and not more often than this
MAX_MODS          = 20      # Zerodha caps modifications per order (~25); replace before that
TAKEOVER_AFTER_SEC = 1.0    # triggered-but-unfilled this long -> re-price
TAKEOVER_TRIES    = 8

_lock = threading.Lock()
_stops: dict = {}           # trade_id -> {order_id, symbol, qty, trigger, mods, last_mod, triggered_at, tries}
_by_order: dict = {}        # order_id -> trade_id
_no_stop: set = set()       # trade ids known to have no resting stop (paper), skips DB lookups


# ── Placement / modification ────────────────────────────────────────────────

def _prices(stop: float):
    trigger = _round_tick(stop, up=False)
    limit = _round_tick(trigger * (1 - LIMIT_BUFFER_PCT / 100), up=False)
    return trigger, limit


def _remember(trade_id: int, order_id: str, symbol: str, qty: int, trigger: float):
    with _lock:
        _no_stop.discard(trade_id)
        _stops[trade_id] = {"order_id": str(order_id), "symbol": symbol, "qty": qty, "trigger": trigger,
                            "mods": 0, "last_mod": time.monotonic(), "triggered_at": None, "tries": 0}
        _by_order[str(order_id)] = trade_id
    _persist(trade_id, str(order_id), trigger)


def _persist(trade_id: int, order_id: Optional[str], trigger: Optional[float]):
    from backend.database import Session, Trade
    db = Session()
    try:
        db.query(Trade).filter(Trade.id == trade_id).update(
            {"broker_sl_order_id": order_id, "broker_sl_trigger": trigger}, synchronize_session=False)
        db.commit()
    except Exception as e:
        print(f"[STOP] could not save stop order for trade #{trade_id}: {e}")
    finally:
        db.close()


def place(trade_id: int, symbol: str, qty: int, stop: float) -> Optional[str]:
    """Place the resting stop for a freshly filled live trade."""
    kite = broker.kite()
    trigger, limit = _prices(stop)
    try:
        oid = kite.place_order(
            variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO, tradingsymbol=symbol,
            transaction_type=kite.TRANSACTION_TYPE_SELL, quantity=qty, product=kite.PRODUCT_MIS,
            order_type=kite.ORDER_TYPE_SL, trigger_price=trigger, price=limit,
        )
    except Exception as e:
        alerts.raise_alert(f"stop:{trade_id}",
                           f"{symbol}: could not place the stop-loss order at Zerodha ({e}). "
                           f"The system's own stop is still active.", trade_id=trade_id, symbol=symbol)
        return None
    _remember(trade_id, oid, symbol, qty, trigger)
    alerts.clear_alert(f"stop:{trade_id}")
    print(f"[STOP] #{trade_id} {symbol}: SL SELL x{qty} trigger {trigger} limit {limit} | order {oid}")
    return str(oid)


def move(trade_id: int, new_stop: float):
    """Raise the resting stop to the new dynamic SL (never lowers it)."""
    s = get(trade_id)
    if not s or s.get("triggered_at"):
        return
    trigger, limit = _prices(new_stop)
    step = max(MIN_STEP_TICKS * live_orders.TICK, s["trigger"] * MIN_STEP_PCT / 100)
    if trigger < s["trigger"] + step or time.monotonic() - s["last_mod"] < MIN_MOD_GAP_SEC:
        return
    kite = broker.kite()
    if s["mods"] >= MAX_MODS:
        # Near Zerodha's modification cap: replace the order
        try:
            kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=s["order_id"])
        except Exception as e:
            print(f"[STOP] #{trade_id} cancel before replace failed: {e}")
            return
        _forget_order(s["order_id"])
        place(trade_id, s["symbol"], s["qty"], new_stop)
        return
    try:
        kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=s["order_id"],
                          trigger_price=trigger, price=limit, order_type=kite.ORDER_TYPE_SL)
    except Exception as e:
        print(f"[STOP] #{trade_id} modify to {trigger} failed: {e}")
        return
    with _lock:
        s = _stops.get(trade_id)
        if s:
            s.update(trigger=trigger, mods=s["mods"] + 1, last_mod=time.monotonic())
    _persist(trade_id, s["order_id"] if s else None, trigger)
    print(f"[STOP] #{trade_id} raised to trigger {trigger} limit {limit}")


def get(trade_id: int) -> Optional[dict]:
    with _lock:
        s = _stops.get(trade_id)
        if s:
            return dict(s)
        if trade_id in _no_stop:
            return None
    # After a restart: recover from the trade row
    from backend.database import Session, Trade
    db = Session()
    try:
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
        except Exception as e:
            print(f"[STOP] lookup for trade #{trade_id} failed: {e}")
            return None
        if t and t.broker_sl_order_id:
            _remember_quiet(trade_id, t.broker_sl_order_id, t.option_symbol,
                            (t.quantity or 0) * (t.lot_size or 0), t.broker_sl_trigger or 0.0)
            with _lock:
                return dict(_stops[trade_id])
        with _lock:
            _no_stop.add(trade_id)
    finally:
        db.close()
    return None


def _remember_quiet(trade_id, order_id, symbol, qty, trigger):
    with _lock:
        _stops[trade_id] = {"order_id": str(order_id), "symbol": symbol, "qty": qty, "trigger": trigger,
                            "mods": 0, "last_mod": 0.0, "triggered_at": None, "tries": 0}
        _by_order[str(order_id)] = trade_id


def _forget_order(order_id: str):
    with _lock:
        tid = _by_order.pop(str(order_id), None)
        if tid is not None and _stops.get(tid, {}).get("order_id") == str(order_id):
            _stops.pop(tid, None)


def forget(trade_id: int):
    with _lock:
        s = _stops.pop(trade_id, None)
        if s:
            _by_order.pop(s["order_id"], None)


def order_ids() -> set:
    with _lock:
        return set(_by_order)


# ── Immediate exit through the resting order ────────────────────────────────

def _order_state(order_id: str) -> dict:
    try:
        hist = broker.kite().order_history(order_id)
        return hist[-1] if hist else {}
    except Exception:
        return {}


def exit_now(trade_id: int) -> Fill:
    """Sell now by converting the resting stop into a LIMIT sell under the bid,
    re-pricing until filled. Raises LiveOrderError if it can't."""
    s = get(trade_id)
    if not s:
        raise LiveOrderError(f"trade #{trade_id} has no resting stop order")
    kite = broker.kite()
    oid, symbol, qty = s["order_id"], s["symbol"], s["qty"]
    for buf in EXIT_BUFFERS_PCT:
        bid, ask, ltp = _touch(symbol)
        ref = bid or ltp
        if not ref:
            raise LiveOrderError(f"no bid/LTP for {symbol}")
        price = _round_tick(ref * (1 - buf / 100), up=False)
        try:
            kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=oid,
                              order_type=kite.ORDER_TYPE_LIMIT, price=price, trigger_price=None)
            print(f"[STOP] #{trade_id} {symbol}: exit now, limit {price} (bid {bid}) | order {oid}")
        except Exception as e:
            st = _order_state(oid)
            if st.get("status") == "COMPLETE":
                forget(trade_id)
                return Fill(oid, float(st.get("average_price") or price), int(st.get("filled_quantity") or qty))
            if st.get("status") in ("CANCELLED", "REJECTED"):
                filled = int(st.get("filled_quantity") or 0)
                forget(trade_id)
                # The resting order is gone; sell whatever is still held directly
                rest = qty - filled
                if rest <= 0:
                    return Fill(oid, float(st.get("average_price") or price), qty)
                f2 = live_orders.sell(symbol, rest)
                avg = ((filled * float(st.get("average_price") or 0)) + rest * f2.avg_price) / qty
                return Fill(f2.order_id, round(avg, 2), qty)
            print(f"[STOP] #{trade_id} modify to limit failed: {e}")
            continue
        st = live_orders._wait_for_fill(oid, timeout=1.5)
        if st.get("status") == "COMPLETE":
            forget(trade_id)
            return Fill(oid, float(st.get("average_price") or price), int(st.get("filled_quantity") or qty))
    st = _order_state(oid)
    filled = int(st.get("filled_quantity") or 0)
    raise LiveOrderError(f"{symbol}: exit order {oid} not filled after re-pricing ({filled}/{qty} sold)",
                         sold_qty=0, avg_price=0.0)   # same order still working; keep its qty


# ── Order updates from Zerodha: fills and gap-through takeover ──────────────

def _on_update(data: dict):
    oid = str(data.get("order_id") or "")
    with _lock:
        tid = _by_order.get(oid)
        s = _stops.get(tid) if tid is not None else None
    if s is None:
        return
    status = data.get("status")
    if status == "COMPLETE":
        _close_from_fill(tid, float(data.get("average_price") or 0), s)
    elif status == "OPEN" and data.get("order_type") == "SL":
        # Triggered: now a working limit order. Start the takeover clock.
        with _lock:
            if tid in _stops and not _stops[tid]["triggered_at"]:
                _stops[tid]["triggered_at"] = time.monotonic()
    elif status in ("CANCELLED", "REJECTED"):
        # Not cancelled by us (exit_now/move forget first) -> the trade is unprotected
        alerts.raise_alert(f"stop:{tid}", f"{s['symbol']}: Zerodha {status.lower()} the stop-loss order "
                           f"({data.get('status_message') or 'no reason given'}). Only the system's own stop "
                           f"is protecting this trade.", trade_id=tid, symbol=s["symbol"])
        forget(tid)


def _close_from_fill(trade_id: int, price: float, s: dict):
    from backend.core.risk_engine import risk_engine
    forget(trade_id)
    res = risk_engine.close_from_broker_fill(trade_id, price, f"Stop-loss order filled at Zerodha @ {price:.2f}")
    if "error" in res:
        print(f"[STOP] #{trade_id} fill at {price} not booked: {res['error']}")


def _takeover_loop():
    while True:
        time.sleep(0.5)
        now = time.monotonic()
        with _lock:
            due = [(tid, dict(s)) for tid, s in _stops.items()
                   if s["triggered_at"] and now - s["triggered_at"] >= TAKEOVER_AFTER_SEC]
        for tid, s in due:
            if s["tries"] >= TAKEOVER_TRIES:
                alerts.raise_alert(f"exit:{tid}", f"{s['symbol']}: stop triggered but the sell is NOT filling "
                                   f"after {s['tries']} re-prices. Position is live -- sell it manually now.",
                                   trade_id=tid, symbol=s["symbol"])
                continue
            bid, ask, ltp = _touch(s["symbol"])
            ref = bid or ltp
            if not ref:
                continue
            price = _round_tick(ref * (1 - EXIT_BUFFERS_PCT[min(s["tries"], len(EXIT_BUFFERS_PCT) - 1)] / 100), up=False)
            kite = broker.kite()
            try:
                kite.modify_order(variety=kite.VARIETY_REGULAR, order_id=s["order_id"],
                                  order_type=kite.ORDER_TYPE_LIMIT, price=price, trigger_price=None)
                print(f"[STOP] #{tid} {s['symbol']}: price gapped through the stop, re-priced to {price}")
            except Exception as e:
                print(f"[STOP] #{tid} takeover re-price failed: {e}")
            with _lock:
                if tid in _stops:
                    _stops[tid]["tries"] += 1
                    _stops[tid]["triggered_at"] = time.monotonic()


live_orders.add_update_listener(_on_update)
threading.Thread(target=_takeover_loop, daemon=True, name="StopTakeover").start()
