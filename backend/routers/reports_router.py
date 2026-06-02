"""reports_router.py — REST endpoints for PnL and ML reports."""

from fastapi import APIRouter, HTTPException, Query
from typing import Optional
from backend.database import TradeEnv
from backend.agents.pnl_agent import pnl_agent
from backend.agents.ml_agent  import ml_agent
from backend.core.tradable_tracker import tradable_tracker

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/tradable/{env}")
def get_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = tradable_tracker.get_report(trade_env)
    if not report:
        return {"status": "no_report", "env": env}
    return report


@router.post("/tradable/{env}/generate")
def generate_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return tradable_tracker.generate_report(trade_env)


@router.get("/pnl/{env}")
def get_pnl_report(env: str, date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = pnl_agent.get_report(trade_env, date)
    if not report:
        return {"status": "no_report", "env": env, "date": date}
    return report


@router.post("/pnl/{env}/generate")
def generate_pnl_report(env: str, date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = pnl_agent.generate_daily_report(trade_env, date)
    return report or {"status": "no_trades"}


@router.get("/pnl/{env}/list")
def list_pnl_reports(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return {"dates": pnl_agent.list_reports(trade_env)}


@router.get("/ml/{env}")
def get_ml_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = ml_agent.get_latest_report(trade_env)
    if not report:
        return {"status": "no_report", "env": env}
    return report


@router.post("/ml/{env}/trigger")
def trigger_ml_analysis(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return ml_agent.trigger_now(trade_env)


@router.get("/ml/weights")
def get_ml_weights():
    return ml_agent.get_weights()
