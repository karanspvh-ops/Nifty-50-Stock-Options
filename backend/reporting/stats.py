"""stats.py — Shared stats aggregation for report builders.

Edit this file to change what summary metrics are computed across a
list of trades (e.g., add Sharpe ratio, max drawdown, streak counts).
"""

from typing import List, Optional


def trade_capital(t: dict) -> float:
    """Capital deployed for one trade: entry × qty × lot_size."""
    return (t.get("entry") or 0) * (t.get("qty") or 0) * (t.get("lot_size") or 1)


def build_stats(trades: List[dict]) -> dict:
    """Aggregate summary stats for a list of trade dicts (from email_sender or pnl_builder)."""
    if not trades:
        return {"n": 0, "net": 0, "wins": 0, "win_pct": 0, "pf": 0, "avg_cap": 0}

    net  = sum(t["pnl"] for t in trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    caps = [trade_capital(t) for t in trades if trade_capital(t)]
    gp   = sum(t["pnl"] for t in trades if t["pnl"] > 0)
    gl   = abs(sum(t["pnl"] for t in trades if t["pnl"] < 0))
    pf   = round(gp / gl, 2) if gl else ("&infin;" if gp > 0 else 0)

    return {
        "n":        len(trades),
        "net":      net,
        "wins":     wins,
        "win_pct":  round(wins / len(trades) * 100) if trades else 0,
        "pf":       pf,
        "avg_cap":  round(sum(caps) / len(caps)) if caps else 0,
    }
