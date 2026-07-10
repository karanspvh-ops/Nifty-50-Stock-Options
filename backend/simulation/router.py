"""router.py — FastAPI endpoints for the simulation / system-check module.

GET  /api/simulation/status  — poll for live check results
POST /api/simulation/run     — trigger a fresh check run
"""

from fastapi import APIRouter
from backend.simulation.runner import run_async, get_status

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.post("/run")
def run():
    """Trigger a fresh system-health check run (non-blocking)."""
    run_async()
    return {"status": "started"}


@router.get("/status")
def status():
    """Return the current check state.  Poll at ~1 s to follow progress."""
    return get_status()
