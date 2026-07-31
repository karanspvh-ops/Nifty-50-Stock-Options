"""manage.py — ES live position management (trailing SL, peak tracking, exit triggers).

Edit this file when changing:
  - trailing SL activation threshold or gap
  - target exit logic
  - how the trail state is recovered after a restart

Trail design (ratcheting, tick-level):
  - _trail_peak[tid] is updated on EVERY Zerodha WebSocket tick for the option token.
  - The 10-second manage loop reads _trail_peak to compute arm/lock decisions and place exits.
  - This means a spike to +10% that reverses within 10 seconds is still captured — the peak
    is never missed regardless of when the manage loop wakes up.
  - Trail lock ratchets continuously: every new peak raises the floor, it never lowers.
"""

from backend.core.market_state   import market
from backend.core.risk_engine    import risk_engine
from backend.execution.option_selector import current_premium, option_premium
from backend.core.stock_universe import get_option_token

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.es.params import ES_TAG


class ESManageMixin:
    """Live position management for ES.  Touch only this file for SL/target changes."""

    # ── Tick-level peak tracker ───────────────────────────────────────────────

    def _on_option_tick(self, token: str, ltp: float):
        """Called on every WebSocket tick for a monitored option.

        Runs in the tick engine's WebSocket thread — must be fast and non-blocking.
        Only updates _trail_peak; all exit decisions remain in the 10-second manage loop.
        """
        tid = self._option_token_to_trade.get(token)
        if tid is None:
            return
        # Track highest premium seen at tick resolution
        if ltp > self._trail_peak.get(tid, 0.0):
            self._trail_peak[tid] = ltp

    # ── 10-second manage loop ─────────────────────────────────────────────────

    def _manage(self, env: TradeEnv):
        sl_pct  = float(self._p("hard_sl_pct"))
        tgt_pct = float(self._p("target_pct"))
        arm_pct = float(self._p("trail_activate_pct"))
        gap_pct = float(self._p("trail_gap_pct"))

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
                tok     = get_option_token(t.option_symbol)
                premium = option_premium(tok, t.option_symbol)
                if not premium:
                    continue
            except Exception:
                continue

            pnl_pct = (premium - t.entry_price) / t.entry_price * 100
            tid     = t.id

            # ── Register tick callback on first sight of this trade ───────────
            if tid not in self._trail_armed:
                if tok and tid not in self._trade_to_option_token:
                    self._trail_peak[tid]                  = max(premium, t.entry_price)
                    self._option_token_to_trade[tok]       = tid
                    self._trade_to_option_token[tid]       = tok
                    market.register_tick_callback(tok, self._on_option_tick)

                # Recover trail state from DB on restart
                if t.dynamic_sl_price and t.dynamic_sl_price > t.trade_sl_price:
                    self._trail_armed[tid]  = True
                    recovered_lock = (t.dynamic_sl_price - t.entry_price) / t.entry_price * 100
                    self._trail_locked[tid] = recovered_lock
                    print(f"[ES] Trail state recovered from DB for {t.symbol}: locked={recovered_lock:+.1f}%")
                elif t.highest_price and t.highest_price >= t.entry_price * (1 + arm_pct / 100):
                    peak_pnl = (t.highest_price - t.entry_price) / t.entry_price * 100
                    self._trail_armed[tid]  = True
                    self._trail_locked[tid] = peak_pnl - gap_pct
                    print(f"[ES] Trail re-armed from highest_price for {t.symbol}: "
                          f"peak={peak_pnl:.1f}% locked={self._trail_locked[tid]:+.1f}%")
                else:
                    self._trail_armed[tid]  = False
                    self._trail_locked[tid] = -sl_pct

            # ── Tick-level peak (updated by _on_option_tick between loop cycles) ──
            tick_peak  = max(self._trail_peak.get(tid, t.entry_price), premium)
            self._trail_peak[tid] = tick_peak   # ensure current premium is captured too
            peak_pct   = (tick_peak - t.entry_price) / t.entry_price * 100

            # Sync highest_price to DB using tick-level peak
            if t.highest_price is None or tick_peak > t.highest_price:
                with DBSession() as hp_db:
                    hp_db.query(Trade).filter(Trade.id == t.id).update(
                        {"highest_price": tick_peak}, synchronize_session=False)
                    hp_db.commit()
                t.highest_price = tick_peak

            # Worst pnl across this tick and the previous tick — catches SL breaches
            # that recovered within the 10-second polling gap.
            prev_pnl  = self._prev_pnl.get(tid)
            worst_pnl = min(pnl_pct, prev_pnl) if prev_pnl is not None else pnl_pct
            self._prev_pnl[tid] = pnl_pct

            # ── Trail arm check (uses tick-level peak, not current loop price) ──
            if peak_pct >= arm_pct and not self._trail_armed[tid]:
                self._trail_armed[tid] = True
                print(f"[ES] Trail armed on {t.symbol} at peak +{peak_pct:.1f}% "
                      f"(current +{pnl_pct:.1f}%)")

            # ── Ratcheting trail lock (raises floor on every new peak) ──────────
            if self._trail_armed[tid]:
                new_lock = peak_pct - gap_pct
                if new_lock > self._trail_locked[tid]:
                    self._trail_locked[tid] = new_lock
                    new_dyn_sl = round(t.entry_price * (1 + self._trail_locked[tid] / 100), 2)
                    with DBSession() as db:
                        db.query(Trade).filter(Trade.id == tid).update(
                            {"dynamic_sl_price": new_dyn_sl}, synchronize_session=False)
                        db.commit()

            locked = self._trail_locked.get(tid, -sl_pct)

            exit_reason = None
            if pnl_pct >= tgt_pct:
                exit_reason = f"ES Target +{tgt_pct:.0f}%"
            elif worst_pnl <= locked:
                if self._trail_armed.get(tid):
                    exit_reason = (f"ES Trail lock {locked:+.1f}%"
                                   + (f" [gap breach: prev={prev_pnl:+.1f}%]"
                                      if prev_pnl is not None and prev_pnl < pnl_pct else ""))
                else:
                    exit_reason = (f"ES SL -{sl_pct:.0f}%"
                                   + (f" [gap breach: prev={prev_pnl:+.1f}%]"
                                      if prev_pnl is not None and prev_pnl < pnl_pct else ""))

            if exit_reason:
                try:
                    risk_engine.force_exit_trade(t.id, exit_reason)
                    # Unregister tick callback and clean up trail state
                    opt_tok = self._trade_to_option_token.pop(tid, None)
                    if opt_tok:
                        market.unregister_tick_callback(opt_tok)
                        self._option_token_to_trade.pop(opt_tok, None)
                    self._trail_armed.pop(tid, None)
                    self._trail_locked.pop(tid, None)
                    self._trail_peak.pop(tid, None)
                    self._prev_pnl.pop(tid, None)
                    print(f"[ES] Exit {t.symbol} | {exit_reason} | PnL {pnl_pct:+.1f}% | Peak {peak_pct:+.1f}%")
                except Exception as e:
                    print(f"[ES] Exit error {t.symbol}: {e}")
