"""router.py — FastAPI REST endpoints for reports.

This file contains ONLY route definitions — no logic, no HTML generation,
no SMTP calls. All heavy lifting is in the sibling modules:

  PnL reports         →  backend/reporting/pnl_builder.py
  ML reports          →  backend/reporting/ml_analyzer.py
  Tradable signals    →  backend/universe/tradable_tracker.py
  Email delivery      →  backend/reporting/email_sender.py
  HTML generation     →  backend/reporting/html_builder.py

Edit this file only when you need to add, rename, or remove an endpoint.
"""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import Optional

from backend.database import TradeEnv
from backend.reporting.pnl_builder import pnl_agent
from backend.reporting.ml_analyzer import ml_agent
from backend.universe.tradable_tracker import tradable_tracker
from backend.reporting.email_sender import send_report

router = APIRouter(prefix="/api/reports", tags=["reports"])


# ── Tradable signals ───────────────────────────────────────────────────────────

@router.get("/tradable/{env}")
def get_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = tradable_tracker.get_report(trade_env)
    return report or {"status": "no_report", "env": env}


@router.post("/tradable/{env}/generate")
def generate_tradable_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    return tradable_tracker.generate_report(trade_env)


# ── PnL reports ────────────────────────────────────────────────────────────────

@router.get("/pnl/{env}")
def get_pnl_report(env: str, date: Optional[str] = Query(None)):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = pnl_agent.get_report(trade_env, date)
    return report or {"status": "no_report", "env": env, "date": date}


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


# ── ML reports ─────────────────────────────────────────────────────────────────

@router.get("/ml/{env}")
def get_ml_report(env: str):
    try:
        trade_env = TradeEnv(env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {env}")
    report = ml_agent.get_latest_report(trade_env)
    return report or {"status": "no_report", "env": env}


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


# ── Email ──────────────────────────────────────────────────────────────────────

class EmailReportPayload(BaseModel):
    env:         str
    email:       str
    period:      str
    custom_from: Optional[str] = None
    custom_to:   Optional[str] = None


@router.post("/email")
def email_report(payload: EmailReportPayload):
    if not payload.custom_from and payload.period == "custom":
        raise HTTPException(status_code=400, detail="custom_from and custom_to required")
    try:
        trade_env = TradeEnv(payload.env)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid env: {payload.env}")
    try:
        result = send_report(
            env=trade_env,
            to_email=payload.email,
            period=payload.period,
            custom_from=payload.custom_from,
            custom_to=payload.custom_to,
        )
    except ValueError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=502, detail=str(e))
    return result


@router.post("/send-now")
def send_daily_report_now():
    from backend.core.daily_report_scheduler import daily_report_scheduler
    return daily_report_scheduler.send_now()
