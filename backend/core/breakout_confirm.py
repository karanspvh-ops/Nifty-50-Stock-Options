"""
breakout_confirm.py — 5-min candle breakout confirmation + Volume Profile.

Confirms an Opening-Breakout entry only when the move is "real":

  1. MOMENTUM  — recent 5-min candles keep making lower-lows (PUT) or
                 higher-highs (CALL) vs the previous candle.
  2. VOLUME    — the breakout candle prints large volume (>= 1.3x the
                 recent 20-candle average).
  3. VOLUME PROFILE — price is breaking out of the recent Value Area.
                 We compute the profile over BOTH a short (20-candle) and a
                 long (50-candle) window:
                   • 20 candles → the live opening structure (last ~100 min)
                   • 50 candles → the broader recent context (POC/VAH/VAL)
                 A PUT confirms when LTP breaks below the Value-Area-Low;
                 a CALL confirms when LTP breaks above the Value-Area-High.

Both windows are reported so the trader sees the important price levels
(POC = point of control, VAH/VAL = value-area high/low).
"""

from dataclasses import dataclass, field
from typing import Optional
from backend.core.market_state   import market
from backend.core.volume_profile import build_profile, key_levels_summary

CONSEC_MIN = 2      # min consecutive momentum candles
VOL_MULT   = 1.3    # breakout candle volume vs 20-candle average
RECENT_N   = 4      # candles inspected for the momentum streak
VP_SHORT   = 20     # short Volume-Profile window
VP_LONG    = 50     # long  Volume-Profile window
# Fix #2 — sharp-move override: a decisive thrust (big move + high volume)
# confirms even without 2 consecutive stair-step candles.
SPIKE_MOVE_PCT = 2.0
SPIKE_VOL_MULT = 2.0


@dataclass
class BreakoutSignal:
    confirmed:      bool
    consec:         int
    vol_ratio:      float
    breaking_short: bool          # broke 20-candle value area
    breaking_long:  bool          # broke 50-candle value area
    vp20:           dict = field(default_factory=dict)
    vp50:           dict = field(default_factory=dict)
    reason:         str  = ""


def confirm_breakout(token: str, direction: str, move_pct: float = 0.0) -> BreakoutSignal:
    candles = market.get_candles(token, n=VP_LONG)
    if len(candles) < CONSEC_MIN + 1:
        return BreakoutSignal(False, 0, 0.0, False, False,
                              reason="insufficient candle history")

    # ── 1. Consecutive lower-lows / higher-highs vs previous candle ───────────
    recent = candles[-(RECENT_N + 1):]
    consec = 0
    for i in range(len(recent) - 1, 0, -1):
        c, p = recent[i], recent[i - 1]
        if direction == "put":
            ok = c["low"]  < p["low"]  and c["close"] <= p["close"]
        else:
            ok = c["high"] > p["high"] and c["close"] >= p["close"]
        if ok:
            consec += 1
        else:
            break

    # ── 2. Volume of the breakout candle vs recent average ────────────────────
    vols     = [c.get("volume", 0) for c in candles[-VP_SHORT:]]
    avg_vol  = sum(vols) / max(len(vols), 1)
    last_vol = candles[-1].get("volume", 0)
    vol_ratio   = round(last_vol / avg_vol, 2) if avg_vol else 0.0
    vol_strong  = vol_ratio >= VOL_MULT

    # ── 3. Volume Profile over 20 and 50 candles ─────────────────────────────
    # Guard: only build a profile when we have enough candles for a meaningful
    # value area; early-session (e.g. first 30 min = ~6 candles) profiles on
    # 2-3 candles produce trivially wide value areas that always confirm.
    vp20 = build_profile(candles[-VP_SHORT:]) if len(candles) >= VP_SHORT else {}
    vp50 = build_profile(candles[-VP_LONG:])  if len(candles) >= VP_LONG  else {}
    ltp  = candles[-1]["close"]

    def breaking(vp: dict) -> bool:
        if not vp:
            return False
        return (ltp < vp.get("val", ltp)) if direction == "put" else (ltp > vp.get("vah", ltp))

    bshort, blong = breaking(vp20), breaking(vp50)

    # Confirmation: value-area break PLUS either
    #   (a) >=2 consecutive momentum candles on >=1.3x volume, OR
    #   (b) Fix #2 sharp thrust: move >=2% on >=2x volume (momentum may be 1)
    momentum_path = (consec >= CONSEC_MIN) and vol_strong
    spike_path    = (abs(move_pct) >= SPIKE_MOVE_PCT) and (vol_ratio >= SPIKE_VOL_MULT)
    confirmed = (bshort or blong) and (momentum_path or spike_path)

    arrow = "lower-lows" if direction == "put" else "higher-highs"
    path  = "spike" if (spike_path and not momentum_path) else "momentum"
    reason = (
        f"[{path}] {consec} consec {arrow}; vol x{vol_ratio}; move {move_pct:+.1f}%; "
        f"break VA20={bshort} VA50={blong} | "
        f"VP50 POC {vp50.get('poc')} VAH {vp50.get('vah')} VAL {vp50.get('val')}"
    )
    return BreakoutSignal(
        confirmed=confirmed, consec=consec, vol_ratio=vol_ratio,
        breaking_short=bshort, breaking_long=blong,
        vp20=key_levels_summary(vp20), vp50=key_levels_summary(vp50),
        reason=reason,
    )
