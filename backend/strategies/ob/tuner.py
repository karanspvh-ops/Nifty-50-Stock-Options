"""tuner.py — OB ML auto-tuning of the trailing gap parameter.

Completely isolated from trading logic. Edit this file for:
  - tuning frequency (MIN_TRADES_TO_TUNE, RETUNE_EVERY)
  - give-back / peak-low thresholds that trigger adjustments
  - bounds on the trail_gap_pct adjustment range
"""

from datetime import datetime

from backend.database import Session as DBSession, Trade, TradeStatus, TradeEnv
from backend.strategies.ob.params import OB_TAG, MIN_TRADES_TO_TUNE, RETUNE_EVERY


class OBTunerMixin:
    """ML auto-tuning for OB trailing gap.  Isolated — safe to edit without affecting trading."""

    def analyze_and_tune(self, env: TradeEnv = TradeEnv.PAPER) -> dict:
        """Study trailing-stop behavior across completed trades; adjust trail_gap_pct if warranted.

        Logic:
          - Large avg give-back vs current gap → gap too loose → tighten by 3%.
          - Low avg peak + poor win-rate → gap too tight → widen by 3%.
          - Bounded [8%, 35%]. Applies only if auto_tune_enabled=True.
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

        wins     = [t for t in trades if (t.pnl or 0) > 0]
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

        cur_gap  = self._p("trail_gap_pct")
        avg_give = round(sum(givebacks) / len(givebacks), 1) if givebacks else 0
        avg_peak = round(sum(peaks) / len(peaks), 1) if peaks else 0

        new_gap   = cur_gap
        rationale = ""
        if givebacks and avg_give > cur_gap * 1.25:
            new_gap   = max(8.0, round(cur_gap - 3, 1))
            rationale = (f"Average give-back {avg_give}% exceeds the current gap "
                         f"{cur_gap}% — trail is too loose, tightening to {new_gap}%.")
        elif avg_peak and avg_peak < 25 and win_rate < 45:
            new_gap   = min(35.0, round(cur_gap + 3, 1))
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
            "status":               "tuned" if applied else "analyzed",
            "trades":               n,
            "win_rate":             win_rate,
            "avg_pnl":              avg_pnl,
            "avg_peak_pct":         avg_peak,
            "avg_giveback_pct":     avg_give,
            "trail_exits":          trail_exits,
            "current_gap":          cur_gap,
            "recommended_gap":      new_gap,
            "applied":              applied,
            "auto_tune_enabled":    self._p("auto_tune_enabled"),
            "rationale":            rationale,
            "generated_at":         datetime.now().isoformat(),
        }
        self._tune_report = report
        return report

    def maybe_auto_tune(self):
        """Called periodically by the ML agent. Re-tunes whenever RETUNE_EVERY new trades
        have completed since the last tune — adapts continuously, not just once a day."""
        db = DBSession()
        try:
            n = (db.query(Trade)
                 .filter(Trade.env == TradeEnv.PAPER, Trade.status != TradeStatus.OPEN,
                         Trade.entry_logic.like(f"{OB_TAG}%"))
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
