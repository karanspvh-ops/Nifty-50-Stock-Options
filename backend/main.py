"""
main.py — FastAPI application entry point.
Managed by PM2 — runs 24/7, survives browser/terminal close.
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

# Force UTF-8 console output so log prints containing the ₹ symbol (and other
# non-cp1252 chars) never raise UnicodeEncodeError on the Windows console — such
# a crash inside a strategy tick would otherwise abort the whole tick.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except Exception:
        pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from backend.database import init_db
from backend.routers.settings_router import router as settings_router
from backend.routers.session_router  import router as session_router
from backend.routers.trades_router   import router as trades_router
from backend.routers.market_router   import router as market_router
from backend.routers.risk_router     import router as risk_router
from backend.routers.scanner_router  import router as scanner_router
from backend.routers.trading_router  import router as trading_router
from backend.routers.reports_router  import router as reports_router
from backend.routers.broker_router   import router as broker_router
from backend.routers.backtest_router import router as backtest_router
from backend.core.broker             import broker
from backend.core.tick_engine        import tick_engine
from backend.core.risk_engine        import risk_engine
from backend.core.trading_engine     import trading_engine
from backend.core.tradable_tracker   import tradable_tracker
from backend.core.opening_breakout    import opening_breakout
from backend.core.early_scalp         import early_scalp
from backend.routers.strategy_router import router as strategy_router
from backend.routers.early_scalp_router import router as early_scalp_router
from backend.core.stock_universe     import refresh_instrument_list
from backend.agents.pnl_agent        import pnl_agent
from backend.agents.ml_agent         import ml_agent
from backend.core.daily_report_scheduler import daily_report_scheduler
from backend.core.pre_market_check      import pre_market_scheduler
from backend.routers.simulation_router  import router as simulation_router


def _schedule_dryrun_at_905():
    """Fire the strategy dry-run at 09:05 IST — after feeds stabilise, before ES entry window."""
    import threading
    from datetime import datetime, timezone, timedelta

    IST = timezone(timedelta(hours=5, minutes=30))

    def _wait_and_run():
        now   = datetime.now(IST)
        target = now.replace(hour=9, minute=5, second=0, microsecond=0)
        if now >= target:
            # Already past 09:05 today (e.g. backend restarted mid-session) — run immediately
            delay = 0
        else:
            delay = (target - now).total_seconds()
        if delay > 0:
            print(f"[DRYRUN] Will run at 09:05 IST (in {delay:.0f}s)")
            threading.Event().wait(timeout=delay)
        from backend.simulation.dryrun_runner import run_dryrun_async
        print("[DRYRUN] 09:05 IST — starting strategy code dry-run...")
        run_dryrun_async()

    threading.Thread(target=_wait_and_run, daemon=True).start()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ──────────────────────────────────────────────────
    print("[BOOT] Initialising database...")
    init_db()
    print("[BOOT] Database ready.")

    if broker.has_token():
        print("[BOOT] Zerodha token valid — loading instruments + starting feed...")
        refresh_instrument_list(force=True)
        tick_engine.start()
        print("[BOOT] Tick engine running.")
    else:
        print("[BOOT] No Zerodha token for today. Feed/trading gated until login.")
        print("[BOOT] Open the dashboard and complete the Zerodha login.")
        refresh_instrument_list()   # loads cached universe if present

    print("[BOOT] Launching historical backfill (warms up indicators)...")
    from backend.core.backfill import start_backfill
    start_backfill()

    print("[BOOT] Starting risk engine...")
    risk_engine.start()
    print("[BOOT] Risk engine running.")

    print("[BOOT] Starting trading engine...")
    trading_engine.start()
    print("[BOOT] Trading engine running.")
    print("[BOOT] Starting tradable tracker...")
    tradable_tracker.start()
    print("[BOOT] Tradable tracker running.")
    print("[BOOT] Starting Opening Breakout strategy...")
    opening_breakout.start()
    print("[BOOT] Opening Breakout strategy running.")
    print("[BOOT] Starting Early Scalp strategy...")
    early_scalp.start()
    print("[BOOT] Early Scalp strategy running.")
    print("[BOOT] Starting PnL agent...")
    pnl_agent.start()
    print("[BOOT] Starting ML agent...")
    ml_agent.start()
    print("[BOOT] Daily report scheduler ready (fires on last trade close).")
    print("[BOOT] Starting pre-market health check scheduler...")
    pre_market_scheduler.start()

    print("[BOOT] Running startup system health check...")
    from backend.simulation.runner import run_async as _sim_run
    _sim_run()

    print("[BOOT] Strategy dry-run scheduled for 09:05 IST (after feeds stabilise).")
    _schedule_dryrun_at_905()

    print("[BOOT] All systems GO. PM2 will restart on crash.")
    yield
    # ── Shutdown ─────────────────────────────────────────────────
    pre_market_scheduler.stop()
    ml_agent.stop()
    pnl_agent.stop()
    early_scalp.stop()
    opening_breakout.stop()
    tradable_tracker.stop()
    print("[SHUTDOWN] Stopping trading engine...")
    trading_engine.stop()
    print("[SHUTDOWN] Stopping risk engine...")
    risk_engine.stop()
    print("[SHUTDOWN] Stopping tick engine...")
    tick_engine.stop()
    print("[SHUTDOWN] Graceful shutdown complete.")


app = FastAPI(
    title       = "Nifty 50 Options Trading Engine",
    description = "Multi-agent, real-time algorithmic trading platform",
    version     = "1.0.0",
    lifespan    = lifespan,
)

# ── CORS (React frontend on port 3000) ───────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["http://localhost:3000", "http://127.0.0.1:3000",
                         "http://localhost:3002", "http://127.0.0.1:3002"],
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ──────────────────────────────────────────────────────────────────
app.include_router(settings_router)
app.include_router(session_router)
app.include_router(trades_router)
app.include_router(market_router)
app.include_router(risk_router)
app.include_router(scanner_router)
app.include_router(trading_router)
app.include_router(reports_router)
app.include_router(strategy_router)
app.include_router(early_scalp_router)
app.include_router(broker_router)
app.include_router(backtest_router)
app.include_router(simulation_router)


@app.get("/health")
def health():
    return {"status": "ok", "engine": "running"}


@app.get("/api/backfill/status")
def backfill_status():
    from backend.core.backfill import get_status
    return get_status()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=False)
