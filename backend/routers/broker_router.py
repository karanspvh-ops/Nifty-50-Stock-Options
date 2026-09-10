"""broker_router.py — Zerodha login flow + broker status."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.broker import broker
from backend.core.pre_market_check import pre_market_scheduler

router = APIRouter(prefix="/api/broker", tags=["broker"])


@router.get("/status")
def status():
    return broker.status()


@router.get("/login-url")
def login_url():
    return {"login_url": broker.login_url()}


class LoginPayload(BaseModel):
    request_token: str


@router.post("/login")
def login(payload: LoginPayload):
    """
    Exchange the request_token (from the browser redirect) for an
    access_token. Call once per trading day.
    """
    try:
        data = broker.generate_session(payload.request_token.strip())
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Login failed: {e}")

    # Token acquired — (re)bring the data feed up with the NEW token.
    # A fresh login invalidates any previous token, so the running feed must
    # be restarted to rebuild KiteTicker with the new access token.
    try:
        from backend.core.stock_universe import refresh_instrument_list
        from backend.core.tick_engine     import tick_engine
        from backend.core.backfill         import start_backfill
        from backend.market_data.nifty_options_collector import nifty_options_collector
        refresh_instrument_list(force=True)   # resolve Kite tokens
        tick_engine.stop()                    # drop any feed on the old token
        import time as _t; _t.sleep(1)
        tick_engine.start()                   # start fresh with the new token
        # main.py's boot sequence only starts this when a token already exists
        # AT BOOT TIME. If the backend process comes up before the day's login
        # (an overnight restart, or the login just happening after boot -- the
        # normal daily flow once the process has been running for a while), that
        # startup branch never runs and the collector's thread is never spawned
        # at all for that day -- not failing, just nonexistent. start() is a
        # no-op if it's already running, so calling it here on every login is
        # always safe and gives the collector the same daily restart hook
        # tick_engine already gets.
        nifty_options_collector.start()
        start_backfill()                      # warm up indicators (DB + Kite history)
    except Exception as e:
        print(f"[BROKER] post-login startup warning: {e}")

    return {"status": "ok", "user": data.get("user_name")}


@router.get("/health-check")
def health_check():
    """Run the pre-market GO/NO-GO health check immediately and return results."""
    return pre_market_scheduler.run_now()
