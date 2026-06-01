"""
tick_engine.py — Angel One SmartWebSocketV2 real-time feed manager.

Responsibilities:
  1. Login via SmartAPI and obtain JWT + feed token
  2. Subscribe to all stocks in the active index
  3. Push every tick to MarketState + CandleBuilder
  4. Handle disconnects: halt trading immediately, retry with back-off
  5. Recompute sector % moves every 5 seconds

Hard rule: If feed drops, _trading_halted = True until feed is back.
"""

import json, time, threading, os, sys
from datetime import datetime
from SmartApi import SmartConnect
from SmartApi.smartWebSocketV2 import SmartWebSocketV2
import pyotp

# Resolve root path
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state  import market
from backend.core.candle_builder import process_tick
from backend.core.stock_universe import (
    get_stocks_for_index, get_meta, get_sectors_for_index, get_stocks_in_sector
)
from backend.core.settings_manager import get_active_index


# ── Constants ─────────────────────────────────────────────────────────────────
EXCHANGE_TYPE_NSE_CM = 1          # NSE Cash (for equity ticks)
FEED_MODE_LTP        = 1          # LTP only (no close/volume)
FEED_MODE_QUOTE      = 2          # LTP + OHLC + volume + prev close (needed for % move)
MAX_TOKENS_PER_SUB   = 50         # Angel One limit per subscription call
RECONNECT_DELAY      = 5          # seconds between reconnect attempts
SECTOR_UPDATE_INTERVAL = 5        # seconds between sector aggregation


class TickEngine:
    def __init__(self):
        self._smart:       SmartConnect      = None
        self._ws:          SmartWebSocketV2  = None
        self._auth_token:  str               = ""
        self._feed_token:  str               = ""
        self._client_id:   str               = ""

        self._subscribed_tokens: list = []
        self._running:     bool = False
        self._ws_thread:   threading.Thread = None
        self._sector_thread: threading.Thread = None

        self._config = self._load_config()

    def _load_config(self) -> dict:
        cfg_path = os.path.join(ROOT, "config.json")
        with open(cfg_path) as f:
            return json.load(f)

    # ── Login ─────────────────────────────────────────────────────────────────

    def login(self) -> bool:
        try:
            cfg        = self._config
            totp       = pyotp.TOTP(cfg["totp_secret"]).now()
            self._smart = SmartConnect(api_key=cfg["api_key"])
            data       = self._smart.generateSession(
                clientCode=cfg["client_id"],
                password=cfg["mpin"],
                totp=totp
            )
            if not data["status"]:
                print(f"[TICK ENGINE] Login failed: {data['message']}")
                return False

            self._auth_token = data["data"]["jwtToken"]
            self._feed_token = data["data"]["feedToken"]
            self._client_id  = cfg["client_id"]
            print(f"[TICK ENGINE] Login OK | client: {self._client_id}")
            return True
        except Exception as e:
            print(f"[TICK ENGINE] Login exception: {e}")
            return False

    # ── Subscription helpers ─────────────────────────────────────────────────

    def _build_token_list(self) -> list:
        # Always subscribe to the full NIFTY200 superset so every stock streams.
        # Index switching (50/100/200) is then just a view filter — instant,
        # no unsubscribe/re-subscribe needed.
        stocks = get_stocks_for_index("NIFTY200")
        tokens = [s["token"] for s in stocks]
        self._subscribed_tokens = tokens
        # Angel One expects: [{"exchangeType": 1, "tokens": [...]}]
        # Split into chunks of MAX_TOKENS_PER_SUB
        chunks = [tokens[i:i+MAX_TOKENS_PER_SUB]
                  for i in range(0, len(tokens), MAX_TOKENS_PER_SUB)]
        return [{"exchangeType": EXCHANGE_TYPE_NSE_CM, "tokens": chunk}
                for chunk in chunks]

    # ── WebSocket callbacks ──────────────────────────────────────────────────

    def _on_data(self, ws, message):
        """Called for every incoming tick."""
        try:
            token     = str(message.get("token", ""))
            ltp       = float(message.get("last_traded_price", 0)) / 100  # paise → ₹
            volume    = int(message.get("volume_trade_for_the_day", 0))
            # QUOTE mode field for previous-day close (in paise)
            prev_close = float(message.get("closed_price", 0)) / 100

            ts = datetime.now()

            if token and ltp > 0:
                meta = get_meta(token)
                symbol = meta.get("symbol", token)

                # 1. Update market state
                market.update_tick(token, ltp, volume, prev_close, ts)

                # 2. Feed into candle builder
                process_tick(token, symbol, ltp, volume, ts)

        except Exception as e:
            print(f"[TICK ENGINE] on_data error: {e}")

    def _on_open(self, ws):
        print("[TICK ENGINE] WebSocket connected. Subscribing...")
        market.set_feed_reconnected()
        token_list = self._build_token_list()
        for sub in token_list:
            self._ws.subscribe(
                correlation_id="tick_engine",
                mode=FEED_MODE_QUOTE,
                token_list=[sub]
            )
        print(f"[TICK ENGINE] Subscribed to {len(self._subscribed_tokens)} stocks.")

    def _on_error(self, ws, error):
        print(f"[TICK ENGINE] WebSocket error: {error}")
        market.set_feed_connected(False, str(error))

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"[TICK ENGINE] WebSocket closed: {close_status_code} {close_msg}")
        market.set_feed_connected(False, f"Closed: {close_msg}")
        if self._running:
            print(f"[TICK ENGINE] Reconnecting in {RECONNECT_DELAY}s...")
            time.sleep(RECONNECT_DELAY)
            self._connect()

    # ── Connection ────────────────────────────────────────────────────────────

    def _connect(self):
        try:
            if not self.login():
                print("[TICK ENGINE] Login failed. Retrying in 30s...")
                time.sleep(30)
                if self._running:
                    self._connect()
                return

            self._ws = SmartWebSocketV2(
                auth_token  = self._auth_token,
                api_key     = self._config["api_key"],
                client_code = self._client_id,
                feed_token  = self._feed_token,
            )
            self._ws.on_open    = self._on_open
            self._ws.on_data    = self._on_data
            self._ws.on_error   = self._on_error
            self._ws.on_close   = self._on_close

            market.set_feed_connected(True)
            self._ws.connect()   # blocks until close

        except Exception as e:
            print(f"[TICK ENGINE] Connect exception: {e}")
            market.set_feed_connected(False, str(e))
            if self._running:
                time.sleep(RECONNECT_DELAY)
                self._connect()

    # ── Sector aggregator (runs every N seconds) ─────────────────────────────

    def _sector_loop(self):
        while self._running:
            try:
                index   = get_active_index()
                sectors = get_sectors_for_index(index)
                result  = {}
                all_moves = market.get_all_stock_moves()

                for sector in sectors:
                    stocks = get_stocks_in_sector(sector, index)
                    pcts   = []
                    stock_list = []
                    for s in stocks:
                        move = all_moves.get(s["token"])
                        if move:
                            pcts.append(move["pct_change"])
                            stock_list.append({
                                "token":      s["token"],
                                "symbol":     s["symbol"],
                                "pct_change": move["pct_change"],
                                "direction":  move["direction"],
                                "ltp":        move["ltp"],
                            })
                    if pcts:
                        avg_pct = round(sum(pcts) / len(pcts), 2)
                        result[sector] = {
                            "pct_change": avg_pct,
                            "direction":  "up" if avg_pct > 0 else ("down" if avg_pct < 0 else "flat"),
                            "stocks":     sorted(stock_list, key=lambda x: abs(x["pct_change"]), reverse=True),
                        }

                market.update_sector_moves(result)
            except Exception as e:
                print(f"[SECTOR] Aggregation error: {e}")

            time.sleep(SECTOR_UPDATE_INTERVAL)

    # ── Public API ────────────────────────────────────────────────────────────

    def start(self):
        if self._running:
            print("[TICK ENGINE] Already running.")
            return
        self._running = True
        print("[TICK ENGINE] Starting...")

        # WebSocket thread
        self._ws_thread = threading.Thread(target=self._connect, daemon=True)
        self._ws_thread.start()

        # Sector aggregator thread
        self._sector_thread = threading.Thread(target=self._sector_loop, daemon=True)
        self._sector_thread.start()

        print("[TICK ENGINE] Running. Feed + sector aggregator active.")

    def stop(self):
        self._running = False
        if self._ws:
            try:
                self._ws.close_connection()
            except:
                pass
        market.set_feed_connected(False, "Engine stopped by user")
        print("[TICK ENGINE] Stopped.")

    def get_status(self) -> dict:
        return {
            **market.get_status(),
            "subscribed_tokens": len(self._subscribed_tokens),
            "active_index":      get_active_index(),
        }


# ── Global singleton ──────────────────────────────────────────────────────────
tick_engine = TickEngine()
