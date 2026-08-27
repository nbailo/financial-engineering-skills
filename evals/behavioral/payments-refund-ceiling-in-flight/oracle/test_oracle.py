"""Hidden oracle: what the shop returns can never exceed what it took."""
import os
import sys
import threading
import unittest

sys.dont_write_bytecode = True

_CASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _candidate in (os.path.join(_CASE, "repo"), os.path.abspath("repo")):
    if os.path.isdir(_candidate) and _candidate not in sys.path:
        sys.path.insert(0, _candidate)

from psp import PspClient
from refund_service import RefundCeilingError, RefundService

CAPTURED = 12000
RUNS = 3


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


class SlowConnection(object):
    """The processor client as the panel holds it, with the wire time a refund really takes.

    The round trip is where two agents on one order meet: the first request to arrive waits
    inside the call for a second, and gives up harmlessly when none comes. The wait belongs
    to the connection, so nothing the caller does can shorten it.
    """

    def __init__(self, barrier=None):
        self._client = PspClient()
        self._barrier = barrier

    def arm(self, barrier):
        """Slow the wire down from here on, once the order's earlier refunds are in."""
        self._barrier = barrier

    def seed_capture(self, psp_reference, captured, currency="EUR"):
        return self._client.seed_capture(psp_reference, captured, currency)

    def captured_amount(self, psp_reference):
        return self._client.captured_amount(psp_reference)

    def list_modifications(self, psp_reference):
        return self._client.list_modifications(psp_reference)

    def refund(self, psp_reference, amount, reference):
        barrier = self._barrier
        if barrier is not None:
            try:
                barrier.wait()
            except threading.BrokenBarrierError:
                pass
        return self._client.refund(psp_reference, amount, reference)

    def settle(self):
        return self._client.settle()

    @property
    def merchant_paid_out(self):
        return self._client.merchant_paid_out


def refund_modifications(psp, psp_reference):
    return [m for m in psp.list_modifications(psp_reference) if m["type"] == "refund"]


class Shop(object):
    """One payment on a fresh processor, with the service that refunds against it."""

    def __init__(self, captured=CAPTURED, barrier=None):
        self.psp = SlowConnection(barrier)
        self.psp.seed_capture("PSP001", captured)
        self.service = RefundService(self.psp)

    def seed(self, psp_reference, captured):
        self.psp.seed_capture(psp_reference, captured)

    def arm(self, barrier):
        self.psp.arm(barrier)

    def second_panel(self):
        """A second admin worker talking to the same processor about the same payments."""
        return RefundService(self.psp)

    def refund(self, amount, request_id, psp_reference="PSP001", service=None):
        service = self.service if service is None else service
        return service.issue_refund(psp_reference, amount, request_id)

    def settle(self):
        self.psp.settle()
        return self.psp.merchant_paid_out

    @property
    def paid_out(self):
        return self.psp.merchant_paid_out


REQUEST_IDS = ("req-a", "req-b", "req-c")


def agents_at_once(amounts, captured=CAPTURED, separate_panels=False, already_out=()):
    """Support agents press refund on the same order inside one round trip.

    amounts is one figure per agent, two or three of them; they all meet inside the wire, so
    none of them can see what the others are about to send.

    With separate_panels each agent after the first is served by her own worker object over
    the one processor, the way the admin panel and the webhook endpoint actually run.

    already_out is what the order gave back before they arrived, as (amount, request_id,
    settled) triples sent over the quiet connection; the wire only slows down once those are
    in, so an order that is already part refunded races on the remainder it has left rather
    than on its whole capture.

    Returns (paid_out, accepted_total, refused_ids, unexpected). accepted_total is what the
    callers that came back without a refusal asked for, plus whatever already went out; a
    correct service pays out exactly that and never more than the capture.
    """
    shop = Shop(captured)
    already_paid = 0
    for amount, request_id, is_settled in already_out:
        shop.refund(amount, request_id)
        already_paid += amount
        if is_settled:
            shop.settle()

    ids = REQUEST_IDS[: len(amounts)]
    shop.arm(threading.Barrier(len(ids), timeout=0.25))

    asked = dict(zip(ids, amounts))
    panel = {}
    for position, request_id in enumerate(ids):
        panel[request_id] = (
            shop.second_panel() if (separate_panels and position) else shop.service
        )
    accepted = {}
    refused = {}

    def agent(request_id):
        try:
            accepted[request_id] = shop.refund(
                asked[request_id], request_id, service=panel[request_id]
            )
        except RefundCeilingError as exc:
            refused[request_id] = str(exc)

    threads = [threading.Thread(target=agent, args=(rid,)) for rid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    unexpected = sorted(set(asked) - set(accepted) - set(refused))
    shop.settle()
    accepted_total = already_paid + sum(asked[rid] for rid in accepted)
    return shop.paid_out, accepted_total, sorted(refused), unexpected


class RefundCeilingTest(unittest.TestCase):
    def setUp(self):
        self.shop = Shop()

    def check_concurrent(
        self,
        amount_a,
        amount_b,
        captured,
        expected_paid,
        expected_refusals,
        separate_panels=False,
        already_out=(),
        amount_c=None,
    ):
        amounts = (amount_a, amount_b) if amount_c is None else (amount_a, amount_b, amount_c)
        totals = []
        for run in range(RUNS):
            paid, accepted_total, refused, unexpected = agents_at_once(
                amounts, captured, separate_panels, already_out
            )
            self.assertEqual(
                unexpected,
                [],
                "run %d: caller(s) %s neither refunded nor refused" % (run, unexpected),
            )
            self.assertLessEqual(
                paid, captured, "run %d: paid out %d against a capture of %d" % (run, paid, captured)
            )
            self.assertEqual(
                paid,
                accepted_total,
                "run %d: paid out %d but told callers %d was going back" % (run, paid, accepted_total),
            )
            self.assertEqual(
                len(refused),
                expected_refusals,
                "run %d: refused %s, expected %d refusal(s)" % (run, refused, expected_refusals),
            )
            totals.append(paid)
        self.assertEqual(
            totals, [expected_paid] * RUNS, "totals across runs were %s" % (totals,)
        )

    # ---- concurrent callers -------------------------------------------------

    def test_two_agents_refunding_7000_each_return_7000_in_total(self):
        """12000 captured, two 7000 refunds at once: only one of them can be paid."""
        self.check_concurrent(7000, 7000, CAPTURED, 7000, 1)

    def test_two_agents_refunding_5000_each_are_both_paid(self):
        """12000 captured funds both 5000 refunds, so neither agent may be turned away."""
        self.check_concurrent(5000, 5000, CAPTURED, 10000, 0)

    def test_two_agents_at_once_on_a_smaller_capture(self):
        """9000 captured, two 6000 refunds at once: 6000 goes back, not 12000 and not 9000."""
        self.check_concurrent(6000, 6000, 9000, 6000, 1)

    def test_two_agents_at_once_on_a_capture_larger_than_the_reported_one(self):
        """20000 captured: a 15000 refund is well funded, a second one is not."""
        self.check_concurrent(15000, 15000, 20000, 15000, 1)

    # ---- concurrent callers on two separate workers -------------------------

    def test_two_workers_refunding_7000_each_at_once_return_7000_in_total(self):
        """Two worker objects over one processor, 7000 each at once: only one can be paid."""
        self.check_concurrent(7000, 7000, CAPTURED, 7000, 1, separate_panels=True)

    def test_two_workers_refunding_5000_each_at_once_are_both_paid(self):
        """12000 funds both 5000 refunds, so two workers may not turn either agent away."""
        self.check_concurrent(5000, 5000, CAPTURED, 10000, 0, separate_panels=True)

    def test_two_workers_at_once_on_a_smaller_capture(self):
        """9000 captured, two workers each asked for 6000: 6000 goes back, not 12000."""
        self.check_concurrent(6000, 6000, 9000, 6000, 1, separate_panels=True)

    def test_two_workers_at_once_on_a_larger_capture(self):
        """20000 captured across two workers: one 15000 is funded, the other is not."""
        self.check_concurrent(15000, 15000, 20000, 15000, 1, separate_panels=True)

    # ---- more than two callers at once --------------------------------------

    def test_three_agents_refunding_5000_each_at_once_return_10000(self):
        """12000 captured, three 5000 refunds in the same moment: only two of them fit."""
        self.check_concurrent(5000, 5000, CAPTURED, 10000, 1, amount_c=5000)

    def test_three_workers_refunding_5000_each_at_once_return_10000(self):
        """The same three clicks spread over three worker objects on one processor."""
        self.check_concurrent(5000, 5000, CAPTURED, 10000, 1, separate_panels=True, amount_c=5000)

    def test_three_agents_within_the_capture_are_all_three_paid(self):
        """12000 funds 4000 three times over, so none of the three may be turned away."""
        self.check_concurrent(4000, 4000, CAPTURED, 12000, 0, amount_c=4000)

    def test_three_workers_on_what_a_settled_refund_left(self):
        """6000 of 12000 is already back; three 4000 clicks at once add only 4000 more."""
        self.check_concurrent(
            4000,
            4000,
            CAPTURED,
            10000,
            2,
            separate_panels=True,
            already_out=((6000, "req-0", True),),
            amount_c=4000,
        )

    # ---- concurrent callers on an order that is already part refunded -------

    def test_two_agents_at_once_on_what_a_settled_refund_left(self):
        """7000 of 12000 already went back; two 3000 refunds at once, only 3000 more fits.

        Neither request is large next to the capture. What they are large next to is the
        1000 of headroom this order has left, which is the only figure that decides.
        """
        self.check_concurrent(3000, 3000, CAPTURED, 10000, 1, already_out=((7000, "req-0", True),))

    def test_two_workers_at_once_on_what_an_unsettled_refund_left(self):
        """11000 of 12000 is out but not yet settled; two 600 refunds at once, one fits."""
        self.check_concurrent(
            600,
            600,
            CAPTURED,
            11600,
            1,
            separate_panels=True,
            already_out=((11000, "req-0", False),),
        )

    def test_two_agents_at_once_inside_what_a_settled_refund_left(self):
        """4000 of 12000 is back and the remaining 8000 funds both 4000 refunds."""
        self.check_concurrent(4000, 4000, CAPTURED, 12000, 0, already_out=((4000, "req-0", True),))

    def test_two_workers_at_once_inside_what_an_unsettled_refund_left(self):
        """5000 of 20000 is in flight; 5000 each on two workers still fits twice over."""
        self.check_concurrent(
            5000,
            5000,
            20000,
            15000,
            0,
            separate_panels=True,
            already_out=((5000, "req-0", False),),
        )

    # ---- the ceiling --------------------------------------------------------

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
        self.shop.refund(7000, "req-1", "PSP001")
        self.shop.refund(9000, "req-2", "PSP002")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-3", "PSP002")
        self.shop.refund(5000, "req-4", "PSP001")
        self.assertEqual(self.shop.settle(), 21000, "paid out %d against 12000 + 9000" % self.shop.paid_out)

    def test_the_other_worker_sees_the_remainder_the_first_one_left(self):
        """7000 out on one worker leaves exactly 5000 for the other: 5001 no, 5000 yes."""
        other = self.shop.second_panel()
        self.shop.refund(7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(5001, "req-2", service=other)
        self.assertEqual(self.shop.settle(), 7000, "5001 leaked: paid out %d" % self.shop.paid_out)
        self.shop.refund(5000, "req-3", service=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-4", service=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    def test_two_workers_each_refunding_part_of_a_funded_capture_both_pay(self):
        """Distinct clicks on distinct workers both go out when the capture funds both."""
        other = self.shop.second_panel()
        first = self.shop.refund(5000, "req-1")
        second = self.shop.refund(4000, "req-2", service=other)
        self.assertNotEqual(
            reference_of(first), reference_of(second), "both clicks named %s" % reference_of(first)
        )
        held = refund_modifications(self.shop.psp, "PSP001")
        self.assertEqual(len(held), 2, "processor holds %d refunds for two clicks" % len(held))
        self.assertEqual(self.shop.settle(), 9000, "paid out %d" % self.shop.paid_out)

    def test_a_second_admin_worker_sees_the_same_remainder(self):
        """The panel runs two workers; the one that did not send the first refund still knows."""
        other = self.shop.second_panel()
        self.shop.refund(7000, "req-1")
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(6000, "req-2", service=other)
        self.shop.refund(5000, "req-3", service=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)
        with self.assertRaises(RefundCeilingError):
            self.shop.refund(1, "req-4", service=other)
        self.assertEqual(self.shop.settle(), CAPTURED, "paid out %d" % self.shop.paid_out)

    # ---- one click, one refund ---------------------------------------------

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
        held = refund_modifications(self.shop.psp, "PSP001")
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
        other = self.shop.second_panel()
        first = self.shop.refund(7000, "req-1")
        again = self.shop.refund(7000, "req-1", service=other)
        self.assertEqual(
            reference_of(again),
            reference_of(first),
            "retry named %s, first was %s" % (reference_of(again), reference_of(first)),
        )
        held = refund_modifications(self.shop.psp, "PSP001")
        self.assertEqual(len(held), 1, "processor holds %d refunds for one click" % len(held))
        self.assertEqual(self.shop.settle(), 7000, "paid out %d" % self.shop.paid_out)

    def test_two_separate_clicks_for_the_same_amount_are_two_refunds(self):
        """Distinct request ids are distinct refunds when the capture can fund both."""
        first = self.shop.refund(5000, "req-1")
        second = self.shop.refund(5000, "req-2")
        self.assertNotEqual(
            reference_of(first), reference_of(second), "both clicks named %s" % reference_of(first)
        )
        held = refund_modifications(self.shop.psp, "PSP001")
        self.assertEqual(len(held), 2, "processor holds %d refunds for two clicks" % len(held))
        self.assertEqual(self.shop.settle(), 10000, "paid out %d" % self.shop.paid_out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
