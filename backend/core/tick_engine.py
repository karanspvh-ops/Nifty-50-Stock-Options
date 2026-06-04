"""
tick_engine.py — Zerodha KiteTicker real-time feed manager.

Streams live ticks for the full NIFTY200 stock universe (and, on demand,
ATM option contracts) via Kite's WebSocket. Pushes every tick into
MarketState + the candle builder, and recomputes sector momentum every
few seconds.

Requires a valid Zerodha access token (broker.has_token()). If the feed
drops, trading halts immediately (no blind trades); KiteTicker auto-reconnects.
"""

import os, sys, time, threading
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from kiteconnect import KiteTicker

from backend.core.market_state   import market
from backend.core.candle_builder import process_tick
from backend.core.stock_universe import (
    get_stocks_for_index, get_meta, get_sectors_for_index, get_stocks_in_sector,
)
from backend.core.settings_manager import get_active_index
from backend.core.broker import broker

SECTOR_UPDATE_INTERVAL = 5      # seconds between sector aggregation


class TickEngine:
    def __init__(self):
        self._kws: KiteTicker = None
        self._running:   bool = False
        self._sector_thread: threading.Thread = None
        self._subscribed_tokens: list = []     # ints
        self._option_meta: dict = {}           # token(str) -> {symbol, name}

    # ── Token list ────────────────────────────────────────────────────────────
    def _stock_tokens(self) -> list:
        # Always stream the full NIFTY200 superset; index switch is a view filter.
        stocks = get_stocks_for_index("NIFTY200")
        toks = []
        for s in stocks:
            try:
                toks.append(int(s["token"]))
            except (ValueError, TypeError):
                pass
        return toks

    # ── KiteTicker callbacks ──────────────────────────────────────────────────
    def _on_connect(self, ws, response):
        toks = self._stock_tokens()
        self._subscribed_tokens = toks
        ws.subscribe(toks)
        ws.set_mode(ws.MODE_QUOTE, toks)   # QUOTE: ltp + ohlc + volume
        market.set_feed_reconnected()
        print(f"[TICK ENGINE] Connected. Subscribed to {len(toks)} stocks.")

    def _on_ticks(self, ws, ticks):
        ts = datetime.now()
        for tk in ticks:
            try:
                token = str(tk.get("instrument_token", ""))
                ltp   = float(tk.get("last_price", 0))          # already in ₹
                vol   = int(tk.get("volume_traded", 0) or 0)
                ohlc  = tk.get("ohlc") or {}
                prev_close = float(ohlc.get("close", 0) or 0)   # prev-day close
                if not token or ltp <= 0:
                    continue
                meta   = get_meta(token)
                symbol = meta.get("symbol") or self._option_meta.get(token, {}).get("symbol") or token
                market.update_tick(token, ltp, vol, prev_close, ts)
                process_tick(token, symbol, ltp, vol, ts)
            except Exception as e:
                print(f"[TICK ENGINE] tick parse error: {e}")

    def _on_close(self, ws, code, reason):
        print(f"[TICK ENGINE] WebSocket closed: {code} {reason}")
        market.set_feed_connected(False, f"closed: {reason}")

    def _on_error(self, ws, code, reason):
        print(f"[TICK ENGINE] WebSocket error: {code} {reason}")
        market.set_feed_connected(False, f"error: {reason}")

    def _on_reconnect(self, ws, attempts):
        print(f"[TICK ENGINE] Reconnecting… attempt {attempts}")

    def _on_noreconnect(self, ws):
        print("[TICK ENGINE] Reconnect failed permanently.")
        market.set_feed_connected(False, "reconnect failed")

    # ── Dynamic option subscription (real option prices for trades) ───────────
    def subscribe_options(self, contracts: list):
        """contracts: list of dicts {token, tradingsymbol, name}. Subscribes
        these option instruments so their live LTP flows into market_state."""
        if not self._kws:
            return
        new = []
        for c in contracts:
            tok = str(c["token"])
            if tok in self._option_meta:
                continue
            self._option_meta[tok] = {"symbol": c.get("tradingsymbol", tok), "name": c.get("name")}
            try:
                new.append(int(tok))
            except (ValueError, TypeError):
                pass
        if new:
            try:
                self._kws.subscribe(new)
                self._kws.set_mode(self._kws.MODE_QUOTE, new)
                print(f"[TICK ENGINE] Subscribed {len(new)} option contracts.")
            except Exception as e:
                print(f"[TICK ENGINE] option subscribe error: {e}")

    # ── Sector aggregator ─────────────────────────────────────────────────────
    def _sector_loop(self):
        while self._running:
            try:
                index   = "NIFTY200"            # combined universe…
                sectors = get_sectors_for_index(index, fno_only=True)   # …F&O only
                result  = {}
                moves   = market.get_all_stock_moves()
                for sector in sectors:
                    stocks, pcts, lst = get_stocks_in_sector(sector, index, fno_only=True), [], []
                    for s in stocks:
                        mv = moves.get(s["token"])
                        if mv:
                            pcts.append(mv["pct_change"])
                            lst.append({"token": s["token"], "symbol": s["symbol"],
                                        "pct_change": mv["pct_change"], "direction": mv["direction"],
                                        "ltp": mv["ltp"]})
                    if pcts:
                        avg = round(sum(pcts)/len(pcts), 2)
                        result[sector] = {"pct_change": avg,
                            "direction": "up" if avg > 0 else ("down" if avg < 0 else "flat"),
                            "stocks": sorted(lst, key=lambda x: abs(x["pct_change"]), reverse=True)}
                market.update_sector_moves(result)
            except Exception as e:
                print(f"[SECTOR] aggregation error: {e}")
            time.sleep(SECTOR_UPDATE_INTERVAL)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            print("[TICK ENGINE] already running.")
            return
        if not broker.has_token():
            print("[TICK ENGINE] No Zerodha access token — feed not started. Login required.")
            return
        self._running = True

        self._kws = KiteTicker(broker.api_key, broker.access_token)
        self._kws.on_connect      = self._on_connect
        self._kws.on_ticks        = self._on_ticks
        self._kws.on_close        = self._on_close
        self._kws.on_error        = self._on_error
        self._kws.on_reconnect    = self._on_reconnect
        self._kws.on_noreconnect  = self._on_noreconnect

        market.set_feed_connected(True)
        self._kws.connect(threaded=True)       # background thread, non-blocking

        self._sector_thread = threading.Thread(target=self._sector_loop, daemon=True)
        self._sector_thread.start()
        print("[TICK ENGINE] Started (KiteTicker).")

    def stop(self):
        self._running = False
        if self._kws:
            try:
                self._kws.close()
            except Exception:
                pass
        market.set_feed_connected(False, "engine stopped")
        print("[TICK ENGINE] Stopped.")

    def get_kite(self):
        """Shared authenticated Kite session (used by backfill / orders)."""
        return broker.kite()

    def get_status(self) -> dict:
        return {**market.get_status(),
                "subscribed_tokens": len(self._subscribed_tokens),
                "option_contracts":  len(self._option_meta),
                "active_index":      get_active_index()}


tick_engine = TickEngine()
