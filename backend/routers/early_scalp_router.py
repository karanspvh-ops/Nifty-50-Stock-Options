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


class ForceEntryPayload(BaseModel):
    symbol:    str
    direction: str   # "call" or "put"


@router.post("/force-entry")
def force_entry(payload: ForceEntryPayload):
    """Paper-mode only: enter one ES trade immediately, bypassing the time gate.
    Used for testing the dashboard entry flow outside the 9:25-9:30 window."""
    from backend.core.settings_manager import is_live_mode
    if is_live_mode():
        return {"status": "rejected", "reason": "force-entry is only allowed in paper mode"}

    from backend.core.market_state   import market
    from backend.core.session_manager import get_or_create_session
    from backend.execution.order_executor import place_entry_order
    from backend.database import TradeEnv
    from backend.universe.instrument_cache import SYMBOL_TO_TOKEN

    direction = payload.direction.lower()
    if direction not in ("call", "put"):
        return {"status": "rejected", "reason": "direction must be 'call' or 'put'"}

    token = SYMBOL_TO_TOKEN.get(payload.symbol)
    if not token:
        return {"status": "rejected", "reason": f"symbol {payload.symbol} not found in instrument cache"}

    mv  = market.get_stock_move(token)
    ltp = mv.get("ltp") if mv else None
    if not ltp:
        return {"status": "rejected", "reason": f"no live LTP for {payload.symbol} — feed may not have this token"}

    env     = TradeEnv.PAPER
    session = get_or_create_session(env)
    params  = early_scalp.get_params()

    trade = place_entry_order(
        env=env, symbol=payload.symbol, token=token,
        direction=direction, session_id=session["id"],
        entry_logic=f"[ES] FORCE-ENTRY (paper test) | ltp={ltp}",
        indicators={"gap_pct": 0, "opening_move": 0, "consec_1m": 0,
                    "consec_3m": 0, "vol_ratio": 0, "oi_signal": "n/a", "score": 0},
        sl_pct_override=float(params["hard_sl_pct"]),
        target_pct_override=float(params["target_pct"]),
        max_positions=int(params["max_positions"]),
    )

    if not trade:
        return {"status": "rejected", "reason": "place_entry_order returned None — check feed and funds"}

    return {
        "status":         "entered",
        "trade_id":       trade.id,
        "symbol":         trade.symbol,
        "option_symbol":  trade.option_symbol,
        "direction":      trade.direction.value,
        "entry_price":    trade.entry_price,
        "quantity":       trade.quantity,
        "lot_size":       trade.lot_size,
        "sl_price":       trade.trade_sl_price,
    }
