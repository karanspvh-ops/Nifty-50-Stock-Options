"""instrument_cache.py — Zerodha instrument resolution and NSE token cache.

Edit this file to change:
  - How the NFO option cache is loaded/written
  - Token resolution logic (NSE equity + NFO lot sizes)
  - Cache file paths

Do NOT import this module before backend.core.broker is initialised;
refresh_instrument_list() can be called safely at any time after login.
"""

import os
import json
from typing import Dict, List, Optional

from backend.universe.sector_map import SECTOR_OF, _membership

# ── Cache file paths ──────────────────────────────────────────────────────────
_BASE_DIR              = os.path.join(os.path.dirname(__file__), "../..")
_INSTRUMENT_CACHE_PATH = os.path.join(_BASE_DIR, "instrument_cache.json")   # NFO options
_TOKENS_CACHE_PATH     = os.path.join(_BASE_DIR, "universe_tokens.json")    # symbol→token
_LOTS_CACHE_PATH       = os.path.join(_BASE_DIR, "universe_lots.json")      # symbol→lot_size
_NIFTY_OPT_CACHE_PATH  = os.path.join(_BASE_DIR, "nifty_instrument_cache.json")  # NIFTY index options
_NIFTY_INDEX_PATH      = os.path.join(_BASE_DIR, "nifty_index_token.json")       # NIFTY 50 index token

# ── Runtime maps (rebuilt each login from Zerodha instruments) ─────────────────
SYMBOL_TO_TOKEN: Dict[str, str] = {}
STOCK_MASTER:    Dict[str, dict] = {}   # token → {symbol, sector, indices, lot_size}
_LOT_SIZE:       Dict[str, int]  = {}   # symbol → lot size (from NFO master)

# NFO option contract index (loaded lazily)
_NFO_CACHE:         dict = {}   # token → meta
_NFO_SYMBOL_INDEX:  dict = {}   # tradingsymbol → token (reverse lookup)

# NIFTY 50 index option chain + the index instrument itself -- resolved in the
# SAME refresh_instrument_list() pass OB/ES already rely on (boot + every daily
# re-login in broker_router.py), instead of a separate kite.instruments("NFO")
# call from the collector's own background thread. That independent call was
# the actual cause of the collector silently failing to resolve its chain for
# days at a stretch: OB/ES never re-fetch instruments themselves after login,
# they just read this cache -- which is why they never had the same failure.
_NIFTY_OPT_CACHE:  dict = {}   # token → meta, name == "NIFTY" CE/PE only
_NIFTY_INDEX_TOKEN: Optional[str] = None


def _rebuild_master(token_map: Dict[str, str]):
    """Rebuild STOCK_MASTER and SYMBOL_TO_TOKEN from a fresh symbol→token map."""
    SYMBOL_TO_TOKEN.clear()
    STOCK_MASTER.clear()
    for sym, token in token_map.items():
        indices = _membership(sym)
        if not indices:
            continue
        SYMBOL_TO_TOKEN[sym] = token
        STOCK_MASTER[token] = {
            "symbol":   sym,
            "sector":   SECTOR_OF.get(sym, "OTHER"),
            "indices":  indices,
            "lot_size": _LOT_SIZE.get(sym, 0),
        }


def _try_load_cache_at_import():
    """Load cached tokens + lots at import so the universe is available offline."""
    try:
        if os.path.exists(_TOKENS_CACHE_PATH):
            tokens = json.load(open(_TOKENS_CACHE_PATH))
            if os.path.exists(_LOTS_CACHE_PATH):
                _LOT_SIZE.update({k: int(v) for k, v in
                                   json.load(open(_LOTS_CACHE_PATH)).items()})
            _rebuild_master(tokens)
    except Exception:
        pass

    global _NIFTY_OPT_CACHE, _NIFTY_INDEX_TOKEN
    try:
        if os.path.exists(_NIFTY_OPT_CACHE_PATH):
            _NIFTY_OPT_CACHE = json.load(open(_NIFTY_OPT_CACHE_PATH))
        if os.path.exists(_NIFTY_INDEX_PATH):
            _NIFTY_INDEX_TOKEN = json.load(open(_NIFTY_INDEX_PATH)).get("token")
    except Exception:
        pass


def refresh_instrument_list(force: bool = False) -> dict:
    """
    Resolve the universe from Zerodha's instrument dump (requires a valid
    access token). Maps each universe symbol to its NSE instrument_token,
    captures F&O lot sizes, and caches NFO option contracts for ATM lookup.
    Falls back to the on-disk cache if the API isn't available yet.
    Returns the NFO cache dict.
    """
    from backend.core.broker import broker
    if not broker.has_token():
        if os.path.exists(_TOKENS_CACHE_PATH):
            _try_load_cache_at_import()
            print("[UNIVERSE] No Zerodha token yet — using cached tokens.")
        return _load_nfo_cache()

    try:
        kite = broker.kite()
        nse  = kite.instruments("NSE")
        nfo  = kite.instruments("NFO")

        # NSE equity token resolution
        resolved: Dict[str, str] = {}
        nifty_index_token: Optional[str] = None
        for i in nse:
            if i.get("instrument_type") == "EQ" and i.get("tradingsymbol") in SECTOR_OF:
                resolved[i["tradingsymbol"]] = str(i["instrument_token"])
            elif i.get("segment") == "INDICES" and i.get("tradingsymbol") == "NIFTY 50":
                nifty_index_token = str(i["instrument_token"])

        # NFO cache + lot sizes
        nfo_cache = {}
        nifty_opt_cache = {}
        for i in nfo:
            itype = i.get("instrument_type", "")
            name  = i.get("name", "")
            if itype in ("CE", "PE") and name == "NIFTY":
                exp = i.get("expiry")
                nifty_opt_cache[str(i["instrument_token"])] = {
                    "tradingsymbol":   i.get("tradingsymbol", ""),
                    "name":            name,
                    "expiry":          exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                    "strike":          float(i.get("strike", 0)),
                    "instrument_type": itype,
                    "lot_size":        int(i.get("lot_size", 0)),
                }
                continue
            if name not in SECTOR_OF:
                continue
            if itype in ("CE", "PE"):
                exp = i.get("expiry")
                nfo_cache[str(i["instrument_token"])] = {
                    "tradingsymbol":   i.get("tradingsymbol", ""),
                    "name":            name,
                    "expiry":          exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                    "strike":          float(i.get("strike", 0)),
                    "instrument_type": itype,
                    "lot_size":        int(i.get("lot_size", 0)),
                }
            elif itype == "FUT":
                try:
                    _LOT_SIZE[name] = int(i.get("lot_size", 0))
                except (ValueError, TypeError):
                    pass

        _rebuild_master(resolved)

        global _NIFTY_OPT_CACHE, _NIFTY_INDEX_TOKEN
        _NIFTY_OPT_CACHE   = nifty_opt_cache
        _NIFTY_INDEX_TOKEN = nifty_index_token

        try:
            json.dump(resolved,         open(_TOKENS_CACHE_PATH, "w"))
            json.dump(_LOT_SIZE,        open(_LOTS_CACHE_PATH, "w"))
            json.dump(nfo_cache,        open(_INSTRUMENT_CACHE_PATH, "w"))
            json.dump(nifty_opt_cache,  open(_NIFTY_OPT_CACHE_PATH, "w"))
            json.dump({"token": nifty_index_token}, open(_NIFTY_INDEX_PATH, "w"))
        except Exception as e:
            print(f"[UNIVERSE] cache write warning: {e}")

        invalidate_nfo_cache()

        from backend.universe.scanner import get_stocks_for_index
        print(f"[UNIVERSE] Zerodha instruments loaded | NSE resolved: {len(resolved)} | "
              f"NFO options: {len(nfo_cache)} | NIFTY index options: {len(nifty_opt_cache)} | "
              f"N50={len(get_stocks_for_index('NIFTY50'))}, "
              f"N100={len(get_stocks_for_index('NIFTY100'))}, "
              f"N200={len(get_stocks_for_index('NIFTY200'))}")
        return nfo_cache

    except Exception as e:
        print(f"[UNIVERSE] Zerodha instrument load failed ({e}); using cache.")
        _try_load_cache_at_import()
        return _load_nfo_cache()


def _load_nfo_cache() -> dict:
    global _NFO_CACHE, _NFO_SYMBOL_INDEX
    if _NFO_CACHE:
        return _NFO_CACHE
    if os.path.exists(_INSTRUMENT_CACHE_PATH):
        try:
            data = json.load(open(_INSTRUMENT_CACHE_PATH))
            _NFO_CACHE        = data
            _NFO_SYMBOL_INDEX = {m["tradingsymbol"]: tok
                                 for tok, m in data.items()
                                 if "tradingsymbol" in m}
            return _NFO_CACHE
        except Exception:
            return {}
    return {}


def invalidate_nfo_cache():
    """Call after refresh_instrument_list() so the next lookup re-reads fresh data."""
    global _NFO_CACHE, _NFO_SYMBOL_INDEX
    _NFO_CACHE        = {}
    _NFO_SYMBOL_INDEX = {}


def get_option_token(option_symbol: str) -> Optional[str]:
    """Reverse-lookup Kite instrument_token for an option tradingsymbol. O(1)."""
    if not option_symbol:
        return None
    _load_nfo_cache()
    return _NFO_SYMBOL_INDEX.get(option_symbol)


def load_instrument_cache() -> dict:
    return _load_nfo_cache()


def get_nifty_option_chain() -> dict:
    """token → meta for every NIFTY index CE/PE, resolved in the same
    refresh_instrument_list() pass as everything else (boot + daily re-login) --
    no separate REST call needed from the collector's own thread."""
    return dict(_NIFTY_OPT_CACHE)


def get_nifty_index_token() -> Optional[str]:
    """NIFTY 50 index instrument_token (NSE:INDICES), cached from the same pass."""
    return _NIFTY_INDEX_TOKEN


def find_option_token(symbol: str, expiry: str, strike: float, option_type: str) -> Optional[str]:
    """Find Kite instrument_token for a specific option contract.
    expiry: ISO date 'YYYY-MM-DD'. option_type: CE / PE."""
    cache = _load_nfo_cache()
    for token, meta in cache.items():
        if (meta.get("name") == symbol.upper() and
            meta.get("expiry") == expiry and
            meta.get("instrument_type") == option_type.upper() and
            abs(float(meta.get("strike", 0)) - strike) < 0.01):
            return token
    return None


# Load cache at import (paths now defined)
_try_load_cache_at_import()
