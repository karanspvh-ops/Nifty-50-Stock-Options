"""enums.py — All database-level enumerations.

Edit this file to add new trade states, environments, or directions.
These are shared across all ORM models and all application code.
"""

import enum


class TradeEnv(str, enum.Enum):
    PAPER = "paper"   # Testing Lab
    LIVE  = "live"    # Battle Ground


class TradeDirection(str, enum.Enum):
    CALL = "call"
    PUT  = "put"


class TradeStatus(str, enum.Enum):
    OPEN      = "open"
    CLOSED    = "closed"
    SL_HIT   = "sl_hit"
    TARGET    = "target"
    BREAKEVEN = "breakeven"
    KILLED    = "killed"   # portfolio SL triggered


class SessionStatus(str, enum.Enum):
    SCANNING = "scanning"
    ACTIVE   = "active"
    PAUSED   = "paused"
    STOPPED  = "stopped"
    KILLED   = "killed"
