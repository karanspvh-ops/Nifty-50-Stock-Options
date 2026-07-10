"""quantity.py — Position sizing: how many lots to buy given available capital."""


def calc_quantity(available_funds: float, premium: float, lot_size: int,
                  max_positions: int = 5) -> int:
    """Divide available capital evenly across max_positions slots.

    Returns the number of LOTS (not contracts) to buy. Always at least 1.
    """
    budget    = available_funds / max(max_positions, 1)
    cost_1lot = premium * lot_size
    if cost_1lot <= 0:
        return 1
    return max(int(budget // cost_1lot), 1)
