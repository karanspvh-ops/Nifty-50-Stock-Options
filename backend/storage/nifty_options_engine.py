"""nifty_options_engine.py — separate SQLite engine for NIFTY option chain snapshots.

Deliberately isolated from backend/storage/engine.py (trading.db) so this
background data-collection module can never lock-contend with the live
trading DB. Same create_all()-on-import pattern as engine.py.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH      = os.path.join(_BASE_DIR, "nifty_options.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

nifty_engine  = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
NiftySession  = sessionmaker(bind=nifty_engine, autocommit=False, autoflush=False)
NiftyBase     = declarative_base()


def init_nifty_options_db():
    """Create the nifty_options.db tables if they don't exist yet."""
    import backend.storage.nifty_options_models  # noqa: F401 — register model on NiftyBase
    NiftyBase.metadata.create_all(bind=nifty_engine)
