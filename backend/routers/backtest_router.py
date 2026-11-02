"""backtest_router.py — run/poll strategy backtests over a date window."""

from fastapi import APIRouter
from pydantic import BaseModel
from backend.core import backtest

router = APIRouter(prefix="/api/backtest", tags=["backtest"])


class RunPayload(BaseModel):
    from_date: str   # YYYY-MM-DD
    to_date:   str


@router.post("/run")
def run(payload: RunPayload):
    if backtest.get_status().get("running"):
        return {"status": "already_running"}
    backtest.run_async(payload.from_date, payload.to_date)
    return {"status": "started"}


@router.get("/status")
def status():
    return backtest.get_status()


@router.get("/result")
def result():
    return backtest.get_result()
