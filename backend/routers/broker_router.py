"""broker_router.py — Zerodha login flow + broker status."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.core.broker import broker

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

    # Token acquired — bring the data feed up now
    try:
        from backend.core.stock_universe import refresh_instrument_list
        from backend.core.tick_engine     import tick_engine
        from backend.core.backfill         import start_backfill
        refresh_instrument_list(force=True)   # resolve Kite tokens
        tick_engine.start()                   # start the live feed
        start_backfill()                      # warm up indicators (DB + Kite history)
    except Exception as e:
        print(f"[BROKER] post-login startup warning: {e}")

    return {"status": "ok", "user": data.get("user_name")}
