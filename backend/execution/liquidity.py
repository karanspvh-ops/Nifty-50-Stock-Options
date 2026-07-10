"""liquidity.py — Liquidity guard before entering an option position.

Three checks: recent volume, bid-ask spread, open interest.
Edit this file when adjusting liquidity thresholds.
"""

from datetime import timedelta

from backend.core.broker import broker
from backend.core.clock  import now_ist

MIN_OPTION_VOLUME_LOTS = 3    # require >=3 lots traded in the last LOOKBACK minutes
LIQUIDITY_LOOKBACK_MIN = 10
MAX_SPREAD_PCT         = 20.0 # max bid-ask spread as % of mid
MIN_OI                 = 300  # minimum open interest contracts


def has_liquidity(opt_token: str, opt_symbol: str, lot_size: int) -> bool:
    """Return True only if the contract is safe to enter and exit.

    Rejects stale markets (volume=0), wide spreads (>20%), and thin books (OI<300).
    """
    kite = broker.kite()

    # ── 1. Recent volume ──────────────────────────────────────────────────────
    try:
        end   = now_ist()
        start = end - timedelta(minutes=LIQUIDITY_LOOKBACK_MIN)
        candles   = kite.historical_data(int(opt_token), start, end, "minute")
        total_vol = sum(c.get("volume", 0) for c in candles)
        min_req   = MIN_OPTION_VOLUME_LOTS * max(lot_size, 1)
        if total_vol < min_req:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                  f"vol={total_vol} last {LIQUIDITY_LOOKBACK_MIN}m < {min_req} (stale market)")
            return False
    except Exception as e:
        print(f"[ORDER] Liquidity FAIL {opt_symbol} | volume check error: {e}")
        return False

    # ── 2. Bid-ask spread + OI ────────────────────────────────────────────────
    try:
        key  = f"NFO:{opt_symbol}"
        q    = kite.quote([key])
        data = q.get(key, {})
        oi   = data.get("oi", 0)
        depth    = data.get("depth", {})
        best_bid = (depth.get("buy",  [{}]) or [{}])[0].get("price", 0)
        best_ask = (depth.get("sell", [{}]) or [{}])[0].get("price", 0)

        if oi < MIN_OI:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | OI={oi} < {MIN_OI} (thin market)")
            return False

        if not best_bid or best_bid <= 0:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | no bids (cannot sell — avoid)")
            return False

        if best_ask and best_ask > best_bid:
            mid        = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid * 100
            if spread_pct > MAX_SPREAD_PCT:
                print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                      f"spread {spread_pct:.1f}% > {MAX_SPREAD_PCT}% (wide — exit risk)")
                return False

    except Exception as e:
        print(f"[ORDER] Spread/OI check error {opt_symbol}: {e} — allowing (fail-open)")

    return True


# Backward-compatible private alias used by old code
_has_liquidity = has_liquidity
