"""
settings_manager.py — Singleton for reading/writing trading settings.
All UI changes persist immediately to SQLite. No in-memory drift.
"""

from datetime import datetime
from sqlalchemy.orm import Session as DBSession
from backend.database import Settings, Session


def get_settings() -> dict:
    """Return current settings as a plain dict."""
    db: DBSession = Session()
    try:
        row = db.query(Settings).filter(Settings.id == 1).first()
        if not row:
            return {}
        return {
            "is_live":             row.is_live,
            "available_funds":     row.available_funds,
            "target_profit_pct":   row.target_profit_pct,
            "trade_sl_pct":        row.trade_sl_pct,
            "portfolio_sl_pct":    row.portfolio_sl_pct,
            "dynamic_sl_enabled":  row.dynamic_sl_enabled,
            "active_index":        row.active_index,
            "updated_at":          str(row.updated_at),
        }
    finally:
        db.close()


def update_settings(payload: dict) -> dict:
    """
    Merge payload into Settings row id=1.
    Only keys present in payload are updated.
    """
    allowed = {
        "is_live", "available_funds", "target_profit_pct",
        "trade_sl_pct", "portfolio_sl_pct",
        "dynamic_sl_enabled", "active_index"
    }
    db: DBSession = Session()
    try:
        row = db.query(Settings).filter(Settings.id == 1).first()
        if not row:
            row = Settings(id=1)
            db.add(row)

        for key, val in payload.items():
            if key in allowed:
                setattr(row, key, val)

        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        return get_settings()
    finally:
        db.close()


def is_live_mode() -> bool:
    s = get_settings()
    return s.get("is_live", False)


def get_trade_sl_pct() -> float:
    return get_settings().get("trade_sl_pct", 5.0)


def get_portfolio_sl_pct() -> float:
    return get_settings().get("portfolio_sl_pct", 10.0)


def get_dynamic_sl_enabled() -> bool:
    return get_settings().get("dynamic_sl_enabled", True)


def get_active_index() -> str:
    return get_settings().get("active_index", "NIFTY50")
