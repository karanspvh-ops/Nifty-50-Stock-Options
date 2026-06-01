"""
volume_profile.py — Volume Profile indicator engine.

Computes from a list of 5-min OHLCV candles:
  - POC  (Point of Control)  — price with highest traded volume
  - VAH  (Value Area High)   — upper bound of 70% volume zone
  - VAL  (Value Area Low)    — lower bound of 70% volume zone
  - HVNs (High Volume Nodes) — strong support/resistance levels
  - LVNs (Low Volume Nodes)  — thin zones price slices through fast

Also computes:
  - continuation_probability(direction, current_price)
    → probability that price continues in given direction
    based on volume distribution above vs below current price
"""

from typing import List, Dict, Tuple, Optional
import statistics


def build_profile(candles: List[dict], price_bucket_size: float = 0.5) -> Dict:
    """
    Build a volume profile from OHLCV candles.

    Each candle distributes its volume evenly across its price range
    bucketed to `price_bucket_size`.

    Returns a dict with:
      - levels: {price: volume}
      - poc, vah, val
      - hvn_levels: [price, ...]  (top 20% nodes)
      - lvn_levels: [price, ...]  (bottom 20% nodes)
      - total_volume
    """
    if not candles:
        return {}

    # ── Step 1: distribute volume across price buckets ────────────────────────
    levels: Dict[float, float] = {}

    for c in candles:
        o, h, l, cl, vol = c["open"], c["high"], c["low"], c["close"], c.get("volume", 0)
        if h <= l or vol == 0:
            continue

        # Price range for this candle
        lo_bucket = round(l / price_bucket_size) * price_bucket_size
        hi_bucket = round(h / price_bucket_size) * price_bucket_size

        price  = lo_bucket
        prices = []
        while price <= hi_bucket + price_bucket_size:
            prices.append(round(price, 2))
            price += price_bucket_size

        if not prices:
            continue

        vol_per_level = vol / len(prices)
        for p in prices:
            levels[p] = levels.get(p, 0) + vol_per_level

    if not levels:
        return {}

    # ── Step 2: find POC ──────────────────────────────────────────────────────
    poc = max(levels, key=levels.get)

    # ── Step 3: Value Area (70% of total volume around POC) ──────────────────
    total_volume  = sum(levels.values())
    target_vol    = total_volume * 0.70
    sorted_prices = sorted(levels.keys())

    # Start from POC and expand outward
    poc_idx = sorted_prices.index(poc)
    vah_idx = poc_idx
    val_idx = poc_idx
    accumulated = levels[poc]

    lo_ptr = poc_idx - 1
    hi_ptr = poc_idx + 1

    while accumulated < target_vol:
        vol_up   = levels.get(sorted_prices[hi_ptr], 0) if hi_ptr < len(sorted_prices)  else 0
        vol_down = levels.get(sorted_prices[lo_ptr],  0) if lo_ptr >= 0                  else 0

        if vol_up >= vol_down and hi_ptr < len(sorted_prices):
            accumulated += vol_up
            vah_idx      = hi_ptr
            hi_ptr      += 1
        elif lo_ptr >= 0:
            accumulated += vol_down
            val_idx      = lo_ptr
            lo_ptr      -= 1
        else:
            break

    vah = sorted_prices[vah_idx]
    val = sorted_prices[val_idx]

    # ── Step 4: HVN / LVN classification ─────────────────────────────────────
    vol_values   = list(levels.values())
    if len(vol_values) < 3:
        hvn_threshold = 0
        lvn_threshold = float("inf")
    else:
        mean_vol      = statistics.mean(vol_values)
        stdev_vol     = statistics.stdev(vol_values)
        hvn_threshold = mean_vol + (0.5 * stdev_vol)
        lvn_threshold = mean_vol - (0.5 * stdev_vol)

    hvn_levels = sorted(
        [p for p, v in levels.items() if v >= hvn_threshold],
        key=lambda p: levels[p], reverse=True
    )[:10]

    lvn_levels = sorted(
        [p for p, v in levels.items() if v <= max(lvn_threshold, 0)],
        key=lambda p: levels[p]
    )[:10]

    return {
        "levels":       levels,
        "poc":          poc,
        "vah":          vah,
        "val":          val,
        "hvn_levels":   hvn_levels,
        "lvn_levels":   lvn_levels,
        "total_volume": total_volume,
        "price_range":  (sorted_prices[0], sorted_prices[-1]),
    }


def continuation_probability(
    profile:       dict,
    current_price: float,
    direction:     str        # "up" or "down"
) -> float:
    """
    Returns probability (0.0–1.0) that price continues in direction.

    Logic:
    - For "up": probability = vol_below_current / total_volume
      (more volume below = stronger support = likely to hold and move up)
    - For "down": probability = vol_above_current / total_volume
      (more volume above = strong resistance = likely to hold and move down)
    """
    if not profile or "levels" not in profile:
        return 0.5

    levels       = profile["levels"]
    total_volume = profile.get("total_volume", 1) or 1

    vol_below = sum(v for p, v in levels.items() if p < current_price)
    vol_above = sum(v for p, v in levels.items() if p > current_price)

    if direction == "up":
        # Support below current price drives continuation
        raw = vol_below / total_volume
    else:
        # Resistance above current price drives down continuation
        raw = vol_above / total_volume

    # Clamp to [0.1, 0.9] — never absolute certainty
    return round(min(max(raw, 0.1), 0.9), 3)


def nearest_support(profile: dict, current_price: float) -> Optional[float]:
    """Return the nearest HVN below current_price (acts as support for CALL)."""
    if not profile or "hvn_levels" not in profile:
        return None
    below = [p for p in profile["hvn_levels"] if p < current_price]
    return max(below) if below else profile.get("val")


def nearest_resistance(profile: dict, current_price: float) -> Optional[float]:
    """Return the nearest HVN above current_price (acts as resistance for PUT)."""
    if not profile or "hvn_levels" not in profile:
        return None
    above = [p for p in profile["hvn_levels"] if p > current_price]
    return min(above) if above else profile.get("vah")


def key_levels_summary(profile: dict) -> dict:
    """Compact summary for logging / frontend display."""
    if not profile:
        return {}
    return {
        "poc":         profile.get("poc"),
        "vah":         profile.get("vah"),
        "val":         profile.get("val"),
        "top_hvn":     profile["hvn_levels"][:3] if profile.get("hvn_levels") else [],
        "price_range": profile.get("price_range"),
    }
