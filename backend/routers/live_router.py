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

    from backend.execution import ban_list
    bl = ban_list.status()
    checks.append({"key": "banlist", "label": "F&O ban list loaded", "ok": bl["ready"],
                   "detail": (f"{len(bl['banned'])} banned today: {', '.join(bl['banned']) or 'none'}"
                              if bl["ready"] else f"Not loaded yet ({bl['error'] or 'fetching'})")})

    halted = market.is_trading_halted()
    checks.append({"key": "halt", "label": "Trading not halted", "ok": not halted,
                   "detail": market.get_halt_reason() if halted else "No halt in force"})

    from backend.execution.quantity import capital_limit
    limit = capital_limit()
    margin_ok, margin_detail = False, "Could not read Zerodha balance"
    if auth:
        try:
            eq = broker.kite().margins("equity")
            av = eq.get("available", {}) or {}
            start = float(av.get("opening_balance") or 0) + float(av.get("intraday_payin") or 0)
            cap = min(start, limit)
            margin_ok = cap > 0
            margin_detail = (f"Trading capital ₹{cap:,.0f} (start-of-day funds ₹{start:,.0f}, "
                             f"limit ₹{limit:,.0f}) → ₹{cap / 5:,.0f} per trade")
        except Exception as e:
            margin_detail = f"Balance check failed: {e}"
    checks.append({"key": "margin", "label": "Funds in Zerodha",
                   "ok": margin_ok, "detail": margin_detail})
    return checks


@router.get("/alerts")
def live_alerts():
    """Active live-trading warnings (also pushed over the trades WebSocket)."""
    from backend.core import alerts
    from backend.execution.reconciler import reconciler
    return {"alerts": alerts.active(), "reconciled_at": reconciler.last_run,
            "reconcile": reconciler.last_summary}


@router.post("/alerts/{key}/dismiss")
def dismiss_alert(key: str):
    """Hide a warning. Critical alerts come straight back while the problem lasts."""
    from backend.core import alerts
    alerts.clear_alert(key)
    return {"status": "ok"}


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
        blocking = [c for c in _readiness() if not c["ok"] and c["key"] in ("broker", "feed", "halt", "banlist")]
        if blocking:
            raise HTTPException(409, "Not ready: " + "; ".join(c["detail"] for c in blocking))

    update_settings({key: payload.enabled})
    print(f"[LIVE] {tag} switched to {'LIVE' if payload.enabled else 'PAPER'}")
    return {"status": "ok", "live": payload.enabled}
