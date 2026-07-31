"""params.py — ES time gates, constants, and param load/save mixin."""

import os
import json
from datetime import time as dtime

# ── Time gates ────────────────────────────────────────────────────────────────
WARM_START    = dtime(9, 15)
SCAN_START    = dtime(9, 20)
ENTRY_START   = dtime(9, 25)
ENTRY_END     = dtime(9, 30)
SQUARE_OFF    = dtime(10, 30)
LOOP_SEC      = 10
CANDLE_REFREQ = 60
ES_TAG        = "[ES]"

# ── Params file ───────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
ES_PARAMS_PATH = os.path.join(_ROOT, "es_params.json")

_DEFAULT_PARAMS = {
    "gap_min_pct":        0.5,
    "move_min_pct":       1.0,
    "vol_ratio_min":      1.3,
    "max_positions":      5,
    "hard_sl_pct":        5.0,
    "target_pct":         12.0,
    "trail_activate_pct": 6.0,
    "trail_gap_pct":      5.0,
}


class ESParamsMixin:
    """Load, save, and access ES strategy parameters."""

    def _load_params(self) -> dict:
        p = dict(_DEFAULT_PARAMS)
        if os.path.exists(ES_PARAMS_PATH):
            try:
                p.update(json.load(open(ES_PARAMS_PATH)))
            except Exception:
                pass
        return p

    def _save_params(self):
        try:
            json.dump(self._params, open(ES_PARAMS_PATH, "w"), indent=2)
        except Exception as e:
            print(f"[ES] param save error: {e}")

    def get_params(self) -> dict:
        return dict(self._params)

    def set_params(self, updates: dict) -> dict:
        for k, v in updates.items():
            if k in _DEFAULT_PARAMS:
                self._params[k] = v
        self._save_params()
        print(f"[ES] params updated: {updates}")
        return self.get_params()

    def _p(self, key: str):
        return self._params.get(key, _DEFAULT_PARAMS.get(key))
