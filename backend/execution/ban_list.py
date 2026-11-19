"""ban_list.py — Daily NSE F&O ban list.

NSE bans fresh positions in F&O scripts when OI exceeds 95% of the
Market-Wide Position Limit (MWPL); Zerodha rejects live orders on them.

  - Loaded in the background: at boot, then from 08:00 IST each trading day,
    retrying every minute until today's list is in. Never fetched inside an
    entry (a slow NSE response used to stall the first order of the day).
  - Live trading fails CLOSED: until today's list has loaded, is_ready() is
    False and live entries are refused (order_executor), with an alert.
    Paper trading carries on.

NSE file format (one banned script per line after a title line):
    Securities in Ban For Trade Date 19-NOV-2026:
    1,KAYNES
The previous parser took the first column, so it "banned" the serial number
and never blocked the real script.
"""

import threading
import time
import requests
from datetime import date, time as dtime

from backend.core.clock import now_ist

_lock       = threading.Lock()
_cache_date: date | None = None
_banned:     set          = set()
_last_error: str          = ""

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
FETCH_FROM = dtime(8, 0)
RETRY_SEC = 60


def parse(text: str) -> set:
    banned = set()
    for line in text.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2 and parts[0].isdigit() and parts[1]:
            banned.add(parts[1].upper())
    return banned


def _fetch(for_date: date) -> set:
    url = _NSE_URL.format(date=for_date.strftime("%d%m%Y"))
    resp = requests.get(url, headers=_NSE_HEADERS, timeout=15)
    resp.raise_for_status()
    if "Securities in Ban" not in resp.text:
        raise ValueError(f"unexpected NSE ban file: {resp.text[:80]!r}")
    return parse(resp.text)


def refresh(force: bool = False) -> bool:
    """Load today's list. True when today's list is in memory."""
    global _cache_date, _banned, _last_error
    today = now_ist().date()
    with _lock:
        if _cache_date == today and not force:
            return True
    try:
        banned = _fetch(today)
    except Exception as e:
        _last_error = str(e)
        print(f"[BAN] Could not fetch NSE ban list for {today}: {e} (retrying; live entries blocked)")
        return False
    with _lock:
        _banned, _cache_date, _last_error = banned, today, ""
    print(f"[BAN] {today}: {len(banned)} script(s) in F&O ban: {sorted(banned) or 'none'}")
    from backend.core import alerts
    alerts.clear_alert("banlist")
    return True


def is_ready() -> bool:
    with _lock:
        return _cache_date == now_ist().date()


def status() -> dict:
    with _lock:
        return {"date": str(_cache_date) if _cache_date else None, "ready": _cache_date == now_ist().date(),
                "banned": sorted(_banned), "error": _last_error}


def is_banned(symbol: str) -> bool:
    """True if the underlying is on today's ban list (never fetches inline)."""
    with _lock:
        return symbol.upper() in _banned


def get_banned() -> set:
    with _lock:
        return set(_banned)


def _loop():
    while True:
        now = now_ist()
        if now.weekday() < 5 and now.time() >= FETCH_FROM and not is_ready():
            if not refresh():
                from backend.core import alerts
                if now.time() >= dtime(9, 0):
                    alerts.raise_alert("banlist", "Today's NSE F&O ban list hasn't loaded "
                                       f"({_last_error}). Live entries are blocked until it does.")
        time.sleep(RETRY_SEC)


def start():
    refresh()          # at boot: yesterday-evening's file for today is usually already published
    threading.Thread(target=_loop, daemon=True, name="BanList").start()
