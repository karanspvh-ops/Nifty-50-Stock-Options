"""runner.py — Orchestrates all system checks and holds live state.

Usage:
  run_async()      — start a check run in a background thread
  get_status()     — returns current state (safe to call at any time)

State shape:
  {
    "running": bool,        # True while checks are in progress
    "done":    bool,        # True once the run has finished
    "go":      bool,        # True only when every check passed
    "passed":  int,
    "total":   int,
    "results": [            # populated progressively as each check completes
      {"check": str, "node": str, "ok": bool, "msg": str}
    ],
    "run_at":  str | None,  # ISO timestamp (IST) of the most recent run
  }
"""

import threading
from datetime import datetime, timezone, timedelta
from typing import List

from backend.simulation.checks import (
    check_database,
    check_kite_token,
    check_kite_api,
    check_instruments,
    check_historical_data,
    check_clock,
    check_es_strategy,
    check_ob_strategy,
    check_risk_engine,
    check_smtp,
)

IST = timezone(timedelta(hours=5, minutes=30))

# (display name, node label, check function)
_CHECKS = [
    ("Database",          "storage",       check_database),
    ("Kite Token",        "auth",          check_kite_token),
    ("Kite API",          "zerodha-api",   check_kite_api),
    ("Instruments Cache", "universe",      check_instruments),
    ("Historical Data",   "zerodha-api",   check_historical_data),
    ("Clock (IST)",       "system",        check_clock),
    ("ES Strategy",       "strategy-es",   check_es_strategy),
    ("OB Strategy",       "strategy-ob",   check_ob_strategy),
    ("Risk Engine",       "risk",          check_risk_engine),
    ("SMTP",              "email",         check_smtp),
]

_state = {
    "running": False,
    "done":    False,
    "go":      False,
    "passed":  0,
    "total":   len(_CHECKS),
    "results": [],
    "run_at":  None,
}
_lock = threading.Lock()


def get_status() -> dict:
    with _lock:
        s = dict(_state)
        s["results"] = list(s["results"])
        return s


def run_async():
    """Start a check run in a background thread. No-op if already running."""
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

    for name, node, fn in _CHECKS:
        try:
            ok, msg = fn()
        except Exception as e:
            ok, msg = False, f"Unexpected error: {e}"

        results.append({"check": name, "node": node, "ok": ok, "msg": msg})
        with _lock:
            _state["results"] = list(results)

    passed = sum(1 for r in results if r["ok"])
    go     = passed == len(_CHECKS)

    with _lock:
        _state.update(running=False, done=True, go=go, passed=passed, results=results)

    label = "GO" if go else "NO-GO"
    print(f"[SIM] === {label} ({passed}/{len(_CHECKS)} checks passed) ===")
    for r in results:
        icon = "OK  " if r["ok"] else "FAIL"
        print(f"[SIM]   [{icon}] {r['check']} ({r['node']}): {r['msg']}")
