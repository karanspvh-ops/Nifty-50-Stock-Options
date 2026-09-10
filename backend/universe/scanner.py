"""scanner.py — Public query API for the stock universe.

Edit this file to change what get_stocks_for_index / get_tradable_universe
return — e.g. add custom filters, change sort order, or expose new fields.

All data comes from instrument_cache.STOCK_MASTER which is populated by
instrument_cache.refresh_instrument_list() at login time.
"""

from typing import Dict, List, Optional

from backend.universe.instrument_cache import STOCK_MASTER, SYMBOL_TO_TOKEN
from backend.universe.sector_map import TRADABLE_INDEX


def get_stocks_for_index(index: str, fno_only: bool = False) -> List[dict]:
    out = [
        {"token": token, **meta}
        for token, meta in STOCK_MASTER.items()
        if index in meta["indices"]
    ]
    if fno_only:
        out = [s for s in out if s.get("lot_size", 0) > 0]
    return out


def get_tokens_for_index(index: str, fno_only: bool = False) -> List[str]:
    return [s["token"] for s in get_stocks_for_index(index, fno_only)]


def get_sectors_for_index(index: str, fno_only: bool = False) -> List[str]:
    return sorted({s["sector"] for s in get_stocks_for_index(index, fno_only)})


def get_stocks_in_sector(sector: str, index: str = "NIFTY50",
                          fno_only: bool = False) -> List[dict]:
    return [s for s in get_stocks_for_index(index, fno_only)
            if s["sector"].upper() == sector.upper()]


def get_tradable_universe() -> List[dict]:
    """All options-tradable stocks (NIFTY 200 + F&O extras), deduped."""
    return get_stocks_for_index(TRADABLE_INDEX, fno_only=True)


def get_meta(token: str) -> dict:
    return STOCK_MASTER.get(token, {})


def get_token(symbol: str) -> Optional[str]:
    return SYMBOL_TO_TOKEN.get(symbol)
