"""trading_router.py — Trading engine control & state endpoints."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional
from backend.core.trading_engine import trading_engine
from backend.core.sector_scanner  import sector_scanner

router = APIRouter(prefix="/api/trading", tags=["trading"])


@router.get("/state")
def engine_state():
    return trading_engine.get_state()


@router.post("/start")
def start_engine():
    trading_engine.start()
    return {"status": "started"}


@router.post("/stop")
def stop_engine():
    trading_engine.stop()
    return {"status": "stopped"}


@router.get("/scan/window")
def scan_window():
    return {
        "is_scan_window":     sector_scanner.is_scan_window(),
        "is_trading_window":  sector_scanner.is_trading_window(),
        "is_square_off_time": sector_scanner.is_square_off_time(),
        "is_finalized":       sector_scanner.is_finalized(),
    }
