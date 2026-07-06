"""
stock_universe.py — NSE stock master with index membership + sector map.

Design:
  - Index membership is defined by SYMBOL (stable, authoritative).
      NIFTY50  = the 50 names
      NIFTY100 = NIFTY50 + NIFTY NEXT 50
      NIFTY200 = NIFTY100 + Midcap names
  - NSE cash TOKENS are resolved dynamically from Angel One's instrument
    master at startup (resolve_universe_tokens). This eliminates the
    hand-typed-token errors / duplicate-key drops of the old design.
  - A small seed-token map keeps the Nifty 50 working offline / in tests.

Public API (unchanged):
  get_stocks_for_index, get_tokens_for_index, get_sectors_for_index,
  get_stocks_in_sector, get_meta, get_token, refresh_instrument_list,
  load_instrument_cache, find_option_token
"""

import os, json, requests
from typing import Dict, List, Optional

# ── Sector map (symbol → sector) ──────────────────────────────────────────────
SECTOR_OF: Dict[str, str] = {
    # NIFTY 50
    "ADANIENT": "INFRA", "ADANIPORTS": "INFRA", "APOLLOHOSP": "PHARMA",
    "ASIANPAINT": "FMCG", "AXISBANK": "PVT BANK", "BAJAJ-AUTO": "AUTO",
    "BAJFINANCE": "FIN SERVICE", "BAJAJFINSV": "FIN SERVICE", "BEL": "CAPITAL GOODS",
    "BHARTIARTL": "TELECOM", "BPCL": "ENERGY", "BRITANNIA": "FMCG",
    "CIPLA": "PHARMA", "COALINDIA": "ENERGY", "DRREDDY": "PHARMA",
    "EICHERMOT": "AUTO", "GRASIM": "CEMENT", "HCLTECH": "IT",
    "HDFCBANK": "PVT BANK", "HDFCLIFE": "FIN SERVICE", "HEROMOTOCO": "AUTO",
    "HINDALCO": "METAL", "HINDUNILVR": "FMCG", "ICICIBANK": "PVT BANK",
    "INDUSINDBK": "PVT BANK", "INFY": "IT", "ITC": "FMCG", "JSWSTEEL": "METAL",
    "KOTAKBANK": "PVT BANK", "LT": "CAPITAL GOODS", "LTIM": "IT", "M&M": "AUTO",
    "MARUTI": "AUTO", "NESTLEIND": "FMCG", "NTPC": "ENERGY", "ONGC": "ENERGY",
    "POWERGRID": "ENERGY", "RELIANCE": "ENERGY", "SBILIFE": "FIN SERVICE",
    "SBIN": "PSU BANK", "SHRIRAMFIN": "FIN SERVICE", "SUNPHARMA": "PHARMA",
    "TATACONSUM": "FMCG", "TATAMOTORS": "AUTO", "TATASTEEL": "METAL",
    "TCS": "IT", "TECHM": "IT", "TITAN": "FMCG", "TRENT": "RETAIL",
    "ULTRACEMCO": "CEMENT", "WIPRO": "IT",
    # NIFTY NEXT 50
    "ABB": "CAPITAL GOODS", "ADANIENSOL": "ENERGY", "ADANIGREEN": "ENERGY",
    "ADANIPOWER": "ENERGY", "AMBUJACEM": "CEMENT", "DMART": "RETAIL",
    "BAJAJHLDNG": "FIN SERVICE", "BANKBARODA": "PSU BANK", "BERGEPAINT": "FMCG",
    "BOSCHLTD": "AUTO", "CANBK": "PSU BANK", "CHOLAFIN": "FIN SERVICE",
    "COLPAL": "FMCG", "DABUR": "FMCG", "DIVISLAB": "PHARMA", "DLF": "REALTY",
    "GAIL": "ENERGY", "GODREJCP": "FMCG", "HAVELLS": "CONSUMER DURABLES",
    "HAL": "CAPITAL GOODS", "ICICIGI": "FIN SERVICE", "ICICIPRULI": "FIN SERVICE",
    "IOC": "ENERGY", "INDIGO": "AVIATION", "IRFC": "FIN SERVICE",
    "JINDALSTEL": "METAL", "JIOFIN": "FIN SERVICE", "JSWENERGY": "ENERGY",
    "LICI": "FIN SERVICE", "LODHA": "REALTY", "MOTHERSON": "AUTO",
    "NAUKRI": "IT", "PFC": "FIN SERVICE", "PIDILITIND": "CHEMICALS",
    "PNB": "PSU BANK", "RECLTD": "FIN SERVICE", "SIEMENS": "CAPITAL GOODS",
    "SRF": "CHEMICALS", "TATAPOWER": "ENERGY", "TORNTPHARM": "PHARMA",
    "TVSMOTOR": "AUTO", "VBL": "FMCG", "VEDL": "METAL", "ZOMATO": "RETAIL",
    "ZYDUSLIFE": "PHARMA", "GODREJPROP": "REALTY", "POLYCAB": "CAPITAL GOODS",
    "UNITDSPR": "FMCG", "PAGEIND": "FMCG",
    # NIFTY MIDCAP (to complete NIFTY 200)
    "PERSISTENT": "IT", "COFORGE": "IT", "MPHASIS": "IT", "KPITTECH": "IT",
    "OFSS": "IT", "LTTS": "IT", "TATAELXSI": "IT", "PETRONET": "ENERGY",
    "SUZLON": "ENERGY", "IREDA": "ENERGY", "NHPC": "ENERGY", "SJVN": "ENERGY",
    "OIL": "ENERGY", "IGL": "ENERGY", "GUJGASLTD": "ENERGY", "NMDC": "METAL",
    "SAIL": "METAL", "HINDZINC": "METAL", "NATIONALUM": "METAL",
    "APLAPOLLO": "METAL", "BHEL": "CAPITAL GOODS", "CGPOWER": "CAPITAL GOODS",
    "CUMMINSIND": "CAPITAL GOODS", "THERMAX": "CAPITAL GOODS",
    "OBEROIRLTY": "REALTY", "PHOENIXLTD": "REALTY", "PRESTIGE": "REALTY",
    "AUBANK": "PVT BANK", "BANDHANBNK": "PVT BANK", "FEDERALBNK": "PVT BANK",
    "IDFCFIRSTB": "PVT BANK", "RBLBANK": "PVT BANK", "YESBANK": "PVT BANK",
    "INDIANB": "PSU BANK", "UNIONBANK": "PSU BANK", "BANKINDIA": "PSU BANK",
    "IOB": "PSU BANK", "MAZDOCK": "CAPITAL GOODS", "BDL": "CAPITAL GOODS",
    "COCHINSHIP": "CAPITAL GOODS", "LICHSGFIN": "FIN SERVICE",
    "MUTHOOTFIN": "FIN SERVICE", "MFSL": "FIN SERVICE", "SBICARD": "FIN SERVICE",
    "ANGELONE": "FIN SERVICE", "NUVAMA": "FIN SERVICE", "POLICYBZR": "FIN SERVICE",
    "PAYTM": "FIN SERVICE", "LUPIN": "PHARMA", "AUROPHARMA": "PHARMA",
    "ALKEM": "PHARMA", "BIOCON": "PHARMA", "GLENMARK": "PHARMA",
    "LAURUSLABS": "PHARMA", "MANKIND": "PHARMA", "ABBOTINDIA": "PHARMA",
    "MARICO": "FMCG", "TATACOMM": "TELECOM", "IDEA": "TELECOM",
    "INDUSTOWER": "TELECOM", "ASHOKLEY": "AUTO", "BHARATFORG": "AUTO",
    "BALKRISIND": "AUTO", "MRF": "AUTO", "EXIDEIND": "AUTO", "TIINDIA": "AUTO",
    "ESCORTS": "AUTO", "UNOMINDA": "AUTO", "SONACOMS": "AUTO",
    "COROMANDEL": "CHEMICALS", "DEEPAKNTR": "CHEMICALS", "PIIND": "CHEMICALS",
    "UPL": "CHEMICALS", "TATACHEM": "CHEMICALS", "AARTIIND": "CHEMICALS",
    "DALBHARAT": "CEMENT", "ACC": "CEMENT", "RAMCOCEM": "CEMENT",
    "JKCEMENT": "CEMENT", "SHREECEM": "CEMENT", "VOLTAS": "CONSUMER DURABLES",
    "DIXON": "CONSUMER DURABLES", "CROMPTON": "CONSUMER DURABLES",
    "BLUESTARCO": "CONSUMER DURABLES", "KALYANKJIL": "RETAIL",
    "PATANJALI": "FMCG", "INDHOTEL": "HOSPITALITY", "JUBLFOOD": "FMCG",
    "CONCOR": "LOGISTICS", "DELHIVERY": "LOGISTICS", "GMRAIRPORT": "INFRA",
    "IRCTC": "RETAIL", "IRB": "INFRA", "ABCAPITAL": "FIN SERVICE",
    "HUDCO": "FIN SERVICE", "BSE": "FIN SERVICE", "CDSL": "FIN SERVICE",
    "KEI": "CAPITAL GOODS", "SUPREMEIND": "CAPITAL GOODS", "ASTRAL": "CAPITAL GOODS",
    # ── F&O EXTRAS (beyond NIFTY 200, ~36 names with options on NSE) ──
    "360ONE": "FIN SERVICE", "AMBER": "CONSUMER DURABLES", "CAMS": "FIN SERVICE",
    "ETERNAL": "RETAIL", "FORCEMOT": "AUTO", "FORTIS": "PHARMA",
    "GODFRYPHLP": "FMCG", "GVT&D": "CAPITAL GOODS", "HDFCAMC": "FIN SERVICE",
    "HINDPETRO": "ENERGY", "HYUNDAI": "AUTO", "IEX": "ENERGY",
    "INOXWIND": "ENERGY", "KAYNES": "CAPITAL GOODS", "KFINTECH": "FIN SERVICE",
    "LTF": "FIN SERVICE", "LTM": "OTHER", "MANAPPURAM": "FIN SERVICE",
    "MAXHEALTH": "PHARMA", "MCX": "FIN SERVICE", "MOTILALOFS": "FIN SERVICE",
    "NAM-INDIA": "FIN SERVICE", "NBCC": "INFRA", "NYKAA": "RETAIL",
    "PGEL": "CAPITAL GOODS", "PNBHOUSING": "FIN SERVICE",
    "POWERINDIA": "CAPITAL GOODS", "PREMIERENE": "ENERGY",
    "RADICO": "FMCG", "RVNL": "INFRA", "SAMMAANCAP": "FIN SERVICE",
    "SOLARINDS": "CHEMICALS", "SWIGGY": "RETAIL", "TMPV": "AUTO",
    "VMM": "RETAIL", "WAAREEENER": "ENERGY",
}

# ── Index membership (symbol lists) ───────────────────────────────────────────
NIFTY50_SYMBOLS = [
    "ADANIENT","ADANIPORTS","APOLLOHOSP","ASIANPAINT","AXISBANK","BAJAJ-AUTO",
    "BAJFINANCE","BAJAJFINSV","BEL","BHARTIARTL","BPCL","BRITANNIA","CIPLA",
    "COALINDIA","DRREDDY","EICHERMOT","GRASIM","HCLTECH","HDFCBANK","HDFCLIFE",
    "HEROMOTOCO","HINDALCO","HINDUNILVR","ICICIBANK","INDUSINDBK","INFY","ITC",
    "JSWSTEEL","KOTAKBANK","LT","LTIM","M&M","MARUTI","NESTLEIND","NTPC","ONGC",
    "POWERGRID","RELIANCE","SBILIFE","SBIN","SHRIRAMFIN","SUNPHARMA","TATACONSUM",
    "TATAMOTORS","TATASTEEL","TCS","TECHM","TITAN","TRENT","ULTRACEMCO","WIPRO",
]

NIFTY_NEXT50_SYMBOLS = [
    "ABB","ADANIENSOL","ADANIGREEN","ADANIPOWER","AMBUJACEM","DMART","BAJAJHLDNG",
    "BANKBARODA","BERGEPAINT","BOSCHLTD","CANBK","CHOLAFIN","COLPAL","DABUR",
    "DIVISLAB","DLF","GAIL","GODREJCP","HAVELLS","HAL","ICICIGI","ICICIPRULI",
    "IOC","INDIGO","IRFC","JINDALSTEL","JIOFIN","JSWENERGY","LICI","LODHA",
    "MOTHERSON","NAUKRI","PFC","PIDILITIND","PNB","RECLTD","SIEMENS","SRF",
    "TATAPOWER","TORNTPHARM","TVSMOTOR","VBL","VEDL","ZOMATO","ZYDUSLIFE",
    "GODREJPROP","POLYCAB","UNITDSPR","PAGEIND",
]

NIFTY_MIDCAP_SYMBOLS = [
    "PERSISTENT","COFORGE","MPHASIS","KPITTECH","OFSS","LTTS","TATAELXSI",
    "PETRONET","SUZLON","IREDA","NHPC","SJVN","OIL","IGL","GUJGASLTD","NMDC",
    "SAIL","HINDZINC","NATIONALUM","APLAPOLLO","BHEL","CGPOWER","CUMMINSIND",
    "THERMAX","OBEROIRLTY","PHOENIXLTD","PRESTIGE","AUBANK","BANDHANBNK",
    "FEDERALBNK","IDFCFIRSTB","RBLBANK","YESBANK","INDIANB","UNIONBANK",
    "BANKINDIA","IOB","MAZDOCK","BDL","COCHINSHIP","LICHSGFIN","MUTHOOTFIN",
    "MFSL","SBICARD","ANGELONE","NUVAMA","POLICYBZR","PAYTM","LUPIN","AUROPHARMA",
    "ALKEM","BIOCON","GLENMARK","LAURUSLABS","MANKIND","ABBOTINDIA","MARICO",
    "TATACOMM","IDEA","INDUSTOWER","ASHOKLEY","BHARATFORG","BALKRISIND","MRF",
    "EXIDEIND","TIINDIA","ESCORTS","UNOMINDA","SONACOMS","COROMANDEL","DEEPAKNTR",
    "PIIND","UPL","TATACHEM","AARTIIND","DALBHARAT","ACC","RAMCOCEM","JKCEMENT",
    "SHREECEM","VOLTAS","DIXON","CROMPTON","BLUESTARCO","KALYANKJIL","PATANJALI",
    "INDHOTEL","JUBLFOOD","CONCOR","DELHIVERY","GMRAIRPORT","IRCTC","IRB",
    "ABCAPITAL","HUDCO","BSE","CDSL","KEI","SUPREMEIND","ASTRAL",
]

# F&O extras — stocks WITH listed options on NSE but NOT part of NIFTY 200.
# This bucket plus NIFTY 200 forms the full set of options-tradable underlyings
# (resolved live against Kite's NFO instrument dump on startup).
NIFTY_FNO_EXTRAS = [
    "360ONE","AMBER","CAMS","ETERNAL","FORCEMOT","FORTIS","GODFRYPHLP","GVT&D",
    "HDFCAMC","HINDPETRO","HYUNDAI","IEX","INOXWIND","KAYNES","KFINTECH","LTF",
    "LTM","MANAPPURAM","MAXHEALTH","MCX","MOTILALOFS","NAM-INDIA","NBCC","NYKAA",
    "PGEL","PNBHOUSING","POWERINDIA","PREMIERENE","RADICO","RVNL","SAMMAANCAP",
    "SOLARINDS","SWIGGY","TMPV","VMM","WAAREEENER",
]

_N50  = set(NIFTY50_SYMBOLS)
_N100 = _N50 | set(NIFTY_NEXT50_SYMBOLS)
_N200 = _N100 | set(NIFTY_MIDCAP_SYMBOLS)
_FNO  = _N200 | set(NIFTY_FNO_EXTRAS)   # full F&O-tradable superset (~211)


def _membership(symbol: str) -> List[str]:
    """Indices a symbol belongs to. Every F&O-tradable name is in 'FNO'
    (the scan universe); the NIFTY 50/100/200 labels still apply correctly."""
    out = []
    if symbol in _N50:
        out = ["NIFTY50", "NIFTY100", "NIFTY200", "FNO"]
    elif symbol in _N100:
        out = ["NIFTY100", "NIFTY200", "FNO"]
    elif symbol in _N200:
        out = ["NIFTY200", "FNO"]
    elif symbol in _FNO:
        out = ["FNO"]
    return out


# ── Runtime maps (rebuilt on token resolution from Zerodha instruments) ───────
SYMBOL_TO_TOKEN: Dict[str, str] = {}
STOCK_MASTER:    Dict[str, dict] = {}   # token → {symbol, sector, indices, lot_size}
_LOT_SIZE:       Dict[str, int]  = {}   # symbol → lot size (from NFO master)


def _rebuild_master(token_map: Dict[str, str]):
    """Rebuild STOCK_MASTER and SYMBOL_TO_TOKEN from a symbol→token map."""
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


# Build from cache at import if available (so the system has the universe
# before the daily Zerodha login refreshes it).
def _try_load_cache_at_import():
    try:
        if os.path.exists(_TOKENS_CACHE_PATH):
            tokens = json.load(open(_TOKENS_CACHE_PATH))
            if os.path.exists(_LOTS_CACHE_PATH):
                _LOT_SIZE.update({k: int(v) for k, v in json.load(open(_LOTS_CACHE_PATH)).items()})
            _rebuild_master(tokens)
    except Exception:
        pass


# The whole system now operates on ONE combined universe: every NSE stock
# that has listed options (the "FNO" superset = NIFTY 200 + ~36 mid/small-cap
# F&O extras), filtered to F&O lot_size > 0. No index dropdown.
TRADABLE_INDEX = "FNO"


# ── Public query API ──────────────────────────────────────────────────────────
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

def get_stocks_in_sector(sector: str, index: str = "NIFTY50", fno_only: bool = False) -> List[dict]:
    return [s for s in get_stocks_for_index(index, fno_only)
            if s["sector"].upper() == sector.upper()]

def get_tradable_universe() -> List[dict]:
    """All options-tradable stocks across NIFTY 50/100/200, deduped."""
    return get_stocks_for_index(TRADABLE_INDEX, fno_only=True)

def get_meta(token: str) -> dict:
    return STOCK_MASTER.get(token, {})

def get_token(symbol: str) -> Optional[str]:
    return SYMBOL_TO_TOKEN.get(symbol)


# ── Instrument master (resolved from Zerodha Kite instruments) ────────────────
_BASE_DIR              = os.path.join(os.path.dirname(__file__), "../..")
_INSTRUMENT_CACHE_PATH = os.path.join(_BASE_DIR, "instrument_cache.json")  # NFO options
_TOKENS_CACHE_PATH     = os.path.join(_BASE_DIR, "universe_tokens.json")   # symbol→token
_LOTS_CACHE_PATH       = os.path.join(_BASE_DIR, "universe_lots.json")     # symbol→lot


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
        # Not logged in yet — use cache if present
        if os.path.exists(_TOKENS_CACHE_PATH):
            _try_load_cache_at_import()
            print("[UNIVERSE] No Zerodha token yet — using cached tokens.")
        return _load_nfo_cache()

    try:
        kite = broker.kite()
        nse  = kite.instruments("NSE")
        nfo  = kite.instruments("NFO")

        # ── NSE equity token resolution ───────────────────────────────────────
        resolved: Dict[str, str] = {}
        for i in nse:
            if i.get("instrument_type") == "EQ" and i.get("tradingsymbol") in SECTOR_OF:
                resolved[i["tradingsymbol"]] = str(i["instrument_token"])

        # ── NFO cache + lot sizes ─────────────────────────────────────────────
        nfo_cache = {}
        for i in nfo:
            itype = i.get("instrument_type", "")
            name  = i.get("name", "")
            if name not in SECTOR_OF:
                continue
            if itype in ("CE", "PE"):
                exp = i.get("expiry")
                nfo_cache[str(i["instrument_token"])] = {
                    "tradingsymbol":  i.get("tradingsymbol", ""),
                    "name":           name,
                    "expiry":         exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                    "strike":         float(i.get("strike", 0)),
                    "instrument_type": itype,
                    "lot_size":       int(i.get("lot_size", 0)),
                }
            elif itype == "FUT":
                try:
                    _LOT_SIZE[name] = int(i.get("lot_size", 0))
                except (ValueError, TypeError):
                    pass

        _rebuild_master(resolved)

        # Persist caches
        try:
            json.dump(resolved,  open(_TOKENS_CACHE_PATH, "w"))
            json.dump(_LOT_SIZE, open(_LOTS_CACHE_PATH, "w"))
            json.dump(nfo_cache, open(_INSTRUMENT_CACHE_PATH, "w"))
        except Exception as e:
            print(f"[UNIVERSE] cache write warning: {e}")

        # Invalidate the in-memory NFO cache so get_option_token picks up
        # the newly written file on the next call.
        invalidate_nfo_cache()

        print(f"[UNIVERSE] Zerodha instruments loaded | NSE resolved: {len(resolved)} | "
              f"NFO options: {len(nfo_cache)} | "
              f"N50={len(get_stocks_for_index('NIFTY50'))}, "
              f"N100={len(get_stocks_for_index('NIFTY100'))}, "
              f"N200={len(get_stocks_for_index('NIFTY200'))}")
        return nfo_cache

    except Exception as e:
        print(f"[UNIVERSE] Zerodha instrument load failed ({e}); using cache.")
        _try_load_cache_at_import()
        return _load_nfo_cache()


_NFO_CACHE: dict = {}          # token → meta, loaded once per process
_NFO_SYMBOL_INDEX: dict = {}   # tradingsymbol → token, reverse lookup


def _load_nfo_cache() -> dict:
    global _NFO_CACHE, _NFO_SYMBOL_INDEX
    if _NFO_CACHE:
        return _NFO_CACHE
    if os.path.exists(_INSTRUMENT_CACHE_PATH):
        try:
            data = json.load(open(_INSTRUMENT_CACHE_PATH))
            _NFO_CACHE = data
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
    _NFO_CACHE = {}
    _NFO_SYMBOL_INDEX = {}


def get_option_token(option_symbol: str) -> Optional[str]:
    """Reverse-lookup the Kite instrument_token for an option tradingsymbol.
    Uses a cached reverse index — O(1) instead of O(n) file-read per call."""
    if not option_symbol:
        return None
    _load_nfo_cache()   # populate cache if empty
    return _NFO_SYMBOL_INDEX.get(option_symbol)


def load_instrument_cache() -> dict:
    return _load_nfo_cache()


def find_option_token(symbol: str, expiry: str, strike: float, option_type: str) -> Optional[str]:
    """Find the Kite instrument_token for a specific option contract.
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
