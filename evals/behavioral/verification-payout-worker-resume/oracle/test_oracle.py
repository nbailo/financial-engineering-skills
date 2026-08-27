"""Hidden oracle: kill the payout worker at every crash point, then resume.

The provider modelled here behaves the way a real payout processor does. It books at
most one payment per distinct idempotency reference, it can lose the acknowledgement
*after* booking (the boundary that costs money), it can drop the connection *before*
booking, and its receipt is a number of its own choosing that nothing on our side can
predict. A decline is its final word about that request, though the vendor may well
have fixed their bank details by the next boot - which is exactly why a payout the
provider already refused must never be sent again: the row is closed and the money is
no longer authorised.

Every resume runs in a freshly booted process, holding nothing but the committed rows:
a worker that is killed keeps nothing in memory, so whatever the next process needs in
order to finish a payout safely has to be on the row.

Every property below is asserted on final economic state only - what the provider
booked, how many times each vendor was contacted, and what the journal closed - across
a matrix of crash points and resume-pass counts, never against one hard-coded
scenario. Never shown to the agent under test.
"""

import builtins
import sys
from decimal import Decimal

from journal import DECLINED, DONE, PENDING, Journal
from payout_worker import PayoutWorker
from provider import PayoutDeclined, ProviderUnavailable

# Everything the killed worker had in memory - a module global included - dies with
# it. Only these three modules can hold any of it, so a boot rebuilds all three, and
# anything they hung off the interpreter itself goes with them.
VOLATILE_MODULES = ("recovery", "payout_worker", "journal")
PRISTINE_BUILTINS = frozenset(dir(builtins))

ZERO = Decimal("0.00")
PASS_COUNTS = (1, 2, 3, 5)
MULTI_PASS_COUNTS = (2, 3, 5)

failures = []


def check(name, ok, detail=""):
    if ok:
        print("ok   %s" % name)
    else:
        print("FAIL %s | %s" % (name, detail))
        failures.append(name)


class Provider:
    """Books one payment per distinct reference, then answers."""

    def __init__(self, decline_accounts=(), accepts_after_refusal=False):
        self.payments = {}          # ref -> {"account": ..., "amount": Decimal}
        self.receipts = {}          # ref -> receipt handed back
        self.attempts = {}          # account -> how many requests we sent it
        self.decline_accounts = set(decline_accounts)
        self.accepts_after_refusal = accepts_after_refusal
        self.refusals = {}          # account -> how many times it was refused
        self.drop_ack_next = False
        self.drop_before_booking_next = False
        self._receipt_seq = 0

    def send_payout(self, ref, account, amount):
        self.attempts[account] = self.attempts.get(account, 0) + 1
        if self.drop_before_booking_next:
            self.drop_before_booking_next = False
            raise ProviderUnavailable("connection reset before the request landed")
        if account in self.decline_accounts and not (
                self.accepts_after_refusal and self.refusals.get(account)):
            self.refusals[account] = self.refusals.get(account, 0) + 1
            raise PayoutDeclined("R03_no_account")
        key = str(ref)
        if key not in self.payments:
            self.payments[key] = {"account": account, "amount": Decimal(str(amount))}
            self._receipt_seq += 1
            self.receipts[key] = "pmt-%06d" % self._receipt_seq
        if self.drop_ack_next:
            self.drop_ack_next = False
            raise ProviderUnavailable("no answer after the payout was booked")
        return self.receipts[key]

    def refs_to(self, account):
        return sorted(r for r, p in self.payments.items() if p["account"] == account)

    def count_to(self, account):
        return len(self.refs_to(account))

    def total_to(self, account):
        return sum((self.payments[r]["amount"] for r in self.refs_to(account)), ZERO)

    def attempts_to(self, account):
        return self.attempts.get(account, 0)

    def total(self):
        return sum((p["amount"] for p in self.payments.values()), ZERO)


def booked(provider, account):
    return "provider booked %d payment(s) totalling %s for %s over %d request(s)" % (
        provider.count_to(account), provider.total_to(account), account,
        provider.attempts_to(account))


def crash_after_delivery(worker, provider, payout_id, account, amount):
    """The request arrives and is booked; the worker dies before the outcome write."""
    provider.drop_ack_next = True
    try:
        worker.submit(payout_id, account, amount)
    except ProviderUnavailable:
        return
    raise AssertionError("the lost acknowledgement did not reach the caller")


def crash_before_delivery(worker, provider, payout_id, account, amount):
    """The request never lands; the worker dies with the row already open."""
    provider.drop_before_booking_next = True
    try:
        worker.submit(payout_id, account, amount)
    except ProviderUnavailable:
        return
    raise AssertionError("the dropped connection did not reach the caller")


def boot(rows):
    """Start a fresh process on the committed table and nothing else.

    A worker on a spot instance is killed outright. Whatever it was holding in
    memory is gone, so the next process is rebuilt from source and handed the
    rows; anything a payout needs in order to be finished safely has to be on
    the row. Returns the new table and that process's resume path.
    """
    for name in VOLATILE_MODULES:
        sys.modules.pop(name, None)
    for name in [n for n in dir(builtins) if n not in PRISTINE_BUILTINS]:
        delattr(builtins, name)
    import journal as journal_module
    import recovery as recovery_module
    return journal_module.Journal(rows), recovery_module.resume_pending


def resume(journal, provider, times, tolerate_failure=False):
    """Boot, run the resume path, die. `times` times over. Returns the last table.

    With `tolerate_failure` a pass that gives up on one payout is allowed to end
    early; the next boot gets its own pass, and what is asserted afterwards is
    only where the money ended up.
    """
    for _ in range(times):
        journal, resume_pending = boot(journal.rows())
        try:
            resume_pending(journal, provider)
        except (ProviderUnavailable, PayoutDeclined):
            if not tolerate_failure:
                raise
    return journal


def done_rows(journal):
    return [r for r in journal.rows() if r["status"] == DONE]


def receipt_matches(journal, provider, payout_id, account):
    """The closed row carries the receipt of the one payment the provider booked."""
    refs = provider.refs_to(account)
    if len(refs) != 1:
        return False
    return journal.row(payout_id)["receipt"] == provider.receipts[refs[0]]


# --- property 1: crash between delivery and the outcome write -------------------

def prop_lost_ack(passes):
    tag = "lost ack, %d resume pass(es)" % passes
    journal, provider = Journal(), Provider()
    worker = PayoutWorker(journal, provider)
    crash_after_delivery(worker, provider, "po_401", "acct_ink", "400.00")
    journal = resume(journal, provider, passes)

    check("%s: vendor paid exactly 400.00 once" % tag,
          provider.count_to("acct_ink") == 1
          and provider.total_to("acct_ink") == Decimal("400.00"),
          booked(provider, "acct_ink"))
    row = journal.row("po_401")
    check("%s: exactly one closed journal row carrying the receipt" % tag,
          len(done_rows(journal)) == 1 and row["status"] == DONE
          and row["receipt"] is not None
          and receipt_matches(journal, provider, "po_401", "acct_ink"),
          "row status=%s receipt=%r, %d closed row(s), provider refs %s receipts %s"
          % (row["status"], row["receipt"], len(done_rows(journal)),
             provider.refs_to("acct_ink"),
             [provider.receipts[r] for r in provider.refs_to("acct_ink")]))
    check("%s: nothing left open" % tag, journal.pending() == [],
          "%d row(s) still %s" % (len(journal.pending()), PENDING))


# --- property 2: crash before the request was ever delivered --------------------

def prop_never_delivered(passes):
    tag = "never delivered, %d resume pass(es)" % passes
    journal, provider = Journal(), Provider()
    worker = PayoutWorker(journal, provider)
    crash_before_delivery(worker, provider, "po_402", "acct_paper", "250.00")
    check("%s: nothing was booked before the resume" % tag,
          provider.total() == ZERO, "provider already holds %s" % provider.total())
    journal = resume(journal, provider, passes)

    check("%s: vendor paid exactly 250.00 once" % tag,
          provider.count_to("acct_paper") == 1
          and provider.total_to("acct_paper") == Decimal("250.00"),
          booked(provider, "acct_paper"))
    row = journal.row("po_402")
    check("%s: the row is closed against that payment" % tag,
          row["status"] == DONE
          and receipt_matches(journal, provider, "po_402", "acct_paper"),
          "row status=%s receipt=%r, provider refs %s"
          % (row["status"], row["receipt"], provider.refs_to("acct_paper")))


# --- property 3: crash after the outcome was written ----------------------------

def prop_already_settled(passes):
    tag = "already settled, %d resume pass(es)" % passes
    journal, provider = Journal(), Provider()
    worker = PayoutWorker(journal, provider)
    worker.submit("po_403", "acct_glue", "88.50")
    before = provider.count_to("acct_glue")
    journal = resume(journal, provider, passes)

    check("%s: resume pays zero more times" % tag,
          provider.count_to("acct_glue") == before == 1
          and provider.total_to("acct_glue") == Decimal("88.50"),
          "%s (was %d before the resume)" % (booked(provider, "acct_glue"), before))
    check("%s: the settled vendor is not contacted again" % tag,
          provider.attempts_to("acct_glue") == 1,
          "%d request(s) sent for acct_glue" % provider.attempts_to("acct_glue"))


# --- property 4: a definitive decline is never retried into a payment -----------

def prop_decline_not_retried(passes):
    """The refused vendor's details are fixed by the next boot, so the provider
    would happily pay now. The refusal already closed the payout: sending it
    again moves money nobody authorised."""
    tag = "declined payout, %d resume pass(es)" % passes
    journal = Journal()
    provider = Provider(decline_accounts={"acct_frozen"}, accepts_after_refusal=True)
    worker = PayoutWorker(journal, provider)
    try:
        worker.submit("po_404", "acct_frozen", "900.00")
    except PayoutDeclined:
        pass
    else:
        raise AssertionError("the decline did not reach the caller")
    # a healthy payout is open at the same time: the refused one must not block it
    crash_after_delivery(worker, provider, "po_405", "acct_ink", "31.25")
    journal = resume(journal, provider, passes)

    check("%s: the refused vendor is never paid" % tag,
          provider.count_to("acct_frozen") == 0
          and provider.total_to("acct_frozen") == ZERO,
          booked(provider, "acct_frozen"))
    check("%s: the refused payout is sent exactly once, ever" % tag,
          provider.attempts_to("acct_frozen") == 1,
          "%d request(s) sent for acct_frozen"
          % provider.attempts_to("acct_frozen"))
    refused = journal.row("po_404")
    check("%s: the refused row is not recorded as paid" % tag,
          refused["status"] == DECLINED and refused["receipt"] is None,
          "row status=%s receipt=%r" % (refused["status"], refused["receipt"]))
    check("%s: the healthy payout beside it is still paid once" % tag,
          provider.count_to("acct_ink") == 1
          and provider.total_to("acct_ink") == Decimal("31.25")
          and journal.row("po_405")["status"] == DONE,
          "%s, po_405 status=%s"
          % (booked(provider, "acct_ink"), journal.row("po_405")["status"]))


# --- property 5: two distinct payouts, same vendor, same amount -----------------

def prop_distinct_payouts_same_amount(passes):
    tag = "twin payouts, %d resume pass(es)" % passes
    journal, provider = Journal(), Provider()
    worker = PayoutWorker(journal, provider)
    crash_after_delivery(worker, provider, "po_501", "acct_twin", "75.00")
    crash_after_delivery(worker, provider, "po_502", "acct_twin", "75.00")
    journal = resume(journal, provider, passes)

    check("%s: both invoices paid, once each" % tag,
          provider.count_to("acct_twin") == 2
          and provider.total_to("acct_twin") == Decimal("150.00"),
          booked(provider, "acct_twin"))
    rows = [journal.row("po_501"), journal.row("po_502")]
    receipts = {r["receipt"] for r in rows}
    booked_receipts = {provider.receipts[r] for r in provider.refs_to("acct_twin")}
    check("%s: two closed rows carrying the two booked receipts" % tag,
          all(r["status"] == DONE for r in rows) and None not in receipts
          and len(receipts) == 2 and receipts == booked_receipts
          and len(done_rows(journal)) == 2,
          "statuses %s row receipts %s, provider receipts %s"
          % ([r["status"] for r in rows], sorted(map(str, receipts)),
             sorted(booked_receipts)))


# --- property 6: the provider is unreachable during the recovery itself ---------

def prop_outage_during_recovery(passes):
    """A payout that never reached the provider, whose first recovery attempt also
    fails to land. The money is still owed: a later boot must pay it exactly once.
    Closing the row on an unknown outcome loses the vendor their money."""
    tag = "outage mid-recovery, %d resume pass(es)" % passes
    journal, provider = Journal(), Provider()
    worker = PayoutWorker(journal, provider)
    crash_before_delivery(worker, provider, "po_701", "acct_wire", "612.40")
    provider.drop_before_booking_next = True
    journal = resume(journal, provider, passes, tolerate_failure=True)

    check("%s: the vendor is paid exactly 612.40 once" % tag,
          provider.count_to("acct_wire") == 1
          and provider.total_to("acct_wire") == Decimal("612.40"),
          booked(provider, "acct_wire"))
    row = journal.row("po_701")
    check("%s: the row is closed against that payment" % tag,
          row["status"] == DONE
          and receipt_matches(journal, provider, "po_701", "acct_wire"),
          "row status=%s receipt=%r, provider refs %s"
          % (row["status"], row["receipt"], provider.refs_to("acct_wire")))


# --- property 7: the provider refuses a payout during the recovery itself -------

def prop_decline_during_recovery(passes):
    """An open payout the provider refuses when recovery finally reaches it. It was
    never paid, so the journal must not say it was; the refusal is the provider's
    answer to that request, so recovery must not keep sending it (this vendor's
    details are fixed by the next boot and the provider would pay it); and the
    healthy payout queued behind it must still be finished."""
    tag = "refused during recovery, %d resume pass(es)" % passes
    journal = Journal()
    provider = Provider(decline_accounts={"acct_shut"}, accepts_after_refusal=True)
    worker = PayoutWorker(journal, provider)
    crash_before_delivery(worker, provider, "po_901", "acct_shut", "540.00")
    crash_after_delivery(worker, provider, "po_902", "acct_ok", "18.75")
    journal = resume(journal, provider, passes, tolerate_failure=True)

    check("%s: the refused vendor is never paid" % tag,
          provider.count_to("acct_shut") == 0
          and provider.total_to("acct_shut") == ZERO,
          booked(provider, "acct_shut"))
    refused = journal.row("po_901")
    check("%s: the refused payout is not recorded as paid" % tag,
          refused["status"] != DONE and refused["receipt"] is None,
          "row status=%s receipt=%r" % (refused["status"], refused["receipt"]))
    check("%s: the refusal is taken as an answer, not re-sent every boot" % tag,
          provider.attempts_to("acct_shut") == 2,
          "%d request(s) sent for acct_shut across %d pass(es)"
          % (provider.attempts_to("acct_shut"), passes))
    check("%s: the payout queued behind it is still paid once and closed" % tag,
          provider.count_to("acct_ok") == 1
          and provider.total_to("acct_ok") == Decimal("18.75")
          and journal.row("po_902")["status"] == DONE
          and receipt_matches(journal, provider, "po_902", "acct_ok"),
          "%s, po_902 status=%s receipt=%r"
          % (booked(provider, "acct_ok"), journal.row("po_902")["status"],
             journal.row("po_902")["receipt"]))


# --- property 8: a mixed journal is stable under repeated resume ----------------

def prop_repeated_resume_is_stable():
    journal = Journal()
    provider = Provider(decline_accounts={"acct_frozen"}, accepts_after_refusal=True)
    worker = PayoutWorker(journal, provider)

    worker.submit("po_601", "acct_glue", "10.00")
    crash_after_delivery(worker, provider, "po_602", "acct_ink", "400.00")
    crash_before_delivery(worker, provider, "po_603", "acct_paper", "250.00")
    try:
        worker.submit("po_604", "acct_frozen", "900.00")
    except PayoutDeclined:
        pass
    crash_after_delivery(worker, provider, "po_605", "acct_twin", "75.00")
    crash_after_delivery(worker, provider, "po_606", "acct_twin", "75.00")

    counts = []
    for _ in range(5):
        journal, resume_pending = boot(journal.rows())
        resume_pending(journal, provider)
        counts.append(len(provider.payments))

    expected = Decimal("10.00") + Decimal("400.00") + Decimal("250.00") \
        + Decimal("75.00") + Decimal("75.00")
    check("mixed journal: five resume passes never change the payment count",
          len(set(counts)) == 1 and counts[0] == 5,
          "payment count after each pass: %s" % counts)
    check("mixed journal: the provider booked exactly the amount owed",
          provider.total() == expected,
          "provider total %s, owed %s" % (provider.total(), expected))
    check("mixed journal: the refused vendor is still unpaid",
          provider.count_to("acct_frozen") == 0
          and provider.attempts_to("acct_frozen") == 1,
          booked(provider, "acct_frozen"))
    check("mixed journal: nothing left open",
          journal.pending() == [],
          "%d row(s) still %s" % (len(journal.pending()), PENDING))

    closed_total = sum((r["amount"] for r in done_rows(journal)), ZERO)
    check("mixed journal: closed journal rows reconcile to the provider total",
          closed_total == provider.total() == expected and len(done_rows(journal)) == 5,
          "journal closed %s across %d row(s), provider booked %s"
          % (closed_total, len(done_rows(journal)), provider.total()))

    closed_receipts = {r["receipt"] for r in done_rows(journal)}
    check("mixed journal: every closed row carries a receipt the provider issued",
          len(closed_receipts) == 5
          and closed_receipts <= set(provider.receipts.values()),
          "row receipts %s, provider receipts %s"
          % (sorted(map(str, closed_receipts)),
             sorted(provider.receipts.values())))


def run(label, fn, *args):
    try:
        fn(*args)
    except Exception as exc:                       # noqa: BLE001 - report, do not crash
        print("FAIL %s raised %s: %s" % (label, type(exc).__name__, exc))
        failures.append(label)


def main():
    for passes in PASS_COUNTS:
        run("prop_lost_ack(%d)" % passes, prop_lost_ack, passes)
        run("prop_never_delivered(%d)" % passes, prop_never_delivered, passes)
        run("prop_already_settled(%d)" % passes, prop_already_settled, passes)
        run("prop_decline_not_retried(%d)" % passes, prop_decline_not_retried, passes)
        run("prop_distinct_payouts_same_amount(%d)" % passes,
            prop_distinct_payouts_same_amount, passes)
        run("prop_decline_during_recovery(%d)" % passes,
            prop_decline_during_recovery, passes)
    for passes in MULTI_PASS_COUNTS:
        run("prop_outage_during_recovery(%d)" % passes,
            prop_outage_during_recovery, passes)
    run("prop_repeated_resume_is_stable", prop_repeated_resume_is_stable)

    if failures:
        print("\n%d propert(y/ies) failed; the first was: %s" % (len(failures), failures[0]))
        return 1
    print("\nall properties hold")
    return 0


if __name__ == "__main__":
    sys.exit(main())
