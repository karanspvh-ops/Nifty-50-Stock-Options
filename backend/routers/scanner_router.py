"""scanner_router.py — Endpoints for sector scanner & stock selection."""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from backend.core.sector_scanner  import sector_scanner
from backend.core.entry_engine    import check_entry
from backend.core.indicators      import compute_indicators
from backend.core.market_state    import market
from backend.core.tradable_tracker import tradable_tracker
from backend.database import TradeEnv

router = APIRouter(prefix="/api/scanner", tags=["scanner"])


@router.get("/tradable/active")
def tradable_active():
    """Stocks currently inside their tradable window (for the flashing panel)."""
    return tradable_tracker.get_active()


@router.get("/tradable/history")
def tradable_history(env: str = Query("paper"), date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return tradable_tracker.get_history(trade_env, date)


@router.get("/result")
def scan_result():
    result = sector_scanner.get_result()
    if not result:
        return {"status": "scanning", "result": None}
    return {
        "status":         "finalized" if sector_scanner.is_finalized() else "live",
        "top_sector":     result.top_sector,
        "sector_direction": result.sector_direction,
        "sector_pct":     result.sector_pct,
        "scan_time":      result.scan_time,
        "candidates": [
            {
                "token":            c.token,
                "symbol":           c.symbol,
                "sector":           c.sector,
                "pct_change":       c.pct_change,
                "direction":        c.direction,
                "momentum_score":   c.momentum_score,
                "ltp":              c.ltp,
                "est_premium":      c.est_option_premium,
                "lot_size":         c.lot_size,
                "reasoning":        c.reasoning,
            }
            for c in result.candidates
        ],
    }


@router.get("/entry-signal/{token}")
def entry_signal(token: str, direction: str = "call"):
    signal = check_entry(token, direction)
    return {
        "should_enter":     signal.should_enter,
        "score":            signal.score,
        "max_score":        signal.max_score,
        "conditions_met":   signal.conditions_met,
        "conditions_failed":signal.conditions_failed,
        "reasoning":        signal.reasoning,
    }


@router.get("/indicators/{token}")
def indicators(token: str, n: int = 50):
    candles = market.get_candles(token, n=n)
    if len(candles) < 26:
        raise HTTPException(status_code=404, detail="Not enough candle data")
    ind = compute_indicators(candles)
    return ind or {}
