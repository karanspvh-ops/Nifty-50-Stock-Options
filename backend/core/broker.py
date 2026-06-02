"""
broker.py — Zerodha Kite Connect broker layer.

Single source of truth for the authenticated Kite session. Every module
(tick engine, order executor, instruments, backfill) uses this instead of
talking to the API directly.

Zerodha auth flow (once per trading day):
  1. User opens login_url() in a browser and logs in.
  2. Zerodha redirects to the redirect_url with ?request_token=XXX
  3. We exchange request_token + api_secret for an access_token
     (generate_session). That token is valid until ~6 AM next day.
  4. The access_token is cached in kite_token.json and reused all day.

There is NO programmatic daily login (unlike Angel's TOTP) — the browser
login is required each morning. The token is checked on every startup;
if missing/stale the system runs but trading is gated until login.
"""

import os, json
from datetime import date, datetime
from typing import Optional

from kiteconnect import KiteConnect

ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONFIG_PATH = os.path.join(ROOT, "config.json")
TOKEN_PATH  = os.path.join(ROOT, "kite_token.json")


def _load_config() -> dict:
    with open(CONFIG_PATH) as f:
        return json.load(f)


class Broker:
    def __init__(self):
        cfg              = _load_config()
        self.api_key     = cfg["api_key"]
        self.api_secret  = cfg["api_secret"]
        self.redirect    = cfg.get("redirect_url", "")
        self._kite       = KiteConnect(api_key=self.api_key)
        self._access_token: Optional[str] = None
        self._profile_name: Optional[str] = None
        self._load_token()

    # ── Login flow ────────────────────────────────────────────────────────────
    def login_url(self) -> str:
        return self._kite.login_url()

    def generate_session(self, request_token: str) -> dict:
        """Exchange a request_token for an access_token and persist it."""
        data = self._kite.generate_session(request_token, api_secret=self.api_secret)
        self._access_token = data["access_token"]
        self._kite.set_access_token(self._access_token)
        self._profile_name = data.get("user_name")
        self._save_token(data)
        print(f"[BROKER] Login OK | user: {self._profile_name}")
        return data

    # ── Token persistence ─────────────────────────────────────────────────────
    def _save_token(self, data: dict):
        try:
            json.dump({
                "access_token": self._access_token,
                "user_name":    data.get("user_name"),
                "user_id":      data.get("user_id"),
                "date":         date.today().isoformat(),
                "saved_at":     datetime.now().isoformat(),
            }, open(TOKEN_PATH, "w"))
        except Exception as e:
            print(f"[BROKER] token save error: {e}")

    def _load_token(self):
        if not os.path.exists(TOKEN_PATH):
            return
        try:
            d = json.load(open(TOKEN_PATH))
            # Kite tokens expire ~6 AM next day; only reuse today's token
            if d.get("date") == date.today().isoformat() and d.get("access_token"):
                self._access_token = d["access_token"]
                self._profile_name = d.get("user_name")
                self._kite.set_access_token(self._access_token)
                print(f"[BROKER] Reusing today's access token | user: {self._profile_name}")
        except Exception as e:
            print(f"[BROKER] token load error: {e}")

    # ── Status ────────────────────────────────────────────────────────────────
    def has_token(self) -> bool:
        return self._access_token is not None

    def is_authenticated(self) -> bool:
        """Verify the token actually works (a light profile call)."""
        if not self._access_token:
            return False
        try:
            p = self._kite.profile()
            self._profile_name = p.get("user_name", self._profile_name)
            return True
        except Exception:
            return False

    def status(self) -> dict:
        d = {"broker": "zerodha", "has_token": self.has_token(),
             "user": self._profile_name, "date": date.today().isoformat()}
        if os.path.exists(TOKEN_PATH):
            try:
                d["token_date"] = json.load(open(TOKEN_PATH)).get("date")
            except Exception:
                pass
        d["token_valid_today"] = d.get("token_date") == date.today().isoformat()
        return d

    # ── Access ────────────────────────────────────────────────────────────────
    def kite(self) -> KiteConnect:
        return self._kite

    @property
    def access_token(self) -> Optional[str]:
        return self._access_token


broker = Broker()
