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


# ── Tunable parameters ──────────────────────────────────────────────────────
from typing import Optional

class ParamsPayload(BaseModel):
    move_min_pct:       Optional[float] = None
    max_positions:      Optional[int]   = None
    hard_sl_pct:        Optional[float] = None
    target_pct:         Optional[float] = None
    second_target_pct:  Optional[float] = None
    trail_activate_pct: Optional[float] = None
    trail_gap_pct:      Optional[float] = None
    auto_tune_enabled:  Optional[bool]  = None


@router.get("/params")
def get_params():
    return opening_breakout.get_params()


@router.patch("/params")
def update_params(payload: ParamsPayload):
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    return {"status": "ok", "params": opening_breakout.set_params(updates)}


@router.get("/tune")
def get_tune_report():
    return opening_breakout.get_tune_report()


@router.post("/tune/run")
def run_tune():
    from backend.database import TradeEnv
    return opening_breakout.analyze_and_tune(TradeEnv.PAPER)
