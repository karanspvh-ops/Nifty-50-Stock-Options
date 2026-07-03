"""
early_scalp.py — Early Scalp strategy engine.

Designed as a fast intraday scalp that enters earlier than the Opening Breakout
strategy and captures smaller, higher-probability moves.

Flow (timed to market open):
  09:15 – 09:20  WARMING  : capture 9:15 opens, derive gap from prev close,
                             build initial candidate ranking.
  09:20 – 09:25  SCANNING : pull 1-min + 3-min candles for top candidates,
                             fetch option OI concentration, score each stock.
  09:25 – 09:30  ENTERING : enter top-ranked confirmed candidates (max 3).
  09:30 – 10:30  MANAGING : monitor premium vs SL / target / trail levels.
  10:30           DONE    : square off all remaining ES positions.

Filters (all must pass for entry):
  • Gap from prev close  >= gap_min_pct  (default 0.5%)
  • Opening move from 9:15 >= move_min_pct (default 1.0%)
  • 1-min consecutive candles in direction  >= 2
  • 3-min consecutive candles in direction  >= 1
  • Volume ratio (current bar vs 20-bar avg) >= 1.3x
  • OI signal not against direction (neutral or confirming)

Exit rules (per-trade):
  • Hard SL   : option premium drops -5% from entry
  • Target    : option premium rises +12% from entry
  • Trail     : armed at +8%, gap 5% — locks in gains
  • Time stop : 10:30 AM (square off everything)

Trades are tagged "[ES]" in entry_logic so the generic risk engine ignores them.
"""

import os, sys, threading, time, json
from dataclasses import dataclass, field, asdict
from datetime import datetime, date, time as dtime
from typing import Dict, List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.core.market_state     import market
from backend.core.stock_universe   import get_stocks_for_index, get_meta
from backend.core.settings_manager import is_live_mode
from backend.core.session_manager  import get_or_create_session, check_portfolio_sl
from backend.core.order_executor   import place_entry_order, select_option, option_premium
from backend.core.risk_engine      import risk_engine
from backend.core.broker           import broker
from backend.database import (
    Session as DBSession, Trade, TradeStatus, TradeEnv,
)

# ── Time gates ────────────────────────────────────────────────────────────────
WARM_START    = dtime(9, 15)
SCAN_START    = dtime(9, 20)
ENTRY_START   = dtime(9, 25)
ENTRY_END     = dtime(9, 30)
SQUARE_OFF    = dtime(10, 30)

LOOP_SEC      = 10           # tick every 10 seconds
CANDLE_REFREQ = 60           # re-pull 1-min candles at most once per minute

ES_TAG        = "[ES]"

# ── Default parameters ────────────────────────────────────────────────────────
_PARAMS_PATH = os.path.join(ROOT, "es_params.json")
_DEFAULT_PARAMS = {
    "gap_min_pct":        0.5,    # min gap from prev close
    "move_min_pct":       1.0,    # min move from 9:15 open
    "vol_ratio_min":      1.3,    # min volume vs 20-bar average
    "max_positions":      5,      # simultaneous open ES slots
    "hard_sl_pct":        5.0,
    "target_pct":         12.0,
    "trail_activate_pct": 8.0,
    "trail_gap_pct":      5.0,
}


# ── Dataclasses ───────────────────────────────────────────────────────────────
@dataclass
class ScalpCandidate:
    symbol:       str
    token:        str
    gap_pct:      float          # move from previous close (all-day)
    opening_move: float          # move from 9:15 open
    vol_ratio:    float          # current volume vs 20-bar avg
    oi_signal:    str            # "bullish" | "bearish" | "neutral"
    oi_ce:        int
    oi_pe:        int
    direction:    str            # "call" | "put"
    score:        float
    confirmed:    bool           # passes ALL entry filters
    consec_1m:    int            # consecutive 1-min candles in direction
    consec_3m:    int            # consecutive 3-min candles in direction
    ltp:          float
    est_premium:  float          # estimated option premium
    entered:      bool = False
    skip_reason:  str  = ""      # why it was skipped (if not confirmed)


@dataclass
class EarlyScalpPlan:
    status:       str            # idle|warming|scanning|entering|managing|done
    market_trend: str            # bullish|bearish|mixed
    nifty_move:   float
    candidates:   List[ScalpCandidate] = field(default_factory=list)
    note:         str  = ""
    generated_at: str  = ""


# ── Strategy class ────────────────────────────────────────────────────────────
class EarlyScalp:
    def __init__(self):
        self._running  = False
        self._thread: Optional[threading.Thread] = None
        self._enabled  = True
        self._today    = None
        self._open_ref: Dict[str, float] = {}      # token -> 9:15 open
        self._prev_close: Dict[str, float] = {}    # token -> prev close (derived)
        self._open_ref_final: set = set()          # tokens where ref is true 9:15 candle
        self._plan: Optional[EarlyScalpPlan] = None
        self._phase = "IDLE"
        self._params = self._load_params()
        self._last_candle_time: float = 0          # epoch sec of last 1-min pull
        self._candle_cache: Dict[str, list] = {}   # symbol -> 1-min candles today
        self._oi_cache: Dict[str, dict] = {}       # symbol -> {ce_oi, pe_oi, fetched_at}
        self._trail_locked: Dict[int, float] = {}  # trade_id -> locked_pnl_pct
        self._trail_armed:  Dict[int, bool]  = {}  # trade_id -> armed?

    # ── Params ────────────────────────────────────────────────────────────────
    def _load_params(self) -> dict:
        p = dict(_DEFAULT_PARAMS)
        if os.path.exists(_PARAMS_PATH):
            try:
                p.update(json.load(open(_PARAMS_PATH)))
            except Exception:
                pass
        return p

    def _save_params(self):
        try:
            json.dump(self._params, open(_PARAMS_PATH, "w"), indent=2)
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

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="EarlyScalp")
        self._thread.start()
        print("[ES] Early Scalp strategy started.")

    def stop(self):
        self._running = False

    def set_enabled(self, on: bool):
        self._enabled = on

    def _loop(self):
        while self._running:
            try:
                self._tick()
            except Exception as e:
                print(f"[ES] tick error: {e}")
            time.sleep(LOOP_SEC)

    # ── Daily reset ───────────────────────────────────────────────────────────
    def _daily_reset(self, today: date):
        self._today = today
        self._open_ref.clear()
        self._prev_close.clear()
        self._open_ref_final.clear()
        self._plan = None
        self._phase = "IDLE"
        self._last_candle_time = 0
        self._candle_cache.clear()
        self._oi_cache.clear()
        self._trail_locked.clear()
        self._trail_armed.clear()
        print("[ES] Daily reset complete.")

    # ── Per-tick state machine ─────────────────────────────────────────────────
    def _tick(self):
        now = datetime.now()
        t   = now.time()

        if self._today != now.date():
            self._daily_reset(now.date())

        if not self._enabled or not market.is_feed_connected():
            return

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER

        if check_portfolio_sl(env):
            self._phase = "KILLED"
            return

        # Square off — only run once on transition, not every tick
        if t >= SQUARE_OFF:
            if self._phase != "DONE":
                self._square_off(env)
                self._phase = "DONE"
                if self._plan:
                    self._plan.status = "done"
                from backend.core.daily_report_scheduler import daily_report_scheduler
                daily_report_scheduler.notify_session_done("ES")
            return

        # Pre-open
        if t < WARM_START:
            self._phase = "IDLE"
            return

        # Warming phase: capture 9:15 open references
        self._capture_open_refs()

        if t < SCAN_START:
            self._phase = "WARMING"
            if self._plan is None or self._plan.status == "idle":
                self._plan = EarlyScalpPlan(
                    status="warming", market_trend="neutral", nifty_move=0.0,
                    note="Capturing 9:15 opening references…",
                    generated_at=now.isoformat()
                )
            return

        # Scanning phase: build/update the plan
        if t < ENTRY_START:
            self._phase = "SCANNING"
            self._maybe_refresh_candles(now)
            self._plan = self._build_plan(status="scanning")
            return

        # Entering phase
        if t <= ENTRY_END:
            self._phase = "ENTERING"
            self._maybe_refresh_candles(now)
            self._plan = self._build_plan(status="entering")
            self._enter_positions(env)

        # Managing phase
        self._manage(env)
        if t > ENTRY_END:
            self._phase = "MANAGING"
            self._maybe_refresh_candles(now)
            self._plan = self._build_plan(status="managing")

    # ── Capture 9:15 opening price from 5-min candles ────────────────────────
    def _capture_open_refs(self):
        today = datetime.now().date()
        moves = market.get_all_stock_moves()
        for token, mv in moves.items():
            # Derive prev close from day_pct and ltp
            ltp = mv.get("ltp")
            pct = mv.get("pct_change", 0)
            if ltp and pct and token not in self._prev_close:
                try:
                    self._prev_close[token] = ltp / (1 + pct / 100)
                except ZeroDivisionError:
                    pass

            if token in self._open_ref_final:
                continue
            ref = self._session_open_from_candles(token, today)
            if ref is not None:
                self._open_ref[token] = ref
                self._open_ref_final.add(token)
            elif token not in self._open_ref and ltp:
                self._open_ref[token] = ltp

    def _session_open_from_candles(self, token: str, day) -> Optional[float]:
        for c in market.get_candles(token, n=260):
            ts = c.get("ts")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if dt.date() == day and dt.time() >= WARM_START:
                return c.get("open")
        return None

    def _opening_move(self, token: str, ltp: float) -> float:
        ref = self._open_ref.get(token)
        if not ref:
            return 0.0
        return round((ltp - ref) / ref * 100, 2)

    def _gap_pct(self, token: str, ltp: float) -> float:
        pc = self._prev_close.get(token)
        if not pc:
            mv = market.get_stock_move(token)
            return round(mv.get("pct_change", 0), 2) if mv else 0.0
        return round((ltp - pc) / pc * 100, 2)

    # ── 1-min candle refresh ──────────────────────────────────────────────────
    def _maybe_refresh_candles(self, now: datetime):
        """Pull 1-min candles for top candidates once per CANDLE_REFREQ seconds."""
        if time.time() - self._last_candle_time < CANDLE_REFREQ:
            return
        self._last_candle_time = time.time()

        # Get top 20 stocks by |gap_pct| to limit API calls
        moves = market.get_all_stock_moves()
        ranked = sorted(
            [(tok, mv) for tok, mv in moves.items()
             if abs(mv.get("pct_change", 0)) >= self._p("gap_min_pct") * 0.5],
            key=lambda x: abs(x[1].get("pct_change", 0)),
            reverse=True
        )[:20]

        today = now.date()
        kite  = broker.kite()
        start = datetime.combine(today, dtime(9, 0))
        end   = now

        for token, mv in ranked:
            sym = mv.get("symbol") or get_meta(token).get("symbol", "")
            if not sym:
                continue
            try:
                cs = kite.historical_data(int(token), start, end, "minute")
                cs = [c for c in cs if c["date"].date() == today]
                self._candle_cache[sym] = cs
            except Exception:
                pass

    # ── OI fetch for a single stock ───────────────────────────────────────────
    def _fetch_oi(self, symbol: str, ltp: float, direction: str) -> dict:
        cached = self._oi_cache.get(symbol, {})
        if cached.get("fetched_at") and time.time() - cached["fetched_at"] < 120:
            return cached

        try:
            tok_ce, sym_ce, strike, expiry = select_option(symbol, ltp, "call")
            tok_pe, sym_pe, _,      _      = select_option(symbol, ltp, "put")
            if not sym_ce or not sym_pe:
                return {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}

            kite = broker.kite()
            q = kite.quote([f"NFO:{sym_ce}", f"NFO:{sym_pe}"])
            ce_oi = q.get(f"NFO:{sym_ce}", {}).get("oi", 0) or 0
            pe_oi = q.get(f"NFO:{sym_pe}", {}).get("oi", 0) or 0

            if ce_oi > pe_oi * 1.25:
                signal = "bullish"   # more call OI — call writers confident upside limited?
            elif pe_oi > ce_oi * 1.25:
                signal = "bearish"   # more put OI
            else:
                signal = "neutral"

            result = {"oi_ce": int(ce_oi), "oi_pe": int(pe_oi),
                      "signal": signal, "fetched_at": time.time()}
            self._oi_cache[symbol] = result
            return result
        except Exception:
            return {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}

    # ── Candle momentum helpers ───────────────────────────────────────────────
    def _consec_candles(self, candles: list, direction: str) -> int:
        """Count trailing consecutive green (call) or red (put) candles."""
        count = 0
        for c in reversed(candles[-6:]):
            if direction == "call":
                if c.get("close", 0) >= c.get("open", 0):
                    count += 1
                else:
                    break
            else:
                if c.get("close", 0) <= c.get("open", 0):
                    count += 1
                else:
                    break
        return count

    def _to_3min(self, candles_1m: list) -> list:
        """Aggregate 1-min candles into 3-min candles."""
        result = []
        for i in range(0, len(candles_1m), 3):
            grp = candles_1m[i:i + 3]
            if not grp:
                continue
            result.append({
                "date":   grp[0]["date"],
                "open":   grp[0]["open"],
                "high":   max(c["high"] for c in grp),
                "low":    min(c["low"]  for c in grp),
                "close":  grp[-1]["close"],
                "volume": sum(c.get("volume", 0) for c in grp),
            })
        return result

    def _vol_ratio(self, candles_1m: list) -> float:
        if len(candles_1m) < 3:
            return 0.0
        vols = [c.get("volume", 0) for c in candles_1m[:-1]]
        avg  = sum(vols) / max(len(vols), 1)
        last = candles_1m[-1].get("volume", 0)
        return round(last / avg, 2) if avg else 0.0

    # ── Plan builder ──────────────────────────────────────────────────────────
    def _build_plan(self, status: str) -> EarlyScalpPlan:
        moves = market.get_all_stock_moves()
        if not moves:
            return EarlyScalpPlan(
                status=status, market_trend="neutral", nifty_move=0.0,
                note="Waiting for market data…", generated_at=datetime.now().isoformat()
            )

        # Nifty trend
        nifty_move = 0.0
        for tok, mv in moves.items():
            sym = mv.get("symbol", "")
            if sym in ("NIFTY 50", "Nifty 50", "NIFTY50"):
                nifty_move = mv.get("pct_change", 0.0)
                break

        # Market breadth
        ups = downs = 0
        for mv in moves.values():
            p = mv.get("pct_change", 0)
            if   p >=  0.3: ups   += 1
            elif p <= -0.3: downs += 1
        total = ups + downs
        net = (ups - downs) / total if total else 0.0
        if   net >=  0.3: market_trend, allowed = "bullish", {"call"}
        elif net <= -0.3: market_trend, allowed = "bearish", {"put"}
        else:             market_trend, allowed = "mixed",   {"call", "put"}

        move_min  = float(self._p("move_min_pct"))
        gap_min   = float(self._p("gap_min_pct"))
        vr_min    = float(self._p("vol_ratio_min"))

        candidates: List[ScalpCandidate] = []

        for s in get_stocks_for_index("FNO", fno_only=True):
            token  = s["token"]
            symbol = s["symbol"]
            mv     = moves.get(token)
            if not mv:
                continue
            ltp      = mv.get("ltp") or 0
            if ltp <= 0:
                continue
            gap      = self._gap_pct(token, ltp)
            op_move  = self._opening_move(token, ltp)
            direction = "call" if gap > 0 else "put"

            if direction not in allowed:
                continue
            if abs(gap) < gap_min:
                continue
            if abs(op_move) < move_min * 0.5:
                continue

            # 1-min candles (from cache)
            candles_1m = self._candle_cache.get(symbol, [])
            candles_3m = self._to_3min(candles_1m) if candles_1m else []
            consec_1m  = self._consec_candles(candles_1m, direction) if candles_1m else 0
            consec_3m  = self._consec_candles(candles_3m, direction) if candles_3m else 0
            vr         = self._vol_ratio(candles_1m) if candles_1m else 0.0

            # OI (only fetch for stocks that already pass other filters to save API calls)
            oi = {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}
            if abs(op_move) >= move_min and consec_1m >= 1:
                oi = self._fetch_oi(symbol, ltp, direction)

            oi_signal = oi.get("signal", "neutral")

            # OI contradicts direction → skip
            oi_against = (direction == "call" and oi_signal == "bearish") or \
                         (direction == "put"  and oi_signal == "bullish")

            # Composite score
            oi_confirming = (direction == "call" and oi_signal == "bullish") or \
                            (direction == "put"  and oi_signal == "bearish")
            score = round(abs(gap) * abs(op_move) * max(vr, 0.5) *
                          (1.2 if oi_confirming else 0.8 if oi_against else 1.0), 3)

            # Full confirmation (vol_ratio is a score bonus, not a hard gate)
            confirmed = (
                abs(op_move) >= move_min and
                abs(gap) >= gap_min and
                consec_1m >= 2 and
                consec_3m >= 1 and
                not oi_against
            )

            skip_reason = ""
            if not confirmed:
                reasons = []
                if abs(op_move) < move_min:     reasons.append(f"move {op_move:+.1f}%<{move_min}%")
                if abs(gap)     < gap_min:      reasons.append(f"gap {gap:+.1f}%<{gap_min}%")
                if consec_1m < 2:               reasons.append(f"1m-consec={consec_1m}<2")
                if consec_3m < 1:               reasons.append(f"3m-consec={consec_3m}<1")
                if oi_against:                  reasons.append(f"OI={oi_signal}")
                skip_reason = "; ".join(reasons)

            est_premium = round(ltp * 0.02, 2)

            candidates.append(ScalpCandidate(
                symbol=symbol, token=token,
                gap_pct=round(gap, 2), opening_move=round(op_move, 2),
                vol_ratio=vr, oi_signal=oi_signal,
                oi_ce=oi.get("oi_ce", 0), oi_pe=oi.get("oi_pe", 0),
                direction=direction, score=score,
                confirmed=confirmed, consec_1m=consec_1m, consec_3m=consec_3m,
                ltp=ltp, est_premium=est_premium, entered=False,
                skip_reason=skip_reason,
            ))

        # Mark already-entered stocks
        entered_syms = self._entered_symbols(
            TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER
        )
        for c in candidates:
            if c.symbol in entered_syms:
                c.entered = True

        candidates.sort(key=lambda c: c.score, reverse=True)

        return EarlyScalpPlan(
            status=status, market_trend=market_trend,
            nifty_move=round(nifty_move, 2),
            candidates=candidates[:20],
            note=f"Breadth {net:+.0%} | {len([c for c in candidates if c.confirmed])} confirmed",
            generated_at=datetime.now().isoformat(),
        )

    # ── Entry logic ───────────────────────────────────────────────────────────
    def _entered_symbols(self, env: TradeEnv) -> set:
        """Return every symbol ES has touched today — open OR already closed.
        Prevents re-entering a symbol after SL hit or target exit."""
        today = date.today().isoformat()
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= today,
            ).all()
        return {t.symbol for t in trades}

    def _count_open(self, env: TradeEnv) -> int:
        with DBSession() as db:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).count()

    def _count_today(self, env: TradeEnv) -> int:
        """Total ES trades entered today (open + closed)."""
        today = date.today().isoformat()
        with DBSession() as db:
            return db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= today,
            ).count()

    def _net_pnl_today(self, env: TradeEnv) -> float:
        """Cumulative ES PnL today (closed trades only)."""
        today = date.today().isoformat()
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
                Trade.entered_at >= today,
                Trade.status != TradeStatus.OPEN,
            ).all()
        return sum((t.pnl or 0) for t in trades)

    def _refills_blocked(self, env: TradeEnv) -> bool:
        """After 5 ES trades have been entered, block further refills if net PnL is positive
        (protect the gain — no need to push for more)."""
        if self._count_today(env) < 5:
            return False
        return self._net_pnl_today(env) > 0

    def _revalidate(self, c: "ScalpCandidate") -> tuple[bool, str]:
        """Re-check criteria at entry time against the LATEST candle and LTP.
        Returns (ok, reason). The plan can be 30-60s stale by the time a slot
        opens, so we don't trust c.confirmed alone — the market may have turned."""
        mv  = market.get_stock_move(c.token)
        ltp = mv.get("ltp") if mv else None
        if not ltp:
            return False, "no LTP"

        gap     = self._gap_pct(c.token, ltp)
        op_move = self._opening_move(c.token, ltp)
        move_min = float(self._p("move_min_pct"))
        gap_min  = float(self._p("gap_min_pct"))

        # Direction must still match
        live_dir = "call" if gap > 0 else "put"
        if live_dir != c.direction:
            return False, f"direction flipped (was {c.direction}, now {live_dir})"

        if abs(gap) < gap_min:
            return False, f"gap {gap:+.2f}% < {gap_min}%"
        if abs(op_move) < move_min:
            return False, f"move {op_move:+.2f}% < {move_min}% (faded)"

        # Most recent 1-min candle must still be in trade direction
        candles_1m = self._candle_cache.get(c.symbol, [])
        if candles_1m:
            last = candles_1m[-1]
            last_bull = last.get("close", 0) >= last.get("open", 0)
            if c.direction == "call" and not last_bull:
                return False, "last 1m candle is RED (reversal)"
            if c.direction == "put" and last_bull:
                return False, "last 1m candle is GREEN (reversal)"

            consec_1m = self._consec_candles(candles_1m, c.direction)
            if consec_1m < 2:
                return False, f"1m-consec={consec_1m}<2 (momentum lost)"

        return True, "fresh criteria pass"

    def _enter_positions(self, env: TradeEnv):
        if not self._plan:
            return

        # Day-total guard: once 5 ES trades fired and PnL is positive, stop refilling
        if self._refills_blocked(env):
            return

        max_pos = int(self._p("max_positions"))
        open_n  = self._count_open(env)
        if open_n >= max_pos:
            return

        entered = self._entered_symbols(env)
        slots   = max_pos - open_n
        session = get_or_create_session(env)

        for c in self._plan.candidates:
            if slots <= 0:
                break
            if not c.confirmed or c.entered or c.symbol in entered:
                continue

            # Re-validate at entry — plan may be stale
            ok, why = self._revalidate(c)
            if not ok:
                print(f"[ES] {c.symbol} REVAL SKIP — {why}")
                continue

            reason = (
                f"{ES_TAG} Early Scalp | gap {c.gap_pct:+.1f}% from prev close | "
                f"opening move {c.opening_move:+.1f}% from 9:15 | "
                f"1m-consec={c.consec_1m} 3m-consec={c.consec_3m} | "
                f"vol {c.vol_ratio:.1f}x | OI={c.oi_signal} | score={c.score:.2f} | "
                f"REVAL: {why}"
            )
            try:
                trade = place_entry_order(
                    env=env, symbol=c.symbol, token=c.token,
                    direction=c.direction, session_id=session["id"],
                    entry_logic=reason,
                    indicators={
                        "gap_pct": c.gap_pct, "opening_move": c.opening_move,
                        "consec_1m": c.consec_1m, "consec_3m": c.consec_3m,
                        "vol_ratio": c.vol_ratio, "oi_signal": c.oi_signal,
                        "score": c.score,
                    },
                    sl_pct_override=float(self._p("hard_sl_pct")),
                    target_pct_override=float(self._p("target_pct")),
                )
                if trade:
                    c.entered = True
                    entered.add(c.symbol)
                    slots -= 1
                    print(f"[ES] Entered {c.symbol} {c.direction.upper()} | "
                          f"gap={c.gap_pct:+.1f}% move={c.opening_move:+.1f}% "
                          f"vol={c.vol_ratio:.1f}x OI={c.oi_signal}")
            except Exception as e:
                print(f"[ES] Entry error {c.symbol}: {e}")

    # ── Position management ───────────────────────────────────────────────────
    def _manage(self, env: TradeEnv):
        sl_pct    = float(self._p("hard_sl_pct"))
        tgt_pct   = float(self._p("target_pct"))
        arm_pct   = float(self._p("trail_activate_pct"))
        gap_pct   = float(self._p("trail_gap_pct"))

        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).all()

        for t in trades:
            if not t.entry_price:
                continue
            try:
                from backend.core.order_executor import current_premium, option_premium
                from backend.core.stock_universe import get_option_token
                tok     = get_option_token(t.option_symbol)
                premium = option_premium(tok, t.option_symbol)
                if not premium:
                    continue
            except Exception:
                continue

            pnl_pct = (premium - t.entry_price) / t.entry_price * 100

            # Update trail state — recover from DB if in-memory state was lost
            tid = t.id
            if tid not in self._trail_armed:
                # Recover from DB: if dynamic_sl_price is set the trail was already armed
                if t.dynamic_sl_price and t.dynamic_sl_price > t.trade_sl_price:
                    self._trail_armed[tid]  = True
                    recovered_lock = (t.dynamic_sl_price - t.entry_price) / t.entry_price * 100
                    self._trail_locked[tid] = recovered_lock
                    print(f"[ES] Trail state recovered from DB for {t.symbol}: locked={recovered_lock:+.1f}%")
                # Also recover from highest_price if dynamic_sl wasn't written yet
                elif t.highest_price and t.highest_price >= t.entry_price * (1 + arm_pct / 100):
                    peak_pnl = (t.highest_price - t.entry_price) / t.entry_price * 100
                    self._trail_armed[tid]  = True
                    self._trail_locked[tid] = peak_pnl - gap_pct
                    print(f"[ES] Trail re-armed from highest_price for {t.symbol}: peak={peak_pnl:.1f}% locked={self._trail_locked[tid]:+.1f}%")
                else:
                    self._trail_armed[tid]  = False
                    self._trail_locked[tid] = -sl_pct

            if pnl_pct >= arm_pct and not self._trail_armed[tid]:
                self._trail_armed[tid] = True
                print(f"[ES] Trail armed on {t.symbol} at +{pnl_pct:.1f}%")

            if self._trail_armed[tid]:
                new_lock = pnl_pct - gap_pct
                if new_lock > self._trail_locked[tid]:
                    self._trail_locked[tid] = new_lock
                    # Persist trail SL to DB so it survives restarts
                    new_dyn_sl = round(t.entry_price * (1 + self._trail_locked[tid] / 100), 2)
                    with DBSession() as db:
                        db.query(Trade).filter(Trade.id == tid).update(
                            {"dynamic_sl_price": new_dyn_sl}, synchronize_session=False)
                        db.commit()

            locked = self._trail_locked.get(tid, -sl_pct)

            exit_reason = None
            if pnl_pct >= tgt_pct:
                exit_reason = f"ES Target +{tgt_pct:.0f}%"
            elif pnl_pct <= locked:
                if self._trail_armed.get(tid):
                    exit_reason = f"ES Trail lock {locked:+.1f}%"
                else:
                    exit_reason = f"ES SL -{sl_pct:.0f}%"

            if exit_reason:
                try:
                    risk_engine.force_exit_trade(t.id, exit_reason)
                    self._trail_armed.pop(tid, None)
                    self._trail_locked.pop(tid, None)
                    print(f"[ES] Exit {t.symbol} | {exit_reason} | PnL {pnl_pct:+.1f}%")
                except Exception as e:
                    print(f"[ES] Exit error {t.symbol}: {e}")

    # ── Square off ────────────────────────────────────────────────────────────
    def _square_off(self, env: TradeEnv):
        with DBSession() as db:
            trades = db.query(Trade).filter(
                Trade.env == env,
                Trade.status == TradeStatus.OPEN,
                Trade.entry_logic.like(f"%{ES_TAG}%"),
            ).all()
            trade_data = [(t.id, t.option_symbol) for t in trades]

        if not trade_data:
            return

        for tid, _ in trade_data:
            try:
                risk_engine.force_exit_trade(tid, "ES Time-stop 10:30")
                print(f"[ES] Squared off trade #{tid} at 10:30")
            except Exception as e:
                print(f"[ES] Square off error #{tid}: {e}")

    # ── Public API ────────────────────────────────────────────────────────────
    def get_plan(self) -> dict:
        if not self._plan:
            return {
                "status": "idle", "phase": self._phase,
                "market_trend": "neutral", "nifty_move": 0.0,
                "candidates": [], "note": "Strategy not yet started.",
                "generated_at": "", "params": self.get_params(),
            }
        p = self._plan
        return {
            "status":       p.status,
            "phase":        self._phase,
            "market_trend": p.market_trend,
            "nifty_move":   p.nifty_move,
            "candidates":   [asdict(c) for c in p.candidates],
            "note":         p.note,
            "generated_at": p.generated_at,
            "params":       self.get_params(),
        }

    def get_state(self) -> dict:
        return {
            "enabled":        self._enabled,
            "phase":          self._phase,
            "open_positions": self._count_open(
                TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER
            ),
            "today":          str(self._today),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
early_scalp = EarlyScalp()
