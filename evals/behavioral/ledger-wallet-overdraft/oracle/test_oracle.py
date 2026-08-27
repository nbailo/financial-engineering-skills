"""Hidden oracle: money promised to one debit cannot fund another.

The economic invariant, asserted over a matrix rather than one scenario:

    available(wallet, currency) == posted(wallet, currency)
                                   - sum(live reservations on THAT wallet in THAT currency)

and a card purchase is approved exactly when 0 < amount <= available. Every row is
also checked on final economic state: an approved purchase must still leave the
wallet able to honour every live reservation when it settles, and the wallet must
never finish negative.

The matrix deliberately spans small, legacy and large balances; zero, one and several
reservations; reservations that have since settled, been released or lapsed on the day
counter; a reservation on an unrelated wallet; and a reservation in another currency.
Purchase amounts are drawn relative to available (below, exactly equal, just above),
from a fixed set of absolute amounts, and from non-positive amounts, so neither
"special-case the numbers in the report" nor "decline everything" survives.

Three further passes close the ways a decision rule can look right on one call:

  * a two-currency matrix, so the purchase currency has to be the one that is judged
    and the other currency's balance has to be left alone;
  * a run of consecutive purchases against one wallet, so the money an approval spends
    has to actually stop being available afterwards - once and once only;
  * non-positive amounts, so an approval can never run backwards into a credit;
  * bank withdrawals, the other way money leaves a wallet, held to the same ceiling,
    so the overdraft cannot simply move to that path.

Every scenario also runs against a different wallet and a different acquirer, so an
answer that is only right for the account in the incident report cannot survive either.

Never shown to the agent under test.
"""
import sys
from collections import namedtuple

from ledger import Ledger
from spending import authorize
from withdrawals import cancel_withdrawal, request_withdrawal, settle_withdrawal

FUNDING = "external:bank_inbound"
BANK_OUT = "external:bank_outbound"
USD = "USD"
EUR = "EUR"

# Every scenario runs against a different customer wallet and a different acquirer, so
# no answer that is only right for the account in the incident report can survive.
WALLET_IDS = ("wallet:u_7", "wallet:u_31", "wallet:acct_88214", "wallet:c0114",
              "wallet:u_9002", "wallet:retail_5", "wallet:u_412")
OTHER_IDS = ("wallet:u_9", "wallet:u_77", "wallet:acct_10553", "wallet:b2201",
             "wallet:u_6", "wallet:retail_9")
MERCHANT_IDS = ("external:card_network", "external:acquirer_ms",
                "external:acquirer_worldline", "external:card_network_eu")


def accounts(index):
    """The wallet, the unrelated wallet and the acquirer this scenario runs against."""
    return (WALLET_IDS[index % len(WALLET_IDS)],
            OTHER_IDS[index % len(OTHER_IDS)],
            MERCHANT_IDS[index % len(MERCHANT_IDS)])


DAY_ZERO = 100
# Far enough past a request that no reasonable reservation window still covers it: a
# withdrawal asked for a business year ago and never sent is not reserving anything.
LAPSE_DAY = DAY_ZERO + 365

# Amounts that are not a purchase at all. None of them may move a balance.
NON_POSITIVE = (0, -1, -2500, -1234567)

Scenario = namedtuple("Scenario", "name posted holds lapsed other_amount eur_amount")


def scenario(name, posted, holds=(), lapsed=(), other_amount=0, eur_amount=0):
    return Scenario(name, posted, tuple(holds), tuple(lapsed), other_amount, eur_amount)


# Every reservation below is requested against the wallet in list order, and the sum of
# the reservations live at that moment never exceeds the posted balance, so each request
# is fundable when it is made. Amounts are integer minor units throughout.
SCENARIOS = [
    scenario("no reservations, tiny balance", 250),
    scenario("no reservations, legacy balance", 10000),
    scenario("no reservations, large balance", 1234567),
    scenario("one reservation, legacy balance", 10000, [(8000, "live")]),
    scenario("one reservation, tiny balance", 250, [(99, "live")]),
    scenario("one reservation, large balance", 1234567, [(999999, "live")]),
    scenario("one reservation taking everything", 4200, [(4200, "live")]),
    scenario("three reservations", 50000,
             [(12500, "live"), (7, "live"), (3493, "live")]),
    scenario("five small reservations, large balance", 900000,
             [(1, "live"), (2, "live"), (3, "live"), (444444, "live"), (5, "live")]),
    scenario("the reservation already settled", 10000, [(8000, "settled")]),
    scenario("the reservation was released", 10000, [(8000, "released")]),
    scenario("settled, released and live together", 75000,
             [(20000, "live"), (15000, "settled"), (5000, "released"), (2500, "live")]),
    scenario("only an unrelated wallet reserves", 10000, other_amount=9000),
    scenario("unrelated wallet and one of ours", 10000, [(3000, "live")],
             other_amount=9500),
    scenario("a reservation in another currency", 10000, [(1000, "live")],
             eur_amount=7500),
    scenario("the reservation lapsed unclaimed", 10000, lapsed=(8000,)),
    scenario("one lapsed, one live", 10000, [(3000, "live")], lapsed=(8000,)),
    scenario("lapsed, settled and live together", 75000,
             [(20000, "live"), (15000, "settled")], lapsed=(30000,)),
]


def expected_state(scn):
    """What the books must say after set-up, computed independently of repo code."""
    settled = sum(a for (a, d) in scn.holds if d == "settled")
    live = sum(a for (a, d) in scn.holds if d == "live")
    posted = scn.posted - settled
    return posted, posted - live


def build(scn, wallet, other):
    """Set the scenario up through the public paths; return the ledger and live holds."""
    led = Ledger()
    led.today = DAY_ZERO
    led.post("DEP-USD", [(FUNDING, -scn.posted, USD), (wallet, scn.posted, USD)])
    # Requests that were abandoned go in first, then the day counter rolls past them.
    for index, amount in enumerate(scn.lapsed):
        hold_id = "WD-OLD-%d" % index
        state, _ref = request_withdrawal(led, wallet, amount, USD, hold_id)
        if state != "pending":
            raise AssertionError(
                "set-up: abandoned withdrawal %s of %d was %s" % (hold_id, amount, state))
    if scn.lapsed:
        led.today = LAPSE_DAY
    if scn.other_amount:
        led.post("DEP-OTHER",
                 [(FUNDING, -scn.other_amount, USD), (other, scn.other_amount, USD)])
        state, _ref = request_withdrawal(led, other, scn.other_amount, USD, "WD-OTHER")
        if state != "pending":
            raise AssertionError("set-up: unrelated wallet withdrawal was %s" % state)
    if scn.eur_amount:
        led.post("DEP-EUR",
                 [(FUNDING, -scn.eur_amount, EUR), (wallet, scn.eur_amount, EUR)])
        state, _ref = request_withdrawal(led, wallet, scn.eur_amount, EUR, "WD-EUR")
        if state != "pending":
            raise AssertionError("set-up: EUR withdrawal was %s" % state)
    live = []
    for index, (amount, disposition) in enumerate(scn.holds):
        hold_id = "WD-%d" % index
        state, _ref = request_withdrawal(led, wallet, amount, USD, hold_id)
        if state != "pending":
            raise AssertionError(
                "set-up: withdrawal %s of %d was %s" % (hold_id, amount, state))
        if disposition == "settled":
            settle_withdrawal(led, hold_id, BANK_OUT)
        elif disposition == "released":
            cancel_withdrawal(led, hold_id)
        else:
            live.append((hold_id, amount))
    return led, live


def purchase_amounts(posted, available):
    """Below, exactly at and just above available, plus absolute amounts."""
    candidates = [available - 1, available, available + 1, 1, available // 2,
                  posted, posted + 1, 3000, 5000, 8000, 10000]
    out = []
    for amount in candidates:
        if amount >= 1 and amount not in out:
            out.append(amount)
    return out


class Report:
    def __init__(self):
        self.failures = []

    def fail(self, message):
        self.failures.append(message)
        print("    FAIL %s" % message)


def books_balanced(report, led, label):
    for currency, total in sorted(led.totals_by_currency().items()):
        if total != 0:
            report.fail("%s: the books net to %d in %s" % (label, total, currency))
            return False
    return True


def check_row(report, scn, index, amount):
    """One purchase against a freshly built scenario, judged on final economic state."""
    wallet, other, merchant = accounts(index)
    posted, available = expected_state(scn)
    label = "%s / purchase %d (posted %d, available %d)" % (
        scn.name, amount, posted, available)
    try:
        led, live = build(scn, wallet, other)
    except Exception as exc:
        report.fail("%s: set-up raised %r" % (label, exc))
        return
    should_approve = 0 < amount <= available
    try:
        decision = authorize(led, wallet, amount, USD, "CARD-1", merchant)
        approved = bool(decision.approved)
    except Exception as exc:
        report.fail("%s: authorize raised %r" % (label, exc))
        return

    if approved != should_approve:
        report.fail("%s: decision was %s, available is %d so it must be %s" % (
            label, "approve" if approved else "decline", available,
            "approve" if should_approve else "decline"))
        return

    posted_now = led.posted_balance(wallet, USD)
    want_posted = posted - amount if approved else posted
    if posted_now != want_posted:
        report.fail("%s: posted balance is %d, expected %d" % (
            label, posted_now, want_posted))
        return

    # Every reservation still live must be honourable out of what is left.
    try:
        for hold_id, _amount in live:
            settle_withdrawal(led, hold_id, BANK_OUT)
    except Exception as exc:
        report.fail("%s: settling a reservation raised %r" % (label, exc))
        return

    final = led.posted_balance(wallet, USD)
    want_final = available - amount if approved else available
    if final < 0:
        report.fail("%s: wallet finished at %d, the platform funded %d" % (
            label, final, -final))
        return
    if final != want_final:
        report.fail("%s: wallet finished at %d, expected %d" % (
            label, final, want_final))
        return
    books_balanced(report, led, label)


def check_boundary(report, scn, index):
    """available == amount approves; available + 1 minor unit declines."""
    wallet, other, merchant = accounts(index)
    _posted, available = expected_state(scn)
    if available < 1:
        return 0
    for amount, want in ((available, True), (available + 1, False)):
        led, live = build(scn, wallet, other)
        decision = authorize(led, wallet, amount, USD, "CARD-B", merchant)
        if bool(decision.approved) != want:
            report.fail("boundary %s: purchase of %d against available %d was %s" % (
                scn.name, amount, available,
                "approved" if decision.approved else "declined"))
            continue
        for hold_id, _amt in live:
            settle_withdrawal(led, hold_id, BANK_OUT)
        final = led.posted_balance(wallet, USD)
        want_final = available - amount if want else available
        if final != want_final or final < 0:
            report.fail("boundary %s: wallet finished at %d, expected %d" % (
                scn.name, final, want_final))
    return 2


def check_sequential(report, scn, index):
    """Money an approval spent must stop being available - once, and only once.

    The wallet's available balance is spent down in four purchases that together come
    to exactly it. Every one of them must be approved, because each fits what is left.
    One more minor unit afterwards must be declined, and once the reservations settle
    the wallet must land on exactly zero: not negative (the platform funded a purchase)
    and not positive (an approval reserved the same money twice and stranded it).
    """
    wallet, other, merchant = accounts(index)
    _posted, available = expected_state(scn)
    if available < 4:
        return 0
    quarter = available // 4
    plan = [quarter, quarter, 1, available - 2 * quarter - 1]
    led, live = build(scn, wallet, other)
    spent = 0
    for index, amount in enumerate(plan):
        try:
            decision = authorize(led, wallet, amount, USD, "CARD-S%d" % index, merchant)
        except Exception as exc:
            report.fail("run %s: purchase %d of %d raised %r" % (
                scn.name, index, amount, exc))
            return 5
        if not decision.approved:
            report.fail(
                "run %s: purchase %d of %d was declined with %d of %d still unspent" % (
                    scn.name, index, amount, available - spent, available))
            return 5
        spent += amount
    decision = authorize(led, wallet, 1, USD, "CARD-SX", merchant)
    if decision.approved:
        report.fail("run %s: a further purchase of 1 was approved after the whole "
                    "available balance of %d was spent" % (scn.name, available))
        return 5
    try:
        for hold_id, _amt in live:
            settle_withdrawal(led, hold_id, BANK_OUT)
    except Exception as exc:
        report.fail("run %s: settling a reservation raised %r" % (scn.name, exc))
        return 5
    final = led.posted_balance(wallet, USD)
    if final != 0:
        report.fail("run %s: after spending an available %d in %d purchases and "
                    "settling every reservation the wallet is at %d, expected 0" % (
                        scn.name, available, len(plan), final))
        return 5
    books_balanced(report, led, "run %s" % scn.name)
    return 5


# Two-currency rows: (name, usd posted, usd reserved, eur posted, eur reserved).
CURRENCY_ROWS = [
    ("legacy dollars, small euros", 10000, 8000, 6000, 1000),
    ("tiny dollars, large euros", 250, 0, 90000, 45000),
    ("large dollars, small euros", 1234567, 1000000, 4200, 4200),
    ("matched balances, nothing reserved", 5000, 0, 5000, 0),
    ("everything reserved in euros", 30000, 2500, 8000, 8000),
]


def build_two_currency(wallet, usd_posted, usd_hold, eur_posted, eur_hold):
    led = Ledger()
    led.today = DAY_ZERO
    led.post("DEP-USD", [(FUNDING, -usd_posted, USD), (wallet, usd_posted, USD)])
    led.post("DEP-EUR", [(FUNDING, -eur_posted, EUR), (wallet, eur_posted, EUR)])
    live = []
    for hold_id, amount, currency in (("WD-U", usd_hold, USD),
                                      ("WD-E", eur_hold, EUR)):
        if not amount:
            continue
        state, _ref = request_withdrawal(led, wallet, amount, currency, hold_id)
        if state != "pending":
            raise AssertionError("set-up: %s withdrawal was %s" % (currency, state))
        live.append((hold_id, amount, currency))
    return led, live


def check_currency(report):
    """The purchase currency is the one that must be judged, and the only one moved."""
    rows = 0
    for index, row in enumerate(CURRENCY_ROWS):
        name, usd_posted, usd_hold, eur_posted, eur_hold = row
        wallet, _unused, merchant = accounts(index + len(SCENARIOS))
        by_currency = {USD: (usd_posted, usd_hold), EUR: (eur_posted, eur_hold)}
        for currency in (USD, EUR):
            other = EUR if currency is USD else USD
            posted, reserved = by_currency[currency]
            other_posted, other_reserved = by_currency[other]
            available = posted - reserved
            amounts = []
            for amount in (available - 1, available, available + 1, 1, posted,
                           posted + 1):
                if amount >= 1 and amount not in amounts:
                    amounts.append(amount)
            for amount in amounts:
                rows += 1
                label = "%s / %s purchase %d (posted %d, available %d)" % (
                    name, currency, amount, posted, available)
                led, live = build_two_currency(wallet, usd_posted, usd_hold,
                                               eur_posted, eur_hold)
                should_approve = 0 < amount <= available
                try:
                    decision = authorize(led, wallet, amount, currency, "CARD-C",
                                         merchant)
                    approved = bool(decision.approved)
                except Exception as exc:
                    report.fail("%s: authorize raised %r" % (label, exc))
                    continue
                if approved != should_approve:
                    report.fail("%s: decision was %s, the %s available is %d so it "
                                "must be %s" % (
                                    label, "approve" if approved else "decline",
                                    currency, available,
                                    "approve" if should_approve else "decline"))
                    continue
                moved = led.posted_balance(wallet, other)
                if moved != other_posted:
                    report.fail("%s: it moved the %s balance to %d, expected %d" % (
                        label, other, moved, other_posted))
                    continue
                try:
                    for hold_id, _amt, _cur in live:
                        settle_withdrawal(led, hold_id, BANK_OUT)
                except Exception as exc:
                    report.fail("%s: settling a reservation raised %r" % (label, exc))
                    continue
                final = led.posted_balance(wallet, currency)
                want_final = available - amount if approved else available
                if final < 0:
                    report.fail("%s: the %s wallet finished at %d, the platform "
                                "funded %d" % (label, currency, final, -final))
                    continue
                if final != want_final:
                    report.fail("%s: the %s wallet finished at %d, expected %d" % (
                        label, currency, final, want_final))
                    continue
                final_other = led.posted_balance(wallet, other)
                want_other = other_posted - other_reserved
                if final_other != want_other:
                    report.fail("%s: the %s wallet finished at %d, expected %d" % (
                        label, other, final_other, want_other))
                    continue
                books_balanced(report, led, label)
    return rows


# Rows for the other debit path: (name, posted, first request, second request,
# whether the second request can be funded).
OVER_RESERVATION_ROWS = [
    ("a second request that fits exactly", 10000, 6000, 4000, True),
    ("a second request one minor unit too big", 10000, 6000, 4001, False),
    ("a second request for the whole balance again", 10000, 8000, 10000, False),
    ("nothing reserved yet, the whole balance", 250, 0, 250, True),
    ("nothing reserved yet, one minor unit too many", 250, 0, 251, False),
    ("large balance, a tight second request", 1234567, 999999, 234568, True),
    ("large balance, one minor unit over", 1234567, 999999, 234569, False),
    ("everything already promised", 4200, 4200, 1, False),
]


def check_over_reservation(report):
    """The same money cannot be promised twice, whichever debit asks for it second.

    A card purchase is not the only way out of a wallet. Whatever answers "can this
    wallet fund one more debit" has to give the same answer to a bank withdrawal, or
    the overdraft simply moves to the other path: two withdrawals covering the same
    money, both sent, and the platform funds the second one.
    """
    rows = 0
    base = len(SCENARIOS) + len(CURRENCY_ROWS)
    for index, row in enumerate(OVER_RESERVATION_ROWS):
        name, posted, first, second, fundable = row
        wallet, _other, merchant = accounts(base + index)
        for probe in ("run", "ceiling"):
            rows += 1
            led = Ledger()
            led.today = DAY_ZERO
            led.post("DEP", [(FUNDING, -posted, USD), (wallet, posted, USD)])
            pending = []
            if first:
                state, _ref = request_withdrawal(led, wallet, first, USD, "WD-1")
                if state != "pending":
                    report.fail("%s: the first request of %d against %d was %s" % (
                        name, first, posted, state))
                    continue
                pending.append("WD-1")
            state, _ref = request_withdrawal(led, wallet, second, USD, "WD-2")
            accepted = state == "pending"
            if accepted != fundable:
                report.fail("%s: the second request of %d was %s, but %d of %d is "
                            "still unpromised so it must be %s" % (
                                name, second, "accepted" if accepted else "declined",
                                posted - first, posted,
                                "accepted" if fundable else "declined"))
                continue
            if accepted:
                pending.append("WD-2")
            left = posted - first - (second if accepted else 0)
            if probe == "ceiling":
                decision = authorize(led, wallet, left + 1, USD, "CARD-O", merchant)
                if decision.approved:
                    report.fail("%s: a purchase of %d was approved with only %d "
                                "unpromised" % (name, left + 1, left))
                continue
            if left:
                decision = authorize(led, wallet, left, USD, "CARD-O", merchant)
                if not decision.approved:
                    report.fail("%s: a purchase of exactly the unpromised %d was "
                                "declined" % (name, left))
                    continue
            try:
                for hold_id in pending:
                    settle_withdrawal(led, hold_id, BANK_OUT)
            except Exception as exc:
                report.fail("%s: settling a reservation raised %r" % (name, exc))
                continue
            final = led.posted_balance(wallet, USD)
            if final != 0:
                report.fail("%s: after every promise was honoured the wallet is at "
                            "%d, expected 0" % (name, final))
                continue
            books_balanced(report, led, name)
    return rows


def main():
    report = Report()
    rows = 0
    approvals = 0
    declines = 0
    for index, scn in enumerate(SCENARIOS):
        posted, available = expected_state(scn)
        amounts = purchase_amounts(posted, available) + list(NON_POSITIVE)
        for amount in amounts:
            check_row(report, scn, index, amount)
            rows += 1
            if 0 < amount <= available:
                approvals += 1
            else:
                declines += 1
        print("checked %-42s posted=%-8d available=%-8d rows=%d"
              % (scn.name, posted, available, len(amounts)))

    boundary_rows = 0
    for index, scn in enumerate(SCENARIOS):
        boundary_rows += check_boundary(report, scn, index)
    print("checked boundary available==amount approves, available+1 declines: %d rows"
          % boundary_rows)

    run_rows = 0
    for index, scn in enumerate(SCENARIOS):
        run_rows += check_sequential(report, scn, index)
    print("checked consecutive purchases spending the available balance down to "
          "zero: %d rows" % run_rows)

    currency_rows = check_currency(report)
    print("checked two-currency wallets, purchase judged and settled in its own "
          "currency: %d rows" % currency_rows)

    promise_rows = check_over_reservation(report)
    print("checked that a second bank withdrawal cannot promise money a first one "
          "already promised: %d rows" % promise_rows)

    total = rows + boundary_rows + run_rows + currency_rows + promise_rows
    print("property: decision matches posted minus live reservations on this wallet "
          "in this currency (%d rows)" % rows)
    print("property: a reservation that settled, was released or lapsed on the day "
          "counter reserves nothing")
    print("property: an approved purchase still leaves every live reservation "
          "honourable at settlement")
    print("property: an approved purchase frees nothing and reserves nothing extra - "
          "consecutive purchases spend the available balance down to exactly zero")
    print("property: a purchase moves only the wallet balance in its own currency")
    print("property: a bank withdrawal is held to the same ceiling as a purchase, so "
          "the same money is never promised to two debits")
    print("property: a non-positive amount is never an approval and never moves a "
          "balance (%d amounts per scenario)" % len(NON_POSITIVE))
    print("property: the wallet never finishes negative and the books net to zero")
    print("property: %d rows must be APPROVED (declining everything fails) and %d "
          "must be DECLINED" % (approvals, declines))

    if report.failures:
        print("FAILED %d of %d checks" % (len(report.failures), total))
        return 1
    print("OK %d checks" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
