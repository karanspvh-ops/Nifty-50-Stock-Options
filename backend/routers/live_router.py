"""live_router.py — Per-strategy Go-Live switches.

GET  /api/live/status        — live flags, open trades per strategy, readiness checks
POST /api/live/{strategy}    — switch ES or OB between paper and live

Going live needs the typed phrase "GO LIVE ES" / "GO LIVE OB", a valid Zerodha
session and a connected feed. A switch is refused while that strategy has open
trades, because the strategy only manages trades of its current env: flipping
mid-trade would leave the open trades without their trail/target management.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from backend.core.broker import broker
from backend.core.market_state import market
from backend.core.settings_manager import get_settings, update_settings
from backend.database import Session, Trade, TradeStatus, TradeEnv

router = APIRouter(prefix="/api/live", tags=["live"])

STRATEGIES = ("ES", "OB")


class LiveSwitch(BaseModel):
    enabled: bool
    confirm: str = ""


def _open_trades(tag: str) -> dict:
    db = Session()
    try:
        rows = (db.query(Trade.env)
                .filter(Trade.status == TradeStatus.OPEN,
                        Trade.entry_logic.like(f"[{tag}]%"))
                .all())
    finally:
        db.close()
    live = sum(1 for (env,) in rows if env == TradeEnv.LIVE)
    return {"paper": len(rows) - live, "live": live}


def _readiness() -> list:
    checks = []
    auth = broker.has_token() and broker.is_authenticated()
    checks.append({"key": "broker", "label": "Zerodha session", "ok": auth,
                   "detail": f"Signed in as {broker._profile_name}" if auth else "Sign in to Zerodha first"})

    feed = market.is_feed_connected()
    checks.append({"key": "feed", "label": "Live price feed", "ok": feed,
                   "detail": "Connected" if feed else "Feed is not connected"})

    halted = market.is_trading_halted()
    checks.append({"key": "halt", "label": "Trading not halted", "ok": not halted,
                   "detail": market.get_halt_reason() if halted else "No halt in force"})

    funds = float(get_settings().get("available_funds") or 0)
    margin_ok, margin_detail = False, "Could not read Zerodha margin"
    if auth:
        try:
            eq = broker.kite().margins("equity")
            cash = float(eq.get("available", {}).get("live_balance") or eq.get("net") or 0)
            margin_ok = cash >= funds > 0
            margin_detail = (f"Zerodha balance ₹{cash:,.0f} vs strategy capital ₹{funds:,.0f}"
                             if funds > 0 else "Set strategy capital in settings")
        except Exception as e:
            margin_detail = f"Margin check failed: {e}"
    checks.append({"key": "margin", "label": "Account balance covers capital",
                   "ok": margin_ok, "detail": margin_detail})
    return checks


@router.get("/account")
def live_account():
    """Latest Zerodha balance (pushed live over the trades WebSocket too)."""
    from backend.core.account_monitor import account_monitor
    return account_monitor.latest() or account_monitor.refresh() or {"ok": False, "error": "Not signed in to Zerodha"}


@router.get("/status")
def live_status():
    s = get_settings()
    return {
        "strategies": {
            tag: {"live": bool(s.get(f"{tag.lower()}_live")), "open": _open_trades(tag)}
            for tag in STRATEGIES
        },
        "readiness": _readiness(),
    }


@router.post("/{strategy}")
def set_live(strategy: str, payload: LiveSwitch):
    tag = strategy.upper()
    if tag not in STRATEGIES:
        raise HTTPException(404, f"Unknown strategy {strategy}")

    key = f"{tag.lower()}_live"
    if bool(get_settings().get(key)) == payload.enabled:
        return {"status": "unchanged", "live": payload.enabled}

    open_now = _open_trades(tag)
    if open_now["paper"] or open_now["live"]:
        raise HTTPException(409, f"{tag} has open trades. Wait for them to close before switching.")

    if payload.enabled:
        phrase = f"GO LIVE {tag}"
        if payload.confirm.strip().upper() != phrase:
            raise HTTPException(400, f'Type "{phrase}" to confirm')
        blocking = [c for c in _readiness() if not c["ok"] and c["key"] in ("broker", "feed", "halt")]
        if blocking:
            raise HTTPException(409, "Not ready: " + "; ".join(c["detail"] for c in blocking))

    update_settings({key: payload.enabled})
    print(f"[LIVE] {tag} switched to {'LIVE' if payload.enabled else 'PAPER'}")
    return {"status": "ok", "live": payload.enabled}
