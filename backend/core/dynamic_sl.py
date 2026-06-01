"""
dynamic_sl.py — Dynamic Stop-Loss calculator.

Rules (exactly as specified):
  1. Hard SL floor:    entry * (1 - trade_sl_pct/100)
                       Dynamic SL can NEVER go below this.

  2. Break-even floor: If dynamic SL has been raised above entry price
                       and price falls back to entry → exit at break-even.

  3. Profit lock at 15%+ threshold:
       - Compute continuation probability via Volume Profile
       - prob > 0.6  (likely continuing) → lock 5% profit   SL
       - prob ≤ 0.6  (likely reversing)  → lock 10% profit  SL
       These are expressed relative to the current LTP, not entry.

  4. SL can only move UP for CALL (ratchet) and DOWN for PUT — never backwards.

Returns a SLDecision dataclass with the new SL price + reasoning.
"""

from dataclasses import dataclass, field
from typing import Optional
from backend.core.volume_profile import (
    build_profile, continuation_probability,
    nearest_support, nearest_resistance, key_levels_summary
)
from backend.core.market_state import market


# ── Thresholds ────────────────────────────────────────────────────────────────
PROFIT_LOCK_TRIGGER_PCT  = 15.0   # activate dynamic SL once trade is +15%
HIGH_PROB_THRESHOLD      = 0.60   # above this → lock 5% | below → lock 10%
LOCK_HIGH_PROB_PCT       = 5.0    # profit locked when prob is HIGH
LOCK_LOW_PROB_PCT        = 10.0   # profit locked when prob is LOW


@dataclass
class SLDecision:
    new_sl_price:     float
    hard_sl_price:    float
    lock_type:        str              # "hard", "breakeven", "lock_5pct", "lock_10pct", "initial"
    prob_continuation: float = 0.0
    reasoning:        str  = ""
    vp_summary:       dict = field(default_factory=dict)


def compute_dynamic_sl(
    token:           str,
    direction:       str,    # "call" or "put"
    entry_price:     float,
    current_ltp:     float,
    trade_sl_pct:    float,  # e.g. 5.0 for 5%
    current_dyn_sl:  Optional[float] = None,   # previously set dynamic SL
    n_candles:       int = 20,
) -> SLDecision:
    """
    Compute the new dynamic SL price for an open trade.

    Called every tick for every open trade by the RiskEngine.
    """

    # ── 1. Hard SL (absolute floor, never breachable) ─────────────────────────
    if direction == "call":
        hard_sl = round(entry_price * (1 - trade_sl_pct / 100), 2)
    else:  # put — entry is the option premium paid
        hard_sl = round(entry_price * (1 - trade_sl_pct / 100), 2)

    # ── 2. Current profit % ───────────────────────────────────────────────────
    profit_pct = ((current_ltp - entry_price) / entry_price) * 100

    # ── 3. If not yet in 15% profit → stay at hard SL ─────────────────────────
    if profit_pct < PROFIT_LOCK_TRIGGER_PCT:
        candidate_sl = hard_sl
        return SLDecision(
            new_sl_price      = candidate_sl,
            hard_sl_price     = hard_sl,
            lock_type         = "initial",
            prob_continuation = 0.0,
            reasoning         = (
                f"Trade at {profit_pct:.1f}% profit — below {PROFIT_LOCK_TRIGGER_PCT}% trigger. "
                f"Holding hard SL at {hard_sl:.2f}."
            ),
        )

    # ── 4. Profit ≥ 15% — compute Volume Profile ──────────────────────────────
    candles = market.get_candles(token, n=n_candles)
    profile = build_profile(candles) if candles else {}

    vp_direction = "up" if direction == "call" else "down"
    prob = continuation_probability(profile, current_ltp, vp_direction)

    # ── 5. Choose lock level ──────────────────────────────────────────────────
    if prob > HIGH_PROB_THRESHOLD:
        lock_pct  = LOCK_HIGH_PROB_PCT
        lock_type = "lock_5pct"
    else:
        lock_pct  = LOCK_LOW_PROB_PCT
        lock_type = "lock_10pct"

    # SL = current LTP minus lock_pct (expressed as % of LTP)
    candidate_sl = round(current_ltp * (1 - lock_pct / 100), 2)

    # ── 6. Never go below hard SL ─────────────────────────────────────────────
    candidate_sl = max(candidate_sl, hard_sl)

    # ── 7. Break-even floor — once SL > entry, never pull back below entry ────
    if current_dyn_sl is not None and current_dyn_sl >= entry_price:
        candidate_sl = max(candidate_sl, entry_price)
        if candidate_sl == entry_price:
            lock_type = "breakeven"

    # ── 8. Ratchet — SL can only move UP (for call) never backwards ──────────
    if current_dyn_sl is not None:
        if direction == "call":
            candidate_sl = max(candidate_sl, current_dyn_sl)
        else:
            candidate_sl = min(candidate_sl, current_dyn_sl)  # PUT: SL only moves down

    # ── 9. Volume Profile support as additional signal ─────────────────────────
    if profile:
        if direction == "call":
            support = nearest_support(profile, current_ltp)
            if support and support > hard_sl:
                # Use support as SL if it's tighter than computed
                vp_sl = round(support * 0.995, 2)
                candidate_sl = max(candidate_sl, vp_sl)
        else:
            resistance = nearest_resistance(profile, current_ltp)
            if resistance and resistance < (entry_price * (1 + trade_sl_pct / 100)):
                vp_sl = round(resistance * 1.005, 2)
                candidate_sl = min(candidate_sl, vp_sl)

    reasoning = (
        f"Profit: {profit_pct:.1f}% | Prob continuation: {prob:.2f} | "
        f"Lock type: {lock_type} | Lock %: {lock_pct}% | "
        f"New SL: {candidate_sl:.2f} | Hard SL floor: {hard_sl:.2f}"
    )

    return SLDecision(
        new_sl_price      = candidate_sl,
        hard_sl_price     = hard_sl,
        lock_type         = lock_type,
        prob_continuation = prob,
        reasoning         = reasoning,
        vp_summary        = key_levels_summary(profile),
    )


def should_exit_breakeven(
    entry_price:    float,
    current_ltp:    float,
    current_dyn_sl: Optional[float],
) -> bool:
    """
    True if dynamic SL was set above entry AND price fell back to entry.
    Triggers a no-profit-no-loss exit.
    """
    if current_dyn_sl is None:
        return False
    if current_dyn_sl < entry_price:
        return False
    # Price has returned to entry level
    return current_ltp <= entry_price
