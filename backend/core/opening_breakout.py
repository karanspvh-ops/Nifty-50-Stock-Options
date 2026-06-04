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
from backend.core.stock_universe   import get_meta, get_stocks_in_sector, get_token
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
# If positions aren't filled by 9:45, keep watching for a good trade in
# 5-min extensions (9:50 → 9:55 → 10:00) before giving up for the day.
ENTRY_HARD_DEADLINE = dtime(10, 0)
SQUARE_OFF      = dtime(15, 15)

MOVE_MIN_PCT        = 1.5          # opening breakout trigger
MOVE_TARGET_PCT     = 2.0          # preferred move
MAX_POSITIONS       = 3
RFACTOR_MIN         = 0.8

# Sector qualification (fixes the marginal-breadth flip + thin single-stock sectors)
MIN_SECTOR_MOVERS   = 2            # a sector needs >=2 stocks that broke out >=1.5%
SECTOR_MIN_PCT      = 0.5          # and a meaningful average move (ignore noise)

# ── Gap-acceleration fast-track ───────────────────────────────────────────────
# A strong gap with confirming sector momentum may enter ~5 min early (9:35)
# instead of waiting for the 9:40 finalize.
GAP_MIN_PCT          = 3.0         # |today open vs prev close| gap
EARLY_SECTOR_MIN_PCT = 1.0         # chosen sector must show real momentum

HARD_SL_PCT         = 10.0         # option premium hard stop
TARGET_PCT          = 50.0         # single-lot full-exit target
SECOND_TARGET_PCT   = 100.0        # multi-lot runner target
TRAIL_ACTIVATE_PCT  = 20.0         # start trailing once +20%
TRAIL_GAP_PCT       = 20.0         # trail 20% off the peak

OB_TAG = "[OB]"
LOOP_SEC = 5

# ── Tunable parameters (persisted; editable from UI; auto-tuned by ML) ─────────
import json
_PARAMS_PATH = os.path.join(ROOT, "ob_params.json")
_DEFAULT_PARAMS = {
    "move_min_pct":       MOVE_MIN_PCT,
    "max_positions":      MAX_POSITIONS,
    "hard_sl_pct":        HARD_SL_PCT,
    "target_pct":         TARGET_PCT,
    "second_target_pct":  SECOND_TARGET_PCT,
    "trail_activate_pct": TRAIL_ACTIVATE_PCT,
    "trail_gap_pct":      TRAIL_GAP_PCT,
    "auto_tune_enabled":  True,    # ML may adjust trail_gap once >=50 trades
}
MIN_TRADES_TO_TUNE = 50            # don't auto-tune until enough history


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

        # finalize plan at 09:40
        if self._plan is None or self._plan.status in ("scanning", "preview"):
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
        in_extended = (ENTRY_DEADLINE < t <= ENTRY_HARD_DEADLINE) and open_ob < max_pos
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
        moves  = market.get_all_stock_moves()
        for token, mv in moves.items():
            if token not in self._open_ref and mv.get("ltp"):
                self._open_ref[token] = mv["ltp"]

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

        # ── Pick the STRONGEST-MOVING qualifying sector (fixes #1 & #2) ──────
        # FIX #1: don't decide direction from marginal net breadth — pick the
        #   sector with the biggest absolute move; its own sign sets direction.
        # FIX #2: a sector must have >= MIN_SECTOR_MOVERS breakout stocks
        #   (disqualifies thin single-stock sectors like AVIATION) and a
        #   meaningful average move (SECTOR_MIN_PCT) to avoid noise.
        qual = {}
        for sec, d in sector_moves.items():
            sp = d.get("pct_change", 0)
            if abs(sp) < SECTOR_MIN_PCT:
                continue
            sdir   = "call" if sp > 0 else "put"
            stocks = d.get("stocks", [])
            movers = sum(
                1 for s in stocks
                if (sdir == "call" and s.get("pct_change", 0) >=  MOVE_MIN_PCT)
                or (sdir == "put"  and s.get("pct_change", 0) <= -MOVE_MIN_PCT)
            )
            if movers >= MIN_SECTOR_MOVERS:
                qual[sec] = (sp, sdir, movers)

        if not qual:
            return TradePlan(status=status, trend="neutral", sector=None, direction=None,
                             sector_pct=0.0,
                             note=(f"No sector with ≥{MIN_SECTOR_MOVERS} breakout movers — "
                                   f"no broad-based move to trade. Sitting out."),
                             generated_at=datetime.now().isoformat())

        sector              = max(qual, key=lambda k: abs(qual[k][0]))
        sector_pct_, direction, n_movers = qual[sector]
        data  = sector_moves[sector]
        trend = "bullish" if direction == "call" else "bearish"

        sector_pct = data.get("pct_change", 0)
        candidates: List[PlanStock] = []

        for s in get_stocks_in_sector(sector, "NIFTY200", fno_only=True):
            token  = s["token"]
            meta   = get_meta(token)
            if meta.get("lot_size", 0) <= 0:
                continue
            mv  = market.get_stock_move(token)
            if not mv:
                continue
            ltp = mv.get("ltp") or 0
            day_move = mv.get("pct_change", 0)
            # Direction filter — must align with the chosen side
            if direction == "call" and day_move <= 0:   continue
            if direction == "put"  and day_move >= 0:    continue
            opening_move = self._opening_move(token, ltp)
            # Use opening move if we have a ref, else fall back to day move
            move = opening_move if self._open_ref.get(token) else day_move
            sec_avg = abs(sector_pct) or 0.1
            r_factor = round(abs(day_move) / sec_avg, 2)
            if r_factor < RFACTOR_MIN:
                continue
            est_premium = round(ltp * 0.015, 2)
            candidates.append(PlanStock(
                symbol=s["symbol"], token=token,
                opening_move=move, day_move=day_move,
                r_factor=r_factor, ltp=ltp, est_premium=est_premium,
                eligible=abs(move) >= self._p("move_min_pct"),
            ))

        # Rank: prefer eligible (already moved), then by |move| * r_factor
        candidates.sort(key=lambda c: (c.eligible, abs(c.opening_move) * c.r_factor), reverse=True)
        top = candidates[:int(self._p("max_positions"))]

        # Attach breakout confirmation (momentum + volume + VP levels) to top picks
        from backend.core.breakout_confirm import confirm_breakout
        for c in top:
            try:
                bo = confirm_breakout(c.token, direction)
                c.confirmed = bo.confirmed
                c.consec    = bo.consec
                c.vol_ratio = bo.vol_ratio
                c.vp_poc    = bo.vp50.get("poc")
                c.vp_vah    = bo.vp50.get("vah")
                c.vp_val    = bo.vp50.get("val")
            except Exception:
                pass

        note = (f"Top {len(top)} {sector} stocks; entry needs ≥{self._p('move_min_pct')}% move "
                f"+ momentum (consecutive {'lower-lows' if direction=='put' else 'higher-highs'}) "
                f"+ large volume + Volume-Profile value-area break.")
        return TradePlan(
            status=status, trend=trend, sector=sector, direction=direction,
            sector_pct=sector_pct, stocks=top, note=note,
            generated_at=datetime.now().isoformat(),
        )

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
        sector_strong = abs(self._plan.sector_pct) >= EARLY_SECTOR_MIN_PCT
        return gap_pct >= GAP_MIN_PCT and sector_strong

    # ── Entry ─────────────────────────────────────────────────────────────────
    def _enter_positions(self, env: TradeEnv, early_only: bool = False):
        if not self._plan:
            return
        open_ob = self._count_open_ob(env)
        session = get_or_create_session(env)
        max_pos = int(self._p("max_positions"))

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
            bo = confirm_breakout(ps.token, self._plan.direction)
            if not bo.confirmed:
                print(f"[OB] {ps.symbol} not confirmed — {bo.reason}")
                continue

            tag = "GAP-ACCEL early entry" if early else "Opening breakout"
            reason = (f"{OB_TAG} {tag} | {self._plan.trend} | sector {self._plan.sector} "
                      f"({self._plan.sector_pct:+.2f}%) | {ps.symbol} moved {move:+.2f}% from open "
                      f"| R-factor {ps.r_factor} | ATM {self._plan.direction.upper()} "
                      f"| CONFIRM: {bo.reason}")
            trade = place_entry_order(
                env=env, symbol=ps.symbol, token=ps.token,
                direction=self._plan.direction, session_id=session["id"],
                entry_logic=reason,
                indicators={"opening_move": move, "r_factor": ps.r_factor,
                            "consec_candles": bo.consec, "vol_ratio": bo.vol_ratio,
                            "vp20": bo.vp20, "vp50": bo.vp50},
                sl_pct_override=self._p("hard_sl_pct"), target_pct_override=self._p("target_pct"),
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

            # update peak
            if t.highest_price is None or ltp > t.highest_price:
                self._set_highest(t.id, ltp)
                t.highest_price = ltp

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
        """Called periodically (e.g. by the ML agent). Tunes at most once/day."""
        today = str(datetime.now().date())
        if self._last_tune == today:
            return
        self._last_tune = today
        try:
            self.analyze_and_tune(TradeEnv.PAPER)
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
