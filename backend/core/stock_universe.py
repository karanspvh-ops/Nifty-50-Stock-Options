"""
stock_universe.py — Static + dynamic stock master.

Contains:
  - NSE token → symbol mapping for Nifty 50 / 100 / 200
  - Sector classification
  - Helper functions to filter by index
  - Instrument list refresher (fetches latest tokens from Angel One)
"""

import os, json, requests
from typing import Dict, List

# ── Static master (token → metadata) ─────────────────────────────────────────
# Format: "TOKEN": {"symbol", "sector", "indices": [...], "lot_size"}
STOCK_MASTER: Dict[str, dict] = {
    # ── NIFTY 50 ──────────────────────────────────────────────────────────────
    "1594":  {"symbol": "INFY",        "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 300},
    "11536": {"symbol": "TCS",         "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 150},
    "13538": {"symbol": "TECHM",       "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 300},
    "7229":  {"symbol": "HCLTECH",     "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 350},
    "3787":  {"symbol": "WIPRO",       "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1500},
    "1333":  {"symbol": "HDFCBANK",    "sector": "PVT BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 550},
    "4963":  {"symbol": "ICICIBANK",   "sector": "PVT BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 700},
    "1922":  {"symbol": "KOTAKBANK",   "sector": "PVT BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 400},
    "5258":  {"symbol": "INDUSINDBK",  "sector": "PVT BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 500},
    "5900":  {"symbol": "AXISBANK",    "sector": "PVT BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 625},
    "3045":  {"symbol": "SBIN",        "sector": "PSU BANK",    "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1500},
    "2885":  {"symbol": "RELIANCE",    "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 250},
    "526":   {"symbol": "BPCL",        "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1800},
    "2475":  {"symbol": "ONGC",        "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1925},
    "11630": {"symbol": "NTPC",        "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 3000},
    "14977": {"symbol": "POWERGRID",   "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 2700},
    "236":   {"symbol": "ASIANPAINT",  "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 200},
    "1394":  {"symbol": "HINDUNILVR",  "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 300},
    "1660":  {"symbol": "ITC",         "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1600},
    "547":   {"symbol": "BRITANNIA",   "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 200},
    "2031":  {"symbol": "M&M",         "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 350},
    "10999": {"symbol": "MARUTI",      "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 100},
    "1348":  {"symbol": "HEROMOTOCO",  "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 300},
    "16669": {"symbol": "BAJAJ-AUTO",  "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 75},
    "910":   {"symbol": "EICHERMOT",   "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 100},
    "317":   {"symbol": "BAJFINANCE",  "sector": "FIN SERVICE", "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 125},
    "16675": {"symbol": "BAJAJFINSV",  "sector": "FIN SERVICE", "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 500},
    "1232":  {"symbol": "GRASIM",      "sector": "CEMENT",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 475},
    "11532": {"symbol": "ULTRACEMCO",  "sector": "CEMENT",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 100},
    "1363":  {"symbol": "HINDALCO",    "sector": "METAL",       "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 2150},
    "3499":  {"symbol": "TATASTEEL",   "sector": "METAL",       "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 5500},
    "3456":  {"symbol": "TATAMOTORS",  "sector": "AUTO",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1400},
    "3351":  {"symbol": "SUNPHARMA",   "sector": "PHARMA",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 700},
    "881":   {"symbol": "DRREDDY",     "sector": "PHARMA",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 125},
    "694":   {"symbol": "CIPLA",       "sector": "PHARMA",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 650},
    "11483": {"symbol": "LT",          "sector": "CAPITAL GOODS","indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 150},
    "10604": {"symbol": "BHARTIARTL",  "sector": "TELECOM",     "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 475},
    "157":   {"symbol": "APOLLOHOSP",  "sector": "PHARMA",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 125},
    "10940": {"symbol": "DIVISLAB",    "sector": "PHARMA",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 200},
    "467":   {"symbol": "HDFCLIFE",    "sector": "FIN SERVICE", "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1100},
    "21808": {"symbol": "SBILIFE",     "sector": "FIN SERVICE", "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 750},
    "17963": {"symbol": "NESTLEIND",   "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 800},
    "3432":  {"symbol": "TATACONSUM",  "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 900},
    "11287": {"symbol": "UPL",         "sector": "CHEMICALS",   "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1300},
    "3506":  {"symbol": "TITAN",       "sector": "FMCG",        "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 375},
    "20374": {"symbol": "COALINDIA",   "sector": "ENERGY",      "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 4200},
    "15083": {"symbol": "ADANIPORTS",  "sector": "INFRA",       "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 1250},
    "25":    {"symbol": "ADANIENT",    "sector": "INFRA",       "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 500},
    "17818": {"symbol": "LTIM",        "sector": "IT",          "indices": ["NIFTY50","NIFTY100","NIFTY200"], "lot_size": 150},

    # ── NIFTY 100 additional ──────────────────────────────────────────────────
    "21614": {"symbol": "PERSISTENT",  "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 60},
    "18096": {"symbol": "COFORGE",     "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 150},
    "11375": {"symbol": "MPHASIS",     "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 175},
    "11543": {"symbol": "KPITTECH",    "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 500},
    "7406":  {"symbol": "TATAELXSI",   "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 100},
    "2263":  {"symbol": "OFSS",        "sector": "IT",          "indices": ["NIFTY100","NIFTY200"], "lot_size": 100},
    "11483": {"symbol": "LT",          "sector": "CAPITAL GOODS","indices": ["NIFTY100","NIFTY200"], "lot_size": 150},
    "4668":  {"symbol": "UNIONBANK",   "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 4000},
    "1214":  {"symbol": "CANBK",       "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 2700},
    "1270":  {"symbol": "PNB",         "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 4000},
    "4668":  {"symbol": "BANKINDIA",   "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 3300},
    "1624":  {"symbol": "BANKBARODA",  "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 3750},
    "1023":  {"symbol": "INDIANB",     "sector": "PSU BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 700},
    "5215":  {"symbol": "AUBANK",      "sector": "PVT BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 1000},
    "3721":  {"symbol": "RBLBANK",     "sector": "PVT BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 1800},
    "11483": {"symbol": "IDFCFIRSTB",  "sector": "PVT BANK",    "indices": ["NIFTY100","NIFTY200"], "lot_size": 7500},
    "16714": {"symbol": "NUVAMA",      "sector": "FIN SERVICE", "indices": ["NIFTY100","NIFTY200"], "lot_size": 250},
    "19061": {"symbol": "ANGELONE",    "sector": "FIN SERVICE", "indices": ["NIFTY100","NIFTY200"], "lot_size": 250},
    "11351": {"symbol": "BOSCHLTD",    "sector": "AUTO",        "indices": ["NIFTY100","NIFTY200"], "lot_size": 50},
    "8467":  {"symbol": "TIINDIA",     "sector": "AUTO",        "indices": ["NIFTY100","NIFTY200"], "lot_size": 175},
    "2127":  {"symbol": "OBEROIRLTY",  "sector": "REALTY",      "indices": ["NIFTY100","NIFTY200"], "lot_size": 600},
    "17971": {"symbol": "LODHA",       "sector": "REALTY",      "indices": ["NIFTY100","NIFTY200"], "lot_size": 650},
    "20560": {"symbol": "NBCC",        "sector": "REALTY",      "indices": ["NIFTY100","NIFTY200"], "lot_size": 4200},
    "10217": {"symbol": "SUZLON",      "sector": "ENERGY",      "indices": ["NIFTY100","NIFTY200"], "lot_size": 5400},
    "14418": {"symbol": "IREDA",       "sector": "ENERGY",      "indices": ["NIFTY100","NIFTY200"], "lot_size": 2250},
    "11385": {"symbol": "VEDL",        "sector": "METAL",       "indices": ["NIFTY100","NIFTY200"], "lot_size": 2000},
    "11723": {"symbol": "JSWSTEEL",    "sector": "METAL",       "indices": ["NIFTY100","NIFTY200"], "lot_size": 600},
}

# ── Reverse lookup: symbol → token ───────────────────────────────────────────
SYMBOL_TO_TOKEN: Dict[str, str] = {
    v["symbol"]: k for k, v in STOCK_MASTER.items()
}


def get_stocks_for_index(index: str) -> List[dict]:
    """Return list of stock dicts for NIFTY50 / NIFTY100 / NIFTY200."""
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
    return [
        s for s in get_stocks_for_index(index)
        if s["sector"].upper() == sector.upper()
    ]


def get_meta(token: str) -> dict:
    return STOCK_MASTER.get(token, {})


def get_token(symbol: str) -> str | None:
    return SYMBOL_TO_TOKEN.get(symbol)


# ── Dynamic instrument list (fetches from Angel One) ─────────────────────────
_INSTRUMENT_CACHE_PATH = os.path.join(
    os.path.dirname(__file__), "../../instrument_cache.json"
)

def refresh_instrument_list() -> dict:
    """
    Downloads the full Angel One instrument master and caches it locally.
    Returns token → tradingsymbol dict for NSE_FO (options).
    Call once at startup to keep token data fresh.
    """
    url = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        instruments = r.json()
        # Filter NSE_FO only (for options lookup)
        nse_fo = {
            i["token"]: {
                "symbol":        i["symbol"],
                "name":          i["name"],
                "expiry":        i.get("expiry", ""),
                "strike":        i.get("strike", ""),
                "lotsize":       i.get("lotsize", "1"),
                "instrumenttype": i.get("instrumenttype", ""),
                "exch_seg":      i.get("exch_seg", ""),
            }
            for i in instruments
            if i.get("exch_seg") == "NFO"
        }
        with open(_INSTRUMENT_CACHE_PATH, "w") as f:
            json.dump(nse_fo, f)
        print(f"[UNIVERSE] Instrument list refreshed: {len(nse_fo)} NFO instruments cached.")
        return nse_fo
    except Exception as e:
        print(f"[UNIVERSE] Failed to refresh instrument list: {e}")
        return {}


def load_instrument_cache() -> dict:
    if os.path.exists(_INSTRUMENT_CACHE_PATH):
        with open(_INSTRUMENT_CACHE_PATH) as f:
            return json.load(f)
    return refresh_instrument_list()


def find_option_token(symbol: str, expiry: str, strike: float, option_type: str) -> str | None:
    """
    Find the NSE token for a specific option contract.
    expiry format: DDMMMYYYY  e.g. 26JUN2025
    option_type: CE or PE
    """
    cache = load_instrument_cache()
    target_name   = symbol.upper()
    target_expiry = expiry.upper()
    target_strike = int(strike * 100)  # Angel stores strike * 100
    target_type   = option_type.upper()

    for token, meta in cache.items():
        if (meta["name"]    == target_name and
            meta["expiry"]  == target_expiry and
            meta["instrumenttype"] == target_type and
            int(float(meta.get("strike", 0))) == target_strike):
            return token
    return None
