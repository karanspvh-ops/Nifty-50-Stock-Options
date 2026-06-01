"""
session_manager.py — Creates / retrieves today's TradingSession row.
Also manages the portfolio state and the daily kill-switch.
"""

from datetime import date, datetime
from sqlalchemy.orm import Session as DBSession
from backend.database import (
    Session, TradingSession, PortfolioState,
    TradeEnv, SessionStatus
)
from backend.core.settings_manager import get_settings


def today() -> str:
    return date.today().isoformat()


def get_or_create_session(env: TradeEnv) -> dict:
    """
    Fetch today's session for the given env, or create a fresh one.
    Returns the session as a dict.
    """
    db: DBSession = Session()
    try:
        row = (
            db.query(TradingSession)
            .filter(TradingSession.date == today(), TradingSession.env == env)
            .first()
        )
        if not row:
            s   = get_settings()
            row = TradingSession(
                date             = today(),
                env              = env,
                status           = SessionStatus.SCANNING,
                opening_funds    = s.get("available_funds", 0.0),
                portfolio_sl_pct = s.get("portfolio_sl_pct", 10.0),
            )
            db.add(row)
            db.commit()
            db.refresh(row)

            # Also seed portfolio state
            _seed_portfolio_state(db, env, row.opening_funds)

        return _session_to_dict(row)
    finally:
        db.close()


def update_session_status(env: TradeEnv, status: SessionStatus):
    db: DBSession = Session()
    try:
        row = (
            db.query(TradingSession)
            .filter(TradingSession.date == today(), TradingSession.env == env)
            .first()
        )
        if row:
            row.status = status
            if status in (SessionStatus.STOPPED, SessionStatus.KILLED):
                row.stopped_at = datetime.utcnow()
            db.commit()
    finally:
        db.close()


def update_session_pnl(env: TradeEnv, pnl_delta: float):
    """Add pnl_delta to today's session realised_pnl."""
    db: DBSession = Session()
    try:
        row = (
            db.query(TradingSession)
            .filter(TradingSession.date == today(), TradingSession.env == env)
            .first()
        )
        if row:
            row.realised_pnl = (row.realised_pnl or 0.0) + pnl_delta
            db.commit()
    finally:
        db.close()


def set_selected_stocks(env: TradeEnv, sector: str, stocks: list):
    db: DBSession = Session()
    try:
        row = (
            db.query(TradingSession)
            .filter(TradingSession.date == today(), TradingSession.env == env)
            .first()
        )
        if row:
            row.selected_sector = sector
            row.selected_stocks = stocks
            db.commit()
    finally:
        db.close()


# ── Portfolio state helpers ───────────────────────────────────────────────────

def _seed_portfolio_state(db: DBSession, env: TradeEnv, opening: float):
    existing = (
        db.query(PortfolioState)
        .filter(PortfolioState.date == today(), PortfolioState.env == env)
        .first()
    )
    if not existing:
        ps = PortfolioState(
            date            = today(),
            env             = env,
            opening_balance = opening,
            current_balance = opening,
            peak_balance    = opening,
        )
        db.add(ps)
        db.commit()


def get_portfolio_state(env: TradeEnv) -> dict:
    db: DBSession = Session()
    try:
        row = (
            db.query(PortfolioState)
            .filter(PortfolioState.date == today(), PortfolioState.env == env)
            .first()
        )
        if not row:
            return {}
        return {
            "opening_balance":  row.opening_balance,
            "current_balance":  row.current_balance,
            "peak_balance":     row.peak_balance,
            "drawdown_pct":     row.drawdown_pct,
            "trading_halted":   row.trading_halted,
            "halt_reason":      row.halt_reason,
        }
    finally:
        db.close()


def check_portfolio_sl(env: TradeEnv) -> bool:
    """
    Returns True if portfolio SL has been breached today.
    If breached for the first time, marks the session as KILLED.
    """
    db: DBSession = Session()
    try:
        ps = (
            db.query(PortfolioState)
            .filter(PortfolioState.date == today(), PortfolioState.env == env)
            .first()
        )
        if not ps:
            return False
        if ps.trading_halted:
            return True

        opening = ps.opening_balance or 1
        loss_pct = ((opening - ps.current_balance) / opening) * 100

        # Fetch SL threshold
        session_row = (
            db.query(TradingSession)
            .filter(TradingSession.date == today(), TradingSession.env == env)
            .first()
        )
        sl_threshold = session_row.portfolio_sl_pct if session_row else 10.0

        if loss_pct >= sl_threshold:
            ps.trading_halted = True
            ps.halt_reason    = f"Portfolio SL {sl_threshold}% breached (loss: {loss_pct:.2f}%)"
            ps.drawdown_pct   = loss_pct
            if session_row:
                session_row.status              = SessionStatus.KILLED
                session_row.portfolio_sl_breached = True
                session_row.stopped_at          = datetime.utcnow()
            db.commit()
            return True

        ps.drawdown_pct = loss_pct
        db.commit()
        return False
    finally:
        db.close()


def update_portfolio_balance(env: TradeEnv, new_balance: float):
    db: DBSession = Session()
    try:
        ps = (
            db.query(PortfolioState)
            .filter(PortfolioState.date == today(), PortfolioState.env == env)
            .first()
        )
        if ps:
            ps.current_balance = new_balance
            if new_balance > (ps.peak_balance or 0):
                ps.peak_balance = new_balance
            db.commit()
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────
def _session_to_dict(row: TradingSession) -> dict:
    return {
        "id":                     row.id,
        "date":                   row.date,
        "env":                    row.env,
        "status":                 row.status,
        "opening_funds":          row.opening_funds,
        "realised_pnl":           row.realised_pnl,
        "portfolio_sl_pct":       row.portfolio_sl_pct,
        "portfolio_sl_breached":  row.portfolio_sl_breached,
        "selected_sector":        row.selected_sector,
        "selected_stocks":        row.selected_stocks,
        "started_at":             str(row.started_at),
    }
