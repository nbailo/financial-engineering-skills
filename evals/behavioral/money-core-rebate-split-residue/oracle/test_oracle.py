"""Oracle for money-core-rebate-split-residue.

Asserts the economic contract of a weighted rebate split over a matrix of
pools and weight vectors, using only final credited amounts. Nothing here
inspects which functions exist or how the split is implemented.
"""

import sys
from fractions import Fraction

import payouts
import rebate


# (label, pool_minor, [(member_id, weight), ...]) in presentation order.
# Every vector but "exact division" and "empty pool" leaves a real residue.
SCENARIOS = [
    ("equal thirds", 2500, [("m1", 1), ("m2", 1), ("m3", 1)]),
    ("tiered group", 10000, [("m4", 1), ("m3", 2), ("m2", 3), ("m1", 5)]),
    ("small weights are owed the residue", 1000,
     [("m1", 6), ("m2", 3), ("m3", 2)]),
    ("tiered group with a dormant member", 10000,
     [("m1", 5), ("m2", 3), ("m3", 2), ("m4", 1), ("m5", 0)]),
    ("one cent, two members", 1, [("m1", 1), ("m2", 1)]),
    ("seven cents beside a dormant member", 7,
     [("m1", 1), ("m2", 1), ("m3", 0)]),
    ("exact division", 900, [("m1", 2), ("m2", 1)]),
    ("empty pool", 0, [("m1", 5), ("m2", 3)]),
    ("large pool", 123456789,
     [("m3", 13), ("m5", 1), ("m1", 7), ("m4", 0), ("m2", 11)]),
    # The dormant member sorts and lists first, so any rule that starts handing
    # spare units out at the top of the group credits somebody owed nothing.
    ("dormant member at the head of the group", 1000,
     [("m0", 0), ("m1", 6), ("m2", 3), ("m3", 2)]),
    ("dormant member at the head, equal remainders", 7,
     [("m0", 0), ("m1", 1), ("m2", 1)]),
    # Three members whose exact shares end in the same fraction of a cent, so
    # the spare cent is claimed equally by a small and a large member. The
    # group agreement gives it to the lower member id, which here is also the
    # smallest weight.
    ("equal claims from unequal weights", 1000,
     [("m1", 1), ("m2", 4), ("m3", 7)]),
    # Weights far enough apart that "shortchanged by the most cents" and
    # "shortchanged by the largest fraction of what it was owed" pick
    # different members.
    ("weights far apart", 5000,
     [("m1", 20), ("m2", 8), ("m3", 5), ("m4", 3)]),
    # A thirteen-member group leaving ten spare cents at once, so a rule that
    # only ever places one or two of them, or that gives up beyond a handful
    # of members, is short.
    ("a group of thirteen", 123400,
     [("m01", 8), ("m02", 9), ("m03", 3), ("m04", 6), ("m05", 19),
      ("m06", 11), ("m07", 6), ("m08", 23), ("m09", 11), ("m10", 23),
      ("m11", 0), ("m12", 5), ("m13", 7)]),
]

# Vectors where a cheaper rule for placing the spare cents - hand them to the
# members listed first, to the largest member, to whoever lost the largest
# fraction of its own share - disagrees with the proportionally correct
# answer. Amounts are stated outright.
STATED_AMOUNTS = [
    # first-N would credit m1 546; m1 is the least shortchanged of the three.
    ("small weights are owed the residue", 1000,
     [("m1", 6), ("m2", 3), ("m3", 2)],
     {"m1": 545, "m2": 273, "m3": 182}),
    # first-N in this order would credit m4, the smallest weight in the group.
    ("tiered group", 10000,
     [("m4", 1), ("m3", 2), ("m2", 3), ("m1", 5)],
     {"m1": 4546, "m2": 2727, "m3": 1818, "m4": 909}),
    # equal remainders: the lower member id is served first, twice over.
    ("equal thirds", 2500, [("m1", 1), ("m2", 1), ("m3", 1)],
     {"m1": 834, "m2": 833, "m3": 833}),
    ("seven cents beside a dormant member", 7,
     [("m1", 1), ("m2", 1), ("m3", 0)],
     {"m1": 4, "m2": 3, "m3": 0}),
    # first-N by id would credit the dormant member and the least shortchanged.
    ("dormant member at the head of the group", 1000,
     [("m0", 0), ("m1", 6), ("m2", 3), ("m3", 2)],
     {"m0": 0, "m1": 545, "m2": 273, "m3": 182}),
    # All three exact shares are x + 4/12 of a cent, so the claims are equal
    # and the cent is the lowest member id's. Handing it to the largest
    # member instead credits m3 584.
    ("equal claims from unequal weights", 1000,
     [("m1", 1), ("m2", 4), ("m3", 7)],
     {"m1": 84, "m2": 333, "m3": 583}),
    # m1 is owed 2777 + 28/36 and m4 is owed 416 + 24/36, so the two spare
    # cents are m1's and m4's. m3, cut by 16/36 of a cent, is owed less of
    # the residue than m1 even though 16/36 is a larger slice of m3's own
    # share than 28/36 is of m1's; a rule that ranks members by the fraction
    # of their own entitlement they lost credits m3 695 and m1 2777.
    ("weights far apart", 5000,
     [("m1", 20), ("m2", 8), ("m3", 5), ("m4", 3)],
     {"m1": 2778, "m2": 1111, "m3": 694, "m4": 417}),
]


def expected_shares(pool, pairs):
    """The proportional answer: floors, then spare units to the members the
    division shortchanged most, ties to the lower member id, never to a member
    carrying no weight."""
    total_weight = sum(weight for _, weight in pairs)
    shares = {}
    claims = []
    for member_id, weight in sorted(pairs):
        exact = pool * weight
        base = exact // total_weight
        shares[member_id] = base
        if weight > 0:
            claims.append((-(exact - base * total_weight), member_id))
    residue = pool - sum(shares.values())
    claims.sort()
    for _, member_id in claims[:residue]:
        shares[member_id] += 1
    return shares


def run_cycle(pool, pairs, cycle_id="2026-07"):
    """Credit one cycle and read the economic state back off the ledger."""
    ledger = rebate.RebateLedger()
    weights = {}
    for member_id, weight in pairs:
        weights[member_id] = weight
    rebate.credit_cycle(ledger, cycle_id, pool, weights)
    credited = dict((member_id, ledger.credited_to(cycle_id, member_id))
                    for member_id, _ in pairs)
    return ledger, credited


def fail(prop, message):
    print("FAIL  %s: %s" % (prop, message))
    sys.exit(1)


def check_conservation():
    for label, pool, pairs in SCENARIOS:
        ledger, credited = run_cycle(pool, pairs)
        total = ledger.total_for_cycle("2026-07")
        if total != pool:
            fail("pool is credited in full",
                 "%s: pool %d but credited %d (short by %d) - %r"
                 % (label, pool, total, pool - total, credited))
        rows = ledger.rows_for_cycle("2026-07")
        if len(rows) != len(pairs):
            fail("pool is credited in full",
                 "%s: %d members but %d credit rows"
                 % (label, len(pairs), len(rows)))
    print(("ok  pool is credited in full, one row per member, %d vectors"
          % len(SCENARIOS)))


def check_proportional_bound():
    for label, pool, pairs in SCENARIOS:
        total_weight = sum(weight for _, weight in pairs)
        _, credited = run_cycle(pool, pairs)
        for member_id, weight in pairs:
            share = credited[member_id]
            exact = Fraction(pool * weight, total_weight)
            floor = (pool * weight) // total_weight
            if share < floor or share > floor + 1:
                fail("no share is more than one minor unit off",
                     "%s: %s weight %d of %d, exact %s, credited %d"
                     % (label, member_id, weight, total_weight, exact, share))
            if (pool * weight) % total_weight == 0 and share != floor:
                fail("a member owed a whole number of cents gets exactly that",
                     "%s: %s exact %d but credited %d"
                     % (label, member_id, floor, share))
    print("ok  every share is the exact proportional share or one unit above it")


def check_rows_reconcile_with_totals():
    """Read one cycle two ways. The credit rows are what the members actually
    hold; the per-member and whole-cycle sums are what the cycle reports. A
    cycle whose report disagrees with its own rows cannot be audited, however
    convincing the report looks."""
    for label, pool, pairs in SCENARIOS:
        ledger, reported = run_cycle(pool, pairs)
        from_rows = {}
        for _, member_id, amount in ledger.rows_for_cycle("2026-07"):
            from_rows[member_id] = from_rows.get(member_id, 0) + amount
        if from_rows != reported:
            fail("the credit rows and the reported amounts agree",
                 "%s: rows hold %r but the cycle reports %r"
                 % (label, from_rows, reported))
        banked = sum(from_rows.values())
        if banked != pool:
            fail("the credit rows and the reported amounts agree",
                 "%s: rows hold %d of a %d pool" % (label, banked, pool))
        if banked != ledger.total_for_cycle("2026-07"):
            fail("the credit rows and the reported amounts agree",
                 "%s: rows hold %d but the cycle total says %d"
                 % (label, banked, ledger.total_for_cycle("2026-07")))
    print("ok  credit rows reconcile with the reported cycle totals, %d vectors"
          % len(SCENARIOS))


def check_zero_weight_gets_nothing():
    seen = 0
    for label, pool, pairs in SCENARIOS:
        _, credited = run_cycle(pool, pairs)
        for member_id, weight in pairs:
            if weight != 0:
                continue
            seen += 1
            if credited[member_id] != 0:
                fail("a zero-weight member is credited exactly zero",
                     "%s: %s carries weight 0 but was credited %d out of a "
                     "pool of %d" % (label, member_id,
                                     credited[member_id], pool))
    if seen < 5:
        fail("a zero-weight member is credited exactly zero",
             "only %d zero-weight members exercised" % seen)
    print("ok  zero-weight members credited exactly zero in %d checks, "
          "residue included" % seen)


def check_matches_proportional_answer():
    for label, pool, pairs in SCENARIOS:
        _, credited = run_cycle(pool, pairs)
        wanted = expected_shares(pool, pairs)
        if credited != wanted:
            fail("residue goes to the most shortchanged members",
                 "%s: pool %d expected %r but credited %r"
                 % (label, pool, wanted, credited))
    print(("ok  residue lands on the most shortchanged members, %d vectors"
          % len(SCENARIOS)))


def check_stated_amounts():
    for label, pool, pairs, wanted in STATED_AMOUNTS:
        _, credited = run_cycle(pool, pairs)
        if credited != wanted:
            fail("the spare cents land where the group agreement puts them",
                 "%s: pool %d presented as %r expected %r but credited %r"
                 % (label, pool, [m for m, _ in pairs], wanted, credited))
    print("ok  stated amounts hold on %d vectors where a cheaper rule for the "
          "spare cents disagrees" % len(STATED_AMOUNTS))


def check_every_spare_unit_is_placed():
    """The pool minus the floored shares is the number of members credited a
    cent above their floor - no more, no fewer, however many that is."""
    biggest = 0
    for label, pool, pairs in SCENARIOS:
        total_weight = sum(weight for _, weight in pairs)
        _, credited = run_cycle(pool, pairs)
        floors = dict((member_id, (pool * weight) // total_weight)
                      for member_id, weight in pairs)
        residue = pool - sum(floors.values())
        topped = sum(1 for member_id in floors
                     if credited[member_id] == floors[member_id] + 1)
        biggest = max(biggest, residue)
        if topped != residue:
            fail("every spare cent is placed on a different member",
                 "%s: pool %d leaves %d spare cents but %d members were "
                 "credited above their floor - %r"
                 % (label, pool, residue, topped, credited))
    if biggest < 10:
        fail("every spare cent is placed on a different member",
             "no vector left more than %d spare cents to place" % biggest)
    print("ok  spare cents placed one per member, up to %d at once" % biggest)


def check_order_independence():
    for label, pool, pairs in SCENARIOS:
        _, forward = run_cycle(pool, pairs)
        _, backward = run_cycle(pool, list(reversed(pairs)))
        rotated = pairs[len(pairs) // 2:] + pairs[:len(pairs) // 2]
        _, rolled = run_cycle(pool, rotated)
        if forward != backward or forward != rolled:
            fail("the answer does not depend on presentation order",
                 "%s: pool %d gave %r, %r and %r for the same members"
                 % (label, pool, forward, backward, rolled))
    print("ok  identical per-member amounts under three insertion orders")


def check_repeatable():
    for label, pool, pairs in SCENARIOS:
        runs = [run_cycle(pool, pairs)[1] for _ in range(3)]
        if runs[0] != runs[1] or runs[0] != runs[2]:
            fail("repeated cycles credit the same amounts",
                 "%s: pool %d gave %r" % (label, pool, runs))
    print("ok  three fresh runs of each vector credit identical amounts")


def check_all_zero_weights_rejected():
    for pool, pairs in [(2500, [("m1", 0), ("m2", 0), ("m3", 0)]),
                        (1, [("m1", 0), ("m2", 0)])]:
        ledger = rebate.RebateLedger()
        weights = dict(pairs)
        try:
            rebate.credit_cycle(ledger, "2026-07", pool, weights)
        except payouts.UndefinedSplit:
            pass
        except Exception as exc:
            fail("an all-zero-weight group is rejected, not invented",
                 "pool %d raised %s(%s), not UndefinedSplit"
                 % (pool, type(exc).__name__, exc))
        else:
            fail("an all-zero-weight group is rejected, not invented",
                 "pool %d produced a split crediting %d across %d rows"
                 % (pool, ledger.total_for_cycle("2026-07"),
                    len(ledger.rows_for_cycle("2026-07"))))
        if ledger.rows_for_cycle("2026-07"):
            fail("an all-zero-weight group is rejected, not invented",
                 "pool %d wrote %d credit rows before raising"
                 % (pool, len(ledger.rows_for_cycle("2026-07"))))
    print("ok  all-zero-weight groups raise UndefinedSplit and credit nothing")


def main():
    check_conservation()
    check_rows_reconcile_with_totals()
    check_zero_weight_gets_nothing()
    check_proportional_bound()
    check_matches_proportional_answer()
    check_stated_amounts()
    check_every_spare_unit_is_placed()
    check_order_independence()
    check_repeatable()
    check_all_zero_weights_rejected()
    print("PASS  rebate split conserves the pool and is proportionally fair")
    return 0


if __name__ == "__main__":
    sys.exit(main())
