"""
trades_router.py — Endpoints to list trades for Testing Lab and Battle Ground.
"""

from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session as DBSession
from typing import Optional
from datetime import date

from backend.database import Trade, TradingSession, TradeEnv, get_db

router = APIRouter(prefix="/api/trades", tags=["trades"])


def _trade_to_dict(t: Trade) -> dict:
    # For OPEN trades the stored pnl/pnl_pct are stale (only written on exit),
    # so overlay live numbers from the OPTION premium feed. Best-effort: any
    # failure falls back to the stored values.
    live_ltp = None
    live_pnl = t.pnl
    live_pnl_pct = t.pnl_pct
    if str(t.status).lower() == "open" and t.entry_price:
        try:
            from backend.core.order_executor import current_premium
            live_ltp = current_premium(t)
            if live_ltp:
                live_pnl_pct = round((live_ltp - t.entry_price) / t.entry_price * 100, 2)
                qty_total    = (t.quantity or 0) * (t.lot_size or 0)
                live_pnl     = round((live_ltp - t.entry_price) * qty_total, 2)
        except Exception:
            pass

    return {
        "id":                t.id,
        "session_id":        t.session_id,
        "env":               t.env,
        "status":            t.status,
        "direction":         t.direction,
        "symbol":            t.symbol,
        "option_symbol":     t.option_symbol,
        "strike":            t.strike,
        "expiry":            t.expiry,
        "option_type":       t.option_type,
        "entry_price":       t.entry_price,
        "exit_price":        t.exit_price,
        "live_ltp":          live_ltp,                  # null when closed
        "quantity":          t.quantity,
        "lot_size":          t.lot_size,
        "trade_sl_pct":      t.trade_sl_pct,
        "trade_sl_price":    t.trade_sl_price,
        "dynamic_sl_price":  t.dynamic_sl_price,
        "target_price":      t.target_price,
        "highest_price":     t.highest_price,
        "pnl":               live_pnl,
        "pnl_pct":           live_pnl_pct,
        "entry_logic":       t.entry_logic,
        "exit_logic":        t.exit_logic,
        "indicators_snapshot": t.indicators_snapshot,
        "entered_at":        str(t.entered_at),
        "exited_at":         str(t.exited_at) if t.exited_at else None,
    }


@router.get("/")
def list_trades(
    env:    Optional[str] = Query("paper", description="paper or live"),
    date_:  Optional[str] = Query(None,    alias="date"),
    all_:   Optional[bool] = Query(False,  alias="all"),
    status: Optional[str] = Query(None),
    db:     DBSession      = Depends(get_db),
):
    query = db.query(Trade).filter(Trade.env == TradeEnv(env))
    if not all_:
        trade_date = date_ or date.today().isoformat()
        query = query.join(TradingSession).filter(TradingSession.date == trade_date)
    if status:
        query = query.filter(Trade.status == status)
    trades = query.order_by(Trade.entered_at.desc()).all()
    return [_trade_to_dict(t) for t in trades]


@router.get("/{trade_id}")
def get_trade(trade_id: int, db: DBSession = Depends(get_db)):
    t = db.query(Trade).filter(Trade.id == trade_id).first()
    if not t:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Trade not found")
    return _trade_to_dict(t)
