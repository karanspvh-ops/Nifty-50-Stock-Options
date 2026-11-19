"""option_selector.py — ATM option selection and premium lookup via Kite."""

from datetime import date
from typing import Optional, Tuple

from backend.core.market_state   import market
from backend.core.stock_universe import load_instrument_cache
from backend.core.broker         import broker
from backend.core.quotes         import quotes


def option_bid_ask(opt_token: str, opt_symbol: str) -> tuple:
    """Return (bid, ask, spread_pct): live feed order book first (FULL-mode
    ticks, no API call), else one batched quote. (None, None, None) if neither."""
    if not opt_symbol:
        return None, None, None
    ba = market.get_bid_ask(opt_token) if opt_token else None
    if ba and ba[0] and ba[1]:
        bid, ask = ba
        return bid, ask, round((ask - bid) / ((ask + bid) / 2) * 100, 2)
    try:
        key = f"NFO:{opt_symbol}"
        q   = quotes.get([key])
        depth = q.get(key, {}).get("depth", {})
        buy_levels  = depth.get("buy",  [])
        sell_levels = depth.get("sell", [])
        bid = float(buy_levels[0]["price"])  if buy_levels  and buy_levels[0]["price"]  > 0 else None
        ask = float(sell_levels[0]["price"]) if sell_levels and sell_levels[0]["price"] > 0 else None
        if bid and ask and (bid + ask) > 0:
            spread_pct = round((ask - bid) / ((ask + bid) / 2) * 100, 2)
        else:
            spread_pct = None
        return bid, ask, spread_pct
    except Exception:
        return None, None, None


def option_premium(opt_token: str, opt_symbol: str) -> Optional[float]:
    """Current option premium: live tick first, Kite quote fallback."""
    if not opt_symbol:
        return None
    p = market.get_ltp(opt_token) if opt_token else None
    if p:
        return p
    try:
        key = f"NFO:{opt_symbol}"
        q   = quotes.get([key])
        return float(q[key]["last_price"])
    except Exception:
        return None


def current_premium(trade) -> Optional[float]:
    """Current premium of an open trade's option contract."""
    from backend.core.stock_universe import get_option_token
    tok = get_option_token(trade.option_symbol)
    return option_premium(tok, trade.option_symbol)


# ── Expiry rollover ─────────────────────────────────────────────────────────
# Stock options are physically settled, and Zerodha restricts fresh positions
# in them as expiry approaches. Within the last ROLLOVER_SESSIONS trading
# sessions before expiry (expiry day included) entries use the NEXT month's
# ATM contract. If Zerodha rejects a live BUY for expiry/physical-delivery
# reasons anyway, force_rollover_today() switches every entry for the rest of
# the day, whatever the calendar rule says.
ROLLOVER_SESSIONS = 3
_forced_rollover_on: Optional[date] = None


def sessions_to_expiry(today: date, expiry: date) -> int:
    """Trading sessions from today to expiry, both counted (weekdays; holidays not known here)."""
    from datetime import timedelta
    n, d = 0, today
    while d <= expiry:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def force_rollover_today(reason: str):
    global _forced_rollover_on
    if _forced_rollover_on != date.today():
        _forced_rollover_on = date.today()
        print(f"[EXPIRY] Rolling all entries to next month for today: {reason}")
        from backend.core import alerts
        alerts.raise_alert("expiry-roll", f"Zerodha refused a near-expiry order ({reason}). All entries today "
                           f"use next month's ATM contract.", level="warning")


def _use_next_month(today: date, nearest: date) -> bool:
    return _forced_rollover_on == today or sessions_to_expiry(today, nearest) <= ROLLOVER_SESSIONS


def select_option(symbol: str, ltp: float, direction: str
                  ) -> Tuple[Optional[str], Optional[str], float, str]:
    """Return (token, tradingsymbol, strike, expiry_iso) for the ATM option:
    nearest expiry, or next month's inside the rollover window."""
    cache       = load_instrument_cache()
    option_type = "CE" if direction == "call" else "PE"
    today       = date.today()

    contracts = [(t, m) for t, m in cache.items()
                 if m.get("name") == symbol.upper()
                 and m.get("instrument_type") == option_type]
    if not contracts:
        return None, None, 0.0, ""

    def exp_date(m):
        try:    return date.fromisoformat(m["expiry"])
        except Exception: return date(2099, 1, 1)

    future = [(t, m) for t, m in contracts if exp_date(m) >= today]
    if not future:
        return None, None, 0.0, ""
    expiries = sorted({exp_date(m) for _, m in future})
    nearest  = expiries[0]
    if _use_next_month(today, nearest) and len(expiries) > 1:
        nearest = expiries[1]
    same    = [(t, m) for t, m in future if exp_date(m) == nearest]

    tok, m = min(same, key=lambda x: abs(float(x[1]["strike"]) - ltp))

    try:
        from backend.core.tick_engine import tick_engine
        tick_engine.subscribe_options([{"token": tok,
                                         "tradingsymbol": m["tradingsymbol"],
                                         "name": m["name"]}])
    except Exception:
        pass

    return tok, m["tradingsymbol"], float(m["strike"]), m["expiry"]
