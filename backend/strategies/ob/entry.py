"""entry.py — OB plan building, re-validation, and position entry.

Edit this file for:
  - changing sector selection / breadth logic
  - adjusting breakout confirmation criteria
  - re-validation at entry time (move retention, candle reversal)
  - early gap-acceleration entry logic
"""

from datetime import date, datetime
from typing import List, Optional

from backend.core.market_state      import market
from backend.core.stock_universe    import get_stocks_for_index
from backend.core.session_manager   import get_or_create_session, set_selected_stocks
from backend.core.breakout_confirm  import confirm_breakout
from backend.execution.order_executor import place_entry_order

from backend.database import TradeEnv, TradeDirection, SessionStatus
from backend.core.session_manager import update_session_status

from backend.strategies.ob.params import (
    OB_TAG, MOVE_MIN_PCT, CANDIDATE_POOL, RFACTOR_MIN,
    MIN_SECTOR_MOVERS, SECTOR_MIN_PCT, CLARITY_NET, BREADTH_MIN_PCT,
)
from backend.strategies.ob.types import PlanStock, TradePlan


def _log_ob_confirmed(env, ps: "PlanStock", bo, move: float) -> Optional[int]:
    """Insert a row the moment a candidate passes confirm_breakout() -- before any
    of the downstream gates (vol floor, consec-exhausted, refill block, reval) run.
    Returns the row id so the outcome can be patched in once known, or None if the
    row wasn't written -- either way the caller should not treat entry as blocked
    by logging (this is telemetry, not a trading gate)."""
    try:
        from backend.database import Session as DBSession
        from backend.storage.models import OBCandidate
        db = DBSession()
        try:
            row = OBCandidate(
                date=date.today().isoformat(), env=env, symbol=ps.symbol, token=ps.token,
                sector=ps.sector, direction=TradeDirection(ps.direction),
                opening_move_pct=move, r_factor=ps.r_factor, consec=bo.consec,
                vol_ratio=bo.vol_ratio, vp_poc=bo.vp50.get("poc"), vp_vah=bo.vp50.get("vah"),
                vp_val=bo.vp50.get("val"), confirm_reason=bo.reason,
                confirmed_at=datetime.now(),
            )
            db.add(row)
            db.commit()
            db.refresh(row)
            return row.id
        finally:
            db.close()
    except Exception as e:
        print(f"[OB] candidate log insert failed: {e}")
        return None


def _log_ob_outcome(candidate_id: Optional[int], blocked_reason: Optional[str] = None,
                     was_traded: bool = False, trade_id: Optional[int] = None):
    """Patch the confirmed-candidate row with what happened after confirmation."""
    if candidate_id is None:
        return
    try:
        from backend.database import Session as DBSession
        from backend.storage.models import OBCandidate
        db = DBSession()
        try:
            row = db.get(OBCandidate, candidate_id)
            if row:
                row.blocked_reason = blocked_reason
                row.was_traded     = was_traded
                row.trade_id       = trade_id
                db.commit()
        finally:
            db.close()
    except Exception as e:
        print(f"[OB] candidate log update failed: {e}")


class OBEntryMixin:
    """Plan building and position entry for OB.  Touch only this file for entry logic changes."""

    # ── Plan builder ──────────────────────────────────────────────────────────

    def _build_plan(self, status: str) -> Optional[TradePlan]:
        sector_moves = market.get_sector_moves()
        if not sector_moves:
            return TradePlan(status=status, trend="neutral", sector=None,
                             direction=None, sector_pct=0.0,
                             note="Waiting for market data…",
                             generated_at=datetime.now().isoformat())

        # Smart direction: lock a side only when breadth is decisive
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

        # Qualifying sectors
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
        direction  = qual[sector][1]

        # Candidates: all F&O stocks; each takes its own direction
        candidates: List[PlanStock] = []
        for s in get_stocks_for_index("FNO", fno_only=True):
            token        = s["token"]
            stock_sector = s.get("sector", "")
            if stock_sector not in qual:
                continue
            mv = market.get_stock_move(token)
            if not mv:
                continue
            ltp      = mv.get("ltp") or 0
            day_move = mv.get("pct_change", 0)
            sdir = "call" if day_move > 0 else "put"
            if sdir not in allowed:
                continue
            opening_move = self._opening_move(token, ltp)
            move = opening_move if self._open_ref.get(token) else day_move
            if abs(move) < self._p("move_min_pct"):
                continue
            own_sec_avg = abs(sector_moves.get(stock_sector, {}).get("pct_change", 0)) or 0.1
            r_factor    = round(abs(day_move) / own_sec_avg, 2)
            if r_factor < RFACTOR_MIN:
                continue
            candidates.append(PlanStock(
                symbol=s["symbol"], token=token, sector=stock_sector, direction=sdir,
                opening_move=move, day_move=day_move,
                r_factor=r_factor, ltp=ltp, est_premium=round(ltp * 0.015, 2),
                eligible=True,
            ))

        sector_strength = {sec: abs(d.get("pct_change", 0)) for sec, d in sector_moves.items()}
        candidates.sort(
            key=lambda c: sector_strength.get(c.sector, 0) * abs(c.opening_move),
            reverse=True
        )
        top = candidates[:CANDIDATE_POOL]

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

    # ── Entry-time re-validation ──────────────────────────────────────────────

    def _revalidate_ob(self, ps: PlanStock, current_move: float) -> "tuple[bool, str]":
        """Re-validate at entry: move retention, last candle direction, majority reversal.

        Three checks:
          1. Move retention: current move must be >=60% of plan's recorded move.
          2. Immediate candle: last candle must still be in trade direction.
          3. Multi-candle: if 2 of the last 3 prior candles reversed, momentum gone.
        """
        if ps.opening_move:
            need = abs(ps.opening_move) * 0.6
            if abs(current_move) < need:
                return False, (f"move faded {current_move:+.2f}% vs plan {ps.opening_move:+.2f}% "
                               f"(need >={need:.1f}%, 60% retention)")

        try:
            candles = market.get_candles(ps.token, n=3)
            if candles:
                lc        = candles[-1]
                last_bull = lc.get("close", 0) >= lc.get("open", 0)
                if ps.direction == "call" and not last_bull:
                    return False, "last candle RED — immediate reversal, skip"
                if ps.direction == "put" and last_bull:
                    return False, "last candle GREEN — immediate reversal, skip"

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

    # ── Entry ─────────────────────────────────────────────────────────────────

    def _enter_positions(self, env: TradeEnv, early_only: bool = False):
        if not self._plan:
            return
        open_ob = self._count_open_ob(env)
        session = get_or_create_session(env)
        max_pos = int(self._p("max_positions"))

        total_ob_today = self._count_ob_today(env)
        net_ob_pnl     = self._ob_net_pnl_today(env) if total_ob_today >= max_pos else 0.0

        for ps in self._plan.stocks:
            if open_ob >= max_pos:
                break
            if ps.entered or self._already_in(env, ps.symbol):
                continue

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

            early = False
            if early_only:
                if not self._gap_qualifies(ps):
                    continue
                early = True

            if market.is_trading_halted():
                break

            bo = confirm_breakout(ps.token, ps.direction, move_pct=move)
            if not bo.confirmed:
                print(f"[OB] {ps.symbol} not confirmed — {bo.reason}")
                continue

            # Persist the confirmed candidate now, before any downstream gate can
            # short-circuit it -- this is the full scan->confirmed funnel, not just
            # what got traded. Outcome fields get patched below as they're known.
            candidate_id = _log_ob_confirmed(env, ps, bo, move)

            vol_floor = float(self._p("vol_ratio_min"))
            if bo.vol_ratio < vol_floor:
                print(f"[OB] {ps.symbol} SKIP — vol {bo.vol_ratio:.1f}x below floor {vol_floor}x (no momentum signal)")
                _log_ob_outcome(candidate_id, blocked_reason=f"vol {bo.vol_ratio:.1f}x below floor {vol_floor}x")
                continue
            if bo.vol_ratio < 3.0:
                print(f"[OB] {ps.symbol} LOW VOL {bo.vol_ratio:.1f}x — sector drift trade, liquidity check in executor")
            if bo.consec > int(self._p("consec_max")):
                print(f"[OB] {ps.symbol} skipped — {bo.consec} consec candles > max {self._p('consec_max')} (exhausted)")
                _log_ob_outcome(candidate_id, blocked_reason=f"{bo.consec} consec candles > max {self._p('consec_max')}")
                continue

            if total_ob_today >= max_pos and net_ob_pnl > 0:
                if not self._is_extreme_conviction(ps, bo, move):
                    print(f"[OB] {ps.symbol} REFILL BLOCK — "
                          f"day net +Rs.{net_ob_pnl:,.0f} after {total_ob_today} trades, "
                          f"vol {bo.vol_ratio:.1f}x / R {ps.r_factor:.1f} not spike-level")
                    _log_ob_outcome(candidate_id, blocked_reason=(
                        f"refill block — day net +Rs.{net_ob_pnl:,.0f} after {total_ob_today} trades, "
                        f"not spike-level"))
                    continue
                print(f"[OB] {ps.symbol} EXTREME CONVICTION REFILL — "
                      f"vol {bo.vol_ratio:.1f}x / R {ps.r_factor:.1f} / move {move:+.2f}% "
                      f"(day net +Rs.{net_ob_pnl:,.0f})")

            ok, reval_why = self._revalidate_ob(ps, move)
            if not ok:
                print(f"[OB] {ps.symbol} REVAL SKIP — {reval_why}")
                _log_ob_outcome(candidate_id, blocked_reason=f"reval skip — {reval_why}")
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
                sl_pct_override=self._p("hard_sl_pct"),
                target_pct_override=self._p("target_pct"),
                max_positions=int(self._p("max_positions")),
            )
            if trade:
                ps.entered = True
                open_ob += 1
                update_session_status(env, SessionStatus.ACTIVE)
                _log_ob_outcome(candidate_id, was_traded=True, trade_id=trade.id)
                print(f"[OB] ENTERED {ps.symbol} {self._plan.direction.upper()} @ "
                      f"{trade.entry_price:.2f} | SL 10% target 50%")
            else:
                _log_ob_outcome(candidate_id, blocked_reason="order placement failed")
