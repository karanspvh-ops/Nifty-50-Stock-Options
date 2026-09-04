"""liquidity.py — Liquidity guard before entering an option position.

Four checks in order:
  0. NSE F&O ban list  — fresh positions blocked by SEBI/NSE (broker rejects live)
  1. Recent volume     — must have traded >= MIN_OPTION_VOLUME_LOTS in last 10 min
  2. Open interest     — must have >= MIN_OI_LOTS of open interest (in lots, not units)
  3. Bid-ask spread    — spread must be <= MAX_SPREAD_PCT of mid price

Edit this file when adjusting liquidity thresholds.
"""

from datetime import timedelta

from backend.core.broker   import broker
from backend.core.clock    import now_ist
from backend.execution.ban_list import is_banned

MIN_OPTION_VOLUME_LOTS = 15    # lots traded in last LOOKBACK minutes (was 3 — too low)
MIN_OI_LOTS            = 500   # minimum open interest in LOTS — Zerodha rejects below this
MAX_SPREAD_PCT         = 20.0  # max bid-ask spread as % of mid
LIQUIDITY_LOOKBACK_MIN = 10


def has_liquidity(opt_token: str, opt_symbol: str, lot_size: int,
                  symbol: str = "") -> bool:
    """Return True only if the contract is safe to enter and exit.

    Checks (in order): ban list, recent volume, open interest, bid-ask spread.
    All failure paths are fail-closed — any error blocks the trade.
    """
    kite = broker.kite()

    # ── 0. NSE F&O ban list ───────────────────────────────────────────────────
    if symbol and is_banned(symbol):
        print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
              f"{symbol} is on the NSE F&O ban list today (broker will reject live order)")
        return False

    # ── 1. Recent volume ──────────────────────────────────────────────────────
    try:
        end       = now_ist()
        start     = end - timedelta(minutes=LIQUIDITY_LOOKBACK_MIN)
        candles   = kite.historical_data(int(opt_token), start, end, "minute")
        total_vol = sum(c.get("volume", 0) for c in candles)
        min_units = MIN_OPTION_VOLUME_LOTS * max(lot_size, 1)
        if total_vol < min_units:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                  f"vol={total_vol} units ({total_vol // max(lot_size,1)} lots) "
                  f"in last {LIQUIDITY_LOOKBACK_MIN}m < {MIN_OPTION_VOLUME_LOTS} lots required")
            return False
    except Exception as e:
        print(f"[ORDER] Liquidity FAIL {opt_symbol} | volume check error: {e}")
        return False

    # ── 2. Open interest (in lots) + bid-ask spread ───────────────────────────
    try:
        key  = f"NFO:{opt_symbol}"
        q    = kite.quote([key])
        data = q.get(key, {})

        oi      = data.get("oi", 0) or 0
        oi_lots = oi / max(lot_size, 1)
        if oi_lots < MIN_OI_LOTS:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                  f"OI={oi} units ({oi_lots:.1f} lots) < {MIN_OI_LOTS} lots required")
            return False

        depth    = data.get("depth", {})
        best_bid = (depth.get("buy",  [{}]) or [{}])[0].get("price", 0)
        best_ask = (depth.get("sell", [{}]) or [{}])[0].get("price", 0)

        if not best_bid or best_bid <= 0:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | no bids in order book")
            return False

        if best_ask and best_ask > best_bid:
            mid        = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid * 100
            if spread_pct > MAX_SPREAD_PCT:
                print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                      f"spread {spread_pct:.1f}% > {MAX_SPREAD_PCT}% (wide — exit risk)")
                return False

    except Exception as e:
        print(f"[ORDER] Liquidity FAIL {opt_symbol} | OI/spread check error: {e} — rejecting")
        return False

    return True


# Backward-compatible private alias used by old code
_has_liquidity = has_liquidity
