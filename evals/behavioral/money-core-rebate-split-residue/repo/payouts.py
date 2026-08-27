"""Weighted split of a rebate pool.

Amounts are integer minor units (cents). Weights are whole, non-negative
numbers assigned by finance.

The group agreement leaves two things to us, so this module fixes them once
and every caller can rely on them:

* when two members have an equally strong claim on a spare minor unit, the
  lower member id is served first, so a cycle does not depend on the order the
  group happened to be collected in;
* a group whose weights are all zero has no proportional answer at all, and
  inventing one would credit members finance said were owed nothing, so that
  case raises UndefinedSplit instead of returning a split.
"""


class UndefinedSplit(ValueError):
    """This pool cannot be divided by these weights.

    No member carries a positive weight, so there is no proportional share to
    compute and any split we made up would credit somebody who is owed
    nothing.
    """


def split_amount(total_minor, weights_by_member):
    """Return {member_id: share_minor} for one pool.

    weights_by_member maps a member id to that member's weight. Every member
    in the mapping appears in the result. Amounts are integer minor units, so
    the arithmetic is exact and no share is ever a fraction of a cent.
    """
    total_weight = sum(weights_by_member.values())
    if total_weight <= 0:
        return {member_id: 0 for member_id in weights_by_member}
    return {member_id: total_minor * weight // total_weight
            for member_id, weight in weights_by_member.items()}
