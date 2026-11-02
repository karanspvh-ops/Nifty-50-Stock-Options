"""checks.py — Individual pre-flight system checks.

Each function returns (ok: bool, msg: str).
No function should raise — all exceptions are caught and returned as failures.

To add a new check:
  1. Define a function here following the (bool, str) contract.
  2. Register it in runner.py _CHECKS list with a name and node label.
"""

import os
import json
import smtplib
from datetime import date, datetime, timedelta, time as dtime
from pathlib import Path
from typing import Tuple

ROOT          = Path(__file__).resolve().parents[2]   # project root
TOKEN_PATH    = ROOT / "kite_token.json"
TOKENS_CACHE  = ROOT / "universe_tokens.json"
INST_CACHE    = ROOT / "instrument_cache.json"


# ── 1. Database ───────────────────────────────────────────────────────────────

def check_database() -> Tuple[bool, str]:
    try:
        from backend.storage.engine import Session
        from backend.storage.models import Trade
        db = Session()
        try:
            count = db.query(Trade).count()
        finally:
            db.close()
        return True, f"Connected — {count} total trades in DB"
    except Exception as e:
        return False, f"Cannot open DB: {e}"


# ── 2. Kite Token (file) ──────────────────────────────────────────────────────

def check_kite_token() -> Tuple[bool, str]:
    if not TOKEN_PATH.exists():
        return False, "kite_token.json not found — complete Zerodha login in the dashboard first"
    try:
        d = json.loads(TOKEN_PATH.read_text())
        token_date = d.get("date", "")
        today = date.today().isoformat()
        if token_date != today:
            return False, f"Token stale (token={token_date}, today={today}) — re-login required"
        user = d.get("user_name", "unknown")
        return True, f"Token valid for {today} | user: {user}"
    except Exception as e:
        return False, f"Token file unreadable: {e}"


# ── 3. Kite API (live call) ───────────────────────────────────────────────────

def check_kite_api() -> Tuple[bool, str]:
    try:
        from backend.core.broker import broker
        if not broker.has_token():
            return False, "No access token in memory — Zerodha login required"
        ok = broker.is_authenticated()
        if ok:
            return True, f"API responded OK | user: {broker._profile_name}"
        return False, "API call rejected — token may be expired, re-login required"
    except Exception as e:
        return False, f"API call failed: {e}"


# ── 4. Instruments Cache ──────────────────────────────────────────────────────

def check_instruments() -> Tuple[bool, str]:
    missing, stale = [], []
    for path, label in [(TOKENS_CACHE, "universe_tokens.json"), (INST_CACHE, "instrument_cache.json")]:
        if not path.exists():
            missing.append(label)
            continue
        age_h = (datetime.now() - datetime.fromtimestamp(path.stat().st_mtime)).total_seconds() / 3600
        if age_h > 20:
            stale.append(f"{label} ({age_h:.0f}h old)")
    if missing:
        return False, f"Missing cache files: {', '.join(missing)}"
    if stale:
        return False, f"Stale caches (expected refresh on login): {', '.join(stale)}"
    return True, "Instrument caches present and fresh"


# ── 5. Historical Data Fetch ──────────────────────────────────────────────────

def check_historical_data() -> Tuple[bool, str]:
    try:
        from backend.core.broker import broker
        if not broker.has_token():
            return False, "Skipped — no Kite token (complete login first)"

        from backend.universe.instrument_cache import SYMBOL_TO_TOKEN
        token = SYMBOL_TO_TOKEN.get("RELIANCE")
        if not token:
            return False, "RELIANCE not in instrument cache — run refresh_instrument_list() first"

        # Walk backward over recent weekdays until one actually has candles --
        # skipping weekends alone isn't enough, an NSE trading holiday (e.g. a
        # Monday festival holiday) is also a weekday with zero candles, and
        # that's not a Kite/token problem, just an expected empty day. Only
        # fail this check if several consecutive weekdays are ALL empty, which
        # would actually indicate a real API/token issue.
        kite = broker.kite()
        day = date.today() - timedelta(days=1)
        tried = []
        for _ in range(7):
            if day.weekday() < 5:
                start   = datetime.combine(day, dtime(9, 15))
                end     = datetime.combine(day, dtime(15, 30))
                candles = kite.historical_data(int(token), start, end, "5minute")
                if candles:
                    return True, f"Fetched {len(candles)} candles for RELIANCE ({day})"
                tried.append(str(day))
            day -= timedelta(days=1)

        return False, f"No candles for RELIANCE on any of the last weekdays tried ({', '.join(tried)})"
    except Exception as e:
        return False, f"Historical data fetch failed: {e}"


# ── 6. System Clock ───────────────────────────────────────────────────────────

def check_clock() -> Tuple[bool, str]:
    try:
        from backend.core.clock import now_ist
        ist_now  = now_ist()
        utc_now  = datetime.utcnow()
        expected = utc_now + timedelta(hours=5, minutes=30)
        drift    = abs((ist_now - expected).total_seconds())
        if drift > 300:
            return False, f"Clock drift too large: {drift:.0f}s (IST={ist_now}, expected≈{expected})"
        return True, f"IST clock OK — {ist_now.strftime('%H:%M:%S')} (drift {drift:.0f}s)"
    except Exception as e:
        return False, f"Clock check failed: {e}"


# ── 7. ES Strategy ────────────────────────────────────────────────────────────

def check_es_strategy() -> Tuple[bool, str]:
    try:
        from backend.core.early_scalp import early_scalp
        params = early_scalp.get_params()
        required = ["hard_sl_pct", "target_pct", "trail_activate_pct", "max_positions", "gap_min_pct"]
        missing  = [k for k in required if k not in params]
        if missing:
            return False, f"ES params missing keys: {missing}"

        # Dry-run: call pure computation logic with synthetic candles
        from backend.strategies.es.filters import ESFiltersMixin
        _mixin = object.__new__(ESFiltersMixin)
        fake = [
            {"open": 100, "high": 102, "low": 99,  "close": 101, "volume": 1000},
            {"open": 101, "high": 103, "low": 100, "close": 102, "volume": 1200},
            {"open": 102, "high": 104, "low": 101, "close": 103, "volume": 1100},
        ]
        cc = _mixin._consec_candles(fake, "call")
        return True, (
            f"ES loaded | {len(params)} params | "
            f"max_pos={params['max_positions']}, sl={params['hard_sl_pct']}%, "
            f"target={params['target_pct']}% | dry-run: {cc} consec candles"
        )
    except Exception as e:
        return False, f"ES strategy error at node: {e}"


# ── 8. OB Strategy ────────────────────────────────────────────────────────────

def check_ob_strategy() -> Tuple[bool, str]:
    try:
        from backend.core.opening_breakout import opening_breakout
        params  = opening_breakout.get_params()
        required = ["hard_sl_pct", "target_pct", "trail_activate_pct", "max_positions"]
        missing  = [k for k in required if k not in params]
        if missing:
            return False, f"OB params missing keys: {missing}"
        return True, (
            f"OB loaded | {len(params)} params | "
            f"max_pos={params['max_positions']}, sl={params['hard_sl_pct']}%, "
            f"target={params['target_pct']}%"
        )
    except Exception as e:
        return False, f"OB strategy error at node: {e}"


# ── 9. Risk Engine ────────────────────────────────────────────────────────────

def check_risk_engine() -> Tuple[bool, str]:
    try:
        from backend.risk.engine import RiskEngine, risk_engine
        # The singleton is already running — just verify it imported cleanly
        # and its class is intact (catches broken mixin imports)
        _ = RiskEngine.__mro__
        running = getattr(risk_engine, "_running", None)
        return True, f"RiskEngine class intact | singleton _running={running}"
    except Exception as e:
        return False, f"RiskEngine import/check failed: {e}"


# ── 10. SMTP Connectivity ─────────────────────────────────────────────────────

def check_smtp() -> Tuple[bool, str]:
    smtp_user = os.getenv("SMTP_USER", "")
    smtp_pass = os.getenv("SMTP_PASS", "")
    if not smtp_user or not smtp_pass:
        return False, "SMTP_USER / SMTP_PASS not configured in .env file"
    try:
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=10) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            # Deliberately do NOT log in — just verifying network reachability
        return True, f"smtp.gmail.com:587 reachable, STARTTLS OK | sender: {smtp_user}"
    except Exception as e:
        return False, f"SMTP connection failed: {e}"
