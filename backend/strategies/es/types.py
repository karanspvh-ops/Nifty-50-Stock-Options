"""types.py — ES dataclasses (ScalpCandidate, EarlyScalpPlan)."""

from dataclasses import dataclass, field
from typing import List


@dataclass
class ScalpCandidate:
    symbol:       str
    token:        str
    gap_pct:      float
    opening_move: float
    vol_ratio:    float
    oi_signal:    str
    oi_ce:        int
    oi_pe:        int
    direction:    str
    score:        float
    confirmed:    bool
    consec_1m:    int
    consec_3m:    int
    ltp:          float
    est_premium:  float
    entered:      bool = False
    skip_reason:  str  = ""


@dataclass
class EarlyScalpPlan:
    status:       str
    market_trend: str
    nifty_move:   float
    candidates:   List[ScalpCandidate] = field(default_factory=list)
    note:         str  = ""
    generated_at: str  = ""
