"""simulation_router.py — Shim: re-exports the canonical router from backend/simulation/."""

from backend.simulation.router import router  # noqa: F401

__all__ = ["router"]
