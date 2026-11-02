"""ban_list.py — Daily NSE F&O ban list cache.

NSE bans fresh positions in F&O scripts when OI exceeds 95% of the
Market-Wide Position Limit (MWPL). Zerodha immediately rejects live
orders on these scripts. Paper mode has no such guard, so without this
check paper trades that can never execute in live pollute the P&L.

Fetched once per trading day, cached in memory for the session.
Fail-open on fetch error: logs a warning but does not block all trading.
"""

import threading
import requests
from datetime import date

from backend.core.clock import now_ist

_lock       = threading.Lock()
_cache_date: date | None = None
_banned:     set          = set()

_NSE_URL = (
    "https://archives.nseindia.com/archives/fo/sec_ban/"
    "fo_secban_{date}.csv"
)
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer":         "https://www.nseindia.com/",
}


def _fetch(for_date: date) -> set:
    date_str = for_date.strftime("%d%m%Y")
    url      = _NSE_URL.format(date=date_str)
    try:
        resp = requests.get(url, headers=_NSE_HEADERS, timeout=15)
        resp.raise_for_status()
        banned = set()
        for line in resp.text.strip().splitlines():
            sym = line.strip().split(",")[0].strip().upper()
            if sym and sym not in ("SECURITY", "SYMBOL", "SR NO", "SR.NO.", ""):
                banned.add(sym)
        print(f"[BAN] {for_date} — {len(banned)} banned scripts: {sorted(banned)}")
        return banned
    except Exception as e:
        print(f"[BAN] Could not fetch NSE ban list for {for_date}: {e} — ban check disabled today")
        return set()


def _refresh_if_stale() -> None:
    global _cache_date, _banned
    today = now_ist().date()
    with _lock:
        if _cache_date != today:
            _banned     = _fetch(today)
            _cache_date = today


def is_banned(symbol: str) -> bool:
    """True if the underlying symbol is on today's NSE F&O ban list."""
    _refresh_if_stale()
    with _lock:
        return symbol.upper() in _banned


def get_banned() -> set:
    """Return today's full ban set (for display / logging)."""
    _refresh_if_stale()
    with _lock:
        return set(_banned)
