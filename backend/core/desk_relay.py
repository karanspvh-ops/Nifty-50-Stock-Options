"""desk_relay.py — Connect the backend to the hosted team site, outbound only.

Runs inside the backend process (no extra program, nothing listening for
outside connections). On start it opens ONE WebSocket to the Cloudflare relay
(frontend/worker/index.js) and keeps it open, reconnecting if it drops:

  - live streams: it reads this backend's own /api/market/ws/market and
    /api/market/ws/trades over loopback and pushes every message up once;
    Cloudflare fans them out to every team browser
  - requests: the relay sends down each HTTP request a team browser makes;
    it is answered by calling this backend over loopback and the response
    goes back up the same socket
  - dashboard data: every dataset the screens poll is read here over loopback
    and pushed up only when it changed (PUSH list); Cloudflare keeps the
    latest copy, so the team site keeps working while this PC is off
  - trade ticks are capped at TICK_MAX_PER_SEC per position on the way up
    (Cloudflare bills incoming messages; 2/s still looks live)

Needs, in config.json:
    "desk_relay": "wss://<worker>.workers.dev/relay",
    "desk_relay_secret": "<same value as the Worker's RELAY_SECRET>"
Without them this does nothing.
"""

import asyncio
import base64
import json
import os
import threading
import time

import requests
import websockets

_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LOCAL_HTTP = "http://127.0.0.1:8000"
LOCAL_WS = "ws://127.0.0.1:8000"
CHANNELS = ("market", "trades")
RETRY_SEC = 5
TEXT_TYPES = ("application/json", "text/", "application/javascript")
CHUNK = 400_000                 # characters per message part (large payloads are split)
TICK_MAX_PER_SEC = 2

# Datasets pushed to the team site: (path, check every N seconds). Paths must
# match exactly what the screens request. {idx} = the active index setting.
PUSH = [
    ("/api/strategy/plan", 2), ("/api/scalp/plan", 2), ("/api/scanner/tradable/active", 2),
    ("/api/market/ranking?index={idx}&order=most_likely", 3), ("/api/market/ranking?index={idx}&order=least_likely", 3),
    ("/api/live/account", 3), ("/api/live/alerts", 3), ("/api/broker/status", 5),
    ("/api/trades?env=paper", 5), ("/api/trades?env=live", 5),
    ("/api/settings/", 10), ("/api/simulation/status", 10), ("/api/simulation/dryrun/status", 10),
    ("/api/nifty-data/status", 10), ("/api/nifty-data/candles?range=today", 15),
    ("/api/nifty-data/minutes?range=today", 15), ("/api/nifty-data/backfill/status", 15),
    ("/api/trades?env=paper&all=true", 30), ("/api/trades?env=live&all=true", 30),
    ("/api/live/status", 60),
    ("/api/reports/pnl/paper", 60), ("/api/reports/ml/paper", 120), ("/api/reports/tradable/paper", 120),
    ("/api/reports/pnl/live", 60), ("/api/reports/ml/live", 120), ("/api/reports/tradable/live", 120),
    ("/api/backtest/status", 120), ("/api/backtest/result", 300),
]


def _config() -> dict:
    try:
        with open(os.path.join(_PROJECT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


class DeskRelay:
    def __init__(self):
        self._running = False
        self.connected_since = None
        self.error = ""
        self.requests_served = 0

    def start(self):
        cfg = _config()
        if not cfg.get("desk_relay") or not cfg.get("desk_relay_secret"):
            print("[DESK] Team site relay not configured (desk_relay / desk_relay_secret in config.json) -- skipping")
            return
        self._running = True
        threading.Thread(target=lambda: asyncio.run(self._main()), daemon=True, name="DeskRelay").start()

    def stop(self):
        self._running = False

    def status(self) -> dict:
        return {"relay": _config().get("desk_relay"), "connected_since": self.connected_since,
                "requests_served": self.requests_served, "error": self.error or None}

    async def _main(self):
        cfg = _config()
        url, secret = cfg["desk_relay"], cfg["desk_relay_secret"]
        while self._running:
            try:
                async with websockets.connect(url, additional_headers={"X-Relay-Secret": secret},
                                              max_size=None, ping_interval=20, ping_timeout=20,
                                              open_timeout=15) as ws:
                    self.connected_since = time.strftime("%H:%M:%S")
                    self.error = ""
                    print(f"[DESK] Connected to team site relay ({url})")
                    lock = asyncio.Lock()

                    async def send(obj):
                        # Split large bodies so no single WebSocket message gets too big
                        body = obj.get("body")
                        if isinstance(body, str) and len(body) > CHUNK:
                            n = (len(body) + CHUNK - 1) // CHUNK
                            async with lock:
                                for i in range(n):
                                    await ws.send(json.dumps({**obj, "part": i, "parts": n,
                                                              "body": body[i * CHUNK:(i + 1) * CHUNK]}))
                            return
                        async with lock:
                            await ws.send(json.dumps(obj))

                    tasks = [asyncio.create_task(self._serve(ws, send))]
                    tasks += [asyncio.create_task(self._stream(ch, send)) for ch in CHANNELS]
                    tasks.append(asyncio.create_task(self._publish(send)))
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        if t.exception():
                            raise t.exception()
            except Exception as e:
                self.error = str(e) or e.__class__.__name__
            if self.connected_since:
                print(f"[DESK] Relay connection lost ({self.error}); reconnecting in {RETRY_SEC}s")
            self.connected_since = None
            await asyncio.sleep(RETRY_SEC)

    async def _serve(self, ws, send):
        """Answer requests the relay forwards from team browsers."""
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("t") == "req":
                asyncio.create_task(self._answer(msg, send))

    async def _answer(self, msg, send):
        loop = asyncio.get_running_loop()
        headers = {"X-Via-Relay": "1"}
        if msg.get("type"):
            headers["Content-Type"] = msg["type"]
        if msg.get("ip"):
            headers["X-Relay-Client-IP"] = msg["ip"]
        if msg.get("agent"):
            headers["User-Agent"] = msg["agent"]
        try:
            r = await loop.run_in_executor(None, lambda: requests.request(
                msg.get("method", "GET"), LOCAL_HTTP + msg.get("path", "/"),
                data=(msg.get("body") or "").encode("utf-8") if msg.get("body") is not None else None,
                headers=headers, timeout=20))
            ctype = r.headers.get("Content-Type", "application/json")
            res = {"t": "res", "id": msg["id"], "status": r.status_code, "type": ctype,
                   "disposition": r.headers.get("Content-Disposition")}
            if ctype.startswith(TEXT_TYPES):
                res["body"] = r.text
            else:
                res["body"], res["b64"] = base64.b64encode(r.content).decode("ascii"), True
        except Exception as e:
            res = {"t": "res", "id": msg["id"], "status": 502, "type": "application/json",
                   "body": json.dumps({"detail": f"Backend error: {e}"})}
        self.requests_served += 1
        await send(res)

    async def _stream(self, channel, send):
        """Forward this backend's own live stream to the relay (one copy for all viewers).
        Per-position trade ticks are coalesced to TICK_MAX_PER_SEC (latest value wins)."""
        gap = 1.0 / TICK_MAX_PER_SEC
        last_sent, held = {}, {}

        async def flush_held():
            while True:
                await asyncio.sleep(gap)
                now = time.monotonic()
                for tid, data in list(held.items()):
                    if now - last_sent.get(tid, 0) >= gap:
                        held.pop(tid, None)
                        last_sent[tid] = now
                        await send({"t": "stream", "ch": channel, "data": data})

        flusher = asyncio.create_task(flush_held()) if channel == "trades" else None
        try:
            while True:
                try:
                    async with websockets.connect(f"{LOCAL_WS}/api/market/ws/{channel}", max_size=None) as local:
                        async for data in local:
                            if channel == "trades" and '"trade_tick"' in data:
                                try:
                                    tid = json.loads(data).get("trade_id")
                                except Exception:
                                    tid = None
                                now = time.monotonic()
                                if tid is not None and now - last_sent.get(tid, 0) < gap:
                                    held[tid] = data          # send the latest one shortly
                                    continue
                                last_sent[tid] = now
                                held.pop(tid, None)
                            await send({"t": "stream", "ch": channel, "data": data})
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await asyncio.sleep(2)      # backend still starting, or its socket restarted
        finally:
            if flusher:
                flusher.cancel()

    async def _publish(self, send):
        """Push each dashboard dataset to the team site whenever it changes."""
        loop = asyncio.get_running_loop()
        last_hash, next_due = {}, {}
        idx = "NIFTY50"
        await asyncio.sleep(3)                  # let the backend finish starting
        while True:
            now = time.monotonic()
            for tmpl, every in PUSH:
                if next_due.get(tmpl, 0) > now:
                    continue
                next_due[tmpl] = now + every
                path = tmpl.format(idx=idx)
                try:
                    r = await loop.run_in_executor(None, lambda: requests.get(LOCAL_HTTP + path, timeout=15))
                except Exception:
                    continue
                if r.status_code != 200:
                    continue
                body = r.text
                if path == "/api/settings/":
                    try:
                        idx = json.loads(body).get("active_index") or idx
                    except Exception:
                        pass
                h = hash(body)
                if last_hash.get(path) == h:
                    continue
                last_hash[path] = h
                await send({"t": "data", "path": path, "body": body})
            await asyncio.sleep(0.5)


desk_relay = DeskRelay()
