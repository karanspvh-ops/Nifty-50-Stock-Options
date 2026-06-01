"""
reset_day.py — Clears today's session/portfolio/trade rows.
Use to wipe test-polluted data or start a fresh trading day.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import date
from backend.database import (
    Session, TradingSession, PortfolioState, Trade, init_db
)

init_db()
today = date.today().isoformat()
db = Session()
try:
    # Delete trades that belong to today's sessions
    today_sessions = db.query(TradingSession).filter(TradingSession.date == today).all()
    sid = [s.id for s in today_sessions]
    if sid:
        n_tr = db.query(Trade).filter(Trade.session_id.in_(sid)).delete(synchronize_session=False)
    else:
        n_tr = 0
    n_ps = db.query(PortfolioState).filter(PortfolioState.date == today).delete()
    n_se = db.query(TradingSession).filter(TradingSession.date == today).delete()
    db.commit()
    print(f"[RESET] Cleared for {today}: trades={n_tr}, portfolio_state={n_ps}, sessions={n_se}")
    print("[RESET] Fresh session will be recreated by the engine on next tick.")
finally:
    db.close()
