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
        "quantity":          t.quantity,
        "lot_size":          t.lot_size,
        "trade_sl_pct":      t.trade_sl_pct,
        "trade_sl_price":    t.trade_sl_price,
        "dynamic_sl_price":  t.dynamic_sl_price,
        "target_price":      t.target_price,
        "highest_price":     t.highest_price,
        "pnl":               t.pnl,
        "pnl_pct":           t.pnl_pct,
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
    status: Optional[str] = Query(None),
    db:     DBSession      = Depends(get_db),
):
    trade_date = date_ or date.today().isoformat()
    query = (
        db.query(Trade)
        .join(TradingSession)
        .filter(
            TradingSession.date == trade_date,
            Trade.env == TradeEnv(env)
        )
    )
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
