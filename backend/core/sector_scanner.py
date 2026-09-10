"""
sector_scanner.py — Sector ranking + top-2 stock selector.

Scanning window: 9:00 AM – 9:45 AM IST
After 9:45 AM: returns the finalized selection (frozen until next day).

Pipeline:
  1. Rank all sectors by absolute average % move
  2. Pick top sector (most momentum, bull or bear)
  3. Within that sector filter stocks by:
       a. Options availability (must have NFO token)
       b. Affordability (option_premium * lot_size ≤ available_funds / 2)
       c. Minimum volume (volume > sector average)
  4. Score each candidate: momentum_score = abs(pct_change) * vol_ratio
  5. Return top 2 with direction (call/put)
"""

import os, sys
from datetime import datetime, time as dtime
from typing import List, Optional, Tuple
from dataclasses import dataclass, field

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state     import market
from backend.core.stock_universe   import get_stocks_in_sector, get_sectors_for_index, get_token
from backend.core.indicators       import compute_indicators, get_market_direction
from backend.core.settings_manager import get_settings, get_active_index


# ── Market timing ─────────────────────────────────────────────────────────────
SCAN_START   = dtime(9, 0)
SCAN_END     = dtime(9, 45)
SQUARE_OFF   = dtime(15, 15)


@dataclass
class StockCandidate:
    token:            str
    symbol:           str
    sector:           str
    pct_change:       float
    direction:        str          # "call" or "put"
    momentum_score:   float
    ltp:              float
    est_option_premium: float = 0.0
    lot_size:         int     = 1
    indicators:       dict    = field(default_factory=dict)
    reasoning:        str     = ""


@dataclass
class ScanResult:
    top_sector:       str
    sector_direction: str          # "bullish" or "bearish"
    sector_pct:       float
    candidates:       List[StockCandidate]
    scan_time:        str


class SectorScanner:
    def __init__(self):
        self._result: Optional[ScanResult] = None
        self._finalized: bool = False

    # ── Main scan ─────────────────────────────────────────────────────────────

    def run_scan(self) -> Optional[ScanResult]:
        """
        Run a full sector scan. Call this repeatedly during 9:00–9:45 window.
        Returns the latest ScanResult (not yet finalized).
        """
        now = datetime.now().time()
        if now > SCAN_END and self._finalized:
            return self._result     # frozen after 9:45

        index         = get_active_index()
        sector_moves  = market.get_sector_moves()

        if not sector_moves:
            return None

        # ── Step 1: rank sectors ───────────────────────────────────────────────
        ranked = sorted(
            sector_moves.items(),
            key=lambda x: abs(x[1].get("pct_change", 0)),
            reverse=True,
        )
        if not ranked:
            return None

        top_sector, top_data  = ranked[0]
        sector_pct            = top_data.get("pct_change", 0)
        sector_dir            = top_data.get("direction", "flat")

        if sector_dir == "flat":
            return None

        trade_direction = "call" if sector_pct > 0 else "put"

        # ── Step 2: get stocks in top sector ──────────────────────────────────
        stocks   = top_data.get("stocks", [])
        settings = get_settings()
        available_funds = settings.get("available_funds") or 500_000

        candidates: List[StockCandidate] = []

        for stock in stocks:
            token  = stock.get("token")
            symbol = stock.get("symbol")
            pct    = stock.get("pct_change", 0)
            ltp    = stock.get("ltp", 0)

            if not token or not ltp:
                continue

            # Directional filter — must align with sector direction
            stock_dir = "call" if pct > 0 else "put"
            if stock_dir != trade_direction:
                continue

            # ── Indicator analysis ─────────────────────────────────────────────
            candles    = market.get_candles(token, n=50)
            indicators = compute_indicators(candles) if len(candles) >= 26 else {}
            ind_dir    = get_market_direction(indicators) if indicators else "neutral"

            # Must have indicator confirmation
            if indicators:
                if trade_direction == "call" and ind_dir == "bearish":
                    continue
                if trade_direction == "put"  and ind_dir == "bullish":
                    continue

            # ── Options availability: only F&O stocks have a lot size ──────────
            from backend.core.stock_universe import get_meta
            meta     = get_meta(token)
            lot_size = meta.get("lot_size", 0)
            if lot_size <= 0:
                continue   # no options on this stock (e.g. non-F&O Nifty 200 name)

            # ── Affordability: rough premium estimate ──────────────────────────
            # Option premium ~ 1-3% of stock price for ATM options
            est_premium = round(ltp * 0.015, 2)     # ~1.5% ATM estimate
            cost     = est_premium * lot_size

            # Must be affordable (max 50% of available funds per trade)
            if cost > available_funds * 0.5:
                continue

            # ── Volume ratio ───────────────────────────────────────────────────
            tick = market.get_tick(token)
            vol  = tick.get("volume", 0) if tick else 0
            avg_sector_vol = (
                sum(s.get("volume", 0) for s in stocks if s.get("volume")) /
                max(len(stocks), 1)
            )
            vol_ratio = vol / max(avg_sector_vol, 1)

            # ── Momentum score ─────────────────────────────────────────────────
            score = abs(pct) * (1 + vol_ratio * 0.3)
            if indicators and indicators.get("volume_spike"):
                score *= 1.2    # boost for volume spike
            if indicators and indicators.get("is_trending"):
                score *= 1.1    # boost for trending

            # ── Reasoning string ───────────────────────────────────────────────
            reasoning = (
                f"Sector: {top_sector} ({sector_pct:+.2f}%) | "
                f"Stock: {pct:+.2f}% | Direction: {trade_direction.upper()} | "
                f"Score: {score:.2f} | Vol ratio: {vol_ratio:.2f}"
            )
            if indicators:
                reasoning += (
                    f" | RSI: {indicators.get('rsi','N/A')} | "
                    f"ADX: {indicators.get('adx','N/A')} | "
                    f"MACD hist: {indicators.get('macd_hist','N/A')}"
                )

            candidates.append(StockCandidate(
                token              = token,
                symbol             = symbol,
                sector             = top_sector,
                pct_change         = pct,
                direction          = trade_direction,
                momentum_score     = round(score, 4),
                ltp                = ltp,
                est_option_premium = est_premium,
                lot_size           = lot_size,
                indicators         = indicators or {},
                reasoning          = reasoning,
            ))

        # ── Step 3: sort by score, take top 2 ────────────────────────────────
        candidates.sort(key=lambda x: x.momentum_score, reverse=True)
        top2 = candidates[:2]

        self._result = ScanResult(
            top_sector       = top_sector,
            sector_direction = "bullish" if sector_pct > 0 else "bearish",
            sector_pct       = sector_pct,
            candidates       = top2,
            scan_time        = datetime.now().isoformat(),
        )

        # Finalize after 9:45
        if now >= SCAN_END and not self._finalized:
            self._finalized = True
            print(f"[SCANNER] FINALIZED at {datetime.now().strftime('%H:%M:%S')} — "
                  f"Top sector: {top_sector} | Stocks: {[c.symbol for c in top2]}")

        return self._result

    def get_result(self) -> Optional[ScanResult]:
        return self._result

    def is_finalized(self) -> bool:
        return self._finalized

    def reset(self):
        """Call at start of each trading day."""
        self._result    = None
        self._finalized = False

    def is_scan_window(self) -> bool:
        now = datetime.now().time()
        return SCAN_START <= now <= SCAN_END

    def is_trading_window(self) -> bool:
        now = datetime.now().time()
        return SCAN_END < now < SQUARE_OFF

    def is_square_off_time(self) -> bool:
        return datetime.now().time() >= SQUARE_OFF


# ── Global singleton ──────────────────────────────────────────────────────────
sector_scanner = SectorScanner()
