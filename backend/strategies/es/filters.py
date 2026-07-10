"""filters.py — ES signal detection filters (gap, move, candles, volume, OI).

Edit this file when refining entry filter criteria.
"""

import time as _time
from datetime import datetime, time as dtime
from typing import Optional

from backend.core.market_state   import market
from backend.core.stock_universe import get_meta
from backend.core.broker         import broker
from backend.strategies.es.params import WARM_START, CANDLE_REFREQ


class ESFiltersMixin:
    """All signal-detection methods for ES.  Touch only this file for filter changes."""

    # ── 9:15 open reference capture ───────────────────────────────────────────

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

    def _capture_open_refs(self):
        today = datetime.now().date()
        moves = market.get_all_stock_moves()
        for token, mv in moves.items():
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

    # ── Price helpers ─────────────────────────────────────────────────────────

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

    # ── Candle helpers ────────────────────────────────────────────────────────

    def _maybe_refresh_candles(self, now: datetime):
        """Pull 1-min candles for top candidates at most once per CANDLE_REFREQ seconds."""
        if _time.time() - self._last_candle_time < CANDLE_REFREQ:
            return
        self._last_candle_time = _time.time()

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

    def _consec_candles(self, candles: list, direction: str) -> int:
        """Trailing consecutive green (call) or red (put) candles."""
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

    # ── OI fetch ──────────────────────────────────────────────────────────────

    def _fetch_oi(self, symbol: str, ltp: float, direction: str) -> dict:
        import time as _t
        cached = self._oi_cache.get(symbol, {})
        if cached.get("fetched_at") and _t.time() - cached["fetched_at"] < 120:
            return cached

        try:
            from backend.execution.option_selector import select_option
            tok_ce, sym_ce, strike, expiry = select_option(symbol, ltp, "call")
            tok_pe, sym_pe, _,      _      = select_option(symbol, ltp, "put")
            if not sym_ce or not sym_pe:
                return {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}

            kite = broker.kite()
            q = kite.quote([f"NFO:{sym_ce}", f"NFO:{sym_pe}"])
            ce_oi = q.get(f"NFO:{sym_ce}", {}).get("oi", 0) or 0
            pe_oi = q.get(f"NFO:{sym_pe}", {}).get("oi", 0) or 0

            if ce_oi > pe_oi * 1.25:
                signal = "bullish"
            elif pe_oi > ce_oi * 1.25:
                signal = "bearish"
            else:
                signal = "neutral"

            result = {"oi_ce": int(ce_oi), "oi_pe": int(pe_oi),
                      "signal": signal, "fetched_at": _t.time()}
            self._oi_cache[symbol] = result
            return result
        except Exception:
            return {"oi_ce": 0, "oi_pe": 0, "signal": "neutral"}
