"""
database.py — SQLAlchemy engine, session factory, and all ORM models.
Single source of truth for schema. All tables created on startup.
"""

import os
from datetime import datetime
from sqlalchemy import (
    create_engine, Column, Integer, Float, String,
    Boolean, DateTime, Text, Enum, ForeignKey, JSON
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
import enum

# ── DB path (sits next to backend/) ──────────────────────────────────────────
BASE_DIR   = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH    = os.path.join(BASE_DIR, "trading.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine  = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base    = declarative_base()


# ── Enums ─────────────────────────────────────────────────────────────────────
class TradeEnv(str, enum.Enum):
    PAPER = "paper"       # Testing Lab
    LIVE  = "live"        # Battle Ground

class TradeDirection(str, enum.Enum):
    CALL = "call"
    PUT  = "put"

class TradeStatus(str, enum.Enum):
    OPEN     = "open"
    CLOSED   = "closed"
    SL_HIT   = "sl_hit"
    TARGET   = "target"
    BREAKEVEN = "breakeven"
    KILLED   = "killed"   # portfolio SL triggered

class SessionStatus(str, enum.Enum):
    SCANNING  = "scanning"
    ACTIVE    = "active"
    PAUSED    = "paused"
    STOPPED   = "stopped"
    KILLED    = "killed"


# ── Table 1: Settings (single row, upserted) ─────────────────────────────────
class Settings(Base):
    __tablename__ = "settings"

    id                    = Column(Integer, primary_key=True, default=1)
    # Trading mode
    is_live               = Column(Boolean, default=False, nullable=False)
    # Risk parameters
    available_funds       = Column(Float,   default=0.0)
    target_profit_pct     = Column(Float,   default=0.0)   # 0 = no fixed target
    trade_sl_pct          = Column(Float,   default=5.0)   # hard SL per trade
    portfolio_sl_pct      = Column(Float,   default=10.0)  # daily kill switch
    dynamic_sl_enabled    = Column(Boolean, default=True)
    # Index selection
    active_index          = Column(String,  default="NIFTY50")  # NIFTY50/100/200
    # Meta
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Table 2: Trading Sessions (one row per trading day) ──────────────────────
class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    date                  = Column(String,  nullable=False, unique=True)  # YYYY-MM-DD
    env                   = Column(Enum(TradeEnv), nullable=False)
    status                = Column(Enum(SessionStatus), default=SessionStatus.SCANNING)
    opening_funds         = Column(Float,   default=0.0)
    realised_pnl          = Column(Float,   default=0.0)
    portfolio_sl_pct      = Column(Float,   default=10.0)
    portfolio_sl_breached = Column(Boolean, default=False)
    selected_sector       = Column(String,  nullable=True)
    selected_stocks       = Column(JSON,    default=list)  # ["INFY","TCS"]
    started_at            = Column(DateTime, default=datetime.utcnow)
    stopped_at            = Column(DateTime, nullable=True)

    trades                = relationship("Trade", back_populates="session")


# ── Table 3: Trades ───────────────────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    session_id            = Column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    env                   = Column(Enum(TradeEnv),       nullable=False)
    status                = Column(Enum(TradeStatus),    default=TradeStatus.OPEN)
    direction             = Column(Enum(TradeDirection), nullable=False)

    # Instrument details
    symbol                = Column(String, nullable=False)   # e.g. INFY
    option_symbol         = Column(String, nullable=True)    # e.g. INFY26JUN5500CE
    strike                = Column(Float,  nullable=True)
    expiry                = Column(String, nullable=True)    # YYYY-MM-DD
    option_type           = Column(String, nullable=True)    # CE / PE

    # Execution
    entry_price           = Column(Float,  nullable=False)
    exit_price            = Column(Float,  nullable=True)
    quantity              = Column(Integer, default=1)
    lot_size              = Column(Integer, default=1)

    # Risk
    trade_sl_pct          = Column(Float,  nullable=False)
    trade_sl_price        = Column(Float,  nullable=True)
    dynamic_sl_price      = Column(Float,  nullable=True)
    target_price          = Column(Float,  nullable=True)
    highest_price         = Column(Float,  nullable=True)   # for trailing SL

    # PnL
    pnl                   = Column(Float,  default=0.0)
    pnl_pct               = Column(Float,  default=0.0)

    # Agent reasoning (filled by Agent 1)
    entry_logic           = Column(Text,   nullable=True)
    exit_logic            = Column(Text,   nullable=True)
    indicators_snapshot   = Column(JSON,   default=dict)  # EMA/RSI/MACD at entry

    # Timestamps
    entered_at            = Column(DateTime, default=datetime.utcnow)
    exited_at             = Column(DateTime, nullable=True)

    session               = relationship("TradingSession", back_populates="trades")


# ── Table 4: Portfolio State (daily snapshot, used by kill-switch) ────────────
class PortfolioState(Base):
    __tablename__ = "portfolio_state"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    date                  = Column(String,  nullable=False, unique=True)
    env                   = Column(Enum(TradeEnv), nullable=False)
    opening_balance       = Column(Float,   default=0.0)
    current_balance       = Column(Float,   default=0.0)
    peak_balance          = Column(Float,   default=0.0)
    drawdown_pct          = Column(Float,   default=0.0)
    trading_halted        = Column(Boolean, default=False)
    halt_reason           = Column(String,  nullable=True)
    updated_at            = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Table 5: Reports ─────────────────────────────────────────────────────────
class Report(Base):
    __tablename__ = "reports"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    report_type           = Column(String,  nullable=False)  # "pnl" or "ml_retrospective"
    date                  = Column(String,  nullable=False)
    env                   = Column(Enum(TradeEnv), nullable=False)
    content               = Column(Text,   nullable=False)   # JSON or markdown
    generated_at          = Column(DateTime, default=datetime.utcnow)


# ── Table 6: Agent Logs (ML reinforcement findings) ──────────────────────────
class AgentLog(Base):
    __tablename__ = "agent_logs"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    agent_name            = Column(String,  nullable=False)  # "pnl_agent" / "ml_agent"
    log_level             = Column(String,  default="info")  # info/warn/error
    message               = Column(Text,   nullable=False)
    payload               = Column(JSON,   default=dict)
    created_at            = Column(DateTime, default=datetime.utcnow)


# ── Table 7: Tick Snapshots (candle store, 5-min OHLCV) ──────────────────────
class Candle(Base):
    __tablename__ = "candles"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    symbol                = Column(String,  nullable=False, index=True)
    interval              = Column(String,  default="5m")
    timestamp             = Column(DateTime, nullable=False, index=True)
    open                  = Column(Float,   nullable=False)
    high                  = Column(Float,   nullable=False)
    low                   = Column(Float,   nullable=False)
    close                 = Column(Float,   nullable=False)
    volume                = Column(Integer, default=0)


# ── Table 8: Tradable Signals (viable-to-trade windows) ───────────────────────
class TradableSignal(Base):
    __tablename__ = "tradable_signals"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    date          = Column(String,  nullable=False, index=True)   # YYYY-MM-DD
    env           = Column(Enum(TradeEnv), nullable=False)
    symbol        = Column(String,  nullable=False)
    token         = Column(String,  nullable=False)
    sector        = Column(String,  nullable=True)
    direction     = Column(Enum(TradeDirection), nullable=False)  # call / put

    # Tradable window
    opened_at     = Column(DateTime, nullable=False)              # entered window
    closed_at     = Column(DateTime, nullable=True)              # left window (None = still active)
    duration_sec  = Column(Integer,  nullable=True)

    # Signal details at open
    entry_score   = Column(Integer,  default=0)
    max_score     = Column(Integer,  default=8)
    ltp_at_open   = Column(Float,    nullable=True)
    ltp_at_close  = Column(Float,    nullable=True)
    sector_pct    = Column(Float,    nullable=True)
    reason        = Column(Text,     nullable=True)

    # Did we actually take a trade on it during this window?
    was_traded    = Column(Boolean,  default=False)


# ── Init: create all tables ───────────────────────────────────────────────────
def init_db():
    Base.metadata.create_all(bind=engine)
    # Seed default settings row if not present
    db = Session()
    try:
        if not db.query(Settings).first():
            db.add(Settings(id=1))
            db.commit()
    finally:
        db.close()

def get_db():
    """FastAPI dependency — yields a DB session."""
    db = Session()
    try:
        yield db
    finally:
        db.close()
