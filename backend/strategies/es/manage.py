"""manage.py — ES live position management (trailing SL, peak tracking, exit triggers).

Edit this file when changing:
  - trailing SL activation threshold or gap
  - target exit logic
  - how the trail state is recovered after a restart

Trail design (ratcheting, tick-level):
  - _trail_peak[tid] is updated on EVERY Zerodha WebSocket tick for the option token.
  - Trail arm + ratchet also happen on every tick: the SL floor rises immediately each
    time a new peak is detected, and dynamic_sl_price is written to DB in a background
    thread without blocking the tick callback.
  - SL breach is evaluated on every tick using the freshly-ratcheted lock — exit fires
    immediately via a background thread.
  - The 1-second loop remains as a safety fallback (highest_price DB sync, target exits,
    restart recovery) but all SL/ratchet logic is primarily tick-driven.
  - Trail lock ratchets continuously: every new peak raises the floor, it never lowers.
  - Dashboard WebSocket clients receive a live push on each tick (throttled to 10 Hz).
"""

import threading
import time as _time

from backend.core.market_state   import market
from backend.core.risk_engine    import risk_engine
from backend.execution.option_selector import current_premium, option_premium
from backend.core.stock_universe import get_option_token

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.es.params import ES_TAG

_PUSH_INTERVAL = 0.1   # minimum seconds between WS pushes per trade (10 Hz max)


class ESManageMixin:
    """Live position management for ES.  Touch only this file for SL/target changes."""

    # ── Tick-level peak tracker + SL check ───────────────────────────────────

    def _on_option_tick(self, token: str, ltp: float):
        """Called on every WebSocket tick for a monitored option.

        Runs in the Zerodha tick thread — must be fast and non-blocking.
        Does three things:
          1. Updates _trail_peak (highest premium seen at tick resolution)
          2. Checks SL breach and spawns exit thread if breached
          3. Pushes live data to dashboard WS clients (throttled)
        """
        tid = self._option_token_to_trade.get(token)
        if tid is None:
            return

        # Skip if exit is already in flight for this trade
        if tid in self._pending_exit:
            return

        # ── 1. Track highest + lowest premium ─────────────────────────────────
        if ltp > self._trail_peak.get(tid, 0.0):
            self._trail_peak[tid] = ltp
        if ltp < self._trail_trough.get(tid, ltp):
            self._trail_trough[tid] = ltp

        # ── 2. Trail arm + ratchet (tick-level) ──────────────────────────────
        entry = self._entry_cache.get(tid)
        if entry is None:
            return

        peak     = self._trail_peak[tid]
        peak_pct = (peak - entry) / entry * 100
        sl_pct   = float(self._p("hard_sl_pct"))
        arm_pct  = float(self._p("trail_activate_pct"))
        gap_pct  = float(self._p("trail_gap_pct"))

        if peak_pct >= arm_pct and not self._trail_armed.get(tid, False):
            self._trail_armed[tid] = True

        if self._trail_armed.get(tid):
            new_lock = peak_pct - gap_pct
            if new_lock > self._trail_locked.get(tid, -sl_pct):
                self._trail_locked[tid] = new_lock
                new_dyn_sl = round(entry * (1 + new_lock / 100), 2)
                threading.Thread(
                    target=self._update_es_dynamic_sl_db,
                    args=(tid, new_dyn_sl),
                    daemon=True,
                ).start()

        locked  = self._trail_locked.get(tid, -sl_pct)
        pnl_pct = (ltp - entry) / entry * 100

        # ── 3. Tick-level SL check ────────────────────────────────────────────
        if pnl_pct <= locked:
            self._pending_exit.add(tid)
            armed  = bool(self._trail_armed.get(tid))
            reason = (f"ES Trail SL {locked:+.1f}% [tick-level]"
                      if armed
                      else f"ES Hard SL -{sl_pct:.0f}% [tick-level]")
            threading.Thread(
                target=self._tick_sl_exit,
                args=(tid, reason),
                daemon=True,
            ).start()
            return

        # ── 4. WS push (throttled to _PUSH_INTERVAL per trade) ───────────────
        now = _time.monotonic()
        if now - self._last_push.get(tid, 0.0) >= _PUSH_INTERVAL:
            self._last_push[tid] = now
            tgt_pct = float(self._p("target_pct"))
            armed   = bool(self._trail_armed.get(tid))
            market.push_trade_update({
                "type":        "trade_tick",
                "trade_id":    tid,
                "symbol":      self._symbol_cache.get(tid, ""),
                "ltp":         round(ltp, 2),
                "pnl_pct":     round(pnl_pct, 2),
                "dynamic_sl":  round(entry * (1 + locked / 100), 2) if armed else None,
                "hard_sl":     round(entry * (1 - sl_pct / 100), 2),
                "target":      round(entry * (1 + tgt_pct / 100), 2),
                "peak_pct":    round(peak_pct, 2),
                "trail_armed": armed,
            })

        # ── 5. Portfolio snapshot (in-memory only — flushed to DB by 1s loop) ─
        self._live_ltp_registry[tid] = ltp
        unrealized = sum(
            (self._live_ltp_registry.get(t, e) - e) * self._position_size_cache.get(t, 0)
            for t, e in self._entry_cache.items()
            if t not in self._pending_exit
        )
        capital = 500000.0
        total   = self._realized_pnl_cache + unrealized
        self._latest_snapshot = {
            "realized_pnl":     round(self._realized_pnl_cache, 2),
            "unrealized_pnl":   round(unrealized, 2),
            "total_pnl":        round(total, 2),
            "total_pct":        round(total / capital * 100, 4),
            "open_trade_count": len([t for t in self._entry_cache if t not in self._pending_exit]),
            "capital":          capital,
        }

    def _tick_sl_exit(self, tid: int, reason: str):
        """Runs in a dedicated daemon thread — executes SL exit at tick speed."""
        result = None
        try:
            result = risk_engine.force_exit_trade(tid, reason)
            if "error" in result:
                print(f"[ES] Tick SL exit skipped tid={tid}: {result['error']}")
            else:
                print(f"[ES] Tick-level SL exit: tid={tid} | {reason}")
                market.push_trade_update({"type": "trade_closed", "trade_id": tid})
        except Exception as e:
            print(f"[ES] Tick SL exit error tid={tid}: {e}")
        finally:
            self._pending_exit.discard(tid)
            # Update realized PnL cache using the actual fill price returned by
            # force_exit_trade — the same value written to trade.exit_price/pnl.
            entry      = self._entry_cache.get(tid, 0.0)
            pos_size   = self._position_size_cache.get(tid, 0.0)
            exit_price = result.get("exit_price", entry) if isinstance(result, dict) else entry
            self._realized_pnl_cache += (exit_price - entry) * pos_size
            # Clean up tick tracking (same as manage-loop exit cleanup)
            opt_tok = self._trade_to_option_token.pop(tid, None)
            if opt_tok:
                market.unregister_tick_callback(opt_tok)
                self._option_token_to_trade.pop(opt_tok, None)
            self._trail_armed.pop(tid,         None)
            self._trail_locked.pop(tid,        None)
            self._trail_peak.pop(tid,          None)
            self._trail_trough.pop(tid,        None)
            self._prev_pnl.pop(tid,            None)
            self._entry_cache.pop(tid,         None)
            self._symbol_cache.pop(tid,        None)
            self._last_push.pop(tid,           None)
            self._live_ltp_registry.pop(tid,   None)
            self._position_size_cache.pop(tid, None)
            self._option_symbol_cache.pop(tid, None)

    def _flush_es_snapshot(self):
        """Write latest in-memory portfolio snapshot to DB. Called once per 1s loop.

        Unrealized PnL is recomputed here from a fresh kite.quote() call (not the
        tick-cached _live_ltp_registry) so the DB row reflects live LTPs rather than
        whatever the last WebSocket tick happened to be, which can lag by seconds.
        """
        try:
            from backend.core.clock  import now_ist
            from backend.core.broker import broker
            from backend.storage.models import ESPortfolioSnapshot

            open_tids = [t for t in self._entry_cache if t not in self._pending_exit]
            unrealized = 0.0
            if open_tids:
                symbols = {tid: self._option_symbol_cache.get(tid) for tid in open_tids}
                keys    = [f"NFO:{sym}" for sym in symbols.values() if sym]
                ltps    = {}
                if keys:
                    try:
                        q = broker.kite().quote(keys)
                        for tid, sym in symbols.items():
                            if sym and f"NFO:{sym}" in q:
                                ltps[tid] = float(q[f"NFO:{sym}"]["last_price"])
                    except Exception as e:
                        print(f"[ES] Snapshot quote fetch error: {e}")
                for tid in open_tids:
                    entry    = self._entry_cache.get(tid, 0.0)
                    ltp      = ltps.get(tid, self._live_ltp_registry.get(tid, entry))
                    pos_size = self._position_size_cache.get(tid, 0.0)
                    unrealized += (ltp - entry) * pos_size

            capital = 500000.0
            total   = self._realized_pnl_cache + unrealized
            now     = now_ist()
            with DBSession() as db:
                db.add(ESPortfolioSnapshot(
                    date             = str(now.date()),
                    snapshot_time    = now,
                    realized_pnl     = round(self._realized_pnl_cache, 2),
                    unrealized_pnl   = round(unrealized, 2),
                    total_pnl        = round(total, 2),
                    total_pct        = round(total / capital * 100, 4),
                    open_trade_count = len(open_tids),
                    capital          = capital,
                ))
                db.commit()
        except Exception as e:
            print(f"[ES] Snapshot flush error: {e}")

    def _update_es_dynamic_sl_db(self, tid: int, new_dyn_sl: float):
        """Background thread: persist dynamic_sl_price to DB without blocking tick callback."""
        try:
            with DBSession() as db:
                db.query(Trade).filter(Trade.id == tid).update(
                    {"dynamic_sl_price": new_dyn_sl}, synchronize_session=False)
                db.commit()
        except Exception as e:
            print(f"[ES] Dynamic SL DB update error tid={tid}: {e}")

    # ── 1-second manage loop (safety fallback — primary ratchet/SL at tick level) ────

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

            tid = t.id

            # Skip trades where a tick-level exit is already in flight
            if tid in self._pending_exit:
                continue

            try:
                tok     = get_option_token(t.option_symbol)
                premium = option_premium(tok, t.option_symbol)
                if not premium:
                    continue
            except Exception:
                continue

            pnl_pct = (premium - t.entry_price) / t.entry_price * 100

            # ── Seed realized PnL cache once per day (handles restart recovery) ──
            if not self._realized_pnl_cache_seeded:
                self._realized_pnl_cache        = self._net_pnl_today(env)
                self._realized_pnl_cache_seeded = True

            # ── First-sight init: set state BEFORE registering tick callback ──
            if tid not in self._trail_armed:
                # Trail/SL state (recovered from DB or fresh)
                if t.dynamic_sl_price and t.dynamic_sl_price > t.trade_sl_price:
                    self._trail_armed[tid]  = True
                    recovered_lock = (t.dynamic_sl_price - t.entry_price) / t.entry_price * 100
                    self._trail_locked[tid] = recovered_lock
                    print(f"[ES] Trail recovered from DB for {t.symbol}: locked={recovered_lock:+.1f}%")
                elif t.highest_price and t.highest_price >= t.entry_price * (1 + arm_pct / 100):
                    peak_pnl = (t.highest_price - t.entry_price) / t.entry_price * 100
                    self._trail_armed[tid]  = True
                    self._trail_locked[tid] = peak_pnl - gap_pct
                    print(f"[ES] Trail re-armed from highest_price for {t.symbol}: "
                          f"peak={peak_pnl:.1f}% locked={self._trail_locked[tid]:+.1f}%")
                else:
                    self._trail_armed[tid]  = False
                    self._trail_locked[tid] = -sl_pct

                # Cache entry/symbol/position-size for tick-level SL, WS, and snapshot
                self._entry_cache[tid]         = t.entry_price
                self._symbol_cache[tid]        = t.symbol
                self._position_size_cache[tid] = t.quantity * t.lot_size
                self._option_symbol_cache[tid] = t.option_symbol

                # Register callback only after all state is ready (no race window)
                if tok and tid not in self._trade_to_option_token:
                    self._trail_peak[tid]                  = max(premium, t.entry_price)
                    self._trail_trough[tid]                = min(premium, t.entry_price)
                    self._option_token_to_trade[tok]       = tid
                    self._trade_to_option_token[tid]       = tok
                    market.register_tick_callback(tok, self._on_option_tick)

            # ── Tick-level peak (updated by _on_option_tick between loop cycles) ──
            tick_peak  = max(self._trail_peak.get(tid, t.entry_price), premium)
            self._trail_peak[tid] = tick_peak
            peak_pct   = (tick_peak - t.entry_price) / t.entry_price * 100

            # Sync highest_price to DB using tick-level peak
            if t.highest_price is None or tick_peak > t.highest_price:
                with DBSession() as hp_db:
                    hp_db.query(Trade).filter(Trade.id == t.id).update(
                        {"highest_price": tick_peak}, synchronize_session=False)
                    hp_db.commit()
                t.highest_price = tick_peak

            # ── Tick-level trough (updated by _on_option_tick between loop cycles) ──
            tick_trough = min(self._trail_trough.get(tid, t.entry_price), premium)
            self._trail_trough[tid] = tick_trough

            # Sync lowest_price to DB using tick-level trough
            if t.lowest_price is None or tick_trough < t.lowest_price:
                with DBSession() as lp_db:
                    lp_db.query(Trade).filter(Trade.id == t.id).update(
                        {"lowest_price": tick_trough}, synchronize_session=False)
                    lp_db.commit()
                t.lowest_price = tick_trough

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

            # ── Loop-level exit check (SL already handled at tick level) ────────
            exit_reason = None
            if pnl_pct >= tgt_pct:
                exit_reason = f"ES Target +{tgt_pct:.0f}%"
            elif worst_pnl <= locked and tid not in self._pending_exit:
                # Fallback: tick-level exit may have missed this (e.g. restart recovery)
                if self._trail_armed.get(tid):
                    exit_reason = (f"ES Trail lock {locked:+.1f}%"
                                   + (f" [gap breach: prev={prev_pnl:+.1f}%]"
                                      if prev_pnl is not None and prev_pnl < pnl_pct else ""))
                else:
                    exit_reason = (f"ES SL -{sl_pct:.0f}%"
                                   + (f" [gap breach: prev={prev_pnl:+.1f}%]"
                                      if prev_pnl is not None and prev_pnl < pnl_pct else ""))

            if exit_reason and tid not in self._pending_exit:
                self._pending_exit.add(tid)
                try:
                    result = risk_engine.force_exit_trade(t.id, exit_reason)
                    market.push_trade_update({"type": "trade_closed", "trade_id": tid})
                    # Update realized PnL cache using the actual fill price returned by
                    # force_exit_trade — the same value written to trade.exit_price/pnl.
                    exit_price = result.get("exit_price", premium) if isinstance(result, dict) else premium
                    self._realized_pnl_cache += (exit_price - t.entry_price) * t.quantity * t.lot_size
                    # Clean up tick tracking
                    opt_tok = self._trade_to_option_token.pop(tid, None)
                    if opt_tok:
                        market.unregister_tick_callback(opt_tok)
                        self._option_token_to_trade.pop(opt_tok, None)
                    self._trail_armed.pop(tid,         None)
                    self._trail_locked.pop(tid,        None)
                    self._trail_peak.pop(tid,          None)
                    self._trail_trough.pop(tid,        None)
                    self._prev_pnl.pop(tid,            None)
                    self._entry_cache.pop(tid,         None)
                    self._symbol_cache.pop(tid,        None)
                    self._last_push.pop(tid,           None)
                    self._live_ltp_registry.pop(tid,   None)
                    self._position_size_cache.pop(tid, None)
                    self._option_symbol_cache.pop(tid, None)
                    self._pending_exit.discard(tid)
                    print(f"[ES] Exit {t.symbol} | {exit_reason} | PnL {pnl_pct:+.1f}% | Peak {peak_pct:+.1f}%")
                except Exception as e:
                    self._pending_exit.discard(tid)
                    print(f"[ES] Exit error {t.symbol}: {e}")

        # ── Snapshot flush (once per 1s loop, outside per-trade loop) ─────────
        if self._latest_snapshot:
            self._flush_es_snapshot()
