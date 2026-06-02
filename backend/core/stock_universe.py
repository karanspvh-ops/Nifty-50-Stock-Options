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

_N50  = set(NIFTY50_SYMBOLS)
_N100 = _N50 | set(NIFTY_NEXT50_SYMBOLS)
_N200 = _N100 | set(NIFTY_MIDCAP_SYMBOLS)


def _membership(symbol: str) -> List[str]:
    out = []
    if symbol in _N50:
        out = ["NIFTY50", "NIFTY100", "NIFTY200"]
    elif symbol in _N100:
        out = ["NIFTY100", "NIFTY200"]
    elif symbol in _N200:
        out = ["NIFTY200"]
    return out


# ── Seed NSE-cash tokens (offline / test fallback; overridden by live master) ──
_SEED_TOKENS: Dict[str, str] = {
    "INFY": "1594", "TCS": "11536", "TECHM": "13538", "HCLTECH": "7229",
    "WIPRO": "3787", "LTIM": "17818", "HDFCBANK": "1333", "ICICIBANK": "4963",
    "KOTAKBANK": "1922", "INDUSINDBK": "5258", "AXISBANK": "5900", "SBIN": "3045",
    "RELIANCE": "2885", "BPCL": "526", "ONGC": "2475", "NTPC": "11630",
    "POWERGRID": "14977", "COALINDIA": "20374", "ASIANPAINT": "236",
    "HINDUNILVR": "1394", "ITC": "1660", "BRITANNIA": "547", "NESTLEIND": "17963",
    "TATACONSUM": "3432", "TITAN": "3506", "M&M": "2031", "MARUTI": "10999",
    "HEROMOTOCO": "1348", "BAJAJ-AUTO": "16669", "EICHERMOT": "910",
    "TATAMOTORS": "3456", "BAJFINANCE": "317", "BAJAJFINSV": "16675",
    "HDFCLIFE": "467", "SBILIFE": "21808", "GRASIM": "1232", "ULTRACEMCO": "11532",
    "HINDALCO": "1363", "TATASTEEL": "3499", "JSWSTEEL": "11723",
    "SUNPHARMA": "3351", "DRREDDY": "881", "CIPLA": "694", "APOLLOHOSP": "157",
    "LT": "11483", "BHARTIARTL": "10604", "ADANIPORTS": "15083", "ADANIENT": "25",
    "TRENT": "1964", "BEL": "383", "SHRIRAMFIN": "4306",
}

# ── Runtime maps (rebuilt on token resolution) ────────────────────────────────
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


# Build once at import using seed tokens (keeps tests / offline working)
_rebuild_master(_SEED_TOKENS)


# ── Public query API ──────────────────────────────────────────────────────────
def get_stocks_for_index(index: str) -> List[dict]:
    return [
        {"token": token, **meta}
        for token, meta in STOCK_MASTER.items()
        if index in meta["indices"]
    ]

def get_tokens_for_index(index: str) -> List[str]:
    return [s["token"] for s in get_stocks_for_index(index)]

def get_sectors_for_index(index: str) -> List[str]:
    return sorted({s["sector"] for s in get_stocks_for_index(index)})

def get_stocks_in_sector(sector: str, index: str = "NIFTY50") -> List[dict]:
    return [s for s in get_stocks_for_index(index)
            if s["sector"].upper() == sector.upper()]

def get_meta(token: str) -> dict:
    return STOCK_MASTER.get(token, {})

def get_token(symbol: str) -> Optional[str]:
    return SYMBOL_TO_TOKEN.get(symbol)


# ── Instrument master (NFO cache for option lookup) ───────────────────────────
_BASE_DIR              = os.path.join(os.path.dirname(__file__), "../..")
_INSTRUMENT_CACHE_PATH = os.path.join(_BASE_DIR, "instrument_cache.json")
_TOKENS_CACHE_PATH     = os.path.join(_BASE_DIR, "universe_tokens.json")
_LOTS_CACHE_PATH       = os.path.join(_BASE_DIR, "universe_lots.json")
_MASTER_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
_CACHE_MAX_AGE_SEC = 20 * 3600   # re-download at most once per ~20h


def _cache_fresh() -> bool:
    """True if the resolved-token cache exists and is recent enough."""
    for p in (_TOKENS_CACHE_PATH, _INSTRUMENT_CACHE_PATH):
        if not os.path.exists(p):
            return False
    import time as _t
    age = _t.time() - os.path.getmtime(_TOKENS_CACHE_PATH)
    return age < _CACHE_MAX_AGE_SEC


def _load_from_cache() -> dict:
    """Rebuild master from cached token map + return cached NFO dict."""
    with open(_TOKENS_CACHE_PATH) as f:
        tokens = json.load(f)
    if os.path.exists(_LOTS_CACHE_PATH):
        try:
            _LOT_SIZE.update({k: int(v) for k, v in json.load(open(_LOTS_CACHE_PATH)).items()})
        except Exception:
            pass
    _rebuild_master(tokens)
    with open(_INSTRUMENT_CACHE_PATH) as f:
        nfo = json.load(f)
    print(f"[UNIVERSE] Loaded from cache | NSE tokens: {len(tokens)} | "
          f"N50={len(get_stocks_for_index('NIFTY50'))}, "
          f"N100={len(get_stocks_for_index('NIFTY100'))}, "
          f"N200={len(get_stocks_for_index('NIFTY200'))}")
    return nfo


def _download_master(timeout_sec: int = 25):
    """Download the master JSON with a hard wall-clock timeout (thread-based)."""
    import threading
    result = {}
    def _worker():
        try:
            r = requests.get(_MASTER_URL, timeout=(10, timeout_sec))
            r.raise_for_status()
            result["data"] = r.json()
        except Exception as e:
            result["error"] = str(e)
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout_sec)
    if t.is_alive():
        raise TimeoutError(f"master download exceeded {timeout_sec}s hard limit")
    if "error" in result:
        raise RuntimeError(result["error"])
    return result["data"]


def refresh_instrument_list(force: bool = False) -> dict:
    """
    Resolve the universe. Uses a local cache (refreshed at most once/20h) so
    boot never re-downloads the ~20MB master and never hangs. On any download
    failure, falls back to the existing NFO cache + seed tokens so the system
    always boots.
    Returns the NFO cache dict.
    """
    # ── Fast path: fresh cache on disk ────────────────────────────────────────
    if not force and _cache_fresh():
        try:
            return _load_from_cache()
        except Exception as e:
            print(f"[UNIVERSE] Cache load failed ({e}); will try download.")

    try:
        instruments = _download_master()

        # ── NFO cache + lot sizes ─────────────────────────────────────────────
        nse_fo = {}
        for i in instruments:
            if i.get("exch_seg") == "NFO":
                nse_fo[i["token"]] = {
                    "symbol":         i["symbol"],
                    "name":           i["name"],
                    "expiry":         i.get("expiry", ""),
                    "strike":         i.get("strike", ""),
                    "lotsize":        i.get("lotsize", "1"),
                    "instrumenttype": i.get("instrumenttype", ""),
                    "exch_seg":       i.get("exch_seg", ""),
                }
                # Capture lot size for the underlying (FUT rows are cleanest)
                name = i.get("name", "")
                if name in SECTOR_OF and i.get("instrumenttype", "").startswith("FUT"):
                    try:
                        _LOT_SIZE[name] = int(i.get("lotsize", "0"))
                    except ValueError:
                        pass
        with open(_INSTRUMENT_CACHE_PATH, "w") as f:
            json.dump(nse_fo, f)

        # ── NSE-cash token resolution ─────────────────────────────────────────
        resolved: Dict[str, str] = {}
        for i in instruments:
            if i.get("exch_seg") == "NSE":
                name   = i.get("name", "")
                symbol = i.get("symbol", "")
                # Equity series rows end with "-EQ"
                if name in SECTOR_OF and symbol.upper().endswith("-EQ"):
                    resolved[name] = i["token"]

        # Merge: start from seed, override with anything resolved live
        merged = dict(_SEED_TOKENS)
        merged.update(resolved)
        _rebuild_master(merged)

        # Persist caches so future boots skip the download
        try:
            with open(_TOKENS_CACHE_PATH, "w") as f:
                json.dump(merged, f)
            with open(_LOTS_CACHE_PATH, "w") as f:
                json.dump(_LOT_SIZE, f)
        except Exception as e:
            print(f"[UNIVERSE] Cache write warning: {e}")

        print(f"[UNIVERSE] Master refreshed | NFO: {len(nse_fo)} | "
              f"NSE tokens resolved: {len(resolved)} | "
              f"Universe stocks: {len(STOCK_MASTER)} "
              f"(N50={len(get_stocks_for_index('NIFTY50'))}, "
              f"N100={len(get_stocks_for_index('NIFTY100'))}, "
              f"N200={len(get_stocks_for_index('NIFTY200'))})")
        return nse_fo

    except Exception as e:
        print(f"[UNIVERSE] Download failed ({e}).")
        # ── Fallback 1: stale token cache if present ──────────────────────────
        if os.path.exists(_TOKENS_CACHE_PATH) and os.path.exists(_INSTRUMENT_CACHE_PATH):
            try:
                print("[UNIVERSE] Falling back to existing token cache.")
                return _load_from_cache()
            except Exception as e2:
                print(f"[UNIVERSE] Cache fallback failed: {e2}")
        # ── Fallback 2: seed tokens + existing NFO cache ──────────────────────
        _rebuild_master(_SEED_TOKENS)
        print(f"[UNIVERSE] Using seed tokens (N50={len(get_stocks_for_index('NIFTY50'))}).")
        try:
            with open(_INSTRUMENT_CACHE_PATH) as f:
                return json.load(f)
        except Exception:
            return {}


def load_instrument_cache() -> dict:
    if os.path.exists(_INSTRUMENT_CACHE_PATH):
        with open(_INSTRUMENT_CACHE_PATH) as f:
            return json.load(f)
    return refresh_instrument_list()


def find_option_token(symbol: str, expiry: str, strike: float, option_type: str) -> Optional[str]:
    """Find the NSE token for a specific option contract."""
    cache = load_instrument_cache()
    target_name   = symbol.upper()
    target_expiry = expiry.upper()
    target_strike = int(strike * 100)
    target_type   = option_type.upper()
    for token, meta in cache.items():
        if (meta["name"] == target_name and
            meta["expiry"] == target_expiry and
            meta["instrumenttype"] == target_type and
            int(float(meta.get("strike", 0))) == target_strike):
            return token
    return None
