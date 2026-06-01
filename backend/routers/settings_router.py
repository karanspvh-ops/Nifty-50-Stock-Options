"""
settings_router.py — REST endpoints for reading and updating trading settings.
Frontend calls these on load and on every UI change.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.core.settings_manager import get_settings, update_settings

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsPayload(BaseModel):
    is_live:            Optional[bool]  = None
    available_funds:    Optional[float] = None
    target_profit_pct:  Optional[float] = None
    trade_sl_pct:       Optional[float] = None
    portfolio_sl_pct:   Optional[float] = None
    dynamic_sl_enabled: Optional[bool]  = None
    active_index:       Optional[str]   = None  # NIFTY50 / NIFTY100 / NIFTY200


@router.get("/")
def read_settings():
    s = get_settings()
    if not s:
        raise HTTPException(status_code=404, detail="Settings not initialised")
    return s


@router.patch("/")
def patch_settings(payload: SettingsPayload):
    data = {k: v for k, v in payload.dict().items() if v is not None}
    if not data:
        raise HTTPException(status_code=400, detail="No valid fields provided")
    updated = update_settings(data)
    return {"status": "ok", "settings": updated}
