"""
entry_engine.py — Entry signal detector.

For a given stock + direction (call/put), determines if NOW is
the right time to enter based on:

CALL entry conditions (all must pass):
  1. Price > EMA20  (stock is above medium-term trend)
  2. Price > EMA200 OR EMA200 not available (longer term support)
  3. EMA5 >= EMA20  (short-term momentum aligns)
  4. RSI 45–72      (not overbought, has room to run)
  5. MACD hist > 0  (bullish momentum)
  6. ADX > 20       (trending, not sideways)
  7. Volume spike   (institutions confirming the move)
  8. Price above VWAP (intraday buyer control)

PUT entry conditions (mirror):
  1. Price < EMA20
  2. Price < EMA200 (or not available)
  3. EMA5 <= EMA20
  4. RSI 28–55
  5. MACD hist < 0
  6. ADX > 20
  7. Volume spike
  8. Price below VWAP

Returns an EntrySignal with: should_enter, score (0–8), reasoning.
Score 5+ = strong signal. Score 6+ = very strong.
"""

from dataclasses import dataclass, field
from typing import Optional, List
from backend.core.indicators import compute_indicators
from backend.core.market_state import market


@dataclass
class EntrySignal:
    should_enter:    bool
    direction:       str         # "call" or "put"
    score:           int         # 0–8 conditions met
    max_score:       int = 8
    conditions_met:  List[str] = field(default_factory=list)
    conditions_failed: List[str] = field(default_factory=list)
    indicators:      dict = field(default_factory=dict)
    reasoning:       str  = ""


ENTRY_SCORE_THRESHOLD = 5       # minimum conditions needed to enter


def check_entry(token: str, direction: str, n_candles: int = 50) -> EntrySignal:
    """
    Evaluate entry signal for a stock.
    direction: "call" or "put"
    """
    candles    = market.get_candles(token, n=n_candles)
    indicators = compute_indicators(candles) if len(candles) >= 26 else None

    if not indicators:
        return EntrySignal(
            should_enter = False,
            direction    = direction,
            score        = 0,
            reasoning    = "Insufficient candle data for analysis",
        )

    met    = []
    failed = []
    c      = indicators

    close  = c.get("close", 0) or 0
    ema5   = c.get("ema5")
    ema20  = c.get("ema20")
    ema200 = c.get("ema200")
    rsi    = c.get("rsi")
    macd_h = c.get("macd_hist")
    adx    = c.get("adx")
    vwap   = c.get("vwap")
    vol_spike = c.get("volume_spike", False)

    # ── Helper: safe format float ─────────────────────────────────────────────
    def fmt(v, d=1): return f"{v:.{d}f}" if v is not None else "N/A"

    if direction == "call":
        # ── Condition 1: Price above EMA20 ────────────────────────────────────
        if ema20 and close > ema20:
            met.append(f"Price {fmt(close)} > EMA20 {fmt(ema20)}")
        else:
            failed.append(f"Price {fmt(close)} <= EMA20 {fmt(ema20)}")

        # ── Condition 2: Price above EMA200 (skip if not enough data) ─────────
        if ema200 is None or close > ema200:
            met.append(f"Price > EMA200 ({fmt(ema200)})")
        else:
            failed.append(f"Price {fmt(close)} <= EMA200 {fmt(ema200)}")

        # ── Condition 3: EMA5 >= EMA20 ────────────────────────────────────────
        if ema5 and ema20 and ema5 >= ema20:
            met.append(f"EMA5 {fmt(ema5)} >= EMA20 {fmt(ema20)}")
        else:
            failed.append(f"EMA5 {fmt(ema5)} < EMA20 {fmt(ema20)}")

        # ── Condition 4: RSI 45–72 ─────────────────────────────────────────────
        if rsi and 45 <= rsi <= 72:
            met.append(f"RSI {fmt(rsi)} in [45-72]")
        else:
            failed.append(f"RSI {fmt(rsi)} outside [45-72]")

        # ── Condition 5: MACD hist positive ───────────────────────────────────
        if macd_h and macd_h > 0:
            met.append(f"MACD hist +{fmt(macd_h, 4)} (bullish)")
        else:
            failed.append(f"MACD hist {fmt(macd_h, 4)} <= 0")

        # ── Condition 6: ADX trending ─────────────────────────────────────────
        if adx and adx > 20:
            met.append(f"ADX {fmt(adx)} > 20 (trending)")
        else:
            failed.append(f"ADX {fmt(adx)} <= 20 (sideways)")

        # ── Condition 7: Volume spike ─────────────────────────────────────────
        if vol_spike:
            met.append("Volume spike confirmed")
        else:
            failed.append("No volume spike")

        # ── Condition 8: Price above VWAP ────────────────────────────────────
        if vwap and close > vwap:
            met.append(f"Price {fmt(close)} > VWAP {fmt(vwap)}")
        else:
            failed.append(f"Price {fmt(close)} <= VWAP {fmt(vwap)}")

    else:  # put
        if ema20 and close < ema20:
            met.append(f"Price {fmt(close)} < EMA20 {fmt(ema20)}")
        else:
            failed.append(f"Price {fmt(close)} >= EMA20 {fmt(ema20)}")

        if ema200 is None or close < ema200:
            met.append(f"Price < EMA200 ({fmt(ema200)})")
        else:
            failed.append(f"Price {fmt(close)} >= EMA200 {fmt(ema200)}")

        if ema5 and ema20 and ema5 <= ema20:
            met.append(f"EMA5 {fmt(ema5)} <= EMA20 {fmt(ema20)}")
        else:
            failed.append(f"EMA5 {fmt(ema5)} > EMA20 {fmt(ema20)}")

        if rsi and 28 <= rsi <= 55:
            met.append(f"RSI {fmt(rsi)} in [28-55]")
        else:
            failed.append(f"RSI {fmt(rsi)} outside [28-55]")

        if macd_h and macd_h < 0:
            met.append(f"MACD hist {fmt(macd_h, 4)} (bearish)")
        else:
            failed.append(f"MACD hist {fmt(macd_h, 4)} >= 0")

        if adx and adx > 20:
            met.append(f"ADX {fmt(adx)} > 20 (trending)")
        else:
            failed.append(f"ADX {fmt(adx)} <= 20 (sideways)")

        if vol_spike:
            met.append("Volume spike confirmed")
        else:
            failed.append("No volume spike")

        if vwap and close < vwap:
            met.append(f"Price {fmt(close)} < VWAP {fmt(vwap)}")
        else:
            failed.append(f"Price {fmt(close)} >= VWAP {fmt(vwap)}")

    score        = len(met)
    should_enter = score >= ENTRY_SCORE_THRESHOLD

    reasoning = (
        f"{'ENTER' if should_enter else 'SKIP'} | {direction.upper()} | "
        f"Score: {score}/{len(met)+len(failed)} | "
        f"Passed: {', '.join(met[:3])} | "
        f"Failed: {', '.join(failed[:2])}"
    )

    return EntrySignal(
        should_enter     = should_enter,
        direction        = direction,
        score            = score,
        max_score        = 8,
        conditions_met   = met,
        conditions_failed= failed,
        indicators       = c,
        reasoning        = reasoning,
    )
