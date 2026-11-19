"""engine.py — SQLAlchemy engine, session factory, and DB initialisation.

Edit this file to change:
  - The database file path
  - SQLite connection arguments (WAL mode, timeouts, etc.)
  - The init_db() seed data (default Settings row)

Do NOT define ORM models here — those live in backend/storage/models.py.
init_db() calls Base.metadata.create_all() which picks up all models that
have been imported before the call — the __init__.py guarantees this order.
"""

import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# ── DB path (sits in the project root, next to backend/) ──────────────────────
_BASE_DIR    = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH      = os.path.join(_BASE_DIR, "trading.db")
DATABASE_URL = f"sqlite:///{DB_PATH}"

engine  = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
Base    = declarative_base()


def init_db():
    """Create all tables and seed the default Settings row."""
    # Import models so they register on Base before create_all
    import backend.storage.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _add_missing_columns()

    from backend.storage.models import Settings
    db = Session()
    try:
        if not db.query(Settings).first():
            db.add(Settings(id=1))
            db.commit()
    finally:
        db.close()


def _add_missing_columns():
    """create_all() never alters existing tables, so add columns introduced
    after the DB was first created. Each entry: (table, column, DDL type+default)."""
    from sqlalchemy import inspect, text
    wanted = [
        ("settings", "es_live", "BOOLEAN NOT NULL DEFAULT 0"),
        ("settings", "ob_live", "BOOLEAN NOT NULL DEFAULT 0"),
    ]
    insp = inspect(engine)
    with engine.begin() as conn:
        for table, col, ddl in wanted:
            if col not in {c["name"] for c in insp.get_columns(table)}:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))


def get_db():
    """FastAPI dependency — yields a DB session and closes it on exit."""
    db = Session()
    try:
        yield db
    finally:
        db.close()
