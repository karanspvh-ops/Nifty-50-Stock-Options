"""
session_router.py — Endpoints for trading session lifecycle and portfolio state.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.session_manager import (
    get_or_create_session, update_session_status,
    get_portfolio_state, check_portfolio_sl
)
from backend.database import TradeEnv, SessionStatus

router = APIRouter(prefix="/api/session", tags=["session"])


class StatusUpdate(BaseModel):
    env:    str   # "paper" or "live"
    status: str   # "scanning" / "active" / "paused" / "stopped" / "killed"


@router.get("/{env}")
def get_session(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return get_or_create_session(trade_env)


@router.post("/status")
def set_status(payload: StatusUpdate):
    try:
        trade_env = TradeEnv(payload.env)
        status    = SessionStatus(payload.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    update_session_status(trade_env, status)
    return {"status": "updated"}


@router.get("/portfolio/{env}")
def portfolio(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    state = get_portfolio_state(trade_env)
    # Also run SL check
    sl_breached = check_portfolio_sl(trade_env)
    state["sl_breached"] = sl_breached
    return state
