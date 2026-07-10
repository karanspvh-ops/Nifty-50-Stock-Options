"""params.py — OB time gates, constants, and param load/save mixin."""

import os
import json
from datetime import time as dtime

# ── Time gates ────────────────────────────────────────────────────────────────
SCAN_START          = dtime(9, 15)
EARLY_ENTRY_FROM    = dtime(9, 35)
PREVIEW_FROM        = dtime(9, 35)
SCAN_END            = dtime(9, 40)
ENTRY_DEADLINE      = dtime(9, 45)
ENTRY_HARD_DEADLINE = dtime(10, 30)
SQUARE_OFF          = dtime(15, 15)

# ── Strategy constants ────────────────────────────────────────────────────────
MOVE_MIN_PCT        = 1.5
MOVE_TARGET_PCT     = 2.0
MAX_POSITIONS       = 5
CANDIDATE_POOL      = 15
RFACTOR_MIN         = 0.8

MIN_SECTOR_MOVERS   = 2
SECTOR_MIN_PCT      = 0.5
CLARITY_NET         = 0.30
BREADTH_MIN_PCT     = 0.5

GAP_MIN_PCT         = 3.0
EARLY_SECTOR_MIN_PCT = 1.0

HARD_SL_PCT         = 10.0
TARGET_PCT          = 50.0
SECOND_TARGET_PCT   = 100.0
TRAIL_ACTIVATE_PCT  = 20.0
TRAIL_GAP_PCT       = 10.0

OB_TAG              = "[OB]"
LOOP_SEC            = 5

MIN_TRADES_TO_TUNE  = 15
RETUNE_EVERY        = 5

# ── Params file ───────────────────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
OB_PARAMS_PATH = os.path.join(_ROOT, "ob_params.json")

_DEFAULT_PARAMS = {
    "move_min_pct":       MOVE_MIN_PCT,
    "move_max_pct":       3.5,
    "ob_spike_vol":       6.0,
    "ob_spike_rfactor":   3.0,
    "ob_spike_move":      2.5,
    "vol_ratio_min":      1.5,
    "consec_max":         3,
    "max_positions":      MAX_POSITIONS,
    "hard_sl_pct":        HARD_SL_PCT,
    "target_pct":         TARGET_PCT,
    "second_target_pct":  SECOND_TARGET_PCT,
    "trail_activate_pct": TRAIL_ACTIVATE_PCT,
    "trail_gap_pct":      TRAIL_GAP_PCT,
    "auto_tune_enabled":  True,
}


class OBParamsMixin:
    """Load, save, and access OB strategy parameters."""

    def _load_params(self) -> dict:
        params = dict(_DEFAULT_PARAMS)
        if os.path.exists(OB_PARAMS_PATH):
            try:
                params.update(json.load(open(OB_PARAMS_PATH)))
            except Exception:
                pass
        return params

    def _save_params(self):
        try:
            json.dump(self._params, open(OB_PARAMS_PATH, "w"), indent=2)
        except Exception as e:
            print(f"[OB] param save error: {e}")

    def get_params(self) -> dict:
        return dict(self._params)

    def set_params(self, updates: dict) -> dict:
        allowed = set(_DEFAULT_PARAMS.keys())
        for k, v in updates.items():
            if k in allowed:
                self._params[k] = v
        self._save_params()
        print(f"[OB] params updated: {updates}")
        return self.get_params()

    def _p(self, key: str):
        return self._params.get(key, _DEFAULT_PARAMS.get(key))
