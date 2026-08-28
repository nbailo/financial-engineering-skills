"""Hidden oracle: what the shop returns can never exceed what it took.

Every concurrent scenario here is built the way the shop really runs. There is ONE processor and
ONE refund book. Each worker pressing refund has its OWN connection object to the processor and
its OWN service object over that connection, because that is what a second admin panel process,
or a second thread in one, actually holds. Anything a repair remembers on a service instance, or
hangs off a connection object, is therefore private to one caller and cannot serialise two.

The agents press refund in the same moment: they meet once at a gate before any of them is
inside the service, and from there the clock does the rest. The two things that take time in the
real shop take time here. The connection's before_send hook waits WIRE before a request reaches
the processor, because a round trip really is a few hundred milliseconds and is exactly the
window a second agent presses refund inside. Every call on the one refund book waits STORAGE
first, because the shop's record is a database rather than a dictionary. Nothing else is slowed
and nothing waits on anything else, so a repair that serialises its callers simply takes its
turns and is never punished for it.

Latency rather than a barrier is what reaches a repair whose window opens late. Anything that
reads what a payment has left, lets go of it, and only then goes to the processor leaves the
record saying nothing about the refund it has just decided to make for as long as that round
trip lasts, and the next worker walks straight into that window. A repair that settles the
question and the commitment against the shared record in one step has no such window.

A call to the processor can also end badly, and the two ways it can end are not the same news.
A connection that turns a request away before it goes has cost the shop nothing, and the room that
request had taken is the payment's again. A request that reached the processor and whose answer
never came home may be an accepted refund sitting there waiting for the nightly run; the shop
cannot see which, so that room is still spoken for until the processor is asked about the reference
the refund was sent under. A repair that reads the second as the first hands the same room out
twice. Both endings are modelled here on a worker's own connection, one on each leg of the round
trip, and both are shown to a caller that then retries.

Room a failure hands back goes back to the order rather than to whoever happens to be standing
over the row. The moment a press that never left the shop gives its room up, the next agent is
entitled to it, so a second worker that met that click while it was still unaccounted for must not
be sending into that room at the same time: if it is, the order pays the same room out twice. That
is a schedule rather than a delay, and it is driven here by explicit events on the two presses'
own connections.

Giving room back is not the same as promising it again. The agent whose refund never left the shop
presses refund a second time, and by then the order may have spent that room on somebody else, so
what her retry may have is whatever the order has at the moment she presses, never what it had when
she first pressed. And a click is one amount on one order: the same click coming back for a
different figure, or against a different order, is not that click, and a repair that pays it or
lets it hand back the room of the refund it really made is wrong on both counts.

Nothing is asserted about who won a race. Only the final economic state is asserted: what the
processor paid the shopper, what the callers were told, how many refunds the processor was
actually asked for, and, for a click that comes back changed, that it was neither paid nor
allowed to give its room away.
"""
import os
import sys
import threading
import time
import traceback
import unittest

sys.dont_write_bytecode = True

_CASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _candidate in (os.path.join(_CASE, "repo"), os.path.abspath("repo")):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from processor import PreSendRejected, Processor, ProcessorClient
from refund_book import RefundBook
from refund_service import RefundCeilingError, RefundService

CAPTURED = 12000
ORDER = "PSP001"
RUNS = 3
WIRE = 0.05          # a request to the processor is a round trip, not a function call
STORAGE = 0.002      # the shop's refund book is a database, not a dictionary
GATE = 5.0           # how long the agents will wait to press refund together
HOLD = 1.0           # how long a caller is given to get somewhere before it is let past
JOIN = 10.0          # every join in this file is bounded, and a thread still alive is a failure
REQUEST_IDS = ("req-a", "req-b", "req-c")


def reference_of(result):
    """The processor's name for a refund, whatever shape the service hands back."""
    if isinstance(result, dict):
        for key in ("psp_reference", "id", "reference"):
            if key in result:
                return result[key]
        return repr(sorted((str(k), str(v)) for k, v in result.items()))
    for key in ("psp_reference", "id", "reference"):
        value = getattr(result, key, None)
        if value is not None:
            return value
    return repr(result)


class Gate(object):
    """Where the agents wait so that they press refund in the same moment.

    They meet here once, before any of them is inside the service. After that nothing lines them
    up: what they see of each other is only what the wire and the book are slow enough to show.
    """

    def __init__(self, parties, timeout=GATE):
        self._condition = threading.Condition()
        self._parties = parties
        self._timeout = timeout
        self._arrived = 0

    def wait(self):
        with self._condition:
            self._arrived += 1
            if self._arrived >= self._parties:
                self._condition.notify_all()
                return
            deadline = time.monotonic() + self._timeout
            while self._arrived < self._parties:
                left = deadline - time.monotonic()
                if left <= 0:
                    return
                self._condition.wait(left)


class Book(object):
    """The shop's refund book as its workers hold it, with the storage round trip visible.

    Every worker is handed this one object and it forwards everything to the one RefundBook
    underneath, so the workers share a book exactly as they would without it. The wrapper exists
    only so the callers can be made to meet inside a call on the book the way they meet inside a
    call to the processor.
    """

    def __init__(self, book):
        self._book = book
        self.before_call = None

    def __getattr__(self, name):
        attribute = getattr(self._book, name)
        if not callable(attribute):
            return attribute

        def called(*args, **kwargs):
            hook = self.before_call
            if hook is not None:
                hook(name)
            return attribute(*args, **kwargs)

        return called


class Worker(object):
    """One admin worker: its own connection to the processor, its own service over it."""

    def __init__(self, processor, book):
        self.client = ProcessorClient(processor)
        self.service = RefundService(self.client, book)

    def arm(self):
        """Give this worker's connection the round trip a real one has."""
        self.client.before_send = self._wire

    def _wire(self, operation, psp_reference):
        time.sleep(WIRE)

    def refuse_before_send(self, operation="refund"):
        """This connection turns the request away before it goes; the processor never sees it."""
        def hook(op, psp_reference):
            if op == operation:
                raise PreSendRejected("no outbound connection for %s" % psp_reference)

        self.client.before_send = hook

    def lose_the_answer(self, operation="refund"):
        """The processor takes the request and does the work; the answer never gets home."""
        def hook(op, psp_reference):
            if op == operation:
                raise TimeoutError("read timed out on %s for %s" % (op, psp_reference))

        self.client.after_send = hook

    def hold_before_send(self, arrived, proceed, operation="refund"):
        """Stop this connection with the request about to leave, and hold it there.

        By the time the outbound leg runs, whatever the shop meant to commit to this request it
        has already committed to: the request is on the point of going. `arrived` is set at that
        moment and the request waits for `proceed` before the processor sees it, so a test can put
        another agent in front of a genuinely in-flight refund without knowing anything about how
        the repair is built.
        """
        def hook(op, psp_reference):
            if op == operation:
                arrived.set()
                if not proceed.wait(GATE):
                    raise AssertionError("the held request was never released")

        self.client.before_send = hook

    def heal(self):
        """The connection is well again; whatever went wrong on it went wrong once."""
        self.client.before_send = None
        self.client.after_send = None

    def refund(self, psp_reference, amount, request_id):
        return self.service.issue_refund(psp_reference, amount, request_id)


class Shop(object):
    """One processor and one refund book, with as many workers as a scenario needs."""

    def __init__(self, captured=CAPTURED):
        self.processor = Processor()
        self.processor.seed_capture(ORDER, captured)
        self.book = Book(RefundBook())
        self.first = self.worker()

    def arm(self):
        """From here on every call on the shop's book costs what a call on a database costs."""
        self.book.before_call = lambda name: time.sleep(STORAGE)

    def worker(self):
        return Worker(self.processor, self.book)

    def seed(self, psp_reference, captured, currency="EUR"):
        self.processor.seed_capture(psp_reference, captured, currency)

    def refund(self, amount, request_id, psp_reference=ORDER, worker=None):
        worker = self.first if worker is None else worker
        return worker.refund(psp_reference, amount, request_id)

    def refunds_held(self, psp_reference=ORDER):
        return [m for m in self.processor.list_modifications(psp_reference)
                if m["type"] == "refund"]

    def settle(self):
        self.processor.settle()
        return self.processor.merchant_paid_out

    @property
    def paid_out(self):
        return self.processor.merchant_paid_out


def agents_at_once(amounts, captured=CAPTURED, already_out=()):
    """Support agents on separate workers press refund on one order inside one round trip.

    amounts is one figure per agent, two or three of them. Each gets a worker of her own over the
    one processor and the one refund book, and they all press refund in the same moment, so none of
    them can see what the others are about to send.

    already_out is what the order gave back before they arrived, as (amount, request_id, settled)
    triples sent over a quiet connection; nothing is slowed down until those are in, so an order
    that is already part refunded races on the remainder it has left rather than on its capture.

    Returns (paid_out, promised, refused_ids, unexpected, refunds_held). `promised` is what the
    callers that came back without a refusal asked for, plus whatever had already gone out; a
    correct service pays out exactly that, never more than the capture, and asks the processor
    for exactly one refund per caller it accepted.
    """
    shop = Shop(captured)
    already_paid = 0
    for amount, request_id, is_settled in already_out:
        shop.refund(amount, request_id)
        already_paid += amount
        if is_settled:
            shop.settle()

    ids = REQUEST_IDS[: len(amounts)]
    asked = dict(zip(ids, amounts))
    gate = Gate(len(ids))
    crew = {}
    for request_id in ids:
        worker = shop.worker()
        worker.arm()
        crew[request_id] = worker
    shop.arm()

    accepted = {}
    refused = {}

    def agent(request_id):
        gate.wait()
        try:
            accepted[request_id] = crew[request_id].refund(
                ORDER, asked[request_id], request_id
            )
        except RefundCeilingError as exc:
            refused[request_id] = str(exc)

    threads = [threading.Thread(target=agent, args=(rid,)) for rid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=JOIN)
    still_running = [t.name for t in threads if t.is_alive()]
    if still_running:
        raise AssertionError("caller(s) %s never came back" % (still_running,))

    unexpected = sorted(set(asked) - set(accepted) - set(refused))
    held = len(shop.refunds_held())
    shop.settle()
    promised = already_paid + sum(asked[rid] for rid in accepted)
    return shop.paid_out, promised, sorted(refused), unexpected, held


class RefundCeilingTest(unittest.TestCase):
    def setUp(self):
        self.shop = Shop()

    def attempt(self, amount, request_id, worker=None, psp_reference=ORDER):
        """Press refund and hand back whatever came of it: the refund, or the answer instead.

        The money is asserted on before the answer is, so a repair that pays twice is reported as
        the two figures that matter rather than as a missing exception.
        """
        try:
            return self.shop.refund(amount, request_id, psp_reference, worker=worker)
        except Exception as exc:  # noqa: BLE001 - the caller decides what the answer means
            return exc

    def check_concurrent(
        self,
        amounts,
        captured,
        expected_paid,
        expected_refusals,
        already_out=(),
    ):
        totals = []
        for run in range(RUNS):
            paid, promised, refused, unexpected, held = agents_at_once(
                amounts, captured, already_out
            )
            self.assertEqual(
                unexpected,
                [],
                "run %d: caller(s) %s neither refunded nor refused" % (run, unexpected),
            )
            self.assertLessEqual(
                paid,
                captured,
                "run %d: paid out %d against a capture of %d" % (run, paid, captured),
            )
            self.assertEqual(
                paid,
                promised,
                "run %d: paid out %d but told callers %d was going back"
                % (run, paid, promised),
            )
            self.assertEqual(
                len(refused),
                expected_refusals,
                "run %d: refused %s, expected %d refusal(s)"
                % (run, refused, expected_refusals),
            )
            self.assertEqual(
                held,
                len(already_out) + len(amounts) - len(refused),
                "run %d: processor was asked for %d refunds, %d caller(s) were told yes"
                % (run, held, len(amounts) - len(refused)),
            )
            totals.append(paid)
        self.assertEqual(
            totals, [expected_paid] * RUNS, "totals across runs were %s" % (totals,)
        )

    # ---- concurrent callers, each on her own worker --------------------------

    def test_two_workers_refunding_7000_each_return_7000_in_total(self):
        """12000 captured, two 7000 refunds at once: only one of them can be paid."""
        self.check_concurrent((7000, 7000), CAPTURED, 7000, 1)

    def test_two_workers_refunding_5000_each_are_both_paid(self):
        """12000 captured funds both 5000 refunds, so neither agent may be turned away."""
        self.check_concurrent((5000, 5000), CAPTURED, 10000, 0)

    def test_two_workers_at_once_on_a_smaller_capture(self):
        """9000 captured, two 6000 refunds at once: 6000 goes back, not 12000 and not 9000."""
        self.check_concurrent((6000, 6000), 9000, 6000, 1)

    def test_two_workers_at_once_on_a_larger_capture(self):
        """20000 captured: a 15000 refund is well funded, a second one is not."""
        self.check_concurrent((15000, 15000), 20000, 15000, 1)

    def test_two_workers_refunding_the_whole_capture_each_pay_it_once(self):
        """Both agents ask for the lot at once; the shop gives the lot back once."""
        self.check_concurrent((CAPTURED, CAPTURED), CAPTURED, CAPTURED, 1)

    # ---- more than two callers at once ---------------------------------------

    def test_three_workers_refunding_5000_each_at_once_return_10000(self):
        """12000 captured, three 5000 refunds in the same moment: only two of them fit."""
        self.check_concurrent((5000, 5000, 5000), CAPTURED, 10000, 1)

    def test_three_workers_within_the_capture_are_all_three_paid(self):
        """12000 funds 4000 three times over, so none of the three may be turned away."""
        self.check_concurrent((4000, 4000, 4000), CAPTURED, 12000, 0)

    def test_three_workers_on_what_a_settled_refund_left(self):
        """6000 of 12000 is already back; three 4000 clicks at once add only 4000 more."""
        self.check_concurrent(
            (4000, 4000, 4000), CAPTURED, 10000, 2, already_out=((6000, "req-0", True),)
        )

    # ---- concurrent callers on an order that is already part refunded --------

    def test_two_workers_at_once_on_what_a_settled_refund_left(self):
        """7000 of 12000 already went back; two 3000 refunds at once, only 3000 more fits.

        Neither request is large next to the capture. What they are large next to is the 1000 of
        headroom this order has left, which is the only figure that decides.
        """
        self.check_concurrent(
            (3000, 3000), CAPTURED, 10000, 1, already_out=((7000, "req-0", True),)
        )

    def test_two_workers_at_once_on_what_an_unsettled_refund_left(self):
        """11000 of 12000 is out but not yet settled; two 600 refunds at once, one fits."""
        self.check_concurrent(
            (600, 600), CAPTURED, 11600, 1, already_out=((11000, "req-0", False),)
        )

    def test_two_workers_at_once_inside_what_a_settled_refund_left(self):
        """4000 of 12000 is back and the remaining 8000 funds both 4000 refunds."""
        self.check_concurrent(
            (4000, 4000), CAPTURED, 12000, 0, already_out=((4000, "req-0", True),)
        )

    def test_two_workers_at_once_inside_what_an_unsettled_refund_left(self):
        """5000 of 20000 is in flight; 5000 each on two workers still fits twice over."""
        self.check_concurrent(
            (5000, 5000), 20000, 15000, 0, already_out=((5000, "req-0", False),)
        )

    # ---- the ceiling ---------------------------------------------------------

    def test_a_refund_the_processor_accepted_is_already_spoken_for(self):
        """A second 7000 cannot go out while the first 7000 is still unsettled."""
        self.shop.refund(7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(7000, "req-2")
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)

    def test_the_genuine_remainder_still_refunds_and_one_minor_unit_more_does_not(self):
        """After 7000 of 12000 has settled, 5000 must go out and 5001 must not."""
        self.shop.refund(7000, "req-1")
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(5001, "req-2")
        self.assertEqual(self.shop.settle(), 7000, "5001 leaked: paid out %d" % self.shop.paid_out)
        self.shop.refund(5000, "req-3")
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-4")
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    def test_settled_and_in_flight_refunds_both_eat_into_the_remainder(self):
        """4000 settled plus 3000 in flight leaves exactly 5000, not 8000."""
        self.shop.refund(4000, "req-1")
        self.shop.settle()
        self.shop.refund(3000, "req-2")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(5001, "req-3")
        self.shop.refund(5000, "req-4")
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    def test_the_ceiling_is_this_payment_s_own_capture(self):
        """A 20000 order refunds 20000 in full; a 9000 order stops at 9000."""
        big = Shop(20000)
        big.refund(12500, "req-1")
        with self.assertRaises(RefundCeilingError):
            big.refund(7501, "req-2")
        big.refund(7500, "req-3")
        self.assertEqual(big.settle(), 20000, "paid out %d on a 20000 capture" % big.paid_out)
        small = Shop(9000)
        small.refund(6000, "req-1")
        with self.assertRaises(RefundCeilingError):
            small.refund(3001, "req-2")
        small.refund(3000, "req-3")
        self.assertEqual(small.settle(), 9000, "paid out %d on a 9000 capture" % small.paid_out)

    def test_one_order_s_refunds_do_not_eat_another_order_s_remainder(self):
        """Two orders on one processor: 7000 back on the first leaves all 9000 on the second."""
        self.shop.seed("PSP002", 9000)
        self.shop.refund(7000, "req-1", ORDER)
        self.shop.refund(9000, "req-2", "PSP002")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-3", "PSP002")
        self.shop.refund(5000, "req-4", ORDER)
        self.assertEqual(
            self.shop.settle(), 21000, "paid out %d against 12000 + 9000" % self.shop.paid_out
        )

    def test_the_other_worker_sees_the_remainder_the_first_one_left(self):
        """7000 out on one worker leaves exactly 5000 for the other: 5001 no, 5000 yes."""
        other = self.shop.worker()
        self.shop.refund(7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(5001, "req-2", worker=other)
        self.assertEqual(self.shop.settle(), 7000, "5001 leaked: paid out %d" % self.shop.paid_out)
        self.shop.refund(5000, "req-3", worker=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-4", worker=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    def test_two_workers_each_refunding_part_of_a_funded_capture_both_pay(self):
        """Distinct clicks on distinct workers both go out when the capture funds both."""
        other = self.shop.worker()
        first = self.shop.refund(5000, "req-1")
        second = self.shop.refund(4000, "req-2", worker=other)
        self.assertNotEqual(
            reference_of(first), reference_of(second), "both clicks named %s" % reference_of(first)
        )
        held = self.shop.refunds_held()
        self.assertEqual(len(held), 2, "processor holds %d refunds for two clicks" % len(held))
        self.assertEqual(self.shop.settle(), 9000, "paid out %d" % self.shop.paid_out)

    def test_a_second_worker_sees_the_same_remainder(self):
        """The panel runs two workers; the one that did not send the first refund still knows."""
        other = self.shop.worker()
        self.shop.refund(7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(6000, "req-2", worker=other)
        self.shop.refund(5000, "req-3", worker=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-4", worker=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    # ---- one click, one refund ----------------------------------------------

    def test_pressing_refund_twice_on_one_click_pays_once(self):
        """A retry of the same click resolves to the first refund, ceiling untouched."""
        first = self.shop.refund(7000, "req-1")
        again = self.shop.refund(7000, "req-1")
        once_more = self.shop.refund(7000, "req-1")
        self.assertEqual(
            [reference_of(again), reference_of(once_more)],
            [reference_of(first), reference_of(first)],
            "retries named %s and %s, first was %s"
            % (reference_of(again), reference_of(once_more), reference_of(first)),
        )
        held = self.shop.refunds_held()
        self.assertEqual(len(held), 1, "processor holds %d refunds for one click" % len(held))
        self.shop.refund(5000, "req-2")
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-3")

    def test_a_retry_that_arrives_after_settlement_still_pays_once(self):
        """The click is retried once the money has already gone; nothing more may leave."""
        first = self.shop.refund(7000, "req-1")
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)
        again = self.shop.refund(7000, "req-1")
        self.assertEqual(
            reference_of(again),
            reference_of(first),
            "retry named %s, first was %s" % (reference_of(again), reference_of(first)),
        )
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)

    def test_a_retry_that_lands_on_the_other_worker_still_pays_once(self):
        """The agent presses refund again, the panel routes her to the other worker."""
        other = self.shop.worker()
        first = self.shop.refund(7000, "req-1")
        again = self.shop.refund(7000, "req-1", worker=other)
        self.assertEqual(
            reference_of(again),
            reference_of(first),
            "retry named %s, first was %s" % (reference_of(again), reference_of(first)),
        )
        held = self.shop.refunds_held()
        self.assertEqual(len(held), 1, "processor holds %d refunds for one click" % len(held))
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)

    def test_two_separate_clicks_for_the_same_amount_are_two_refunds(self):
        """Distinct request ids are distinct refunds when the capture can fund both."""
        first = self.shop.refund(5000, "req-1")
        second = self.shop.refund(5000, "req-2")
        self.assertNotEqual(
            reference_of(first), reference_of(second), "both clicks named %s" % reference_of(first)
        )
        held = self.shop.refunds_held()
        self.assertEqual(len(held), 2, "processor holds %d refunds for two clicks" % len(held))
        self.assertEqual(self.shop.settle(), 10000, "paid out %d" % self.shop.paid_out)

    # ---- a call to the processor that ended badly ----------------------------

    def test_a_request_that_never_left_the_shop_is_measured_again_when_it_is_retried(self):
        """The first agent's 7000 never went out, the second agent's 7000 did.

        Nothing left the shop under the first request, so the room it had taken is the order's
        again and the second agent is entitled to all of it. What the first agent's retry may not
        do is walk back in on the strength of the room it used to hold: by the time she presses
        refund again the order has 5000 left, and 7000 does not fit in 5000 whichever click asks.
        """
        first_agent = self.shop.worker()
        second_agent = self.shop.worker()
        first_agent.refuse_before_send()
        with self.assertRaises(PreSendRejected):
            self.shop.refund(7000, "req-a", worker=first_agent)
        held = self.shop.refunds_held()
        self.assertEqual(
            len(held),
            0,
            "processor holds %d refunds for a request that never left the shop" % len(held),
        )
        self.shop.refund(7000, "req-b", worker=second_agent)
        first_agent.heal()
        retried = self.attempt(7000, "req-a", worker=first_agent)
        paid = self.shop.settle()
        self.assertEqual(
            paid,
            7000,
            "settled %d against a capture of %d: a request that never left the shop was retried "
            "and paid on top of the 7000 another agent had taken in the meantime"
            % (paid, CAPTURED),
        )
        self.assertIsInstance(
            retried,
            RefundCeilingError,
            "the retry of the failed 7000 was answered with %r on an order with 5000 left"
            % (retried,),
        )

    def test_a_request_that_never_left_the_shop_may_be_retried_within_the_remainder(self):
        """The same failure, but only 5000 was taken meanwhile, so the retry fits and must pay.

        Room given back has to be room the click can have again. A repair that refuses the retry
        outright strands a shopper who is genuinely owed the money.
        """
        first_agent = self.shop.worker()
        second_agent = self.shop.worker()
        first_agent.refuse_before_send()
        with self.assertRaises(PreSendRejected):
            self.shop.refund(7000, "req-a", worker=first_agent)
        self.shop.refund(5000, "req-b", worker=second_agent)
        first_agent.heal()
        retried = self.attempt(7000, "req-a", worker=first_agent)
        self.assertNotIsInstance(
            retried,
            Exception,
            "the retry of the failed 7000 was answered with %r on an order with 7000 left"
            % (retried,),
        )
        held = self.shop.refunds_held()
        self.assertEqual(
            len(held), 2, "processor holds %d refunds for two clicks that both fit" % len(held)
        )
        paid = self.shop.settle()
        self.assertEqual(
            paid, CAPTURED, "settled %d against a capture of %d" % (paid, CAPTURED)
        )

    def test_a_refund_whose_answer_was_lost_keeps_its_room_until_the_processor_is_asked(self):
        """The processor took the 7000 and the answer never got home.

        The agent saw an error, so the shop has no name for the refund. The processor has one, and
        the refund is sitting there waiting for the nightly run. Until somebody asks it under the
        reference the click was sent with, that 7000 is spoken for: a second agent must be offered
        the 5000 that is really left and not a penny more, and the first agent pressing refund
        again must be handed the refund she already made rather than a second one.
        """
        first_agent = self.shop.worker()
        second_agent = self.shop.worker()
        first_agent.lose_the_answer()
        lost = self.attempt(7000, "req-a", worker=first_agent)
        self.assertIsInstance(
            lost, Exception, "the caller was told %r about an answer that never arrived" % (lost,)
        )
        self.assertNotIsInstance(
            lost,
            RefundCeilingError,
            "a lost answer was reported to the caller as a refusal: %r" % (lost,),
        )
        held = self.shop.refunds_held()
        self.assertEqual(
            len(held),
            1,
            "processor holds %d refunds after one whose answer was lost" % len(held),
        )
        blocked = self.attempt(7000, "req-b", worker=second_agent)
        paid = self.shop.settle()
        self.assertEqual(
            paid,
            7000,
            "settled %d against a capture of %d: a second 7000 went out while the first one's "
            "answer was still missing" % (paid, CAPTURED),
        )
        self.assertIsInstance(
            blocked,
            RefundCeilingError,
            "a second 7000 was answered with %r while the first 7000 was unaccounted for"
            % (blocked,),
        )
        self.shop.refund(5000, "req-c", worker=second_agent)
        first_agent.heal()
        again = self.attempt(7000, "req-a", worker=first_agent)
        paid = self.shop.settle()
        self.assertEqual(
            paid,
            CAPTURED,
            "settled %d against a capture of %d after the refund with no answer was retried"
            % (paid, CAPTURED),
        )
        still_held = self.shop.refunds_held()
        self.assertEqual(
            len(still_held),
            2,
            "processor holds %d refunds for two clicks, one of them retried" % len(still_held),
        )
        self.assertEqual(
            reference_of(again),
            reference_of(held[0]),
            "the retry came back as %s; the processor has held the first refund as %s all along"
            % (reference_of(again), reference_of(held[0])),
        )

    def test_a_second_press_turned_away_cannot_free_the_room_the_first_press_is_using(self):
        """One click is pressed twice, and it is the second press that never leaves the building.

        The first press is on the wire with 7000 of the order spoken for. The panel sends the
        agent's second press to another worker and that worker's connection turns it away before
        it goes. That failure is news about the second press and about nothing else: the room
        belongs to the request still in flight, so a third agent asking for 7000 must be told it
        does not fit in what is left, and the order must give back 7000 in all.

        Nothing here depends on how long anything takes. The first press is held exactly where a
        real one waits, and every wait is bounded and checked. A repair that serialises its
        callers cannot finish the second press while the first is held, so it is let out of the
        way first and asked the same question afterwards; either way the money is the assertion.
        """
        first = self.shop.worker()
        second = self.shop.worker()
        third = self.shop.worker()
        on_the_wire = threading.Event()
        proceed = threading.Event()
        first.hold_before_send(on_the_wire, proceed)
        pressed = {}
        second_press = {}

        def press_first():
            pressed["answer"] = self.attempt(7000, "req-a", worker=first)

        def press_again():
            second_press["answer"] = self.attempt(7000, "req-a", worker=second)

        holder = threading.Thread(target=press_first)
        holder.start()
        try:
            self.assertTrue(
                on_the_wire.wait(GATE), "the first press never reached the processor"
            )
            second.refuse_before_send()
            retry = threading.Thread(target=press_again)
            retry.start()
            retry.join(timeout=1.0)
            if retry.is_alive():
                # This repair takes its callers in turns, so the second press cannot come back
                # until the first one has. Let the first one go and ask the third agent after.
                proceed.set()
                retry.join(timeout=10.0)
            self.assertFalse(retry.is_alive(), "the second press never came back")
            blocked = self.attempt(7000, "req-c", worker=third)
        finally:
            proceed.set()
            holder.join(timeout=10.0)
        self.assertFalse(holder.is_alive(), "the press held on the wire never came back")
        paid = self.shop.settle()
        self.assertEqual(
            paid,
            7000,
            "settled %d against a capture of %d: a second press of one click that never left the "
            "shop freed the room the first press was still using" % (paid, CAPTURED),
        )
        held = self.shop.refunds_held()
        self.assertEqual(
            len(held), 1, "processor holds %d refunds for one click pressed twice" % len(held)
        )
        self.assertIsInstance(
            blocked,
            RefundCeilingError,
            "a third agent's 7000 was answered with %r while the first 7000 was in flight"
            % (blocked,),
        )
        self.assertNotIsInstance(
            pressed.get("answer"),
            Exception,
            "the press that was on the wire was answered with %r" % (pressed.get("answer"),),
        )

    def test_a_failed_press_cannot_free_room_a_second_press_of_it_is_about_to_send_into(self):
        """One click pressed twice, and it is the first press that never leaves the building.

        The agent presses refund for 7000. Her worker takes that room, and its connection then
        turns the request away before it goes, so the room is the order's again and the next agent
        is entitled to it. Her second press, routed to another worker, met that click while it was
        still in reserve and unaccounted for. That press may not send. The room it would send
        against is not its own and is about to be handed to somebody else, so a press that sends
        anyway returns 14000 on a capture of 12000: 7000 to the third agent the room went to, and
        7000 to a request that was already on its way out under the same click.

        The room that failure hands back is the order's, so exactly one refund of it goes out:
        to the third agent, or to the second press if the repair takes its callers in turns. A
        repair that gives it to neither has not made the money safe, it has stranded it.

        Nothing here waits on a sleep. The press that fails is held on an event exactly where a
        real one waits, the second press announces itself on an event of its own, every join is
        bounded, and a thread still alive after its join is a failure. Every worker's answer,
        refund or exception, is carried back to this thread and asserted on, so an error a worker
        met is an error this test reports rather than one that died where nobody was looking.
        """
        owner = self.shop.worker()
        retry = self.shop.worker()
        third = self.shop.worker()

        owner_on_the_wire = threading.Event()
        release_owner = threading.Event()
        retry_stopped = threading.Event()
        release_retry = threading.Event()

        def owner_before_send(operation, psp_reference):
            """The first press, held on the point of going and then turned away."""
            if operation != "refund":
                return
            owner_on_the_wire.set()
            if not release_owner.wait(GATE):
                raise AssertionError("the first press was never released")
            raise PreSendRejected("no outbound connection for %s" % psp_reference)

        def retry_before_send(operation, psp_reference):
            """The second press has reached the wire, which is further than it should have got."""
            if operation != "refund":
                return
            retry_stopped.set()
            if not release_retry.wait(GATE):
                raise AssertionError("the second press was never released")

        owner.client.before_send = owner_before_send
        retry.client.before_send = retry_before_send

        answers = {}
        dropped = {}

        def press(name, worker, request_id, done=None):
            """Press refund on its own thread, carrying whatever came of it back to this one."""
            def run():
                try:
                    answers[name] = worker.refund(ORDER, 7000, request_id)
                except Exception as exc:  # noqa: BLE001 - carried back, never swallowed
                    answers[name] = exc
                    if isinstance(exc, AssertionError):
                        dropped[name] = traceback.format_exc()
                except BaseException as exc:
                    answers[name] = exc
                    dropped[name] = traceback.format_exc()
                    raise
                finally:
                    if done is not None:
                        done.set()

            return threading.Thread(target=run)

        owner_thread = press("owner", owner, "req-a")
        retry_thread = press("retry", retry, "req-a", done=retry_stopped)
        owner_thread.start()
        try:
            self.assertTrue(
                owner_on_the_wire.wait(GATE), "the first press never reached the wire"
            )
            retry_thread.start()
            if not retry_stopped.wait(HOLD):
                # This repair takes its callers in turns, so the second press cannot get anywhere
                # while the first one is held. Let the first one go and wait for the second after.
                release_owner.set()
                self.assertTrue(retry_stopped.wait(GATE), "the second press never came back")
            release_owner.set()
            owner_thread.join(timeout=JOIN)
            self.assertFalse(
                owner_thread.is_alive(), "the press that was turned away never came back"
            )
            answers["third"] = self.attempt(7000, "req-c", worker=third)
        finally:
            release_owner.set()
            release_retry.set()
            for thread in (owner_thread, retry_thread):
                if thread.ident is not None:
                    thread.join(timeout=JOIN)

        for thread, what in ((owner_thread, "the press that was turned away"),
                             (retry_thread, "the second press of that click")):
            self.assertFalse(thread.is_alive(), "%s never came back" % what)
        self.assertEqual(
            dropped, {}, "a worker died where nobody was looking:\n%s" % "\n".join(
                "%s: %s" % (name, text) for name, text in sorted(dropped.items())
            )
        )
        self.assertEqual(
            sorted(answers),
            ["owner", "retry", "third"],
            "only %s came back out of three presses" % (sorted(answers),),
        )

        accepted = sorted(
            name for name, answer in answers.items() if not isinstance(answer, Exception)
        )
        promised = 7000 * len(accepted)
        held = self.shop.refunds_held()
        paid = self.shop.settle()
        self.assertLessEqual(
            paid,
            CAPTURED,
            "settled %d against a capture of %d: a press that never left the shop handed its room "
            "to a third agent while a second press of the same click was still sending into it"
            % (paid, CAPTURED),
        )
        self.assertEqual(
            paid,
            promised,
            "settled %d but told %s that %d was going back" % (paid, accepted, promised),
        )
        self.assertEqual(
            paid,
            7000,
            "settled %d where 7000 was due: the room a press that never left the shop gave up is "
            "the order's again, so one refund of it goes back - to the agent it was handed to, or "
            "to the second press if this repair takes its callers in turns - and a repair that "
            "gives it to neither has stranded money the shopper is owed" % paid,
        )
        self.assertEqual(
            len(held),
            len(accepted),
            "processor was asked for %d refunds, %d caller(s) were told yes"
            % (len(held), len(accepted)),
        )
        self.assertIsInstance(
            answers["owner"],
            PreSendRejected,
            "the press its own connection turned away before it went was answered with %r"
            % (answers["owner"],),
        )

    def test_a_click_that_comes_back_changed_neither_pays_nor_frees_its_room(self):
        """One click's identity is presented again with different money behind it.

        A click stands for one amount on one order. Presented for another amount, or against
        another order, it is not that click: paying it would refund the same click twice, and
        treating it as a fresh request would let it hand away the room the refund it really made
        is holding. It does neither.
        """
        agent = self.shop.worker()
        other_agent = self.shop.worker()
        self.shop.seed("PSP002", 9000, "USD")
        self.shop.refund(5000, "req-1", worker=agent)
        changed_amount = self.attempt(6000, "req-1", worker=other_agent)
        changed_order = self.attempt(5000, "req-1", worker=other_agent, psp_reference="PSP002")
        paid = self.shop.settle()
        self.assertEqual(
            paid,
            5000,
            "settled %d after a single 5000 click was presented again for 6000 and again against "
            "another order" % paid,
        )
        held = self.shop.refunds_held()
        self.assertEqual(
            len(held), 1, "processor holds %d refunds for one click" % len(held)
        )
        elsewhere = self.shop.refunds_held("PSP002")
        self.assertEqual(
            len(elsewhere),
            0,
            "processor holds %d refunds on an order the click was never made against"
            % len(elsewhere),
        )
        self.assertIsInstance(
            changed_amount,
            Exception,
            "the 5000 click presented again for 6000 was answered with %r" % (changed_amount,),
        )
        self.assertIsInstance(
            changed_order,
            Exception,
            "the click presented against another order was answered with %r" % (changed_order,),
        )
        too_much = self.attempt(7001, "req-2", worker=other_agent)
        self.assertIsInstance(
            too_much,
            RefundCeilingError,
            "7001 was answered with %r on an order with 7000 left" % (too_much,),
        )
        self.shop.refund(7000, "req-3", worker=other_agent)
        paid = self.shop.settle()
        self.assertEqual(
            paid, CAPTURED, "settled %d against a capture of %d" % (paid, CAPTURED)
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
