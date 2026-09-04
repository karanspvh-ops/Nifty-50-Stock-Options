"""
indicators.py — Technical indicator engine.

Computes from a list of 5-min OHLCV candle dicts:
  EMA 5, 20, 200 | RSI 14 | MACD (12,26,9) | ADX 14 | VWAP | Bollinger Bands

Uses the `ta` library (Python 3.14 compatible, no numba dependency).
Returns a snapshot dict of the LATEST bar's values.
"""

import pandas as pd
import ta
from typing import List, Optional


def _to_df(candles: List[dict]) -> pd.DataFrame:
    df = pd.DataFrame(candles)
    df.columns = [c.lower() for c in df.columns]
    # Normalise timestamp column
    if "ts" in df.columns:
        df["timestamp"] = pd.to_datetime(df["ts"], format="mixed", utc=True).dt.tz_localize(None)
    df = df.sort_values("timestamp").reset_index(drop=True)
    for col in ("open", "high", "low", "close"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    return df


def compute_indicators(candles: List[dict]) -> Optional[dict]:
    """
    Returns a dict of latest-bar indicator values.
    Returns None if not enough candles.
    """
    if len(candles) < 26:          # minimum for MACD
        return None

    df = _to_df(candles)

    # ── EMA ────────────────────────────────────────────────────────────────────
    df["ema5"]   = ta.trend.EMAIndicator(df["close"], window=5).ema_indicator()
    df["ema20"]  = ta.trend.EMAIndicator(df["close"], window=20).ema_indicator()
    # EMA200 only if enough data, else NaN
    df["ema200"] = ta.trend.EMAIndicator(df["close"], window=min(200, len(df))).ema_indicator()

    # ── RSI ────────────────────────────────────────────────────────────────────
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()

    # ── MACD ───────────────────────────────────────────────────────────────────
    macd_obj          = ta.trend.MACD(df["close"], window_slow=26, window_fast=12, window_sign=9)
    df["macd"]        = macd_obj.macd()
    df["macd_signal"] = macd_obj.macd_signal()
    df["macd_hist"]   = macd_obj.macd_diff()

    # ── ADX ────────────────────────────────────────────────────────────────────
    adx_obj   = ta.trend.ADXIndicator(df["high"], df["low"], df["close"], window=14)
    df["adx"]  = adx_obj.adx()
    df["adx_pos"] = adx_obj.adx_pos()   # +DI
    df["adx_neg"] = adx_obj.adx_neg()   # -DI

    # ── VWAP (session-based) ──────────────────────────────────────────────────
    df["vwap"] = (df["close"] * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, 1)

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    bb             = ta.volatility.BollingerBands(df["close"], window=20, window_dev=2)
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_mid"]   = bb.bollinger_mavg()

    # ── Volume SMA (20-bar average) ────────────────────────────────────────────
    df["vol_sma20"] = df["volume"].rolling(20).mean()

    # ── Return latest row ─────────────────────────────────────────────────────
    last = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else last

    def safe(val):
        import math
        return None if (val is None or (isinstance(val, float) and math.isnan(val))) else round(float(val), 4)

    return {
        "close":       safe(last["close"]),
        "ema5":        safe(last["ema5"]),
        "ema20":       safe(last["ema20"]),
        "ema200":      safe(last["ema200"]),
        "rsi":         safe(last["rsi"]),
        "macd":        safe(last["macd"]),
        "macd_signal": safe(last["macd_signal"]),
        "macd_hist":   safe(last["macd_hist"]),
        "adx":         safe(last["adx"]),
        "adx_pos":     safe(last["adx_pos"]),
        "adx_neg":     safe(last["adx_neg"]),
        "vwap":        safe(last["vwap"]),
        "bb_upper":    safe(last["bb_upper"]),
        "bb_lower":    safe(last["bb_lower"]),
        "bb_mid":      safe(last["bb_mid"]),
        "volume":      int(last["volume"]),
        "vol_sma20":   safe(last["vol_sma20"]),
        # Cross signals — cast to Python bool to avoid numpy.bool_ serialization issues
        "ema5_cross_above_ema20": bool(
            safe(prev["ema5"]) is not None and
            prev["ema5"] < prev["ema20"] and
            last["ema5"]  > last["ema20"]
        ),
        "ema5_cross_below_ema20": bool(
            safe(prev["ema5"]) is not None and
            prev["ema5"] > prev["ema20"] and
            last["ema5"]  < last["ema20"]
        ),
        "macd_cross_up": bool(
            safe(prev["macd"]) is not None and
            prev["macd"] < prev["macd_signal"] and
            last["macd"] > last["macd_signal"]
        ),
        "macd_cross_down": bool(
            safe(prev["macd"]) is not None and
            prev["macd"] > prev["macd_signal"] and
            last["macd"] < last["macd_signal"]
        ),
        "volume_spike": bool(
            safe(last["vol_sma20"]) is not None and
            last["vol_sma20"] > 0 and
            last["volume"] >= last["vol_sma20"] * 1.5
        ),
        "is_trending": bool(safe(last["adx"]) is not None and last["adx"] > 20),
        "is_sideways": bool(safe(last["adx"]) is not None and last["adx"] < 20),
    }


def get_market_direction(indicators: dict) -> str:
    """
    Quick directional bias from indicators.
    Returns 'bullish', 'bearish', or 'neutral'.
    """
    if not indicators:
        return "neutral"
    c   = indicators.get("close")
    e20 = indicators.get("ema20")
    e5  = indicators.get("ema5")
    rsi = indicators.get("rsi")
    adx = indicators.get("adx")
    macd_h = indicators.get("macd_hist")

    if None in (c, e20, e5, rsi):
        return "neutral"

    bull_score = 0
    bear_score = 0

    if c > e20:       bull_score += 1
    else:             bear_score += 1
    if e5 > e20:      bull_score += 1
    else:             bear_score += 1
    if rsi > 50:      bull_score += 1
    else:             bear_score += 1
    if macd_h and macd_h > 0: bull_score += 1
    elif macd_h:      bear_score += 1

    if bull_score >= 3:  return "bullish"
    if bear_score >= 3:  return "bearish"
    return "neutral"
