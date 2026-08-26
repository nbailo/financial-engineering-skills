"""Weighted split of a rebate pool.

Amounts are integer minor units (cents). Weights are whole numbers.
"""


def split_amount(total_minor, weights):
    """Return each member's share of total_minor, in the order of weights.

    Each share is proportional to that member's weight. Amounts are integer
    minor units, so the arithmetic is exact and no share is ever a fraction of
    a cent.
    """
    if not weights:
        return []
    total_weight = sum(weights)
    if total_weight <= 0:
        return [0 for _ in weights]
    return [total_minor * weight // total_weight for weight in weights]
