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
                        async with lock:
                            await ws.send(json.dumps(obj))

                    tasks = [asyncio.create_task(self._serve(ws, send))]
                    tasks += [asyncio.create_task(self._stream(ch, send)) for ch in CHANNELS]
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
        """Forward this backend's own live stream to the relay (one copy for all viewers)."""
        while True:
            try:
                async with websockets.connect(f"{LOCAL_WS}/api/market/ws/{channel}", max_size=None) as local:
                    async for data in local:
                        await send({"t": "stream", "ch": channel, "data": data})
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(2)      # backend still starting, or its socket restarted


desk_relay = DeskRelay()
