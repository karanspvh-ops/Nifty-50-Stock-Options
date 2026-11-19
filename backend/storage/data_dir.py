"""data_dir.py — Where the SQLite databases live: outside OneDrive.

OneDrive syncs the project folder, and a sync client can lock or replace a
SQLite file mid-write -- tolerable on paper, a corruption risk with live
money. The databases therefore live in DATA_DIR (default C:\\SPVH-Trading-Data,
override with the SPVH_DATA_DIR environment variable or "data_dir" in
config.json).

On the first start after this change, each database is copied there from the
project folder with SQLite's online-backup API (consistent, WAL included) and
the old file is renamed *.moved-out-of-onedrive so it can't be used by mistake.
"""

import json
import os
import sqlite3

_PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DIR = r"C:\SPVH-Trading-Data"


def _configured_dir() -> str:
    env = os.environ.get("SPVH_DATA_DIR")
    if env:
        return env
    try:
        with open(os.path.join(_PROJECT, "config.json"), encoding="utf-8") as f:
            return json.load(f).get("data_dir") or DEFAULT_DIR
    except Exception:
        return DEFAULT_DIR


DATA_DIR = _configured_dir()


def _mtime(path: str) -> float:
    return max((os.path.getmtime(path + s) for s in ("", "-wal") if os.path.exists(path + s)), default=0.0)


def db_path(name: str) -> str:
    os.makedirs(DATA_DIR, exist_ok=True)
    target = os.path.join(DATA_DIR, name)
    legacy = os.path.join(_PROJECT, name)
    # Copy when the project-folder file is the newer one: the first start after
    # this change, or a start while an older backend was still writing there.
    # Once the old file is renamed this never runs again.
    if os.path.exists(legacy) and (not os.path.exists(target) or _mtime(legacy) > _mtime(target)):
        src = sqlite3.connect(legacy)
        dst = sqlite3.connect(target)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print(f"[DATA] Moved {name} out of OneDrive -> {target}")
        for suffix in ("", "-wal", "-shm"):
            old = legacy + suffix
            if os.path.exists(old):
                try:
                    os.replace(old, old + ".moved-out-of-onedrive")
                except OSError as e:
                    print(f"[DATA] Could not rename old {old} ({e}); it is no longer used")
    return target
