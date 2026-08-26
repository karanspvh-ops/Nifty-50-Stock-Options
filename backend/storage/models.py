"""models.py — All ORM table definitions (SQLAlchemy).

Edit this file to:
  - Add a new column to an existing table
  - Add a new table (new class inheriting Base)
  - Change a column type or constraint

After any schema change, either:
  a) Delete trading.db and let init_db() recreate it (dev only), OR
  b) Write an Alembic migration for production use.

Do NOT add query logic here — store queries belong in the calling module.
"""

from datetime import datetime
from sqlalchemy import (
    Column, Integer, Float, String, Boolean,
    DateTime, Text, Enum, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship

from backend.storage.engine import Base
from backend.storage.enums import (
    TradeEnv, TradeDirection, TradeStatus, SessionStatus,
)


# ── Table 1: Settings (single row, upserted) ──────────────────────────────────
class Settings(Base):
    __tablename__ = "settings"

    id                 = Column(Integer, primary_key=True, default=1)
    is_live            = Column(Boolean, default=False, nullable=False)
    available_funds    = Column(Float,   default=0.0)
    target_profit_pct  = Column(Float,   default=0.0)    # 0 = no fixed target
    trade_sl_pct       = Column(Float,   default=5.0)    # hard SL per trade
    portfolio_sl_pct   = Column(Float,   default=10.0)   # daily kill switch
    dynamic_sl_enabled = Column(Boolean, default=True)
    active_index       = Column(String,  default="NIFTY50")
    updated_at         = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Table 2: Trading Sessions (one row per trading day) ───────────────────────
class TradingSession(Base):
    __tablename__ = "trading_sessions"

    id                    = Column(Integer, primary_key=True, autoincrement=True)
    date                  = Column(String,  nullable=False, unique=True)   # YYYY-MM-DD
    env                   = Column(Enum(TradeEnv),     nullable=False)
    status                = Column(Enum(SessionStatus), default=SessionStatus.SCANNING)
    opening_funds         = Column(Float,   default=0.0)
    realised_pnl          = Column(Float,   default=0.0)
    portfolio_sl_pct      = Column(Float,   default=10.0)
    portfolio_sl_breached = Column(Boolean, default=False)
    selected_sector       = Column(String,  nullable=True)
    selected_stocks       = Column(JSON,    default=list)   # ["INFY", "TCS"]
    started_at            = Column(DateTime, default=datetime.utcnow)
    stopped_at            = Column(DateTime, nullable=True)

    trades = relationship("Trade", back_populates="session")


# ── Table 3: Trades ───────────────────────────────────────────────────────────
class Trade(Base):
    __tablename__ = "trades"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    session_id     = Column(Integer, ForeignKey("trading_sessions.id"), nullable=False)
    env            = Column(Enum(TradeEnv),       nullable=False)
    status         = Column(Enum(TradeStatus),    default=TradeStatus.OPEN)
    direction      = Column(Enum(TradeDirection), nullable=False)

    # Instrument
    symbol         = Column(String, nullable=False)    # e.g. INFY
    option_symbol  = Column(String, nullable=True)     # e.g. INFY26JUN5500CE
    strike         = Column(Float,  nullable=True)
    expiry         = Column(String, nullable=True)     # YYYY-MM-DD
    option_type    = Column(String, nullable=True)     # CE / PE

    # Execution
    entry_price    = Column(Float,   nullable=False)
    exit_price     = Column(Float,   nullable=True)
    quantity       = Column(Integer, default=1)
    lot_size       = Column(Integer, default=1)

    # Risk
    trade_sl_pct    = Column(Float, nullable=False)
    trade_sl_price  = Column(Float, nullable=True)
    dynamic_sl_price = Column(Float, nullable=True)
    target_price    = Column(Float, nullable=True)
    highest_price   = Column(Float, nullable=True)     # for trailing SL

    # PnL
    pnl     = Column(Float, default=0.0)
    pnl_pct = Column(Float, default=0.0)

    # Reasoning (filled by PnL agent)
    entry_logic         = Column(Text, nullable=True)
    exit_logic          = Column(Text, nullable=True)
    indicators_snapshot = Column(JSON, default=dict)   # EMA/RSI/MACD at entry

    # Bid/ask snapshot at entry and exit
    bid_at_entry      = Column(Float, nullable=True)
    ask_at_entry      = Column(Float, nullable=True)
    spread_pct_at_entry = Column(Float, nullable=True)
    bid_at_exit       = Column(Float, nullable=True)
    ask_at_exit       = Column(Float, nullable=True)
    spread_pct_at_exit  = Column(Float, nullable=True)

    # Timestamps
    entered_at = Column(DateTime, default=datetime.utcnow)
    exited_at  = Column(DateTime, nullable=True)

    session = relationship("TradingSession", back_populates="trades")


# ── Table 4: Portfolio State (daily snapshot, used by kill-switch) ─────────────
class PortfolioState(Base):
    __tablename__ = "portfolio_state"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    date            = Column(String,  nullable=False, unique=True)
    env             = Column(Enum(TradeEnv), nullable=False)
    opening_balance = Column(Float,   default=0.0)
    current_balance = Column(Float,   default=0.0)
    peak_balance    = Column(Float,   default=0.0)
    drawdown_pct    = Column(Float,   default=0.0)
    trading_halted  = Column(Boolean, default=False)
    halt_reason     = Column(String,  nullable=True)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── Table 5: Reports ──────────────────────────────────────────────────────────
class Report(Base):
    __tablename__ = "reports"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    report_type  = Column(String,  nullable=False)   # "pnl" / "ml_retrospective"
    date         = Column(String,  nullable=False)
    env          = Column(Enum(TradeEnv), nullable=False)
    content      = Column(Text,    nullable=False)   # JSON or markdown
    generated_at = Column(DateTime, default=datetime.utcnow)


# ── Table 6: Agent Logs ───────────────────────────────────────────────────────
class AgentLog(Base):
    __tablename__ = "agent_logs"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    agent_name = Column(String,  nullable=False)   # "pnl_agent" / "ml_agent"
    log_level  = Column(String,  default="info")   # info / warn / error
    message    = Column(Text,    nullable=False)
    payload    = Column(JSON,    default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)


# ── Table 7: Candles (5-min OHLCV store) ─────────────────────────────────────
class Candle(Base):
    __tablename__ = "candles"

    id        = Column(Integer, primary_key=True, autoincrement=True)
    symbol    = Column(String,  nullable=False, index=True)
    interval  = Column(String,  default="5m")
    timestamp = Column(DateTime, nullable=False, index=True)
    open      = Column(Float,   nullable=False)
    high      = Column(Float,   nullable=False)
    low       = Column(Float,   nullable=False)
    close     = Column(Float,   nullable=False)
    volume    = Column(Integer, default=0)


# ── Table 8: Tradable Signals (viable-to-trade windows) ───────────────────────
class TradableSignal(Base):
    __tablename__ = "tradable_signals"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    date         = Column(String,  nullable=False, index=True)    # YYYY-MM-DD
    env          = Column(Enum(TradeEnv),       nullable=False)
    symbol       = Column(String,  nullable=False)
    token        = Column(String,  nullable=False)
    sector       = Column(String,  nullable=True)
    direction    = Column(Enum(TradeDirection), nullable=False)    # call / put

    opened_at    = Column(DateTime, nullable=False)
    closed_at    = Column(DateTime, nullable=True)
    duration_sec = Column(Integer,  nullable=True)

    entry_score  = Column(Integer, default=0)
    max_score    = Column(Integer, default=8)
    ltp_at_open  = Column(Float,   nullable=True)
    ltp_at_close = Column(Float,   nullable=True)
    sector_pct   = Column(Float,   nullable=True)
    reason       = Column(Text,    nullable=True)
    was_traded   = Column(Boolean, default=False)


# ── Table 9: ES Portfolio Snapshots (tick-level, in-memory flush) ─────────────
class ESPortfolioSnapshot(Base):
    __tablename__ = "es_portfolio_snapshots"

    id               = Column(Integer,  primary_key=True, autoincrement=True)
    date             = Column(String,   nullable=False, index=True)   # YYYY-MM-DD
    snapshot_time    = Column(DateTime, nullable=False)               # exact IST timestamp
    realized_pnl     = Column(Float,    default=0.0)
    unrealized_pnl   = Column(Float,    default=0.0)
    total_pnl        = Column(Float,    default=0.0)
    total_pct        = Column(Float,    default=0.0)
    open_trade_count = Column(Integer,  default=0)
    capital          = Column(Float,    default=500000.0)
