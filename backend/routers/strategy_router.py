"""strategy_router.py — Opening Breakout strategy: plan preview + control."""

from fastapi import APIRouter
from pydantic import BaseModel
from backend.core.opening_breakout import opening_breakout

router = APIRouter(prefix="/api/strategy", tags=["strategy"])


@router.get("/plan")
def trade_plan():
    """The pre-trade review: which sector + stocks we intend to trade."""
    return opening_breakout.get_plan()


@router.get("/state")
def strategy_state():
    return opening_breakout.get_state()


class EnablePayload(BaseModel):
    enabled: bool


@router.post("/enable")
def set_enabled(payload: EnablePayload):
    opening_breakout.set_enabled(payload.enabled)
    return {"status": "ok", "enabled": payload.enabled}
