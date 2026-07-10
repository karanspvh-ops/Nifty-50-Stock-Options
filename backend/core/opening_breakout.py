"""
opening_breakout.py — Opening Breakout positional strategy.

Flow (timed to the market open):
  09:15 – 09:40  SCAN     : track each stock's move from the 09:15 open,
                            aggregate sectors, determine overall trend.
  09:35 – 09:40  PREVIEW  : publish a TRADE PLAN (chosen sector, direction,
                            top-3 stocks) a few minutes before entry, so it
                            can be reviewed manually.
  09:40 – 09:45  ENTER    : for each planned stock that has already moved
                            >= 1.5%, buy the ATM option (CALL if bullish
                            sector / PUT if bearish). Up to 3 positions in
                            the same sector. One lot each.
  after entry    MANAGE   : positional hold. Hard SL 10%, trail the stop as
                            price climbs, target +50% (single lot = full
                            exit; multi-lot = sell half at +50%, trail rest
                            to +100%). No trend-based early exit.

Direction is single: only CALLs (bullish day) OR only PUTs (bearish day),
chosen from the overall market trend.

Trades are tagged with "[OB]" in entry_logic so the generic risk engine
leaves them alone — this engine manages them.
"""

import os, sys, threading, time
from dataclasses import dataclass, field, asdict
from datetime import datetime, time as dtime
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state     import market
from backend.core.stock_universe   import get_meta, get_stocks_in_sector, get_token, get_stocks_for_index
from backend.core.settings_manager import is_live_mode, get_active_index
from backend.core.session_manager  import get_or_create_session, check_portfolio_sl
from backend.core.order_executor   import place_entry_order
from backend.core.risk_engine      import risk_engine
from backend.database import (
    Session as DBSession, Trade, TradeStatus, TradeEnv, SessionStatus,
)
from backend.core.session_manager  import update_session_status, set_selected_stocks

# ── Strategy parameters ───────────────────────────────────────────────────────
SCAN_START      = dtime(9, 15)
EARLY_ENTRY_FROM = dtime(9, 35)    # gap-accelerated entries may start here
PREVIEW_FROM    = dtime(9, 35)
SCAN_END        = dtime(9, 40)     # finalize sector/direction (25 min)
ENTRY_DEADLINE  = dtime(9, 45)     # primary entry deadline (30 min)
# If NO trade fired by 9:45, keep hunting for one good trade until 10:30.
# (If at least one trade was already taken by 9:45, we do NOT extend.)
ENTRY_HARD_DEADLINE = dtime(10, 30)
SQUARE_OFF      = dtime(15, 15)

MOVE_MIN_PCT        = 1.5          # opening breakout trigger
MOVE_TARGET_PCT     = 2.0          # preferred move
MAX_POSITIONS       = 5
CANDIDATE_POOL      = 15           # evaluate confirmation on the top-N; enter the first MAX_POSITIONS that confirm
RFACTOR_MIN         = 0.8

# Sector qualification (fixes the marginal-breadth flip + thin single-stock sectors)
MIN_SECTOR_MOVERS   = 2            # a sector needs >=2 stocks that broke out >=1.5%
SECTOR_MIN_PCT      = 0.5          # and a meaningful average move (ignore noise)

# Smart direction: only lock to one side when market breadth is decisive.
# net breadth = (#up - #down) / (#up + #down) of stocks moving >=0.5%.
#   >= +CLARITY_NET → bullish (calls only) ; <= -CLARITY_NET → bearish (puts only)
#   otherwise → MIXED: take the best breakouts on EITHER side.
CLARITY_NET         = 0.30
BREADTH_MIN_PCT     = 0.5          # |move| to count a stock as up/down

# ── Gap-acceleration fast-track ───────────────────────────────────────────────
# A strong gap with confirming sector momentum may enter ~5 min early (9:35)
# instead of waiting for the 9:40 finalize.
GAP_MIN_PCT          = 3.0         # |today open vs prev close| gap
EARLY_SECTOR_MIN_PCT = 1.0         # chosen sector must show real momentum

HARD_SL_PCT         = 10.0         # option premium hard stop
TARGET_PCT          = 50.0         # single-lot full-exit target
SECOND_TARGET_PCT   = 100.0        # multi-lot runner target
TRAIL_ACTIVATE_PCT  = 20.0         # start trailing once +20%
TRAIL_GAP_PCT       = 10.0         # trail 10% off the peak (locks ≥10% profit once armed)

OB_TAG = "[OB]"
LOOP_SEC = 5

# ── Tunable parameters (persisted; editable from UI; auto-tuned by ML) ─────────
import json
_PARAMS_PATH = os.path.join(ROOT, "ob_params.json")
_DEFAULT_PARAMS = {
    "move_min_pct":       MOVE_MIN_PCT,
    "move_max_pct":       3.5,     # skip stocks that moved >3.5% — move already extended
    "ob_spike_vol":       6.0,     # refill override: vol_ratio >= 6x qualifies as spike
    "ob_spike_rfactor":   3.0,     # refill override: R-factor >= 3.0 (stock massively outrunning sector)
    "ob_spike_move":      2.5,     # refill override: move >= 2.5% required alongside high R-factor
    "vol_ratio_min":      1.5,     # floor only (not a hard ceiling) — 1.5x = some momentum exists;
                                   # high-vol spikes still rank first via scoring, but sector-drift
                                   # trades (e.g. broad AUTO rally at 0.2x) are no longer blocked.
                                   # Hard liquidity guards (spread, OI, recent vol) live in order_executor.
    "consec_max":         3,       # skip if 4+ consecutive candles — entering at exhaustion
    "max_positions":      MAX_POSITIONS,
    "hard_sl_pct":        HARD_SL_PCT,
    "target_pct":         TARGET_PCT,
    "second_target_pct":  SECOND_TARGET_PCT,
    "trail_activate_pct": TRAIL_ACTIVATE_PCT,
    "trail_gap_pct":      TRAIL_GAP_PCT,
    "auto_tune_enabled":  True,    # ML may adjust trail_gap once >=50 trades
}
MIN_TRADES_TO_TUNE = 15            # start auto-tuning after a modest sample (acts sooner)
RETUNE_EVERY       = 5             # then re-tune every 5 new completed trades


@dataclass
class PlanStock:
    symbol:        str
    token:         str
    opening_move:  float          # % from 09:15 open
    day_move:      float          # % from prev close
    r_factor:      float
    ltp:           float
    est_premium:   float
    eligible:      bool           # has it moved >= MOVE_MIN_PCT?
    sector:        str  = ""      # the stock's own sector (may differ from lead sector)
    direction:     str  = "call"  # this stock's own side (call/put) — set per stock
    entered:       bool = False
    # Breakout confirmation (candle momentum + volume + Volume Profile)
    confirmed:     bool = False
    consec:        int  = 0
    vol_ratio:     float = 0.0
    vp_poc:        Optional[float] = None
    vp_vah:        Optional[float] = None
    vp_val:        Optional[float] = None


@dataclass
class TradePlan:
    status:        str            # "scanning" | "preview" | "final" | "entering" | "done"
    trend:         str            # "bullish" | "bearish" | "neutral"
    sector:        Optional[str]
    direction:     Optional[str]  # "call" | "put"
    sector_pct:    float
    stocks:        List[PlanStock] = field(default_factory=list)
    note:          str = ""
    generated_at:  str = ""


class OpeningBreakout:
    def __init__(self):
        self._running   = False
        self._thread:   Optional[threading.Thread] = None
        self._enabled   = True
        self._today     = None
        self._open_ref: Dict[str, float] = {}      # token -> 09:15 open price
        self._open_ref_final: set = set()          # tokens whose ref is the true 09:15 candle open
        self._plan:     Optional[TradePlan] = None
        self._phase     = "IDLE"
        self._params    = self._load_params()
        self._last_tune: Optional[str] = None      # date of last auto-tune
        self._tune_report: dict = {}

    # ── Parameter store ───────────────────────────────────────────────────────
    def _load_params(self) -> dict:
        params = dict(_DEFAULT_PARAMS)
        if os.path.exists(_PARAMS_PATH):
            try:
                params.update(json.load(open(_PARAMS_PATH)))
            except Exception:
                pass
        return params

    def _save_params(self):
        try:
            json.dump(self._params, open(_PARAMS_PATH, "w"), indent=2)
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

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="OpeningBreakout")
        self._thread.start()
        print("[OB] Opening Breakout strategy started.")

    def stop(self):
        self._running = False

    def set_enabled(self, on: bool):
        self._enabled = on

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[OB] tick error: {e}")
            time.sleep(LOOP_SEC)

    # ── Per-tick state machine ────────────────────────────────────────────────
    def _tick(self):
        now = datetime.now()
        t   = now.time()

        # daily reset
        if self._today != now.date():
            self._today = now.date()
            self._open_ref.clear()
            self._open_ref_final.clear()
            self._plan  = None
            self._phase = "IDLE"

        if not self._enabled or not market.is_feed_connected():
            return

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER

        # Portfolio kill-switch still applies
        if check_portfolio_sl(env):
            self._phase = "KILLED"
            return

        # Square off
        if t >= SQUARE_OFF:
            if self._phase != "DONE":
                self._square_off(env)
                self._phase = "DONE"
            return

        # Pre-open
        if t < SCAN_START:
            self._phase = "IDLE"
            return

        # ── Capture 09:15 opening reference prices ────────────────────────────
        if SCAN_START <= t <= ENTRY_HARD_DEADLINE:
            self._capture_open_refs()

        # ── SCAN / PREVIEW / FINALIZE ─────────────────────────────────────────
        if t < SCAN_END:
            self._phase = "SCANNING" if t < PREVIEW_FROM else "PREVIEW"
            self._plan  = self._build_plan(status="scanning" if t < PREVIEW_FROM else "preview")
            # Gap-acceleration: high-conviction gap setups may enter ~5 min early
            if t >= EARLY_ENTRY_FROM and self._plan and self._plan.sector:
                self._enter_positions(env, early_only=True)
            return

        # finalize plan at 09:40 — and keep re-evaluating while the entry window
        # is still open if we don't yet have a qualifying sector. This handles a
        # late feed/sector warm-up or a mid-session restart after 09:40, where the
        # first finalize tick can otherwise lock in an empty "no data" plan for
        # the whole day.
        need_plan = (
            self._plan is None
            or self._plan.status in ("scanning", "preview")
            or (t <= ENTRY_HARD_DEADLINE
                and not (self._plan.sector and self._plan.stocks))
        )
        if need_plan:
            self._plan = self._build_plan(status="final")
            self._phase = "PLANNED"
            if self._plan and self._plan.sector:
                set_selected_stocks(env, self._plan.sector,
                                    [s.symbol for s in self._plan.stocks])
                print(f"[OB] PLAN FINAL | {self._plan.trend.upper()} | sector {self._plan.sector} "
                      f"| {self._plan.direction} | stocks: {[s.symbol for s in self._plan.stocks]}")

        # ── ENTER (primary 9:40–9:45; extended in 5-min steps to 10:00 if a
        #    slot is still open and no good trade has been taken yet) ──────────
        open_ob   = self._count_open_ob(env)
        max_pos   = int(self._p("max_positions"))
        in_primary  = t <= ENTRY_DEADLINE
        # Extend only if NOTHING fired by 9:45 — then hunt one trade until 10:30.
        # Guard: if any OB trade was already taken today (open OR closed), the
        # extended hunt must NOT re-activate just because all positions exited.
        ever_traded = self._count_ob_today(env) > 0
        in_extended = (ENTRY_DEADLINE < t <= ENTRY_HARD_DEADLINE) and open_ob == 0 and not ever_traded
        if (in_primary or in_extended) and self._plan and self._plan.sector:
            self._plan.status = "entering"
            self._phase = "ENTERING_EXT" if in_extended else "ENTERING"
            self._enter_positions(env)

        # ── MANAGE open OB trades ─────────────────────────────────────────────
        self._manage(env)
        if self._phase not in ("ENTERING", "ENTERING_EXT"):
            self._phase = "MANAGING"

    # ── Opening reference capture ─────────────────────────────────────────────
    def _capture_open_refs(self):
        """Lock each stock's 09:15 session-open as the reference for opening-move
        %. Prefer the TRUE 09:15 candle open from history (so a mid-session
        restart still measures from 09:15, since candles are backfilled from the
        open); fall back to the first live price seen only if no candle exists."""
        today = datetime.now().date()
        moves = market.get_all_stock_moves()
        for token, mv in moves.items():
            if token in self._open_ref_final:
                continue
            ref = self._session_open_from_candles(token, today)
            if ref is not None:
                # True 09:15 open found — lock it in (upgrading any provisional ref).
                self._open_ref[token] = ref
                self._open_ref_final.add(token)
            elif token not in self._open_ref and mv.get("ltp"):
                # No candle yet (backfill still loading) — provisional ref so the
                # stock isn't ignored; it gets upgraded once its 09:15 candle lands.
                self._open_ref[token] = mv["ltp"]

    def _session_open_from_candles(self, token: str, day) -> Optional[float]:
        """Return the open of the first 5-min candle at/after 09:15 today, or
        None if no such candle is in memory yet."""
        for c in market.get_candles(token, n=260):
            ts = c.get("ts")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if dt.date() == day and dt.time() >= SCAN_START:
                return c.get("open")
        return None

    def _opening_move(self, token: str, ltp: float) -> float:
        ref = self._open_ref.get(token)
        if not ref:
            return 0.0
        return round((ltp - ref) / ref * 100, 2)

    # ── Plan builder (also used by the API for on-demand preview) ─────────────
    def _build_plan(self, status: str) -> Optional[TradePlan]:
        sector_moves = market.get_sector_moves()
        if not sector_moves:
            return TradePlan(status=status, trend="neutral", sector=None,
                             direction=None, sector_pct=0.0,
                             note="Waiting for market data…",
                             generated_at=datetime.now().isoformat())

        # ── SMART DIRECTION: only lock a side when breadth is decisive ───────
        ups = downs = 0
        for d in sector_moves.values():
            for s in d.get("stocks", []):
                m = s.get("pct_change", 0)
                if   m >=  BREADTH_MIN_PCT: ups += 1
                elif m <= -BREADTH_MIN_PCT: downs += 1
        total_breadth = ups + downs
        net = (ups - downs) / total_breadth if total_breadth else 0.0
        if   net >=  CLARITY_NET: market_trend, allowed = "bullish", {"call"}
        elif net <= -CLARITY_NET: market_trend, allowed = "bearish", {"put"}
        else:                     market_trend, allowed = "mixed",   {"call", "put"}

        # ── Qualifying sectors (>=MIN_SECTOR_MOVERS breakout movers), in the
        #    allowed direction(s). Lead sector = strongest absolute (display). ──
        qual = {}
        for sec, d in sector_moves.items():
            sp = d.get("pct_change", 0)
            if abs(sp) < SECTOR_MIN_PCT:
                continue
            sdir = "call" if sp > 0 else "put"
            if sdir not in allowed:
                continue
            movers = sum(
                1 for s in d.get("stocks", [])
                if (sdir == "call" and s.get("pct_change", 0) >=  MOVE_MIN_PCT)
                or (sdir == "put"  and s.get("pct_change", 0) <= -MOVE_MIN_PCT)
            )
            if movers >= MIN_SECTOR_MOVERS:
                qual[sec] = (sp, sdir)

        if not qual:
            return TradePlan(status=status, trend=market_trend, sector=None, direction=None,
                             sector_pct=0.0,
                             note=(f"Market {market_trend} (breadth {net:+.0%}); no qualifying "
                                   f"sector with ≥{MIN_SECTOR_MOVERS} breakout movers. Sitting out."),
                             generated_at=datetime.now().isoformat())

        sector     = max(qual, key=lambda k: abs(qual[k][0]))
        sector_pct = sector_moves[sector].get("pct_change", 0)
        trend      = market_trend
        direction  = qual[sector][1]            # lead direction (for the banner)

        # ── Candidates: scan ALL F&O stocks; each takes its OWN side (call/put),
        #    restricted to the allowed direction(s) for the day. ───────────────
        candidates: List[PlanStock] = []
        for s in get_stocks_for_index("FNO", fno_only=True):
            token  = s["token"]
            stock_sector = s.get("sector", "")
            if stock_sector not in qual:
                continue
            mv  = market.get_stock_move(token)
            if not mv:
                continue
            ltp = mv.get("ltp") or 0
            day_move = mv.get("pct_change", 0)
            sdir = "call" if day_move > 0 else "put"
            if sdir not in allowed:
                continue
            opening_move = self._opening_move(token, ltp)
            move = opening_move if self._open_ref.get(token) else day_move
            if abs(move) < self._p("move_min_pct"):
                continue
            own_sec_avg = abs(sector_moves.get(stock_sector, {}).get("pct_change", 0)) or 0.1
            r_factor = round(abs(day_move) / own_sec_avg, 2)
            if r_factor < RFACTOR_MIN:
                continue
            est_premium = round(ltp * 0.015, 2)
            candidates.append(PlanStock(
                symbol=s["symbol"], token=token, sector=stock_sector, direction=sdir,
                opening_move=move, day_move=day_move,
                r_factor=r_factor, ltp=ltp, est_premium=est_premium,
                eligible=True,
            ))

        # Rank by sector strength × individual move: stocks from the leading sector
        # come first regardless of R-factor, so we don't pick stocks outperforming
        # a flat sector over stocks riding the actual momentum wave of the day.
        sector_strength = {sec: abs(d.get("pct_change", 0)) for sec, d in sector_moves.items()}
        candidates.sort(
            key=lambda c: sector_strength.get(c.sector, 0) * abs(c.opening_move),
            reverse=True
        )
        top = candidates[:CANDIDATE_POOL]

        # Attach breakout confirmation (momentum + volume + VP levels) to top picks
        from backend.core.breakout_confirm import confirm_breakout
        for c in top:
            try:
                bo = confirm_breakout(c.token, c.direction, move_pct=c.opening_move)
                c.confirmed = bo.confirmed
                c.consec    = bo.consec
                c.vol_ratio = bo.vol_ratio
                c.vp_poc    = bo.vp50.get("poc")
                c.vp_vah    = bo.vp50.get("vah")
                c.vp_val    = bo.vp50.get("val")
            except Exception:
                pass

        # Re-rank within top picks using vol_ratio as a signal-strength multiplier.
        # High vol_ratio (spike/concentrated buying) floats to the top of the entry queue;
        # low vol_ratio (sector drift, thin volume) still enters but goes last in the 3 slots.
        # Formula: score × clamp(vol_ratio/3, 0.5, 2.0)
        #   vol 6x → 2.0× weight  (spike — prioritise)
        #   vol 3x → 1.0× weight  (normal)
        #   vol 1.5x → 0.5× weight (drift — enters if slots remain)
        #   vol 0.1x → 0.5× weight (floor — still allowed; hard guards in order_executor)
        top.sort(
            key=lambda c: (
                sector_strength.get(c.sector, 0)
                * abs(c.opening_move)
                * max(0.5, min(2.0, (c.vol_ratio or 0) / 3.0))
            ),
            reverse=True
        )

        secs = ", ".join(sorted({c.sector for c in top}))
        note = (f"Lead sector {sector}; picks scanned across ALL sectors ({secs}). "
                f"Entry needs ≥{self._p('move_min_pct')}% move + (momentum OR sharp "
                f"high-volume thrust) + Volume-Profile value-area break.")
        return TradePlan(
            status=status, trend=trend, sector=sector, direction=direction,
            sector_pct=sector_pct, stocks=top, note=note,
            generated_at=datetime.now().isoformat(),
        )

    # ── Entry-time re-validation (refill guard) ───────────────────────────────
    def _revalidate_ob(self, ps: "PlanStock", current_move: float) -> "tuple[bool, str]":
        """Re-validate an OB candidate at the moment of entry.

        The plan is built at 09:35-09:40. For refill slots that fire at 09:50+
        the market may have partially reversed. Three checks catch that:

        1. Move retention  — current spot move must be >=60% of what the plan
           recorded. If a stock broke +3% at 09:40 and is now +0.8%, the
           breakout thesis has collapsed even though it still clears move_min.

        2. Immediate candle — the most recent candle must still be in the trade
           direction. One red candle in an uptrend = don't enter right now.

        3. Multi-candle reversal — if 2 of the last 3 candles are against the
           trade direction, momentum has shifted. A single noisy candle is
           allowed; a majority says the trend is gone.
        """
        # 1. Move retention
        if ps.opening_move:
            need = abs(ps.opening_move) * 0.6
            if abs(current_move) < need:
                return False, (f"move faded {current_move:+.2f}% vs plan {ps.opening_move:+.2f}% "
                               f"(need >={need:.1f}%, 60% retention)")

        # 2+3. Candle direction checks
        try:
            candles = market.get_candles(ps.token, n=3)
            if candles:
                # Most recent candle must be in direction (immediate signal)
                lc        = candles[-1]
                last_bull = lc.get("close", 0) >= lc.get("open", 0)
                if ps.direction == "call" and not last_bull:
                    return False, "last candle RED — immediate reversal, skip"
                if ps.direction == "put" and last_bull:
                    return False, "last candle GREEN — immediate reversal, skip"

                # Majority: if both prior candles are against direction = trend gone.
                # Exclude candles[-1] — it was already checked individually above.
                if len(candles) >= 2:
                    against = sum(
                        1 for c in candles[:-1]
                        if (ps.direction == "call" and c.get("close", 0) < c.get("open", 0))
                        or (ps.direction == "put"  and c.get("close", 0) > c.get("open", 0))
                    )
                    if against >= 2:
                        return False, f"{against}/2 prior candles reversed — momentum gone"
        except Exception as e:
            print(f"[OB] {ps.symbol} reval candle error: {e}")

        return True, f"reval pass — move {current_move:+.2f}% retained"

    # ── Gap-acceleration qualifier ────────────────────────────────────────────
    def _gap_qualifies(self, ps) -> bool:
        """True if the stock gapped > 3% from prev close AND the chosen sector
        shows strong momentum — allows entry ~5 min before the normal window."""
        tick      = market.get_tick(ps.token)
        open_ref  = self._open_ref.get(ps.token)
        prev_close = tick.get("prev_close") if tick else None
        if not prev_close or not open_ref:
            return False
        gap_pct = abs((open_ref - prev_close) / prev_close * 100)
        own_sec_pct = market.get_sector_moves().get(ps.sector, {}).get("pct_change", 0)
        sector_strong = abs(own_sec_pct) >= EARLY_SECTOR_MIN_PCT
        return gap_pct >= GAP_MIN_PCT and sector_strong

    # ── Entry ─────────────────────────────────────────────────────────────────
    def _enter_positions(self, env: TradeEnv, early_only: bool = False):
        if not self._plan:
            return
        open_ob = self._count_open_ob(env)
        session = get_or_create_session(env)
        max_pos = int(self._p("max_positions"))

        # Pre-compute refill context once (avoids repeated DB hits per candidate).
        # total_ob_today >= max_pos means we are filling a REFILL slot (not initial).
        total_ob_today = self._count_ob_today(env)
        net_ob_pnl     = self._ob_net_pnl_today(env) if total_ob_today >= max_pos else 0.0

        for ps in self._plan.stocks:
            if open_ob >= max_pos:
                break
            if ps.entered or self._already_in(env, ps.symbol):
                continue
            # Must have moved enough to trigger the breakout entry
            mv  = market.get_stock_move(ps.token)
            ltp = mv.get("ltp") if mv else None
            if not ltp:
                continue
            move = self._opening_move(ps.token, ltp) if self._open_ref.get(ps.token) else ps.day_move
            if abs(move) < self._p("move_min_pct"):
                continue
            if abs(move) > float(self._p("move_max_pct")):
                print(f"[OB] {ps.symbol} skipped — move {move:+.2f}% > max {self._p('move_max_pct')}% (exhausted)")
                continue
            # Early window (9:35–9:40): only high-conviction gap setups qualify
            early = False
            if early_only:
                if not self._gap_qualifies(ps):
                    continue
                early = True
            if market.is_trading_halted():
                break

            # ── Breakout confirmation: candle momentum + volume + Volume Profile
            from backend.core.breakout_confirm import confirm_breakout
            bo = confirm_breakout(ps.token, ps.direction, move_pct=move)
            if not bo.confirmed:
                print(f"[OB] {ps.symbol} not confirmed — {bo.reason}")
                continue
            vol_floor = float(self._p("vol_ratio_min"))  # 1.5 — floor, not a ceiling
            if bo.vol_ratio < vol_floor:
                print(f"[OB] {ps.symbol} SKIP — vol {bo.vol_ratio:.1f}x below floor {vol_floor}x (no momentum signal)")
                continue
            if bo.vol_ratio < 3.0:
                print(f"[OB] {ps.symbol} LOW VOL {bo.vol_ratio:.1f}x — sector drift trade, liquidity check in executor")
            if bo.consec > int(self._p("consec_max")):
                print(f"[OB] {ps.symbol} skipped — {bo.consec} consec candles > max {self._p('consec_max')} (exhausted)")
                continue

            # Refill guard: if the initial MAX_POSITIONS slots are done and day OB
            # net PnL is already positive, protect those gains — only allow a refill
            # for extreme-conviction setups (vol spike OR very high R-factor + strong move).
            # Net is calculated from CLOSED trades only; open positions don't count.
            if total_ob_today >= max_pos and net_ob_pnl > 0:
                if not self._is_extreme_conviction(ps, bo, move):
                    print(f"[OB] {ps.symbol} REFILL BLOCK — "
                          f"day net +Rs.{net_ob_pnl:,.0f} after {total_ob_today} trades, "
                          f"vol {bo.vol_ratio:.1f}x / R {ps.r_factor:.1f} not spike-level")
                    continue
                print(f"[OB] {ps.symbol} EXTREME CONVICTION REFILL — "
                      f"vol {bo.vol_ratio:.1f}x / R {ps.r_factor:.1f} / move {move:+.2f}% "
                      f"(day net +Rs.{net_ob_pnl:,.0f})")

            # Entry-time re-validation: move retention + multi-candle reversal check.
            # Catches plan candidates whose thesis has faded or reversed since 09:40.
            ok, reval_why = self._revalidate_ob(ps, move)
            if not ok:
                print(f"[OB] {ps.symbol} REVAL SKIP — {reval_why}")
                continue

            tag = "GAP-ACCEL early entry" if early else "Opening breakout"
            reason = (f"{OB_TAG} {tag} | market {self._plan.trend} | {ps.symbol} ({ps.sector}) "
                      f"moved {move:+.2f}% from open | R-factor {ps.r_factor} "
                      f"| ATM {ps.direction.upper()} | CONFIRM: {bo.reason}")
            trade = place_entry_order(
                env=env, symbol=ps.symbol, token=ps.token,
                direction=ps.direction, session_id=session["id"],
                entry_logic=reason,
                indicators={"opening_move": move, "r_factor": ps.r_factor,
                            "consec_candles": bo.consec, "vol_ratio": bo.vol_ratio,
                            "vp20": bo.vp20, "vp50": bo.vp50},
                sl_pct_override=self._p("hard_sl_pct"), target_pct_override=self._p("target_pct"),
                max_positions=int(self._p("max_positions")),
            )
            if trade:
                ps.entered = True
                open_ob += 1
                update_session_status(env, SessionStatus.ACTIVE)
                print(f"[OB] ENTERED {ps.symbol} {self._plan.direction.upper()} @ "
                      f"{trade.entry_price:.2f} | SL 10% target 50%")

    # ── Management (positional, trailing) ─────────────────────────────────────
    def _manage(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob_trades = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()

        from backend.core.order_executor import current_premium
        for t in ob_trades:
            # Track the OPTION premium, NOT the underlying stock price.
            ltp = current_premium(t)
            if not ltp:
                continue
            entry   = t.entry_price
            pnl_pct = (ltp - entry) / entry * 100

            # update peak — floor at entry_price so highest_price is always >= entry
            peak_candidate = max(ltp, entry)
            if t.highest_price is None or peak_candidate > t.highest_price:
                self._set_highest(t.id, peak_candidate)
                t.highest_price = peak_candidate

            hard_sl_pct = self._p("hard_sl_pct")
            target_pct  = self._p("target_pct")
            second_pct  = self._p("second_target_pct")
            activate_pct= self._p("trail_activate_pct")
            gap_pct     = self._p("trail_gap_pct")

            hard_sl = entry * (1 - hard_sl_pct / 100)
            peak    = t.highest_price or ltp
            peak_pct = (peak - entry) / entry * 100

            # trailing stop becomes active after the activation threshold
            trail_stop = hard_sl
            if peak_pct >= activate_pct:
                trail_stop = max(hard_sl, peak * (1 - gap_pct / 100))
                # Persist trail SL to DB for UI visibility
                new_dsl = round(trail_stop, 2)
                if t.dynamic_sl_price != new_dsl:
                    with DBSession() as db:
                        db.query(Trade).filter(Trade.id == t.id).update(
                            {"dynamic_sl_price": new_dsl}, synchronize_session=False)
                        db.commit()

            # Target (single lot → full exit)
            if pnl_pct >= target_pct and t.quantity <= 1:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Target +{target_pct:.0f}% hit")
                continue
            # Multi-lot: runner trails to second target
            if pnl_pct >= second_pct:
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Runner target +{second_pct:.0f}% hit")
                continue

            # Stop / trail exit
            if ltp <= trail_stop:
                lock = (trail_stop - entry) / entry * 100
                if trail_stop > hard_sl:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Trailing stop hit (locked {lock:+.1f}%)")
                else:
                    risk_engine.force_exit_trade(t.id, f"{OB_TAG} Hard SL -{hard_sl_pct:.0f}% hit")

    def _square_off(self, env: TradeEnv):
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.status == TradeStatus.OPEN, Trade.env == env)
                .all()
            )
            ob = [t for t in trades if (t.entry_logic or "").startswith(OB_TAG)]
        finally:
            db.close()
        for t in ob:
            risk_engine.force_exit_trade(t.id, f"{OB_TAG} Square-off 3:15 PM")

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _count_open_ob(self, env: TradeEnv) -> int:
        db = DBSession()
        try:
            trades = db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env).all()
            return sum(1 for t in trades if (t.entry_logic or "").startswith(OB_TAG))
        finally:
            db.close()

    def _count_ob_today(self, env: TradeEnv) -> int:
        """Count ALL OB trades entered today (open or closed) to guard the extended hunt."""
        from datetime import date, datetime as _dt
        d = date.today()
        from_dt = _dt(d.year, d.month, d.day, 0, 0, 0)
        db = DBSession()
        try:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{OB_TAG}%"),
                Trade.entered_at >= from_dt,
            ).count()
        finally:
            db.close()

    def _ob_net_pnl_today(self, env: TradeEnv) -> float:
        """Net PnL of all CLOSED OB trades today (open positions excluded)."""
        from datetime import date, datetime as _dt
        d = date.today()
        from_dt = _dt(d.year, d.month, d.day, 0, 0, 0)
        db = DBSession()
        try:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{OB_TAG}%"),
                Trade.entered_at >= from_dt,
                Trade.status != TradeStatus.OPEN,
            ).all()
            return sum((t.pnl or 0) for t in trades)
        finally:
            db.close()

    def _is_extreme_conviction(self, ps: "PlanStock", bo: "BreakoutSignal",
                                current_move: float) -> bool:
        """True when the setup is an outlier that justifies a refill even on a
        profitable day — either a concentrated-volume spike OR a stock massively
        outperforming/underperforming its sector with strong price action.

        Examples that qualify:
          - PAGEIND (+2.2% move, vol x6.0)          → vol spike
          - VEDL (R-factor 3.4, -2.9% counter-trend) → high R-factor + strong move
        """
        spike_vol  = float(self._p("ob_spike_vol"))
        spike_rf   = float(self._p("ob_spike_rfactor"))
        spike_move = float(self._p("ob_spike_move"))
        vol_spike  = bo.vol_ratio >= spike_vol
        rf_spike   = ps.r_factor >= spike_rf and abs(current_move) >= spike_move
        return vol_spike or rf_spike

    def _already_in(self, env: TradeEnv, symbol: str) -> bool:
        db = DBSession()
        try:
            return db.query(Trade).filter(
                Trade.status == TradeStatus.OPEN, Trade.env == env,
                Trade.symbol == symbol).count() > 0
        finally:
            db.close()

    def _set_highest(self, trade_id: int, price: float):
        db = DBSession()
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                t.highest_price = price
                db.commit()
        finally:
            db.close()

    # ── ML auto-tuning of the trailing gap (after >= 50 trades) ───────────────
    def analyze_and_tune(self, env: TradeEnv = TradeEnv.PAPER) -> dict:
        """
        Once there are >= 50 completed OB trades, study how the trailing stop
        behaved and recommend (and optionally apply) a better trailing gap.

        Logic:
          - For trades that exited via the trailing stop, measure "give-back"
            = peak_profit% - exit_profit%. A large average give-back means the
            gap is too loose (we surrender too much) -> tighten it.
          - If trades trail out very early (low peak) and win-rate is poor,
            the gap is too tight -> widen it.
          - Bounded to [8%, 35%]; only nudges a few points at a time.
        """
        db = DBSession()
        try:
            trades = (
                db.query(Trade)
                .filter(Trade.env == env, Trade.status != TradeStatus.OPEN,
                        Trade.entry_logic.like(f"{OB_TAG}%"))
                .all()
            )
        finally:
            db.close()

        n = len(trades)
        if n < MIN_TRADES_TO_TUNE:
            return {"status": "insufficient_data", "trades": n,
                    "required": MIN_TRADES_TO_TUNE,
                    "message": f"Need {MIN_TRADES_TO_TUNE} completed trades to auto-tune "
                               f"(have {n}). Trailing gap stays at {self._p('trail_gap_pct')}%."}

        wins   = [t for t in trades if (t.pnl or 0) > 0]
        win_rate = round(len(wins) / n * 100, 1)
        avg_pnl  = round(sum(t.pnl or 0 for t in trades) / n, 2)

        givebacks, peaks = [], []
        trail_exits = 0
        for t in trades:
            if not t.entry_price or not t.highest_price:
                continue
            peak_pct = (t.highest_price - t.entry_price) / t.entry_price * 100
            peaks.append(peak_pct)
            if t.exit_logic and "Trailing stop" in t.exit_logic:
                trail_exits += 1
                givebacks.append(peak_pct - (t.pnl_pct or 0))

        cur_gap   = self._p("trail_gap_pct")
        avg_give  = round(sum(givebacks) / len(givebacks), 1) if givebacks else 0
        avg_peak  = round(sum(peaks) / len(peaks), 1) if peaks else 0

        # ── Decide adjustment ─────────────────────────────────────────────────
        new_gap = cur_gap
        rationale = ""
        if givebacks and avg_give > cur_gap * 1.25:
            new_gap = max(8.0, round(cur_gap - 3, 1))
            rationale = (f"Average give-back {avg_give}% exceeds the current gap "
                         f"{cur_gap}% — trail is too loose, tightening to {new_gap}%.")
        elif avg_peak and avg_peak < 25 and win_rate < 45:
            new_gap = min(35.0, round(cur_gap + 3, 1))
            rationale = (f"Trades peak low (avg {avg_peak}%) with a {win_rate}% win rate — "
                         f"trail is too tight, widening to {new_gap}% to let winners run.")
        else:
            rationale = (f"Current gap {cur_gap}% looks balanced "
                         f"(avg give-back {avg_give}%, avg peak {avg_peak}%). No change.")

        applied = False
        if self._p("auto_tune_enabled") and new_gap != cur_gap:
            self.set_params({"trail_gap_pct": new_gap})
            applied = True

        report = {
            "status":        "tuned" if applied else "analyzed",
            "trades":        n,
            "win_rate":      win_rate,
            "avg_pnl":       avg_pnl,
            "avg_peak_pct":  avg_peak,
            "avg_giveback_pct": avg_give,
            "trail_exits":   trail_exits,
            "current_gap":   cur_gap,
            "recommended_gap": new_gap,
            "applied":       applied,
            "auto_tune_enabled": self._p("auto_tune_enabled"),
            "rationale":     rationale,
            "generated_at":  datetime.now().isoformat(),
        }
        self._tune_report = report
        return report

    def maybe_auto_tune(self):
        """Called periodically by the ML agent. Re-tunes whenever the number of
        completed trades has grown by RETUNE_EVERY since the last tune — so it
        adapts continuously instead of only once a day."""
        from backend.database import Session as _S, Trade as _T, TradeStatus as _TS
        db = _S()
        try:
            n = (db.query(_T)
                 .filter(_T.env == TradeEnv.PAPER, _T.status != _TS.OPEN,
                         _T.entry_logic.like(f"{OB_TAG}%"))
                 .count())
        finally:
            db.close()
        if n < MIN_TRADES_TO_TUNE:
            return
        if n - getattr(self, "_last_tune_count", 0) < RETUNE_EVERY:
            return
        self._last_tune_count = n
        try:
            self.analyze_and_tune(TradeEnv.PAPER)
            print(f"[OB] auto-tuned on {n} completed trades.")
        except Exception as e:
            print(f"[OB] auto-tune error: {e}")

    def get_tune_report(self) -> dict:
        return self._tune_report or {"status": "not_run"}

    # ── Public API ────────────────────────────────────────────────────────────
    def _entry_window_open(self) -> bool:
        t = datetime.now().time()
        return SCAN_START <= t <= ENTRY_HARD_DEADLINE

    def get_plan(self) -> dict:
        """Return the current plan; build an on-demand preview if none yet."""
        plan = self._plan or self._build_plan(status="preview")
        if not plan:
            return {"status": "no_data"}
        d = asdict(plan)
        d["phase"]              = self._phase
        d["enabled"]            = self._enabled
        d["entry_window_open"]  = self._entry_window_open()
        d["trail_gap_pct"]      = self._p("trail_gap_pct")
        return d

    def get_state(self) -> dict:
        return {
            "enabled":     self._enabled,
            "phase":       self._phase,
            "today":       str(self._today) if self._today else None,
            "open_refs":   len(self._open_ref),
            "entry_window_open": self._entry_window_open(),
            "params":      self.get_params(),
            "windows": {"scan": "09:15–09:40", "entry_by": "09:45", "square_off": "15:15"},
        }


opening_breakout = OpeningBreakout()
