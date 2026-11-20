"""live_orders.py — Real Zerodha order placement with fill confirmation.

Every live BUY and SELL goes through here. Orders are marketable LIMIT orders
(priced through the touch with a capped buffer) rather than MARKET orders, so a
thin 9:25 order book can't fill us at an arbitrary price. Each order is polled
until Zerodha reports it COMPLETE, REJECTED or CANCELLED, and the caller gets
the real average fill price and filled quantity back.

Rules:
  - A BUY that isn't filled within the timeout is cancelled. Whatever part did
    fill is returned so the caller records exactly what we hold.
  - A SELL is retried with a fresh bid and a wider buffer. If it still can't be
    filled, LiveOrderError is raised and the trade must stay OPEN in the DB.
  - One exit per trade at a time (claim_exit), so the tick thread, the manage
    loop and the risk checker can never send two SELLs for the same position.
"""

import threading
import time
from dataclasses import dataclass

from backend.core.broker import broker

TICK = 0.05                 # NSE stock-option tick size
BUY_BUFFER_PCT  = 2.0       # pay at most ask + 2%
SELL_BUFFERS_PCT = (3.0, 6.0, 12.0)   # successive SELL attempts: bid - 3%, 6%, 12%
FILL_TIMEOUT_SEC = 4.0
ORDER_TAG = "SPVHALGO"      # every system order carries this; the reconciler only touches tagged symbols


class LiveOrderError(Exception):
    """sold_qty/avg_price describe any part of a SELL that did fill before the
    failure, so the caller can shrink the open position instead of re-selling it."""
    def __init__(self, msg: str, sold_qty: int = 0, avg_price: float = 0.0):
        super().__init__(msg)
        self.sold_qty = sold_qty
        self.avg_price = avg_price


@dataclass
class Fill:
    order_id: str
    avg_price: float
    filled_qty: int         # units (lots * lot_size)


_exit_lock = threading.Lock()
_exits_in_flight: set = set()
_exits_done: set = set()


def claim_exit(trade_id: int) -> bool:
    """Reserve the right to send the SELL for this trade. False if another
    thread is already selling it or it has already been sold."""
    with _exit_lock:
        if trade_id in _exits_in_flight or trade_id in _exits_done:
            return False
        _exits_in_flight.add(trade_id)
        return True


def release_exit(trade_id: int, sold: bool):
    with _exit_lock:
        _exits_in_flight.discard(trade_id)
        if sold:
            _exits_done.add(trade_id)


def _round_tick(price: float, up: bool) -> float:
    steps = price / TICK
    steps = int(steps) + (1 if up and steps != int(steps) else 0)
    return round(max(steps, 1) * TICK, 2)


def _touch(opt_symbol: str):
    """(bid, ask, ltp): the live feed's order book when fresh (no API call),
    else one batched quote."""
    from backend.core.market_state import market
    from backend.core.stock_universe import get_option_token
    tok = get_option_token(opt_symbol)
    ba = market.get_bid_ask(tok, max_age=1.5) if tok else None
    if ba and (ba[0] or ba[1]):
        return ba[0], ba[1], market.get_ltp(tok)
    from backend.core.quotes import quotes
    key = f"NFO:{opt_symbol}"
    q = quotes.get([key], max_age=0.5).get(key, {})
    depth = q.get("depth", {})
    buys, sells = depth.get("buy", []), depth.get("sell", [])
    bid = float(buys[0]["price"]) if buys and buys[0]["price"] > 0 else None
    ask = float(sells[0]["price"]) if sells and sells[0]["price"] > 0 else None
    ltp = float(q.get("last_price") or 0) or None
    return bid, ask, ltp


# ── Order updates pushed by Zerodha on the ticker WebSocket ─────────────────
_upd_cond = threading.Condition()
_order_updates: dict = {}          # order_id -> latest update payload


def on_order_update(data: dict):
    """Called by tick_engine for every order status change Zerodha pushes."""
    oid = str(data.get("order_id") or "")
    if not oid:
        return
    with _upd_cond:
        _order_updates[oid] = data
        _upd_cond.notify_all()
    for fn in list(_update_listeners):
        try:
            fn(data)
        except Exception as e:
            print(f"[LIVE] order-update listener error: {e}")


_update_listeners: list = []


def add_update_listener(fn):
    _update_listeners.append(fn)


TERMINAL = ("COMPLETE", "REJECTED", "CANCELLED")


def _wait_for_fill(order_id: str, timeout: float = None) -> dict:
    """Wait for a terminal state. Zerodha's pushed order updates answer in
    milliseconds; order_history is polled once a second only as a backstop
    (it costs API budget the exits may need)."""
    kite = broker.kite()
    deadline = time.monotonic() + (FILL_TIMEOUT_SEC if timeout is None else timeout)
    next_poll = time.monotonic() + 1.0
    last = {}
    while True:
        with _upd_cond:
            u = _order_updates.get(str(order_id))
            if u:
                last = u
                if u.get("status") in TERMINAL:
                    return u
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return last
            _upd_cond.wait(min(remaining, max(0.0, next_poll - time.monotonic()), 0.25))
        if time.monotonic() >= next_poll:
            next_poll = time.monotonic() + 1.0
            try:
                hist = kite.order_history(order_id)
                if hist:
                    last = hist[-1]
                    if last.get("status") in TERMINAL:
                        return last
            except Exception as e:
                print(f"[LIVE] order_history {order_id} failed: {e}")


def _cancel_and_settle(order_id: str) -> dict:
    """Cancel a still-open order, then return its final state."""
    kite = broker.kite()
    try:
        kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
    except Exception as e:
        print(f"[LIVE] cancel {order_id} failed (may already be filled): {e}")
    return _wait_for_fill(order_id)


def _place(opt_symbol: str, side: str, qty: int, price: float) -> str:
    kite = broker.kite()
    return kite.place_order(
        variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO,
        tradingsymbol=opt_symbol,
        transaction_type=kite.TRANSACTION_TYPE_BUY if side == "BUY" else kite.TRANSACTION_TYPE_SELL,
        quantity=qty, product=kite.PRODUCT_MIS,
        order_type=kite.ORDER_TYPE_LIMIT, price=price, tag=ORDER_TAG,
    )


_inflight_lock = threading.Lock()
_inflight: dict = {}         # option symbol -> number of BUY/SELL calls in progress


def inflight_symbols() -> set:
    with _inflight_lock:
        return {s for s, n in _inflight.items() if n > 0}


def _mark(symbol: str, delta: int):
    with _inflight_lock:
        _inflight[symbol] = _inflight.get(symbol, 0) + delta


def buy(opt_symbol: str, qty: int) -> Fill:
    """BUY qty units. Returns the fill (possibly partial). Raises if nothing filled."""
    _mark(opt_symbol, 1)
    try:
        return _buy(opt_symbol, qty)
    finally:
        _mark(opt_symbol, -1)


def _buy(opt_symbol: str, qty: int) -> Fill:
    bid, ask, ltp = _touch(opt_symbol)
    ref = ask or ltp
    if not ref:
        raise LiveOrderError(f"no ask/LTP for {opt_symbol}")
    price = _round_tick(ref * (1 + BUY_BUFFER_PCT / 100), up=True)
    order_id = _place(opt_symbol, "BUY", qty, price)
    print(f"[LIVE] BUY {opt_symbol} x{qty} limit {price} (ask {ask}) | order {order_id}")

    state = _wait_for_fill(order_id)
    if state.get("status") not in ("COMPLETE", "REJECTED", "CANCELLED"):
        state = _cancel_and_settle(order_id)

    filled = int(state.get("filled_quantity") or 0)
    msg = (state.get("status_message") or "").lower()
    if state.get("status") == "REJECTED" and any(w in msg for w in ("physical", "delivery", "expiry", "expiring")):
        from backend.execution.option_selector import force_rollover_today
        force_rollover_today(state.get("status_message") or "near-expiry rejection")
    if filled <= 0:
        raise LiveOrderError(
            f"BUY {opt_symbol} not filled: {state.get('status')} {state.get('status_message') or ''}".strip())
    avg = float(state.get("average_price") or price)
    if filled < qty:
        print(f"[LIVE] BUY {opt_symbol} PARTIAL {filled}/{qty} @ {avg}")
    return Fill(order_id, avg, filled)


def sell(opt_symbol: str, qty: int) -> Fill:
    _mark(opt_symbol, 1)
    try:
        return _sell(opt_symbol, qty)
    finally:
        _mark(opt_symbol, -1)


def _sell(opt_symbol: str, qty: int) -> Fill:
    """SELL qty units, retrying with wider buffers until fully filled.
    Raises LiveOrderError if any quantity is still held after all attempts."""
    remaining = qty
    proceeds = 0.0
    last_id = ""
    for buf in SELL_BUFFERS_PCT:
        bid, ask, ltp = _touch(opt_symbol)
        ref = bid or ltp
        if not ref:
            raise LiveOrderError(f"no bid/LTP for {opt_symbol}")
        price = _round_tick(ref * (1 - buf / 100), up=False)
        last_id = _place(opt_symbol, "SELL", remaining, price)
        print(f"[LIVE] SELL {opt_symbol} x{remaining} limit {price} (bid {bid}) | order {last_id}")

        state = _wait_for_fill(last_id)
        if state.get("status") not in ("COMPLETE", "REJECTED", "CANCELLED"):
            state = _cancel_and_settle(last_id)

        filled = int(state.get("filled_quantity") or 0)
        if filled:
            proceeds += filled * float(state.get("average_price") or price)
            remaining -= filled
        if remaining <= 0:
            return Fill(last_id, round(proceeds / qty, 2), qty)
        if state.get("status") == "REJECTED":
            break

    sold = qty - remaining
    raise LiveOrderError(
        f"SELL {opt_symbol} incomplete: {sold}/{qty} sold "
        f"(last order {last_id} {state.get('status')} {state.get('status_message') or ''})".strip(),
        sold_qty=sold, avg_price=round(proceeds / sold, 2) if sold else 0.0)
