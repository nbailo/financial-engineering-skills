"""Monthly group rebate.

Each cycle, a pool of cents is credited to the members of a group in
proportion to the weight finance assigns them.
"""

from payouts import split_amount


class RebateLedger:
    """Credits, newest last. One row per member per cycle."""

    def __init__(self):
        self.credits = []

    def credit(self, cycle_id, member_id, amount_minor):
        self.credits.append((cycle_id, member_id, amount_minor))

    def credited_to(self, cycle_id, member_id):
        return sum(amount for cycle, member, amount in self.credits
                   if cycle == cycle_id and member == member_id)

    def total_for_cycle(self, cycle_id):
        return sum(amount for cycle, _, amount in self.credits if cycle == cycle_id)


def credit_cycle(ledger, cycle_id, pool_minor, members):
    """Credit every member of a group its share of one cycle's pool.

    members is a list of (member_id, weight) pairs. Returns the shares in the
    same order.
    """
    shares = split_amount(pool_minor, [weight for _, weight in members])
    for (member_id, _), share in zip(members, shares):
        ledger.credit(cycle_id, member_id, share)
    return shares
