"""
stock_universe.py — Backward-compatible shim.

All stock universe logic has been modularized into backend/universe/.
This file re-exports everything so existing imports keep working.

To understand or edit:
  Sector / index data   →  backend/universe/sector_map.py
  Instrument cache      →  backend/universe/instrument_cache.py
  Public query API      →  backend/universe/scanner.py
  Tradable tracker      →  backend/universe/tradable_tracker.py
"""

from backend.universe.scanner import (          # noqa: F401
    get_stocks_for_index, get_tokens_for_index, get_sectors_for_index,
    get_stocks_in_sector, get_tradable_universe, get_meta, get_token,
)
from backend.universe.instrument_cache import ( # noqa: F401
    refresh_instrument_list, load_instrument_cache,
    get_option_token, find_option_token, invalidate_nfo_cache,
    SYMBOL_TO_TOKEN, STOCK_MASTER,
)
from backend.universe.sector_map import (       # noqa: F401
    SECTOR_OF, NIFTY50_SYMBOLS, NIFTY_NEXT50_SYMBOLS,
    NIFTY_MIDCAP_SYMBOLS, NIFTY_FNO_EXTRAS, TRADABLE_INDEX,
)

__all__ = [
    "get_stocks_for_index", "get_tokens_for_index", "get_sectors_for_index",
    "get_stocks_in_sector", "get_tradable_universe", "get_meta", "get_token",
    "refresh_instrument_list", "load_instrument_cache",
    "get_option_token", "find_option_token", "invalidate_nfo_cache",
    "SYMBOL_TO_TOKEN", "STOCK_MASTER", "SECTOR_OF",
    "NIFTY50_SYMBOLS", "NIFTY_NEXT50_SYMBOLS", "NIFTY_MIDCAP_SYMBOLS",
    "NIFTY_FNO_EXTRAS", "TRADABLE_INDEX",
]
