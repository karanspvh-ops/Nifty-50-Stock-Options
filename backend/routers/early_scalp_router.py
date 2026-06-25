"""
early_scalp_router.py — REST API for the Early Scalp strategy.
"""

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Optional

from backend.core.early_scalp import early_scalp

router = APIRouter(prefix="/api/scalp", tags=["early_scalp"])


@router.get("/plan")
def get_plan():
    """Full plan: candidates, scores, OI, candle counts, params."""
    return early_scalp.get_plan()


@router.get("/state")
def get_state():
    """Strategy phase, enabled flag, open position count."""
    return early_scalp.get_state()


@router.get("/params")
def get_params():
    return early_scalp.get_params()


class ParamsPayload(BaseModel):
    gap_min_pct:        Optional[float] = None
    move_min_pct:       Optional[float] = None
    vol_ratio_min:      Optional[float] = None
    max_positions:      Optional[int]   = None
    hard_sl_pct:        Optional[float] = None
    target_pct:         Optional[float] = None
    trail_activate_pct: Optional[float] = None
    trail_gap_pct:      Optional[float] = None


@router.patch("/params")
def update_params(payload: ParamsPayload):
    updates = {k: v for k, v in payload.dict().items() if v is not None}
    return {"status": "ok", "params": early_scalp.set_params(updates)}


class EnablePayload(BaseModel):
    enabled: bool


@router.post("/enable")
def set_enabled(payload: EnablePayload):
    early_scalp.set_enabled(payload.enabled)
    return {"status": "ok", "enabled": payload.enabled}
