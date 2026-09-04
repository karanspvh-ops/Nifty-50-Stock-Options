"""manage.py — OB live position management (trailing SL, peak tracking, target exits).

Edit this file when changing:
  - trailing stop activation threshold or gap percentage
  - single-lot vs multi-lot exit logic
  - how peak (highest_price) is tracked

Trail design (ratcheting, tick-level):
  - _ob_trail_peak[tid] is updated on EVERY Zerodha WebSocket tick for the option token.
  - Trail arm + ratchet happen on every tick: dynamic_sl_price is written to DB in a
    background thread immediately when a new peak is hit.
  - SL breach is evaluated on every tick using the freshly-ratcheted trail_stop.
  - The 1-second loop is a safety fallback (highest_price DB sync, target exits, restart
    recovery) — all SL/ratchet logic is primarily tick-driven.
"""

import threading

from backend.core.market_state  import market
from backend.core.risk_engine   import risk_engine
from backend.core.stock_universe import get_option_token
from backend.execution.option_selector import current_premium

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.ob.params import OB_TAG


class OBManageMixin:
    """Live position management for OB.  Touch only this file for SL/target changes."""

    # ── Tick-level peak tracker + SL check ───────────────────────────────────

    def _on_ob_option_tick(self, token: str, ltp: float):
        """Called on every WebSocket tick for a monitored OB option.

        Tracks peak, arms trail, ratchets dynamic SL, and triggers SL exit —
        all at tick resolution so the SL floor rises immediately on each new peak.
        """
        tid = self._ob_option_token_to_trade.get(token)
        if tid is None:
            return
        if tid in self._ob_pending_exit:
            return

        # ── 1. Track highest premium ──────────────────────────────────────────
        if ltp > self._ob_trail_peak.get(tid, 0.0):
            self._ob_trail_peak[tid] = ltp

        entry = self._ob_entry_cache.get(tid)
        if entry is None:
            return

        peak     = self._ob_trail_peak[tid]
        peak_pct = (peak - entry) / entry * 100

        hard_sl_pct  = float(self._p("hard_sl_pct"))
        activate_pct = float(self._p("trail_activate_pct"))
        gap_pct      = float(self._p("trail_gap_pct"))
        hard_sl      = entry * (1 - hard_sl_pct / 100)

        # ── 2. Trail arm + ratchet ────────────────────────────────────────────
        if peak_pct >= activate_pct and not self._ob_trail_armed.get(tid, False):
            self._ob_trail_armed[tid] = True
            print(f"[OB] Trail armed on tick for tid={tid} at peak +{peak_pct:.1f}%")

        trail_stop = hard_sl
        if self._ob_trail_armed.get(tid):
            new_trail = max(hard_sl, peak * (1 - gap_pct / 100))
            new_dsl   = round(new_trail, 2)
            if new_dsl != self._ob_trail_locked.get(tid):
                self._ob_trail_locked[tid] = new_dsl
                threading.Thread(
                    target=self._update_ob_dynamic_sl_db,
                    args=(tid, new_dsl),
                    daemon=True,
                ).start()
            trail_stop = new_trail

        # ── 3. Tick-level SL breach check ────────────────────────────────────
        if ltp <= trail_stop:
            self._ob_pending_exit.add(tid)
            lock_pct = (trail_stop - entry) / entry * 100
            reason = (
                f"OB Trailing stop (locked {lock_pct:+.1f}%) [tick-level]"
                if trail_stop > hard_sl
                else f"OB Hard SL -{hard_sl_pct:.0f}% [tick-level]"
            )
            threading.Thread(
                target=self._ob_tick_sl_exit,
                args=(tid, reason),
                daemon=True,
            ).start()

    def _ob_tick_sl_exit(self, tid: int, reason: str):
        """Runs in a dedicated daemon thread — executes OB SL exit at tick speed."""
        try:
            result = risk_engine.force_exit_trade(tid, reason)
            if "error" in result:
                print(f"[OB] Tick SL exit skipped tid={tid}: {result['error']}")
            else:
                print(f"[OB] Tick-level SL exit: tid={tid} | {reason}")
                market.push_trade_update({"type": "trade_closed", "trade_id": tid})
        except Exception as e:
            print(f"[OB] Tick SL exit error tid={tid}: {e}")
        finally:
            self._ob_pending_exit.discard(tid)
            self._ob_cleanup_trade(tid)

    def _update_ob_dynamic_sl_db(self, tid: int, new_dsl: float):
        """Background thread: persist dynamic_sl_price to DB without blocking tick callback."""
        try:
            with DBSession() as db:
                db.query(Trade).filter(Trade.id == tid).update(
                    {"dynamic_sl_price": new_dsl}, synchronize_session=False)
                db.commit()
        except Exception as e:
            print(f"[OB] Dynamic SL DB update error tid={tid}: {e}")

    def _ob_cleanup_trade(self, tid: int):
        """Unregister tick callback and clear in-memory state for a closed OB trade."""
        opt_tok = self._ob_trade_to_option_token.pop(tid, None)
        if opt_tok:
            market.unregister_tick_callback(opt_tok)
            self._ob_option_token_to_trade.pop(opt_tok, None)
        self._ob_trail_armed.pop(tid,  None)
        self._ob_trail_locked.pop(tid, None)
        self._ob_trail_peak.pop(tid,   None)
        self._ob_entry_cache.pop(tid,  None)

    # ── 1-second manage loop (safety fallback — primary ratchet/SL at tick level) ────

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

        for t in ob_trades:
            tid = t.id
            if not t.entry_price:
                continue
            if tid in self._ob_pending_exit:
                continue

            entry = t.entry_price

            # ── First-sight init: register tick callback ──────────────────────
            if tid not in self._ob_entry_cache:
                hard_sl_pct = float(self._p("hard_sl_pct"))
                self._ob_entry_cache[tid] = entry

                # Recover trail state from DB (handles backend restarts mid-trade)
                if t.dynamic_sl_price and t.dynamic_sl_price > entry * (1 - hard_sl_pct / 100):
                    self._ob_trail_armed[tid]  = True
                    self._ob_trail_locked[tid] = t.dynamic_sl_price
                    print(f"[OB] Trail recovered from DB for tid={tid}: dsl={t.dynamic_sl_price}")
                else:
                    self._ob_trail_armed[tid]  = False
                    self._ob_trail_locked[tid] = entry * (1 - hard_sl_pct / 100)

                tok = get_option_token(t.option_symbol)
                if tok and tid not in self._ob_trade_to_option_token:
                    init_peak = max(t.highest_price or entry, entry)
                    self._ob_trail_peak[tid]            = init_peak
                    self._ob_option_token_to_trade[tok] = tid
                    self._ob_trade_to_option_token[tid] = tok
                    market.register_tick_callback(tok, self._on_ob_option_tick)
                    print(f"[OB] Tick callback registered for tid={tid} {t.option_symbol}")

            ltp = current_premium(t)
            if not ltp:
                continue

            pnl_pct = (ltp - entry) / entry * 100

            # Sync highest_price to DB using tick-level peak (tick may have seen higher)
            tick_peak = max(self._ob_trail_peak.get(tid, entry), ltp, entry)
            self._ob_trail_peak[tid] = tick_peak
            if t.highest_price is None or tick_peak > t.highest_price:
                self._set_highest(t.id, tick_peak)
                t.highest_price = tick_peak

            hard_sl_pct = float(self._p("hard_sl_pct"))
            target_pct  = float(self._p("target_pct"))
            second_pct  = float(self._p("second_target_pct"))
            hard_sl     = entry * (1 - hard_sl_pct / 100)

            # Use tick-level trail_stop (already ratcheted by _on_ob_option_tick)
            trail_stop = self._ob_trail_locked.get(tid, hard_sl)

            # ── Target exits ──────────────────────────────────────────────────
            if pnl_pct >= target_pct and t.quantity <= 1:
                self._ob_pending_exit.add(tid)
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Target +{target_pct:.0f}% hit")
                market.push_trade_update({"type": "trade_closed", "trade_id": tid})
                self._ob_cleanup_trade(tid)
                self._ob_pending_exit.discard(tid)
                continue
            if pnl_pct >= second_pct:
                self._ob_pending_exit.add(tid)
                risk_engine.force_exit_trade(t.id, f"{OB_TAG} Runner target +{second_pct:.0f}% hit")
                market.push_trade_update({"type": "trade_closed", "trade_id": tid})
                self._ob_cleanup_trade(tid)
                self._ob_pending_exit.discard(tid)
                continue

            # ── Fallback SL/trail exit (tick callback is primary; loop is safety net) ──
            if ltp <= trail_stop and tid not in self._ob_pending_exit:
                self._ob_pending_exit.add(tid)
                lock_pct = (trail_stop - entry) / entry * 100
                reason = (
                    f"{OB_TAG} Trailing stop (locked {lock_pct:+.1f}%) [loop fallback]"
                    if trail_stop > hard_sl
                    else f"{OB_TAG} Hard SL -{hard_sl_pct:.0f}% hit [loop fallback]"
                )
                risk_engine.force_exit_trade(t.id, reason)
                market.push_trade_update({"type": "trade_closed", "trade_id": tid})
                self._ob_cleanup_trade(tid)
                self._ob_pending_exit.discard(tid)

    def _set_highest(self, trade_id: int, price: float):
        db = DBSession()
        try:
            t = db.query(Trade).filter(Trade.id == trade_id).first()
            if t:
                t.highest_price = price
                db.commit()
        finally:
            db.close()
