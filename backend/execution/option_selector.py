"""option_selector.py — ATM option selection and premium lookup via Kite."""

from datetime import date
from typing import Optional, Tuple

from backend.core.market_state   import market
from backend.core.stock_universe import load_instrument_cache
from backend.core.broker         import broker


def option_premium(opt_token: str, opt_symbol: str) -> Optional[float]:
    """Current option premium: live tick first, Kite quote fallback."""
    if not opt_symbol:
        return None
    p = market.get_ltp(opt_token) if opt_token else None
    if p:
        return p
    try:
        key = f"NFO:{opt_symbol}"
        q   = broker.kite().quote([key])
        return float(q[key]["last_price"])
    except Exception:
        return None


def current_premium(trade) -> Optional[float]:
    """Current premium of an open trade's option contract."""
    from backend.core.stock_universe import get_option_token
    tok = get_option_token(trade.option_symbol)
    return option_premium(tok, trade.option_symbol)


def select_option(symbol: str, ltp: float, direction: str
                  ) -> Tuple[Optional[str], Optional[str], float, str]:
    """Return (token, tradingsymbol, strike, expiry_iso) for the nearest ATM option."""
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
    nearest = min(exp_date(m) for _, m in future)
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
