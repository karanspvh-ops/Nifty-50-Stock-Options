"""types.py — OB dataclasses (PlanStock, TradePlan)."""

from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class PlanStock:
    symbol:        str
    token:         str
    opening_move:  float
    day_move:      float
    r_factor:      float
    ltp:           float
    est_premium:   float
    eligible:      bool
    sector:        str   = ""
    direction:     str   = "call"
    entered:       bool  = False
    confirmed:     bool  = False
    consec:        int   = 0
    vol_ratio:     float = 0.0
    vp_poc:        Optional[float] = None
    vp_vah:        Optional[float] = None
    vp_val:        Optional[float] = None


@dataclass
class TradePlan:
    status:        str
    trend:         str
    sector:        Optional[str]
    direction:     Optional[str]
    sector_pct:    float
    stocks:        List[PlanStock] = field(default_factory=list)
    note:          str = ""
    generated_at:  str = ""
