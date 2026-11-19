"""desk_tunnel.py — Connect the backend to the hosted team site.

When the backend starts it opens a Cloudflare quick tunnel
(`cloudflared tunnel --url http://localhost:8000`), which gives this PC a
public https://<random>.trycloudflare.com address without any domain. That
address changes on every start, so it is registered with the team site
(Cloudflare Pages, /backend/register) and re-sent every HEARTBEAT_SEC. The
site looks it up and connects the team's browsers straight to this backend
(REST + live WebSockets). On shutdown the site is told the backend is offline.

Needs, in config.json:
    "desk_site": "https://<project>.pages.dev",
    "desk_register_secret": "<same value as the site's REGISTER_SECRET>"
and cloudflared installed. Without them this does nothing.

Quick tunnels have no uptime guarantee; if cloudflared exits it is restarted
and the new address re-registered.
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time

import requests

_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HEARTBEAT_SEC = 120
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
_CANDIDATES = [
    r"C:\Program Files (x86)\cloudflared\cloudflared.exe",
    r"C:\Program Files\cloudflared\cloudflared.exe",
]


def _config() -> dict:
    try:
        with open(os.path.join(_PROJECT, "config.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def desk_site() -> str:
    return (_config().get("desk_site") or "").rstrip("/")


class DeskTunnel:
    def __init__(self):
        self._proc = None
        self._running = False
        self.url = None
        self.last_registered = None
        self.error = ""

    def _exe(self):
        return shutil.which("cloudflared") or next((p for p in _CANDIDATES if os.path.exists(p)), None)

    def start(self):
        cfg = _config()
        if not cfg.get("desk_site") or not cfg.get("desk_register_secret"):
            print("[DESK] Team site not configured (desk_site / desk_register_secret in config.json) -- skipping")
            return
        if not self._exe():
            self.error = "cloudflared not installed"
            print("[DESK] cloudflared not installed -- team site can't reach this backend")
            return
        self._running = True
        threading.Thread(target=self._run, daemon=True, name="DeskTunnel").start()

    def stop(self):
        self._running = False
        self._register(online=False)
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()

    def status(self) -> dict:
        return {"site": desk_site() or None, "tunnel": self.url, "registered_at": self.last_registered,
                "error": self.error or None}

    def _register(self, online: bool = True) -> bool:
        cfg = _config()
        site, secret = (cfg.get("desk_site") or "").rstrip("/"), cfg.get("desk_register_secret")
        if not site or not secret or (online and not self.url):
            return False
        try:
            r = requests.post(f"{site}/backend/register", timeout=10,
                              headers={"X-Register-Secret": secret},
                              json={"url": self.url if online else None, "online": online})
            ok = r.status_code == 200
            if ok and online:
                self.last_registered = time.strftime("%H:%M:%S")
                self.error = ""
            elif not ok:
                self.error = f"register failed: HTTP {r.status_code}"
                print(f"[DESK] {self.error}")
            return ok
        except Exception as e:
            self.error = f"register failed: {e}"
            print(f"[DESK] {self.error}")
            return False

    def _run(self):
        while self._running:
            self.url = None
            try:
                self._proc = subprocess.Popen(
                    [self._exe(), "tunnel", "--no-autoupdate", "--url", "http://localhost:8000"],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            except Exception as e:
                self.error = f"cloudflared failed to start: {e}"
                print(f"[DESK] {self.error}")
                time.sleep(30)
                continue
            # cloudflared prints the assigned address on startup
            for line in self._proc.stdout:
                m = URL_RE.search(line)
                if m:
                    self.url = m.group(0)
                    print(f"[DESK] Tunnel up: {self.url} -> registering with {desk_site()}")
                    self._register()
                    threading.Thread(target=self._heartbeat, args=(self._proc,), daemon=True).start()
                    break
            # Drain output until cloudflared exits, then restart it
            for _ in self._proc.stdout:
                pass
            self._proc.wait()
            if self._running:
                print("[DESK] cloudflared exited -- restarting in 10s")
                time.sleep(10)

    def _heartbeat(self, proc):
        while self._running and proc.poll() is None:
            time.sleep(HEARTBEAT_SEC)
            if self._running and proc.poll() is None:
                self._register()


desk_tunnel = DeskTunnel()
