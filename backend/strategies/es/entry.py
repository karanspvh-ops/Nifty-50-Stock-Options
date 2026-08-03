"""entry.py — ES plan building, re-validation, and position entry.

Edit this file for:
  - changing the candidate scoring formula
  - adjusting entry confirmation criteria (consec candles, OI gate)
  - re-validation logic at the moment of entry
"""

import threading
from datetime import datetime
from typing import List, Optional

from backend.core.market_state   import market
from backend.core.stock_universe import get_stocks_for_index
from backend.core.settings_manager import is_live_mode
from backend.core.session_manager  import get_or_create_session
from backend.execution.order_executor import place_entry_order

from backend.database import TradeEnv

from backend.strategies.es.params import (
    ES_TAG, SCAN_START, ENTRY_START, ENTRY_END, SQUARE_OFF,
)
from backend.strategies.es.types import ScalpCandidate, EarlyScalpPlan


class ESEntryMixin:
    """Plan building and position entry for ES.  Touch only this file for entry logic changes."""

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

        move_min = float(self._p("move_min_pct"))
        gap_min  = float(self._p("gap_min_pct"))
        vr_min   = float(self._p("vol_ratio_min"))

        candidates: List[ScalpCandidate] = []

        for s in get_stocks_for_index("FNO", fno_only=True):
            token  = s["token"]
            symbol = s["symbol"]
            mv     = moves.get(token)
            if not mv:
                continue
            ltp = mv.get("ltp") or 0
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

            candles_1m = market.get_1m_candles(token)
            candles_3m = self._to_3min(candles_1m) if candles_1m else []
            consec_1m  = self._consec_candles(candles_1m, direction) if candles_1m else 0
            consec_3m  = self._consec_candles(candles_3m, direction) if candles_3m else 0
            vr         = self._vol_ratio(candles_1m) if candles_1m else 0.0

            oi = {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}
            if abs(op_move) >= move_min and consec_1m >= 1:
                oi = self._fetch_oi(symbol, ltp, direction)

            oi_signal = oi.get("signal", "neutral")
            oi_against = (direction == "call" and oi_signal == "bearish") or \
                         (direction == "put"  and oi_signal == "bullish")
            oi_confirming = (direction == "call" and oi_signal == "bullish") or \
                            (direction == "put"  and oi_signal == "bearish")

            score = round(abs(gap) * abs(op_move) * max(vr, 0.5) *
                          (1.2 if oi_confirming else 0.8 if oi_against else 1.0), 3)

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
                if abs(op_move) < move_min:  reasons.append(f"move {op_move:+.1f}%<{move_min}%")
                if abs(gap)     < gap_min:   reasons.append(f"gap {gap:+.1f}%<{gap_min}%")
                if consec_1m < 2:            reasons.append(f"1m-consec={consec_1m}<2")
                if consec_3m < 1:            reasons.append(f"3m-consec={consec_3m}<1")
                if oi_against:               reasons.append(f"OI={oi_signal}")
                skip_reason = "; ".join(reasons)

            candidates.append(ScalpCandidate(
                symbol=symbol, token=token,
                gap_pct=round(gap, 2), opening_move=round(op_move, 2),
                vol_ratio=vr, oi_signal=oi_signal,
                oi_ce=oi.get("oi_ce", 0), oi_pe=oi.get("oi_pe", 0),
                direction=direction, score=score,
                confirmed=confirmed, consec_1m=consec_1m, consec_3m=consec_3m,
                ltp=ltp, est_premium=round(ltp * 0.02, 2), entered=False,
                skip_reason=skip_reason,
            ))

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER
        entered_syms = self._entered_symbols(env)
        for c in candidates:
            if c.symbol in entered_syms:
                c.entered = True

        candidates.sort(key=lambda c: c.score, reverse=True)

        confirmed_list = [c for c in candidates if c.confirmed]
        if status == "entering" and not confirmed_list:
            all_reasons = "; ".join(
                f"{c.symbol}({c.skip_reason})"
                for c in candidates[:5] if c.skip_reason
            )
            print(f"[ES] ENTRY WINDOW — 0 confirmed candidates. "
                  f"Top skip reasons: {all_reasons or 'no candidates in universe'}")

        return EarlyScalpPlan(
            status=status, market_trend=market_trend,
            nifty_move=round(nifty_move, 2),
            candidates=candidates[:20],
            note=f"Breadth {net:+.0%} | {len(confirmed_list)} confirmed",
            generated_at=datetime.now().isoformat(),
        )

    # ── Entry-time re-validation ──────────────────────────────────────────────

    def _revalidate(self, c: ScalpCandidate) -> "tuple[bool, str]":
        """Re-check criteria at entry time against the latest candle and LTP.
        The plan can be 30-60s stale by the time a slot opens."""
        mv  = market.get_stock_move(c.token)
        ltp = mv.get("ltp") if mv else None
        if not ltp:
            return False, "no LTP"

        gap      = self._gap_pct(c.token, ltp)
        op_move  = self._opening_move(c.token, ltp)
        move_min = float(self._p("move_min_pct"))
        gap_min  = float(self._p("gap_min_pct"))

        live_dir = "call" if gap > 0 else "put"
        if live_dir != c.direction:
            return False, f"direction flipped (was {c.direction}, now {live_dir})"

        if abs(gap) < gap_min:
            return False, f"gap {gap:+.2f}% < {gap_min}%"
        if c.direction == "call" and op_move < move_min:
            return False, f"opening move {op_move:+.2f}% not bullish enough (need >={move_min}%)"
        if c.direction == "put" and op_move > -move_min:
            return False, f"opening move {op_move:+.2f}% not bearish enough (need <={-move_min}%)"

        candles_1m = market.get_1m_candles(c.token)
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

    # ── Entry ─────────────────────────────────────────────────────────────────

    def _enter_positions(self, env: TradeEnv):
        if not self._plan:
            return
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
                    max_positions=int(self._p("max_positions")),
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

    # ── Tick-level entry (sub-second) ─────────────────────────────────────────

    def _on_stock_tick(self, token: str, ltp: float):
        """Tick callback for confirmed candidates during the entry window.

        Runs in the Zerodha WebSocket thread — must be fast and non-blocking.
        Spawns a daemon thread for the actual order placement (Kite API call).
        """
        if datetime.now().time() < ENTRY_START:
            return
        if token in self._entry_pending:
            return

        c = self._plan_index.get(token)
        if not c or not c.confirmed or c.entered:
            return

        ok, why = self._revalidate(c)
        if not ok:
            return

        env = TradeEnv.LIVE if is_live_mode() else TradeEnv.PAPER
        self._entry_pending.add(token)
        threading.Thread(
            target=self._tick_entry,
            args=(c, env, why),
            daemon=True,
        ).start()

    def _tick_entry(self, c: ScalpCandidate, env: TradeEnv, why: str):
        """Place the entry order in a daemon thread — keeps the tick thread free."""
        try:
            # Guard checks (DB-ok here, we're in a thread not the tick callback)
            if self._refills_blocked(env):
                return
            if self._count_open(env) >= int(self._p("max_positions")):
                return
            if c.symbol in self._entered_symbols(env):
                return

            session = get_or_create_session(env)
            reason = (
                f"{ES_TAG} Early Scalp | gap {c.gap_pct:+.1f}% from prev close | "
                f"opening move {c.opening_move:+.1f}% from 9:15 | "
                f"1m-consec={c.consec_1m} 3m-consec={c.consec_3m} | "
                f"vol {c.vol_ratio:.1f}x | OI={c.oi_signal} | score={c.score:.2f} | "
                f"REVAL: {why} [tick-entry]"
            )
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
                max_positions=int(self._p("max_positions")),
            )
            if trade:
                c.entered = True
                print(f"[ES] TICK ENTRY {c.symbol} {c.direction.upper()} | "
                      f"gap={c.gap_pct:+.1f}% move={c.opening_move:+.1f}% "
                      f"vol={c.vol_ratio:.1f}x OI={c.oi_signal} [tick-level]")
        except Exception as e:
            print(f"[ES] Tick entry error {c.symbol}: {e}")
        finally:
            self._entry_pending.discard(c.token)

    def _register_entry_callbacks(self, env: TradeEnv):
        """Sync tick callbacks with the current confirmed candidate list.

        Called every loop tick during the entry window. Registers callbacks for
        newly confirmed stocks, removes callbacks for stocks that are no longer
        confirmed or have already been entered.
        """
        if not self._plan:
            return

        entered = self._entered_symbols(env)
        should_have = {
            c.token for c in self._plan.candidates
            if c.confirmed and not c.entered and c.symbol not in entered
        }

        # Remove callbacks for tokens no longer in the confirmed set
        for tok in list(self._entry_callbacks_registered):
            if tok not in should_have:
                market.unregister_tick_callback(tok)
                self._entry_callbacks_registered.discard(tok)

        # Register for newly confirmed tokens
        for c in self._plan.candidates:
            if c.token not in should_have:
                continue
            if c.token not in self._entry_callbacks_registered:
                market.register_tick_callback(c.token, self._on_stock_tick)
                self._entry_callbacks_registered.add(c.token)
                print(f"[ES] Tick-entry armed: {c.symbol} {c.direction.upper()} "
                      f"(score={c.score:.2f})")
