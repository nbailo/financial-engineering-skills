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

    def rows_for_cycle(self, cycle_id):
        return [row for row in self.credits if row[0] == cycle_id]


def credit_cycle(ledger, cycle_id, pool_minor, weights_by_member):
    """Credit every member of a group its share of one cycle's pool.

    weights_by_member maps member id to the weight finance assigned. Rows are
    written in member id order so the cycle reads back the same way however
    the group was collected. Returns the shares.
    """
    shares = split_amount(pool_minor, weights_by_member)
    for member_id in sorted(shares):
        ledger.credit(cycle_id, member_id, shares[member_id])
    return shares
