"""
exit_engine.py — Exit signal detector (separate from Risk Engine).

The Risk Engine handles SL/target exits (price-based).
This engine handles TREND-based exits:
  - Market goes sideways (ADX < 18)
  - RSI reaches extreme (> 75 for call, < 25 for put)
  - MACD crosses against direction
  - Price breaks below EMA20 (call) or above EMA20 (put)
  - EMA5 crosses against the trade direction

Any ONE of these triggers a trend-reversal exit recommendation.
The trading engine acts on this signal.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from backend.core.indicators import compute_indicators
from backend.core.market_state import market


@dataclass
class ExitSignal:
    should_exit:  bool
    reason:       str
    urgency:      str         # "immediate" | "watch" | "hold"
    indicators:   dict = field(default_factory=dict)
    triggers:     List[str] = field(default_factory=list)


def check_exit(
    token:          str,
    direction:      str,        # "call" or "put"
    entry_price:    float,
    current_ltp:    float,
    n_candles:      int = 30,
) -> ExitSignal:
    """Evaluate if the trend has ended and we should exit."""

    candles    = market.get_candles(token, n=n_candles)
    indicators = compute_indicators(candles) if len(candles) >= 26 else None

    if not indicators:
        return ExitSignal(
            should_exit = False,
            reason      = "Insufficient data",
            urgency     = "hold",
        )

    c         = indicators
    close     = c.get("close", current_ltp) or current_ltp
    ema5      = c.get("ema5")
    ema20     = c.get("ema20")
    rsi       = c.get("rsi")
    adx       = c.get("adx")
    macd_h    = c.get("macd_hist")
    vwap      = c.get("vwap")

    triggers  = []
    urgency   = "hold"

    if direction == "call":
        # ── Sideways / range-bound ─────────────────────────────────────────────
        if adx and adx < 18:
            triggers.append(f"ADX {adx:.1f} < 18 — market went sideways")
            urgency = "watch"

        # ── RSI overbought ────────────────────────────────────────────────────
        if rsi and rsi > 75:
            triggers.append(f"RSI {rsi:.1f} > 75 — overbought, reversal likely")
            urgency = "immediate"

        # ── MACD bearish cross ────────────────────────────────────────────────
        if c.get("macd_cross_down"):
            triggers.append("MACD crossed below signal — momentum reversing")
            urgency = "immediate"

        # ── Price broke below EMA20 ───────────────────────────────────────────
        if ema20 and close < ema20:
            triggers.append(f"Price {close:.1f} broke below EMA20 {ema20:.1f}")
            urgency = "immediate"

        # ── EMA5 crossed below EMA20 ──────────────────────────────────────────
        if c.get("ema5_cross_below_ema20"):
            triggers.append("EMA5 crossed below EMA20 — trend reversal")
            urgency = "immediate"

        # ── Price below VWAP on sustained basis ───────────────────────────────
        if vwap and close < vwap * 0.998:
            triggers.append(f"Price {close:.1f} slipped below VWAP {vwap:.1f}")
            if urgency == "hold":
                urgency = "watch"

    else:  # put
        if adx and adx < 18:
            triggers.append(f"ADX {adx:.1f} < 18 — market went sideways")
            urgency = "watch"

        if rsi and rsi < 25:
            triggers.append(f"RSI {rsi:.1f} < 25 — oversold, bounce likely")
            urgency = "immediate"

        if c.get("macd_cross_up"):
            triggers.append("MACD crossed above signal — bearish momentum fading")
            urgency = "immediate"

        if ema20 and close > ema20:
            triggers.append(f"Price {close:.1f} broke above EMA20 {ema20:.1f}")
            urgency = "immediate"

        if c.get("ema5_cross_above_ema20"):
            triggers.append("EMA5 crossed above EMA20 — bearish trend reversing")
            urgency = "immediate"

        if vwap and close > vwap * 1.002:
            triggers.append(f"Price {close:.1f} recovered above VWAP {vwap:.1f}")
            if urgency == "hold":
                urgency = "watch"

    should_exit = len(triggers) > 0
    reason      = " | ".join(triggers) if triggers else "No exit signals — hold position"

    return ExitSignal(
        should_exit = should_exit,
        reason      = reason,
        urgency     = urgency,
        indicators  = c,
        triggers    = triggers,
    )
