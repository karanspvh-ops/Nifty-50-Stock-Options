"""
ml_agent.py — Agent 2: ML Reinforcement & Retrospective Analysis.

Completely separate from the PnL agent. Runs independently and ONLY
analyses LOSING trades to find improvement patterns.

What it does:
  1. Collects all losing trades across history
  2. Extracts indicator snapshots at entry (RSI, ADX, MACD, EMA alignment)
  3. Clusters losses by failure pattern:
       - "RSI_OVERBOUGHT"  : entered with RSI > 68
       - "WEAK_TREND"      : ADX < 22 at entry (not trending enough)
       - "MACD_DIVERGENCE" : MACD hist was declining at entry
       - "AGAINST_SECTOR"  : stock direction opposed sector direction
       - "LATE_ENTRY"      : EMA5 was already far above EMA20 (overextended)
       - "LOW_VOLUME"      : no volume spike at entry
  4. Computes pattern frequency and avg loss per pattern
  5. Generates improvement recommendations
  6. Adjusts confidence weights fed back to entry_engine thresholds
  7. Writes full retrospective report to DB + reports/ml/

Reports are stored separately. Never mixed with PnL reports.
"""

import os, sys, json, threading, time
from datetime import date, datetime
from typing import List, Dict
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.database import (
    Session as DBSession, Trade, TradingSession, Report,
    AgentLog, TradeStatus, TradeEnv
)

REPORTS_DIR = os.path.join(ROOT, "reports", "ml")
WEIGHTS_FILE = os.path.join(ROOT, "reports", "ml", "weights.json")
os.makedirs(REPORTS_DIR, exist_ok=True)

RUN_INTERVAL = 300    # 5 minutes between ML analysis cycles
MIN_LOSSES_TO_ANALYZE = 3  # need at least 3 losses to generate patterns

# ── Default entry confidence weights (fed back to trading engine) ─────────────
DEFAULT_WEIGHTS = {
    "rsi_upper_bound":    72.0,   # RSI must be below this for CALL entry
    "rsi_lower_bound":    28.0,   # RSI must be above this for PUT entry
    "adx_min":            20.0,   # minimum ADX for trend confirmation
    "macd_hist_min":      0.0,    # MACD hist threshold
    "volume_spike_boost": 1.2,    # score multiplier when volume spike present
    "min_entry_score":    5,      # out of 8
}


class MLAgent:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._weights = self._load_weights()

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="MLAgent")
        self._thread.start()
        print("[ML AGENT] Started.")

    def stop(self):
        self._running = False

    def _loop(self):
        while self._running:
            try:
                self._run_analysis()
            except Exception as e:
                print(f"[ML AGENT] Analysis error: {e}")
            # Opening Breakout: auto-tune the trailing gap once/day (>=50 trades)
            try:
                from backend.core.opening_breakout import opening_breakout
                opening_breakout.maybe_auto_tune()
            except Exception as e:
                print(f"[ML AGENT] OB auto-tune error: {e}")
            time.sleep(RUN_INTERVAL)

    # ── Main analysis pipeline ────────────────────────────────────────────────

    def _run_analysis(self):
        for env in (TradeEnv.PAPER, TradeEnv.LIVE):
            losses = self._get_all_losses(env)
            if len(losses) < MIN_LOSSES_TO_ANALYZE:
                continue
            patterns  = self._detect_patterns(losses)
            recs      = self._build_recommendations(patterns, losses)
            new_weights = self._adjust_weights(patterns)
            report    = self._build_report(env, losses, patterns, recs, new_weights)
            self._save_report(env, report)
            self._save_weights(new_weights)
            self._log(f"[ML AGENT] Analysis complete | env={env.value} | "
                      f"Losses: {len(losses)} | Patterns: {len(patterns)}")

    def _get_all_losses(self, env: TradeEnv) -> List[Trade]:
        db = DBSession()
        try:
            return (
                db.query(Trade)
                .filter(
                    Trade.env    == env,
                    Trade.status.in_([TradeStatus.SL_HIT, TradeStatus.KILLED]),
                    Trade.pnl    != None,
                    Trade.pnl    < 0,
                )
                .order_by(Trade.entered_at.desc())
                .limit(200)
                .all()
            )
        finally:
            db.close()

    # ── Pattern detection ─────────────────────────────────────────────────────

    def _detect_patterns(self, losses: List[Trade]) -> Dict[str, dict]:
        patterns = defaultdict(lambda: {"count": 0, "total_loss": 0.0, "trades": []})

        for t in losses:
            ind   = t.indicators_snapshot or {}
            pnl   = t.pnl or 0
            found = []

            rsi   = ind.get("rsi")
            adx   = ind.get("adx")
            macd_h = ind.get("macd_hist")
            vol_spike = ind.get("volume_spike", False)
            ema5  = ind.get("ema5")
            ema20 = ind.get("ema20")

            # ── Pattern 1: RSI overbought/oversold at entry ───────────────────
            if rsi:
                if t.direction == "call" and rsi > 68:
                    found.append("RSI_OVERBOUGHT_ENTRY")
                elif t.direction == "put"  and rsi < 32:
                    found.append("RSI_OVERSOLD_ENTRY")

            # ── Pattern 2: Weak trend (ADX too low) ───────────────────────────
            if adx and adx < 22:
                found.append("WEAK_TREND_ADX")

            # ── Pattern 3: MACD divergence (hist declining) ───────────────────
            last_sl = ind.get("last_sl_update", {})
            if macd_h is not None and macd_h < 0.001:
                found.append("WEAK_MACD_MOMENTUM")

            # ── Pattern 4: No volume confirmation ────────────────────────────
            if not vol_spike:
                found.append("NO_VOLUME_SPIKE")

            # ── Pattern 5: Overextended EMA (late entry) ─────────────────────
            if ema5 and ema20 and ema20 > 0:
                stretch = abs(ema5 - ema20) / ema20 * 100
                if stretch > 1.5:   # EMA5 > 1.5% away from EMA20
                    found.append("OVEREXTENDED_ENTRY")

            # ── Pattern 6: No patterns found → unknown cause ──────────────────
            if not found:
                found.append("UNKNOWN_CAUSE")

            for pattern in found:
                patterns[pattern]["count"]      += 1
                patterns[pattern]["total_loss"] += abs(pnl)
                patterns[pattern]["trades"].append(t.id)

        # Compute averages
        result = {}
        for k, v in patterns.items():
            result[k] = {
                "count":       v["count"],
                "total_loss":  round(v["total_loss"], 2),
                "avg_loss":    round(v["total_loss"] / v["count"], 2) if v["count"] else 0,
                "pct_of_total": round(v["count"] / len(losses) * 100, 1),
                "trade_ids":   v["trades"][:10],
            }
        return dict(sorted(result.items(), key=lambda x: x[1]["count"], reverse=True))

    # ── Recommendations ───────────────────────────────────────────────────────

    def _build_recommendations(self, patterns: dict, losses: List[Trade]) -> List[dict]:
        recs = []
        for pattern, data in patterns.items():
            if data["pct_of_total"] < 10:
                continue   # skip rare patterns

            if pattern == "RSI_OVERBOUGHT_ENTRY":
                recs.append({
                    "pattern":   pattern,
                    "frequency": f"{data['pct_of_total']}% of losses",
                    "finding":   "Entries were taken with RSI above 68 on CALL trades — price was already overheated.",
                    "action":    "Tighten RSI upper bound from 72 to 65 for CALL entries.",
                    "parameter": "rsi_upper_bound",
                    "current":   self._weights.get("rsi_upper_bound", 72),
                    "suggested": 65,
                })
            elif pattern == "WEAK_TREND_ADX":
                recs.append({
                    "pattern":   pattern,
                    "frequency": f"{data['pct_of_total']}% of losses",
                    "finding":   "Entries made when ADX was below 22 — market was range-bound, not trending.",
                    "action":    "Raise minimum ADX threshold from 20 to 23.",
                    "parameter": "adx_min",
                    "current":   self._weights.get("adx_min", 20),
                    "suggested": 23,
                })
            elif pattern == "NO_VOLUME_SPIKE":
                recs.append({
                    "pattern":   pattern,
                    "frequency": f"{data['pct_of_total']}% of losses",
                    "finding":   "Most losing trades had no volume spike — institutional conviction was absent.",
                    "action":    "Make volume spike a hard requirement (not optional) for entry.",
                    "parameter": "require_volume_spike",
                    "current":   False,
                    "suggested": True,
                })
            elif pattern == "OVEREXTENDED_ENTRY":
                recs.append({
                    "pattern":   pattern,
                    "frequency": f"{data['pct_of_total']}% of losses",
                    "finding":   "EMA5 was already far stretched above EMA20 at entry — entered too late in the move.",
                    "action":    "Add EMA stretch filter: skip entry if EMA5-EMA20 gap > 1.2%.",
                    "parameter": "max_ema_stretch_pct",
                    "current":   None,
                    "suggested": 1.2,
                })
            elif pattern == "WEAK_MACD_MOMENTUM":
                recs.append({
                    "pattern":   pattern,
                    "frequency": f"{data['pct_of_total']}% of losses",
                    "finding":   "MACD histogram was near zero at entry — momentum was not confirmed.",
                    "action":    "Require MACD hist > 0.05 (not just > 0) for entry.",
                    "parameter": "macd_hist_min",
                    "current":   self._weights.get("macd_hist_min", 0),
                    "suggested": 0.05,
                })

        return recs

    # ── Weight adjustment ─────────────────────────────────────────────────────

    def _adjust_weights(self, patterns: dict) -> dict:
        weights = dict(self._weights)

        if "RSI_OVERBOUGHT_ENTRY" in patterns:
            freq = patterns["RSI_OVERBOUGHT_ENTRY"]["pct_of_total"]
            if freq > 25:
                weights["rsi_upper_bound"] = max(62, weights.get("rsi_upper_bound", 72) - 2)

        if "WEAK_TREND_ADX" in patterns:
            freq = patterns["WEAK_TREND_ADX"]["pct_of_total"]
            if freq > 25:
                weights["adx_min"] = min(28, weights.get("adx_min", 20) + 1)

        if "WEAK_MACD_MOMENTUM" in patterns:
            freq = patterns["WEAK_MACD_MOMENTUM"]["pct_of_total"]
            if freq > 30:
                weights["macd_hist_min"] = min(0.1, weights.get("macd_hist_min", 0) + 0.02)

        return weights

    # ── Report builder ────────────────────────────────────────────────────────

    def _build_report(self, env, losses, patterns, recs, new_weights) -> dict:
        total_loss = sum(abs(t.pnl or 0) for t in losses)
        return {
            "report_type":      "ml_retrospective",
            "env":              env.value,
            "generated_at":     datetime.utcnow().isoformat(),
            "total_losses_analyzed": len(losses),
            "total_capital_lost":    round(total_loss, 2),
            "patterns": patterns,
            "recommendations": recs,
            "adjusted_weights": new_weights,
            "previous_weights": self._weights,
            "summary": self._narrative_summary(patterns, recs, total_loss),
        }

    def _narrative_summary(self, patterns, recs, total_loss) -> str:
        if not patterns:
            return "Insufficient loss data for pattern analysis."
        top = list(patterns.items())[0]
        return (
            f"Analysis of {sum(p['count'] for p in patterns.values())} losing trades "
            f"totalling ₹{total_loss:.2f}. "
            f"Primary failure pattern: {top[0]} ({top[1]['pct_of_total']}% of losses, "
            f"avg loss ₹{top[1]['avg_loss']:.2f}). "
            f"{len(recs)} improvement recommendations generated. "
            f"Entry thresholds have been auto-adjusted."
        )

    # ── Persistence ───────────────────────────────────────────────────────────

    def _save_report(self, env: TradeEnv, report: dict):
        db = DBSession()
        try:
            today = date.today().isoformat()
            content = json.dumps(report, indent=2)
            existing = (
                db.query(Report)
                .filter(Report.date == today, Report.env == env,
                        Report.report_type == "ml_retrospective")
                .first()
            )
            if existing:
                existing.content = content
                existing.generated_at = datetime.utcnow()
            else:
                db.add(Report(report_type="ml_retrospective",
                              date=today, env=env, content=content))
            db.commit()
            fname = os.path.join(REPORTS_DIR, f"{today}-{env.value}.json")
            with open(fname, "w") as f:
                json.dump(report, f, indent=2)
        finally:
            db.close()

    def _save_weights(self, weights: dict):
        self._weights = weights
        with open(WEIGHTS_FILE, "w") as f:
            json.dump(weights, f, indent=2)

    def _load_weights(self) -> dict:
        if os.path.exists(WEIGHTS_FILE):
            with open(WEIGHTS_FILE) as f:
                return json.load(f)
        return dict(DEFAULT_WEIGHTS)

    def get_weights(self) -> dict:
        return dict(self._weights)

    def _log(self, msg: str):
        print(msg)
        db = DBSession()
        try:
            db.add(AgentLog(agent_name="ml_agent", message=msg))
            db.commit()
        finally:
            db.close()

    def get_latest_report(self, env: TradeEnv) -> dict:
        db = DBSession()
        try:
            row = (
                db.query(Report)
                .filter(Report.env == env, Report.report_type == "ml_retrospective")
                .order_by(Report.generated_at.desc())
                .first()
            )
            return json.loads(row.content) if row else {}
        finally:
            db.close()

    def trigger_now(self, env: TradeEnv) -> dict:
        """Force-run analysis on demand (REST endpoint)."""
        losses  = self._get_all_losses(env)
        if len(losses) < MIN_LOSSES_TO_ANALYZE:
            return {"status": "insufficient_data",
                    "losses_found": len(losses),
                    "required": MIN_LOSSES_TO_ANALYZE}
        patterns = self._detect_patterns(losses)
        recs     = self._build_recommendations(patterns, losses)
        weights  = self._adjust_weights(patterns)
        report   = self._build_report(env, losses, patterns, recs, weights)
        self._save_report(env, report)
        self._save_weights(weights)
        return report


ml_agent = MLAgent()
