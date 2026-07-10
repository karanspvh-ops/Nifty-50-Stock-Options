"""costs.py — Brokerage and statutory charge calculations.

Edit this file to update:
  - Brokerage flat fee (currently ₹20/leg + 18% GST)
  - STT, exchange, SEBI, and stamp duty rates
"""


def calc_brokerage() -> float:
    """Flat per-trade brokerage: 2 legs × ₹20 + 18% GST."""
    return 47.2


def calc_statutory(t: dict) -> int:
    """Total statutory costs for one trade (STT + exchange + SEBI + stamp + GST)."""
    entry    = t.get("entry")    or 0
    exit_p   = t.get("exit")     or 0
    qty      = t.get("qty")      or 0
    lot_size = t.get("lot_size") or 1

    entry_val = entry  * qty * lot_size
    exit_val  = exit_p * qty * lot_size

    stt         = 0.00125 * exit_val
    exchange    = 0.00053 * (entry_val + exit_val)
    gst_on_exch = 0.18    * exchange
    sebi        = (entry_val + exit_val) * 10 / 10_000_000
    stamp       = 0.00003 * entry_val

    return round(stt + exchange + gst_on_exch + sebi + stamp)
