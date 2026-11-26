"""
database.py — Backward-compatible shim.

All schema and engine logic has been modularized into backend/storage/.
This file re-exports everything so existing imports keep working.

To understand or edit:
  Enums (TradeEnv etc.)  →  backend/storage/enums.py
  ORM models             →  backend/storage/models.py
  Engine / Session / DB  →  backend/storage/engine.py
"""

from backend.storage import (           # noqa: F401
    # models
    Settings, TradingSession, Trade, PortfolioState,
    Report, AgentLog, Candle, TradableSignal,
    # engine
    engine, Session, Base, DB_PATH, DATABASE_URL,
    init_db, get_db,
    # enums
    TradeEnv, TradeDirection, TradeStatus, SessionStatus,
)

__all__ = [
    "Settings", "TradingSession", "Trade", "PortfolioState",
    "Report", "AgentLog", "Candle", "TradableSignal",
    "engine", "Session", "Base", "DB_PATH", "DATABASE_URL",
    "init_db", "get_db",
    "TradeEnv", "TradeDirection", "TradeStatus", "SessionStatus",
]
