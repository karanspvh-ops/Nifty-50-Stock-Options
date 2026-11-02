"""dryrun_runner.py — Orchestrates the strategy code dry-run checks.

Runs each node of the ES and OB pipeline through actual production code
with real historical candle data fetched from Kite. Reports each node as
pass/fail with the exact exception if something breaks.

State shape mirrors runner.py but is keyed "dryrun" in the API.
"""

import threading
from datetime import datetime, timezone, timedelta
from typing import List

from backend.simulation.strategy_dryrun import DRYRUN_CHECKS

IST = timezone(timedelta(hours=5, minutes=30))

_state = {
    "running": False,
    "done":    False,
    "go":      False,
    "passed":  0,
    "total":   len(DRYRUN_CHECKS),
    "results": [],
    "run_at":  None,
}
_lock = threading.Lock()


def get_dryrun_status() -> dict:
    with _lock:
        s = dict(_state)
        s["results"] = list(s["results"])
        return s


def run_dryrun_async():
    """Start a strategy dry-run in a background thread. No-op if already running."""
    with _lock:
        if _state["running"]:
            return
        _state.update(
            running=True, done=False, go=False, passed=0,
            results=[], run_at=datetime.now(IST).isoformat(),
        )
    threading.Thread(target=_run, daemon=True).start()


def _run():
    results: List[dict] = []

    for name, node, fn in DRYRUN_CHECKS:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"Unexpected error: {e}"

        results.append({"check": name, "node": node, "ok": ok, "msg": msg})
        with _lock:
            _state["results"] = list(results)

    passed = sum(1 for r in results if r["ok"])
    go     = passed == len(DRYRUN_CHECKS)

    with _lock:
        _state.update(running=False, done=True, go=go, passed=passed, results=results)

    label = "GO" if go else "NO-GO"
    print(f"[DRYRUN] === {label} ({passed}/{len(DRYRUN_CHECKS)} nodes passed) ===")
    for r in results:
        icon = "OK  " if r["ok"] else "FAIL"
        print(f"[DRYRUN]   [{icon}] {r['check']} ({r['node']}): {r['msg']}")
