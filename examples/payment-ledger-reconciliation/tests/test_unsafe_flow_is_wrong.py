"""Proof that the counter-example is actually wrong, and wrong quietly.

Every case here asserts two things: that nothing raised, and that the number is wrong.
A defect that raises is a defect somebody finds.
"""
import unittest

from demo import AMOUNT_MINOR, CURRENCY, INVOICE, run_safe, run_unsafe
from fake_processor import FakeProcessor, load_settlement_report, load_webhooks
from ledger import CASH, FEES, Ledger, RECEIVABLE, REVENUE, SUSPENSE, exposure, reconcile
from safe_flow import InjectedFailure
from tests.concurrency import race
from unsafe_flow import UnsafeFlow

FIRST_KEY = f"{INVOICE}-attempt1"


def flow_with(ambiguous=()):
    ledger = Ledger()
    return FakeProcessor(ambiguous_keys=set(ambiguous)), ledger, UnsafeFlow(ledger)


class TheSubmissionPath(unittest.TestCase):
    def test_a_timeout_resend_charges_the_customer_twice(self):
        processor, ledger, flow = flow_with(ambiguous=[FIRST_KEY])
        flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY)
        self.assertEqual(processor.charge_count(INVOICE), 2)
        self.assertEqual(processor.charged_total_minor(INVOICE), 2 * AMOUNT_MINOR)
        self.assertEqual(processor.lookups, [], "it never asked about the key it sent")
        self.assertNotEqual(processor.sends[0], processor.sends[1],
                            "and the second send carried a brand new key")
        self.assertEqual(ledger.balance(REVENUE, CURRENCY), -AMOUNT_MINOR,
                         "while the ledger records only one of the two charges")

    def test_an_injected_failure_after_the_charge_leaves_nothing_to_recover(self):
        processor, ledger, flow = flow_with()

        def hook():
            raise InjectedFailure("power lost after the charge")

        with self.assertRaises(InjectedFailure):
            flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY,
                             fail_after_send=hook)
        self.assertEqual(processor.charge_count(INVOICE), 1, "the money moved")
        self.assertEqual(flow.charges, {}, "and nothing local knows it")
        self.assertEqual(ledger.entries(), ())
        flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY)
        self.assertEqual(processor.charge_count(INVOICE), 2,
                         "so the only way forward charges again")


class TheDeliveryPath(unittest.TestCase):
    def setUp(self):
        self.processor, self.ledger, self.flow = flow_with()
        self.flow.pay_invoice(self.processor, INVOICE, AMOUNT_MINOR, CURRENCY)
        self.first, self.redelivery = load_webhooks()

    def test_a_redelivery_is_credited_twice(self):
        self.assertNotEqual(self.first["event_id"], self.redelivery["event_id"])
        self.assertNotEqual(self.first["delivery_id"], self.redelivery["delivery_id"])
        self.assertEqual(self.first["data"]["settlement_id"],
                         self.redelivery["data"]["settlement_id"],
                         "the settlement identity is the only thing stable across the two")
        self.assertEqual(self.flow.handle_webhook(self.first), "APPLIED")
        self.assertEqual(self.flow.handle_webhook(self.redelivery), "APPLIED")
        self.assertEqual(self.ledger.balance(CASH, CURRENCY), 2 * 12125)
        self.assertEqual(self.ledger.balance(FEES, CURRENCY), 2 * 375)

    def test_two_separate_workers_share_no_dedupe_state_and_both_credit(self):
        first = UnsafeFlow(self.ledger, worker="w1")
        second = UnsafeFlow(self.ledger, worker="w2")
        result = race(lambda seam: first.handle_webhook(self.first, seam=seam),
                      lambda seam: second.handle_webhook(self.first, seam=seam))
        self.assertEqual(result.alive, [], "a worker was still running after the join")
        result.raise_errors()
        # One envelope, one delivery_id: a dedupe identity both workers could see would
        # have refused the second. Each worker scopes the identity to itself, which is
        # exactly the false note, so neither ever sees the other.
        self.assertEqual(result.outcomes, ["APPLIED", "APPLIED"])
        self.assertEqual(result.seam_entries, 2,
                         "nothing held either worker out, so both were inside the window; "
                         "that is what makes the safe path's count of one a lock and not "
                         "a harness that failed to run two threads")
        self.assertEqual(self.ledger.balance(CASH, CURRENCY), 2 * 12125)
        self.assertEqual(self.ledger.balance(RECEIVABLE, CURRENCY), -12500)
        self.assertEqual(self.ledger.trial_balance(), {CURRENCY: 0},
                         "each entry balances, which is why nothing complains")

    def test_the_notification_body_is_trusted_and_posted_verbatim(self):
        tampered = dict(self.first, data=dict(self.first["data"], amount_minor=999_999,
                                              fee_minor=0, net_minor=999_999))
        self.assertEqual(self.flow.handle_webhook(tampered), "APPLIED")
        self.assertEqual(self.ledger.balance(CASH, CURRENCY), 999_999,
                         "whatever the payload claimed became the ledger's cash")
        self.assertEqual(self.processor.get_charge("ch_1")["net_minor"], 12125,
                         "while the processor, never asked, says something else")

    def test_an_event_of_any_type_at_all_is_posted(self):
        self.assertEqual(self.flow.handle_webhook(dict(self.first,
                                                       type="payment.refunded")),
                         "APPLIED")
        self.assertEqual(self.ledger.balance(CASH, CURRENCY), 12125,
                         "a refund notification credited cash")


class TheReconciliation(unittest.TestCase):
    def test_the_break_is_plugged_into_suspense_and_the_amounts_then_agree(self):
        report = load_settlement_report()
        processor, ledger, flow = flow_with()
        flow.pay_invoice(processor, INVOICE, AMOUNT_MINOR, CURRENCY)
        for envelope in load_webhooks():
            flow.handle_webhook(envelope)
        before = reconcile(report, ledger)
        self.assertEqual(exposure(before), {CURRENCY: 12850},
                         "the break is there to find, in the currency it is in")
        after = flow.run_reconciliation(report)
        self.assertEqual(ledger.balance(CASH, CURRENCY), report["lines"][0]["net_minor"])
        self.assertEqual(ledger.balance(FEES, CURRENCY), report["lines"][0]["fee_minor"])
        self.assertEqual(ledger.balance(SUSPENSE, CURRENCY), 12500,
                         "the difference went to an account nobody reads")
        self.assertEqual([b.delta("net") for b in after], [0],
                         "and the numbers an operator checks now agree with the report")


class TheTwoPathsDisagree(unittest.TestCase):
    def test_the_unsafe_run_ends_somewhere_else_and_hides_the_difference(self):
        safe_processor, safe_store, safe_breaks = run_safe()
        bad_processor, bad_ledger, bad_breaks = run_unsafe()
        self.assertEqual(safe_processor.charged_total_minor(INVOICE), 12500)
        self.assertEqual(bad_processor.charged_total_minor(INVOICE), 37500)
        self.assertEqual([b.kind for b in safe_breaks], ["amount_mismatch"])
        self.assertEqual(exposure(safe_breaks), {CURRENCY: 25})
        self.assertEqual([b.kind for b in bad_breaks], ["duplicate_entry"])
        self.assertNotEqual(safe_store.ledger.balances(), bad_ledger.balances())
        self.assertEqual(bad_ledger.balances(),
                         {(CASH, "USD"): 12100, (RECEIVABLE, "USD"): -12500,
                          (FEES, "USD"): 400, (REVENUE, "USD"): -12500,
                          (SUSPENSE, "USD"): 12500})
        self.assertNotIn("charged", bad_breaks[0].describe(),
                         "nothing open says the invoice was paid three times")

    def test_five_consecutive_unsafe_runs_are_identically_wrong(self):
        results = [run_unsafe()[1].balances() for _ in range(5)]
        self.assertEqual(results, [results[0]] * 5)
