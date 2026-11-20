"""
risk_router.py — REST endpoints for risk management.

Exposes:
  - Live risk snapshot of all open trades
  - Manual force-exit
  - Portfolio SL status + override
  - Dynamic SL details per trade
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from backend.core.risk_engine    import risk_engine
from backend.core.session_manager import get_portfolio_state, check_portfolio_sl
from backend.database import TradeEnv

router = APIRouter(prefix="/api/risk", tags=["risk"])


# ── Snapshots ──────────────────────────────────────────────────────────────

@router.get("/snapshot")
def risk_snapshot():
    """Live risk status for all open trades."""
    return risk_engine.get_open_risk_snapshot()


@router.get("/portfolio/{env}")
def portfolio_risk(env: str):
    """Portfolio state + SL check for given environment."""
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")

    state      = get_portfolio_state(trade_env)
    sl_breached = check_portfolio_sl(trade_env)
    return {**state, "sl_breached": sl_breached}


# ── Force exit ─────────────────────────────────────────────────────────────

class ForceExitPayload(BaseModel):
    trade_id: int
    reason:   Optional[str] = "Manual exit via dashboard"


@router.post("/force-exit")
def force_exit(payload: ForceExitPayload):
    result = risk_engine.force_exit_trade(
        trade_id = payload.trade_id,
        reason   = payload.reason or "Manual exit",
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ── Engine control ─────────────────────────────────────────────────────────

@router.post("/engine/start")
def start_risk_engine():
    risk_engine.start()
    return {"status": "started"}


@router.post("/engine/stop")
def stop_risk_engine():
    risk_engine.stop()
    return {"status": "stopped"}


# ── Manual halt override ───────────────────────────────────────────────────

@router.post("/resume")
def resume_trading():
    """Manually clear a trading halt (e.g. after portfolio SL override)."""
    from backend.core.market_state import market
    market.resume_trading()
    return {"status": "resumed", "halted": market.is_trading_halted()}


# ── Volume Profile on demand ───────────────────────────────────────────────

@router.get("/volume-profile/{token}")
def volume_profile(token: str, n: int = 20):
    """Compute and return Volume Profile for a stock token."""
    from backend.core.market_state  import market
    from backend.core.volume_profile import build_profile, key_levels_summary
    candles = market.get_candles(token, n=n)
    if not candles:
        raise HTTPException(status_code=404, detail="No candle data available for this token")
    profile = build_profile(candles)
    return {
        "token":   token,
        "candles": len(candles),
        "summary": key_levels_summary(profile),
        "hvn":     profile.get("hvn_levels", [])[:5],
        "lvn":     profile.get("lvn_levels", [])[:5],
    }
