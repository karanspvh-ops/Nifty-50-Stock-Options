"""router.py — FastAPI endpoints for the simulation / system-check module.

GET  /api/simulation/status         — poll system health check results
POST /api/simulation/run            — trigger a fresh system health run
GET  /api/simulation/dryrun/status  — poll strategy dry-run results
POST /api/simulation/dryrun/run     — trigger a fresh strategy dry-run
"""

from fastapi import APIRouter
from backend.simulation.runner import run_async, get_status
from backend.simulation.dryrun_runner import run_dryrun_async, get_dryrun_status

router = APIRouter(prefix="/api/simulation", tags=["simulation"])


@router.post("/run")
def run():
    """Trigger a fresh system-health check run (non-blocking)."""
    run_async()
    return {"status": "started"}


@router.get("/status")
def status():
    """Return the current system-health check state. Poll at ~1 s."""
    return get_status()


@router.post("/dryrun/run")
def dryrun_run():
    """Trigger a fresh strategy dry-run (non-blocking)."""
    run_dryrun_async()
    return {"status": "started"}


@router.get("/dryrun/status")
def dryrun_status():
    """Return the current strategy dry-run state. Poll at ~1 s."""
    return get_dryrun_status()
