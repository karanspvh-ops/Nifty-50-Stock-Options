"""ml_analyzer.py — ML Reinforcement & Retrospective Analysis Agent.

Runs independently from the PnL agent. Analyses LOSING trades to detect
entry-quality failure patterns and auto-adjusts indicator thresholds.

Edit this file to:
  - Add or remove loss patterns (RSI, ADX, MACD, volume, EMA stretch)
  - Change the frequency threshold for a pattern to trigger a recommendation
  - Change how weights are adjusted after each analysis cycle
  - Tune RUN_INTERVAL or MIN_LOSSES_TO_ANALYZE
"""

import os
import json
import threading
import time
from collections import defaultdict
from datetime import date, datetime
from typing import Dict, List

from backend.database import (
    Session as DBSession, Trade, Report, AgentLog,
    TradeStatus, TradeEnv,
)

_ROOT        = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REPORTS_DIR  = os.path.join(_ROOT, "reports", "ml")
WEIGHTS_FILE = os.path.join(_ROOT, "reports", "ml", "weights.json")
os.makedirs(REPORTS_DIR, exist_ok=True)

RUN_INTERVAL          = 300   # 5 minutes between analysis cycles
MIN_LOSSES_TO_ANALYZE = 3

DEFAULT_WEIGHTS = {
    "rsi_upper_bound":    72.0,
    "rsi_lower_bound":    28.0,
    "adx_min":            20.0,
    "macd_hist_min":       0.0,
    "volume_spike_boost":  1.2,
    "min_entry_score":     5,
}


class MLAgent:

    def __init__(self):
        self._running = False
        self._thread: threading.Thread = None
        self._weights = self._load_weights()

    # ── Lifecycle ─────────────────────────────────────────────────────────────

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
            try:
                from backend.strategies.ob.strategy import opening_breakout
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
            patterns    = self._detect_patterns(losses)
            recs        = self._build_recommendations(patterns, losses)
            new_weights = self._adjust_weights(patterns)
            report      = self._build_report(env, losses, patterns, recs, new_weights)
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
                    Trade.env == env,
                    Trade.status.in_([TradeStatus.SL_HIT, TradeStatus.KILLED]),
                    Trade.pnl != None,
                    Trade.pnl < 0,
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
            ind      = t.indicators_snapshot or {}
            pnl      = t.pnl or 0
            found    = []
            rsi      = ind.get("rsi")
            adx      = ind.get("adx")
            macd_h   = ind.get("macd_hist")
            vol_spike = ind.get("volume_spike", False)
            ema5     = ind.get("ema5")
            ema20    = ind.get("ema20")

            if rsi:
                if t.direction == "call" and rsi > 68:
                    found.append("RSI_OVERBOUGHT_ENTRY")
                elif t.direction == "put" and rsi < 32:
                    found.append("RSI_OVERSOLD_ENTRY")
            if adx and adx < 22:
                found.append("WEAK_TREND_ADX")
            if macd_h is not None and macd_h < 0.001:
                found.append("WEAK_MACD_MOMENTUM")
            if not vol_spike:
                found.append("NO_VOLUME_SPIKE")
            if ema5 and ema20 and ema20 > 0:
                stretch = abs(ema5 - ema20) / ema20 * 100
                if stretch > 1.5:
                    found.append("OVEREXTENDED_ENTRY")
            if not found:
                found.append("UNKNOWN_CAUSE")

            for pattern in found:
                patterns[pattern]["count"]      += 1
                patterns[pattern]["total_loss"] += abs(pnl)
                patterns[pattern]["trades"].append(t.id)

        result = {}
        for k, v in patterns.items():
            result[k] = {
                "count":        v["count"],
                "total_loss":   round(v["total_loss"], 2),
                "avg_loss":     round(v["total_loss"] / v["count"], 2) if v["count"] else 0,
                "pct_of_total": round(v["count"] / len(losses) * 100, 1),
                "trade_ids":    v["trades"][:10],
            }
        return dict(sorted(result.items(), key=lambda x: x[1]["count"], reverse=True))

    # ── Recommendations ───────────────────────────────────────────────────────

    def _build_recommendations(self, patterns: dict, losses: List[Trade]) -> List[dict]:
        recs = []
        for pattern, data in patterns.items():
            if data["pct_of_total"] < 10:
                continue
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
            if patterns["RSI_OVERBOUGHT_ENTRY"]["pct_of_total"] > 25:
                weights["rsi_upper_bound"] = max(62, weights.get("rsi_upper_bound", 72) - 2)
        if "WEAK_TREND_ADX" in patterns:
            if patterns["WEAK_TREND_ADX"]["pct_of_total"] > 25:
                weights["adx_min"] = min(28, weights.get("adx_min", 20) + 1)
        if "WEAK_MACD_MOMENTUM" in patterns:
            if patterns["WEAK_MACD_MOMENTUM"]["pct_of_total"] > 30:
                weights["macd_hist_min"] = min(0.1, weights.get("macd_hist_min", 0) + 0.02)
        return weights

    # ── Report builder ────────────────────────────────────────────────────────

    def _build_report(self, env, losses, patterns, recs, new_weights) -> dict:
        total_loss = sum(abs(t.pnl or 0) for t in losses)
        return {
            "report_type":           "ml_retrospective",
            "env":                   env.value,
            "generated_at":          datetime.utcnow().isoformat(),
            "total_losses_analyzed": len(losses),
            "total_capital_lost":    round(total_loss, 2),
            "patterns":              patterns,
            "recommendations":       recs,
            "adjusted_weights":      new_weights,
            "previous_weights":      self._weights,
            "summary":               self._narrative_summary(patterns, recs, total_loss),
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
            today   = date.today().isoformat()
            content = json.dumps(report, indent=2)
            existing = (
                db.query(Report)
                .filter(Report.date == today, Report.env == env,
                        Report.report_type == "ml_retrospective")
                .first()
            )
            if existing:
                existing.content      = content
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

    def _log(self, msg: str):
        print(msg)
        db = DBSession()
        try:
            db.add(AgentLog(agent_name="ml_agent", message=msg))
            db.commit()
        finally:
            db.close()

    # ── Public API ────────────────────────────────────────────────────────────

    def get_weights(self) -> dict:
        return dict(self._weights)

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
        losses = self._get_all_losses(env)
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


# ── Singleton ──────────────────────────────────────────────────────────────────
ml_agent = MLAgent()
