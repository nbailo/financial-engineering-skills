"""The safe path, one property per test. Each name is a claim the implementation makes.

Every concurrency case builds two separate workers over one shared store, because two calls
through one bound method only ever prove that an object is safe against itself.
"""
import dataclasses
import unittest

from demo import AMOUNT_MINOR, BATCH, CURRENCY, INVOICE, run_safe
from fake_processor import (ChargeRequest, DEFAULT_AUTHORITY, FakeProcessor, KeyMismatch,
                            WrongAuthority, load_settlement_report, load_webhooks)
from ledger import (Attribution, CASH, EffectConflict, FEES, Ledger, Posting, RECEIVABLE,
                    REVENUE, Unbalanced, exposure)
from money import (AuthorityScope, InvalidAmount, InvalidCurrency,
                   InvalidIdentifier)
from safe_flow import (AuthorityMismatch, ResponseMismatch, SafeFlow, InjectedFailure,
                       intent_key)
from store import (BreakAlreadyClosed, ForeignAuthority, IntentMismatch, Store,
                   UnknownBreak)
from tests.concurrency import race

KEY = intent_key(INVOICE)
def capture_effect(processor):
    """Our dedupe identity for that backend's ch_1. The counterparty's id alone is not one:
    every backend calls its first charge ch_1."""
    return f"capture:{processor.authority.describe()}:ch_1"


def settlement_effect(processor):
    return f"settlement:{processor.authority.describe()}:st_1"
# Real, separately constructed backends. An intent pins a backend that exists, so these are
# the scopes of actual processors rather than labels with no backend behind them.
OTHER_ACCOUNT = FakeProcessor(AuthorityScope("fakeproc", "acct_other", "us-east-1")).authority
OTHER_REGION = FakeProcessor(AuthorityScope("fakeproc", "acct_main", "eu-west-1")).authority
OTHER_PROVIDER = FakeProcessor(AuthorityScope("otherproc", "acct_main", "us-east-1")).authority


class WatchingProcessor(FakeProcessor):
    """Records what the shared store held at the moment the charge went out."""

    def __init__(self, store, **kwargs):
        super().__init__(**kwargs)
        self.store = store
        self.intents_at_send = []

    def charge(self, req):
        self.intents_at_send.append(self.store.get_intent(req.idempotency_key))
        return super().charge(req)


class MisroutingProcessor(FakeProcessor):
    """Answers every lookup with some OTHER operation's capture."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.other = None

    def lookup(self, idempotency_key: str):
        self.lookups.append(idempotency_key)
        self.calls.append(("lookup", idempotency_key))
        return dict(self.other) if self.other is not None else None


class LyingLookupProcessor(FakeProcessor):
    """Answers `get_charge` with a charge that is not the one it was asked about."""

    def get_charge(self, charge_id: str):
        charge = super().get_charge(charge_id)
        return None if charge is None else dict(charge, charge_id="ch_somebody_else")


class MissettlingProcessor(FakeProcessor):
    """Answers about a charge with a settlement that names a different charge."""

    def settlement_for_charge(self, charge_id: str):
        settled = super().settlement_for_charge(charge_id)
        if settled is None:
            return None
        return dict(settled, charge_id="ch_somebody_else")


def failure_hook():
    def hook():
        raise InjectedFailure("power lost after the charge, before the ledger write")
    return hook


def pay(store, processor, amount=AMOUNT_MINOR, fail=None, currency=CURRENCY, **kwargs):
    return SafeFlow(store, processor).pay_invoice(INVOICE, amount, currency,
                                                  fail_after_send=fail, **kwargs)


def paid(processor=None, settle=True):
    """One captured charge over a fresh shared store, optionally settled at the processor."""
    processor = processor or FakeProcessor()
    store = Store(Ledger())
    pay(store, processor)
    if settle:
        processor.settle("ch_1", BATCH)
    return processor, store


def altered_report(**line):
    payload = load_settlement_report()
    payload["lines"] = [dict(payload["lines"][0], **line)]
    payload["total_net_minor"] = payload["lines"][0]["net_minor"]
    return payload


class NothingReachesTheWireUnvalidated(unittest.TestCase):
    def setUp(self):
        self.processor, self.store = FakeProcessor(), Store(Ledger())

    def assert_no_external_effect(self):
        self.assertEqual(self.processor.calls, [], "nothing was sent and nothing was asked")
        self.assertEqual(self.processor.sends, [])
        self.assertEqual(self.store.intents(), (), "and no intent was created either")
        self.assertEqual(self.store.ledger.entries(), ())

    def test_an_amount_that_is_not_an_exact_positive_integer_has_zero_external_effects(self):
        for amount in (125.0, 0.1 + 0.2, True, False, 0, -1, "12500", None):
            with self.subTest(amount=amount):
                with self.assertRaises(InvalidAmount):
                    pay(self.store, self.processor, amount=amount)
                self.assert_no_external_effect()

    def test_a_currency_that_is_not_a_canonical_code_has_zero_external_effects(self):
        for currency in ("", "usd", "US", "USDT", None, 840):
            with self.subTest(currency=currency):
                with self.assertRaises(InvalidCurrency):
                    pay(self.store, self.processor, currency=currency)
                self.assert_no_external_effect()

    def test_the_same_rule_is_enforced_at_the_processor_boundary(self):
        self.scope = self.processor.authority.fields()
        for amount in (125.0, True, 0, -1):
            with self.subTest(amount=amount):
                with self.assertRaises(InvalidAmount):
                    self.processor.charge(ChargeRequest(KEY, INVOICE, amount, CURRENCY,
                                                        self.scope))
                self.assertEqual(self.processor.sends, [], "it never became a send")
        with self.assertRaises(InvalidCurrency):
            self.processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, "usd",
                                                self.scope))
        self.assertEqual(self.processor.charge_count(INVOICE), 0)


class TheIdentityExistsBeforeTheEffect(unittest.TestCase):
    def test_the_identity_is_committed_before_the_send(self):
        store = Store(Ledger())
        processor = WatchingProcessor(store)
        pay(store, processor)
        self.assertEqual(len(processor.intents_at_send), 1)
        self.assertEqual(processor.intents_at_send[0].state, "PENDING")

    def test_a_key_reused_with_a_different_amount_is_refused_by_the_processor(self):
        processor = FakeProcessor()
        authority = processor.authority.fields()
        processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY, authority))
        with self.assertRaises(KeyMismatch):
            processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR + 1, CURRENCY,
                                           authority))
        self.assertEqual(processor.charge_count(INVOICE), 1)


class EveryOperationIsBoundToOneAuthority(unittest.TestCase):
    def test_identical_labels_are_not_one_authority(self):
        """Provider, account and region are labels. Two backends can wear the same ones and
        know nothing about each other's keys, so identity names the backend as well."""
        a, b = FakeProcessor(), FakeProcessor()
        self.assertEqual(a.authority.fields()[:3], b.authority.fields()[:3],
                         "the labels are deliberately identical")
        self.assertNotEqual(a.authority.fields(), b.authority.fields(),
                            "two independent backends are two authorities")
        # Passing A's own scope back in does NOT hand over A's identity. Every construction
        # is a new backend, so the only way to share one is to share the object.
        impostor = FakeProcessor(a.authority)
        self.assertNotEqual(impostor.authority.fields(), a.authority.fields(),
                            "a backend identity cannot be supplied or reused")
        self.assertEqual(impostor.authority.fields()[:3], a.authority.fields()[:3],
                         "the labels it was given are still honoured")
        self.assertIs(SafeFlow(Store(Ledger()), a).authority, a.authority,
                      "workers share one backend by sharing the object")

    def test_a_book_serves_one_authority_and_says_so_before_anything_is_sent(self):
        """Counterparty ids are unique only inside the counterparty that minted them. Both
        backends call their first charge `ch_1`, so a book holding two of them has two
        things called ch_1 and every index keyed on one is ambiguous. The book states the
        narrower thing it is, and refuses the second authority before any money moves."""
        store = Store(Ledger())
        a, b = FakeProcessor(), FakeProcessor(OTHER_ACCOUNT)
        SafeFlow(store, a).pay_invoice("INV-A", 12_500, CURRENCY)
        with self.assertRaises(ForeignAuthority) as refused:
            SafeFlow(store, b).pay_invoice("INV-B", 40_000, CURRENCY)
        self.assertIn("one book, one authority", str(refused.exception))
        self.assertEqual(b.calls, [], "refused before any lookup and before any send")
        self.assertEqual(b.charge_count("INV-B"), 0, "nothing was charged there")
        self.assertEqual(store.authority(), a.authority.fields())
        self.assertEqual(store.ledger.balance(RECEIVABLE, CURRENCY), 12_500,
                         "the book holds exactly what its own authority captured")

    def test_a_refused_call_never_claims_the_book(self):
        """The book is claimed with the intent it accepts, not by the call that asked. One
        misconfigured worker touching a book once must not own it: claiming on the way in
        would lock the rightful worker out for ever, with no way to clear it."""
        for name, invoice, scope in (
                ("an unusable invoice id", ["INV-B"], OTHER_ACCOUNT),
                ("a blank invoice id", "   ", OTHER_ACCOUNT),
                ("an authority that names nothing", "INV-B",
                 AuthorityScope("", "", "", "be0"))):
            with self.subTest(name):
                store = Store(Ledger())
                with self.assertRaises(InvalidIdentifier):
                    store.open_intent("pay:INV-B:1", invoice_id=invoice,
                                      amount_minor=AMOUNT_MINOR, currency=CURRENCY,
                                      authority=scope)
                self.assertEqual(store.authority(), (), "a refused call claims nothing")
                self.assertEqual(store.intents(), ())
                rightful = FakeProcessor()
                SafeFlow(store, rightful).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY)
                self.assertEqual(store.authority(), rightful.authority.fields())
                self.assertEqual(rightful.charged_total_minor(INVOICE), AMOUNT_MINOR,
                                 "the rightful worker still owns its own book")

    def test_an_interrupted_capture_cannot_be_finished_through_another_backend(self):
        """A accepted the money and the local write never happened. A second backend wearing
        A's labels knows nothing about that key: it must be refused before it is asked."""
        a = FakeProcessor()
        store = Store(Ledger())
        with self.assertRaises(InjectedFailure):
            pay(store, a, fail=failure_hook())
        self.assertEqual(a.charged_total_minor(INVOICE), AMOUNT_MINOR)
        self.assertEqual(store.ledger.entries(), (), "nothing local completed")

        # Built from A's OWN scope: the labels carry over, the identity cannot.
        b = FakeProcessor(a.authority)
        self.assertNotEqual(b.authority.fields(), a.authority.fields())
        with self.assertRaises(AuthorityMismatch):
            SafeFlow(store, b).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY)
        self.assertEqual(b.calls, [], "refused before any lookup and before any send")
        self.assertEqual(b.charge_count(INVOICE), 0, "the other backend recorded nothing")

        SafeFlow(store, a).recover()
        self.assertEqual(a.charge_count(INVOICE), 1, "still one charge at the real backend")
        self.assertEqual(len(store.ledger.entries()), 1, "and the effect is recorded once")


    def test_a_captured_intent_cannot_be_retried_through_another_account(self):
        processor, store = paid(settle=False)
        elsewhere = FakeProcessor(OTHER_ACCOUNT)
        with self.assertRaises(AuthorityMismatch):
            SafeFlow(store, elsewhere).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY)
        self.assertEqual(elsewhere.calls, [], "nothing was asked and nothing was sent")
        self.assertEqual(processor.charge_count(INVOICE), 1)
        self.assertEqual(len(store.ledger.entries()), 1)

    def test_a_pending_intent_cannot_be_retried_through_another_region(self):
        processor, store = FakeProcessor(), Store(Ledger())
        with self.assertRaises(InjectedFailure):
            pay(store, processor, fail=failure_hook())
        elsewhere = FakeProcessor(OTHER_REGION)
        with self.assertRaises(AuthorityMismatch):
            SafeFlow(store, elsewhere).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY)
        self.assertEqual(elsewhere.calls, [])
        self.assertEqual(store.get_intent(KEY).state, "PENDING")
        self.assertEqual(store.ledger.entries(), ())

    def test_a_settlement_for_an_intent_pinned_elsewhere_posts_nothing(self):
        """One shared store, workers at two authorities: the notification reaches the wrong
        one. The processor knows the charge and the settlement is genuine, so nothing else
        in the chain refuses it - only the intent's pinned scope does."""
        processor, store = FakeProcessor(), Store(Ledger())
        charge = processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                                processor.authority.fields()))
        processor.settle(charge["charge_id"], BATCH)
        store.open_intent(KEY, invoice_id=INVOICE, amount_minor=AMOUNT_MINOR,
                          currency=CURRENCY, authority=OTHER_ACCOUNT)
        store.commit_once(
            f"capture:{charge['charge_id']}", (charge["charge_id"],),
            kind="charge_captured", reference=charge["charge_id"],
            postings=[Posting(RECEIVABLE, CURRENCY, AMOUNT_MINOR),
                      Posting(REVENUE, CURRENCY, -AMOUNT_MINOR)],
            intent_updates={KEY: {"state": "CAPTURED",
                                  "charge_id": charge["charge_id"]}})
        self.assertEqual(SafeFlow(store, processor).handle_webhook(load_webhooks()[0]),
                         "WRONG_AUTHORITY")
        self.assertEqual(len(store.ledger.entries()), 1, "only the capture is there")
        self.assertEqual(store.ledger.balance(CASH, CURRENCY), 0)

    def test_the_intent_persists_the_scope_it_was_pinned_to(self):
        processor, store = paid(settle=False)
        self.assertEqual(store.get_intent(KEY).authority, processor.authority.fields())
        self.assertEqual(store.get_intent(KEY).binding,
                         (INVOICE, AMOUNT_MINOR, CURRENCY) + processor.authority.fields())

    def test_a_worker_bound_elsewhere_recovers_nothing(self):
        processor, store = FakeProcessor(), Store(Ledger())
        with self.assertRaises(InjectedFailure):
            pay(store, processor, fail=failure_hook())
        elsewhere = FakeProcessor(OTHER_ACCOUNT)
        self.assertEqual(SafeFlow(store, elsewhere).recover(), ())
        self.assertEqual(elsewhere.calls, [], "it did not even ask about someone else's key")
        self.assertEqual(store.ledger.entries(), ())

    def test_the_processor_refuses_a_request_addressed_to_another_scope(self):
        processor = FakeProcessor()
        with self.assertRaises(WrongAuthority):
            processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                           OTHER_ACCOUNT.fields()))
        self.assertEqual(processor.charge_count(INVOICE), 0)

    def test_the_authority_of_a_bound_worker_cannot_be_swapped_underneath_it(self):
        processor = FakeProcessor()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            processor.authority.account = "acct_other"
        with self.assertRaises(AttributeError):
            processor.authority = OTHER_ACCOUNT


class OneKeyIsOneEconomicDecision(unittest.TestCase):
    def test_a_replay_that_changes_the_amount_is_refused_in_the_captured_path(self):
        processor, store = paid(settle=False)
        with self.assertRaises(IntentMismatch):
            pay(store, processor, amount=AMOUNT_MINOR + 1)
        self.assertEqual(processor.sends, [KEY], "it never reached the wire a second time")
        self.assertEqual(store.get_intent(KEY).amount_minor, AMOUNT_MINOR)

    def test_a_replay_that_changes_the_amount_is_refused_in_the_pending_path(self):
        processor, store = FakeProcessor(), Store(Ledger())
        with self.assertRaises(InjectedFailure):
            pay(store, processor, fail=failure_hook())
        self.assertEqual(store.get_intent(KEY).state, "PENDING")
        with self.assertRaises(IntentMismatch):
            pay(store, processor, amount=AMOUNT_MINOR + 1)
        self.assertEqual(processor.sends, [KEY])

    def test_a_replay_that_changes_a_bound_field_is_refused(self):
        home = FakeProcessor().authority
        store = Store(Ledger())
        store.open_intent(KEY, invoice_id=INVOICE, amount_minor=AMOUNT_MINOR,
                          currency=CURRENCY, authority=home)
        for changed in ({"currency": "EUR"}, {"invoice_id": "INV-9"},
                        {"amount_minor": 1}, {"authority": OTHER_PROVIDER},
                        {"authority": OTHER_ACCOUNT}, {"authority": OTHER_REGION},
                        {"authority": FakeProcessor().authority}):
            with self.subTest(**{k: str(v) for k, v in changed.items()}):
                fields = {"invoice_id": INVOICE, "amount_minor": AMOUNT_MINOR,
                          "currency": CURRENCY, "authority": home, **changed}
                # A changed bound field is IntentMismatch; a changed authority is caught
                # one step earlier by the book's own single-authority rule.
                with self.assertRaises((IntentMismatch, ForeignAuthority)):
                    store.open_intent(KEY, **fields)

    def test_every_retry_is_built_from_the_stored_intent_and_not_from_the_caller(self):
        processor, store = paid(settle=False)
        request = SafeFlow(store, processor).stored_request(KEY)
        self.assertEqual(request, ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                                processor.authority.fields()))


class BothShapesOfAnAmbiguousTimeout(unittest.TestCase):
    def test_a_timeout_after_the_processor_accepted_is_resolved_by_the_lookup(self):
        processor = FakeProcessor(ambiguous_keys={KEY})
        store = Store(Ledger())
        pay(store, processor)
        self.assertEqual(processor.calls, [("charge", KEY), ("lookup", KEY)],
                         "one send, the answer was lost, and it asked rather than resent")
        self.assertEqual(processor.charge_count(INVOICE), 1)
        self.assertEqual(store.get_intent(KEY).state, "CAPTURED")
        self.assertEqual(store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)

    def test_a_timeout_before_the_processor_accepted_is_replayed_under_the_same_key(self):
        processor = FakeProcessor(lossy_keys={KEY})
        store = Store(Ledger())
        pay(store, processor)
        self.assertEqual(processor.calls,
                         [("charge", KEY), ("lookup", KEY), ("charge", KEY)],
                         "the replay came after the question, never before it")
        self.assertEqual(processor.sends, [KEY, KEY],
                         "the replay is the same key, never a fresh one")
        self.assertEqual(processor.charge_count(INVOICE), 1)
        self.assertEqual(processor.get_charge("ch_1")["economic_fields"],
                         [INVOICE, AMOUNT_MINOR, CURRENCY])
        self.assertEqual(store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)

    def test_a_direct_retry_of_a_pending_intent_asks_before_it_sends_anything(self):
        processor, store = FakeProcessor(), Store(Ledger())
        with self.assertRaises(InjectedFailure):
            pay(store, processor, fail=failure_hook())
        self.assertEqual(processor.calls, [("charge", KEY)])
        intent = pay(store, processor)      # a plain retry of the same call, not recovery
        self.assertEqual(processor.calls, [("charge", KEY), ("lookup", KEY)],
                         "the pinned authority was asked before anything could be resent")
        self.assertEqual(processor.sends, [KEY], "and nothing went out a second time")
        self.assertEqual(processor.charge_count(INVOICE), 1)
        self.assertEqual(intent.state, "CAPTURED")
        self.assertEqual(len(store.ledger.entries()), 1)


class AnAnswerIsCheckedBeforeItIsBelieved(unittest.TestCase):
    def test_a_lookup_answering_with_another_operations_capture_posts_nothing(self):
        processor = MisroutingProcessor(lossy_keys={KEY})
        store = Store(Ledger())
        processor.other = processor.charge(ChargeRequest("other:key", "INV-2", 500,
                                                         CURRENCY,
                                                         processor.authority.fields()))
        with self.assertRaises(ResponseMismatch):
            pay(store, processor)
        self.assertEqual(store.ledger.entries(), (), "a misrouted answer posts nothing")
        self.assertEqual(store.get_intent(KEY).state, "PENDING")
        self.assertEqual(processor.charge_count(INVOICE), 0)

    def test_a_capture_answer_missing_the_persisted_key_posts_nothing(self):
        processor, store = FakeProcessor(), Store(Ledger())
        intent = store.open_intent(KEY, invoice_id=INVOICE, amount_minor=AMOUNT_MINOR,
                                   currency=CURRENCY, authority=processor.authority)
        charge = dict(processor.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                                     processor.authority.fields())))
        for broken in ({"idempotency_key": "another:key"}, {"amount_minor": 1},
                       {"currency": "EUR"}, {"invoice_id": "INV-2"},
                       {"authority": OTHER_ACCOUNT.fields()}, {"charge_id": ""}):
            with self.subTest(**{k: str(v) for k, v in broken.items()}):
                with self.assertRaises(ResponseMismatch):
                    SafeFlow(store, processor)._capture(intent, dict(charge, **broken))
                self.assertEqual(store.ledger.entries(), ())

    def test_a_settlement_naming_another_charge_posts_nothing(self):
        processor, store = paid(MissettlingProcessor())
        flow = SafeFlow(store, processor)
        self.assertEqual(flow.handle_webhook(load_webhooks()[0]), "MISROUTED_SETTLEMENT")
        self.assertEqual(len(store.ledger.entries()), 1, "only the capture is there")
        self.assertEqual(store.ledger.balance(CASH, CURRENCY), 0)

    def test_an_answer_about_some_other_charge_posts_nothing(self):
        processor, store = paid(LyingLookupProcessor())
        self.assertEqual(SafeFlow(store, processor).handle_webhook(load_webhooks()[0]),
                         "MISROUTED_CHARGE")
        self.assertEqual(len(store.ledger.entries()), 1, "only the capture is there")
        self.assertEqual(store.ledger.balance(CASH, CURRENCY), 0)

    def test_a_settlement_whose_amounts_do_not_add_up_posts_nothing(self):
        processor, store = paid()
        flow = SafeFlow(store, processor)
        for broken in ({"net_minor": 1}, {"fee_minor": 12.5}, {"gross_minor": 0},
                       {"settlement_id": ""}, {"batch_id": ""},
                       {"authority": OTHER_REGION.fields()}):
            with self.subTest(**{k: str(v) for k, v in broken.items()}):
                original = processor.settlement_for_charge
                processor.settlement_for_charge = (
                    lambda cid, b=broken, f=original: dict(f(cid), **b))
                try:
                    self.assertNotEqual(flow.handle_webhook(load_webhooks()[0]), "APPLIED")
                finally:
                    processor.settlement_for_charge = original
                self.assertEqual(len(store.ledger.entries()), 1)

    def test_no_processor_answer_shape_posts_or_escapes_into_the_caller(self):
        """The processor is trusted for its figures and never for the shape of its answers.

        A proxy that hands back a list, a body decoded into a string, a field that arrived
        as a number: each would otherwise reach `.get`, an index, a ledger reference, an
        effect id or a set. A capture refuses by raising `ResponseMismatch`, which is the
        caller's own operation to handle; a delivery refuses by answering, because a handler
        that raises stops the loop and every settlement queued behind the bad one goes
        unrecorded. Neither posts anything.
        """
        unnamed = (["ch_1"], {"a": 1}, 7, True, "", "   ", None)
        # (which answer is corrupted, the field or None for the whole answer, the value,
        #  the exact refusal that has to follow)
        table = []
        for value, on_capture, on_charge, on_settlement in (
                (None, ResponseMismatch, "UNKNOWN_CHARGE", "NOT_SETTLED_YET"),
                ([], ResponseMismatch, "MALFORMED_CHARGE", "UNIDENTIFIED_SETTLEMENT"),
                ("ch_1", ResponseMismatch, "MALFORMED_CHARGE", "UNIDENTIFIED_SETTLEMENT"),
                (7, ResponseMismatch, "MALFORMED_CHARGE", "UNIDENTIFIED_SETTLEMENT"),
                (True, ResponseMismatch, "MALFORMED_CHARGE", "UNIDENTIFIED_SETTLEMENT")):
            table += [("capture", None, value, on_capture),
                      ("charge", None, value, on_charge),
                      ("settlement", None, value, on_settlement)]
        for value in unnamed:
            table += [("capture", field, value, ResponseMismatch)
                      for field in ("charge_id", "idempotency_key", "invoice_id")]
            table.append(("charge", "charge_id", value, "MALFORMED_CHARGE"))
            table += [("settlement", field, value, "UNIDENTIFIED_SETTLEMENT")
                      for field in ("settlement_id", "batch_id")]

        def corrupt(sound, field, value):
            return value if field is None else dict(sound, **{field: value})

        for answer, field, value, expected in table:
            with self.subTest(answer=answer, field=field, value=repr(value)):
                if answer == "capture":
                    processor, store = FakeProcessor(), Store(Ledger())
                    intent = store.open_intent(KEY, invoice_id=INVOICE,
                                               amount_minor=AMOUNT_MINOR,
                                               currency=CURRENCY,
                                               authority=processor.authority)
                    sound = processor.charge(
                        ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                      processor.authority.fields()))
                    with self.assertRaises(expected):
                        SafeFlow(store, processor)._capture(
                            intent, corrupt(sound, field, value))
                    self.assertEqual(store.ledger.entries(), (), "nothing was posted")
                    self.assertEqual(store.get_intent(KEY).state, "PENDING")
                    continue
                processor, store = paid()
                asked = "get_charge" if answer == "charge" else "settlement_for_charge"
                sound = getattr(processor, asked)("ch_1")
                setattr(processor, asked,
                        lambda _id, s=sound, f=field, v=value: corrupt(s, f, v))
                self.assertEqual(
                    SafeFlow(store, processor).handle_webhook(load_webhooks()[0]),
                    expected)
                self.assertEqual(len(store.ledger.entries()), 1,
                                 "only the capture is there; nothing else was posted")


class AnInjectedFailureBetweenTheEffectAndTheWrite(unittest.TestCase):
    def setUp(self):
        self.processor = FakeProcessor(ambiguous_keys={KEY})
        self.store = Store(Ledger())

    def test_an_injected_failure_after_the_charge_leaves_a_pending_intent_and_no_postings(self):
        with self.assertRaises(InjectedFailure):
            pay(self.store, self.processor, fail=failure_hook())
        self.assertEqual(self.processor.charge_count(INVOICE), 1)
        self.assertEqual(self.store.get_intent(KEY).state, "PENDING")
        self.assertEqual(self.store.ledger.entries(), ())

    def test_a_new_worker_over_the_same_store_completes_it_with_no_second_charge(self):
        with self.assertRaises(InjectedFailure):
            pay(self.store, self.processor, fail=failure_hook())
        self.assertEqual(SafeFlow(self.store, self.processor).recover(), ("ch_1",))
        self.assertEqual(self.processor.charge_count(INVOICE), 1)
        self.assertEqual(self.store.ledger.balance(REVENUE, CURRENCY), -AMOUNT_MINOR)
        self.assertEqual(len(self.store.ledger.entries()), 1)

    def test_recovery_and_a_re_run_are_both_replay_safe(self):
        with self.assertRaises(InjectedFailure):
            pay(self.store, self.processor, fail=failure_hook())
        for _ in range(3):
            SafeFlow(self.store, self.processor).recover()
            pay(self.store, self.processor)
        self.assertEqual(self.processor.charge_count(INVOICE), 1)
        self.assertEqual(len(self.store.ledger.entries()), 1)
        self.assertEqual(self.store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)

    def test_two_separate_workers_recovering_at_once_post_exactly_one_set(self):
        with self.assertRaises(InjectedFailure):
            pay(self.store, self.processor, fail=failure_hook())
        first = SafeFlow(self.store, self.processor)
        second = SafeFlow(self.store, self.processor)
        self.assertIsNot(first, second)
        result = race(lambda seam: first.recover(seam=seam),
                      lambda seam: second.recover(seam=seam))
        self.assertEqual(result.alive, [], "a worker was still running after the join")
        result.raise_errors()
        self.assertEqual(result.sorted_outcomes(), [("ch_1",), ()],
                         "both workers ran and exactly one of them applied the capture")
        self.assertEqual(result.seam_entries, 1,
                         "the shared lock held the second worker out of the window")
        self.assertEqual(len(self.store.ledger.entries()), 1)
        self.assertEqual(self.store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)

    def test_two_separate_workers_paying_one_intent_at_once_charge_once(self):
        processor, store = FakeProcessor(), Store(Ledger())
        first, second = SafeFlow(store, processor), SafeFlow(store, processor)
        self.assertIsNot(first, second)
        result = race(
            lambda seam: first.pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY, seam=seam),
            lambda seam: second.pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY, seam=seam))
        self.assertEqual(result.alive, [], "a worker was still running after the join")
        result.raise_errors()
        self.assertEqual(processor.charge_count(INVOICE), 1,
                         "one economic decision, one charge at the processor")
        self.assertEqual([o.state for o in result.outcomes], ["CAPTURED", "CAPTURED"])
        self.assertEqual(result.seam_entries, 1,
                         "the shared lock held the second worker out of the window")
        self.assertEqual(len(store.ledger.entries()), 1)
        self.assertEqual(store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)


class TheEntryAndItsIdentityCannotDiverge(unittest.TestCase):
    """The one window that is left is the intent transition, and a retry closes it."""

    def setUp(self):
        self.processor, self.store = FakeProcessor(), Store(Ledger())

    def interrupt(self):
        def boom():
            raise InjectedFailure("power lost after the entry, before the intent moved")

        with self.assertRaises(InjectedFailure):
            pay(self.store, self.processor, after_entry=boom)
        self.assertEqual(len(self.store.ledger.entries()), 1, "the entry is visible")
        self.assertIsNotNone(self.store.effect(capture_effect(self.processor)),
                             "and it carries its own dedupe identity, in the same append")
        self.assertEqual(self.store.get_intent(KEY).state, "PENDING",
                         "while the intent transition never happened")

    def test_a_retry_finishes_the_missing_transition_and_posts_nothing_again(self):
        self.interrupt()
        intent = pay(self.store, self.processor)
        self.assertEqual((intent.state, intent.charge_id), ("CAPTURED", "ch_1"))
        self.assertEqual([e.effect_id for e in self.store.ledger.entries()],
                         [capture_effect(self.processor)], "one entry, one effect identity")
        self.assertEqual(self.processor.charge_count(INVOICE), 1)
        self.assertEqual(self.store.ledger.balance(RECEIVABLE, CURRENCY), AMOUNT_MINOR)

    def test_recovery_finishes_the_missing_transition_as_well(self):
        self.interrupt()
        self.assertEqual(SafeFlow(self.store, self.processor).recover(), (),
                         "nothing was applied, because the entry was already there")
        self.assertEqual(self.store.get_intent(KEY).state, "CAPTURED")
        self.assertEqual(len(self.store.ledger.entries()), 1)


class OneSettlementIdentityCreditsOnce(unittest.TestCase):
    def setUp(self):
        self.processor, self.store = paid()
        self.flow = SafeFlow(self.store, self.processor)
        self.first, self.redelivery = load_webhooks()

    def test_the_redelivery_of_one_event_is_recognised_and_credits_nothing(self):
        self.assertEqual(self.flow.handle_webhook(self.first), "APPLIED")
        self.assertEqual(self.flow.handle_webhook(self.redelivery), "DUPLICATE")
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12125)
        self.assertEqual(self.store.ledger.balance(RECEIVABLE, CURRENCY), 0)

    def test_the_settlement_charge_and_batch_are_fields_and_not_memo_text(self):
        self.flow.handle_webhook(self.first)
        entry = self.store.ledger.entries()[-1]
        self.assertEqual(entry.attribution,
                         Attribution("st_1", "ch_1", BATCH, CURRENCY))
        self.assertNotIn("st_1", entry.memo)
        self.assertNotIn("ch_1", entry.memo)
        self.assertNotIn(BATCH, entry.memo)

    def test_two_separate_workers_delivering_at_once_credit_exactly_once(self):
        first = SafeFlow(self.store, self.processor)
        second = SafeFlow(self.store, self.processor)
        self.assertIsNot(first, second)
        result = race(lambda seam: first.handle_webhook(self.first, seam=seam),
                      lambda seam: second.handle_webhook(self.redelivery, seam=seam))
        self.assertEqual(result.alive, [], "a worker was still running after the join")
        result.raise_errors()
        self.assertEqual(result.sorted_outcomes(), ["APPLIED", "DUPLICATE"],
                         "both workers entered the handler and one of them was refused")
        self.assertEqual(result.seam_entries, 1,
                         "the shared lock held the second worker out of the window")
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12125)
        self.assertEqual(self.store.ledger.balance(FEES, CURRENCY), 375)
        self.assertEqual(len(self.store.ledger.entries()), 2)


class TheProcessorIsTheAuthorityAndTheEnvelopeIsNot(unittest.TestCase):
    def setUp(self):
        self.processor, self.store = paid()
        self.flow = SafeFlow(self.store, self.processor)
        self.first = load_webhooks()[0]

    def deliver(self, **data):
        return self.flow.handle_webhook(
            dict(self.first, data=dict(self.first["data"], **data)))

    def test_a_charge_and_settlement_agreeing_on_the_wrong_scope_post_nothing(self):
        """Coherence is not authority. A second backend answers about its own ch_1 with a
        matching settlement, and every figure agrees with itself; the only thing that does
        not agree is the scope the intent was pinned to."""
        elsewhere = FakeProcessor(OTHER_REGION)
        charge = elsewhere.charge(ChargeRequest(KEY, INVOICE, AMOUNT_MINOR, CURRENCY,
                                                elsewhere.authority.fields()))
        settled = elsewhere.settle(charge["charge_id"], BATCH)
        self.assertEqual(charge["charge_id"], self.store.get_intent(KEY).charge_id,
                         "the two backends mint the same charge id, which is the trap")
        self.assertEqual(settled["authority"], charge["authority"], "coherently wrong")
        before = len(self.store.ledger.entries())
        answer = SafeFlow(self.store, elsewhere).handle_webhook(
            dict(self.first, data=dict(self.first["data"],
                                       settlement_id=settled["settlement_id"])))
        self.assertEqual(answer, "WRONG_AUTHORITY")
        self.assertEqual(len(self.store.ledger.entries()), before,
                         "no settlement posting from a scope this intent is not pinned to")

    def test_no_delivery_shape_makes_the_handler_raise(self):
        """The envelope comes from outside. A handler that raises stops the delivery loop,
        and every settlement queued behind the bad one goes unrecorded."""
        shapes = {
            "None": None, "a string": "ch_1", "a list": [], "a number": 7, "a bool": True,
            "no data": {"type": "payment.settled"},
            "data is a list": {"type": "payment.settled", "data": []},
            "data is a string": {"type": "payment.settled", "data": "ch_1"},
            "charge_id is a list": {"type": "payment.settled", "data": {"charge_id": ["ch_1"]}},
            "charge_id is a set": {"type": "payment.settled", "data": {"charge_id": {"ch_1"}}},
            "charge_id is a dict": {"type": "payment.settled", "data": {"charge_id": {"a": 1}}},
            "charge_id is a number": {"type": "payment.settled", "data": {"charge_id": 1}},
            "charge_id is blank": {"type": "payment.settled", "data": {"charge_id": "   "}},
        }
        before = len(self.store.ledger.entries())
        for name, envelope in shapes.items():
            with self.subTest(name):
                answer = self.flow.handle_webhook(envelope)
                self.assertIn(answer, ("MALFORMED_EVENT", "UNEXPECTED_TYPE",
                                       "UNIDENTIFIED_CHARGE"))
        self.assertEqual(len(self.store.ledger.entries()), before, "and nothing posted")
        self.assertEqual(self.deliver(), "APPLIED",
                         "a sound delivery after the bad ones still works")

    def test_a_settlement_whose_fee_takes_the_whole_gross_still_applies(self):
        """Gross must be positive; fee and net only non-negative. A one-minor-unit charge
        whose fee is one minor unit settles to a net of zero, and zero is a true answer."""
        processor = FakeProcessor()
        store = Store(Ledger())
        SafeFlow(store, processor).pay_invoice(INVOICE, 1, CURRENCY)
        settled = processor.settle("ch_1", BATCH)
        self.assertEqual((settled["gross_minor"], settled["fee_minor"],
                          settled["net_minor"]), (1, 1, 0))
        answer = SafeFlow(store, processor).handle_webhook(
            dict(self.first, data=dict(self.first["data"], amount_minor=1, fee_minor=1,
                                       net_minor=0)))
        self.assertEqual(answer, "APPLIED")
        self.assertEqual(store.ledger.balance(RECEIVABLE, CURRENCY), 0)
        self.assertEqual(store.ledger.balance(CASH, CURRENCY), 0)
        self.assertEqual(store.ledger.balance(FEES, CURRENCY), 1)

    def test_no_unidentified_value_ever_becomes_an_identifier(self):
        """A list, a dict, a number, a bool and a whitespace-only string name nothing, so
        none of them may become a ledger reference, an effect id, a dict key or a set
        member. `""` is refused everywhere a name is required and allowed only in an
        attribution field, where empty means "not applicable" rather than "named nothing"."""
        unnamed = ([1], {"a": 1}, 7, True, "   ", None, [], {}, 0, False)
        required = unnamed + ("",)
        store = Store(Ledger())
        before = len(self.store.ledger.entries())
        for value in required:
            with self.subTest(position="a name is required", value=repr(value)):
                with self.assertRaises((InvalidIdentifier, InvalidAmount, InvalidCurrency)):
                    self.processor.charge(ChargeRequest(value, INVOICE, AMOUNT_MINOR,
                                                        CURRENCY,
                                                        self.processor.authority.fields()))
                with self.assertRaises((InvalidIdentifier, InvalidAmount, InvalidCurrency)):
                    store.open_intent(KEY, invoice_id=value, amount_minor=AMOUNT_MINOR,
                                      currency=CURRENCY, authority=self.processor.authority)
                with self.assertRaises(InvalidIdentifier):
                    store.ledger.commit_once(
                        "e", (), kind="settlement", reference=value,
                        postings=[Posting(CASH, CURRENCY, 1),
                                  Posting(REVENUE, CURRENCY, -1)])
        for value in unnamed:
            with self.subTest(position="attribution field", value=repr(value)):
                with self.assertRaises(InvalidIdentifier):
                    store.ledger.commit_once(
                        "e", (), kind="settlement", reference="st_x",
                        attribution=Attribution("st_x", value, "SB-0001", CURRENCY),
                        postings=[Posting(CASH, CURRENCY, 1),
                                  Posting(REVENUE, CURRENCY, -1)])
        self.assertEqual(store.ledger.entries(), (), "nothing unidentified was written")
        self.assertEqual(len(self.store.ledger.entries()), before)

    def test_a_settlement_coherent_with_itself_about_the_wrong_sum_posts_nothing(self):
        """25,000 / 750 / 24,250 adds up perfectly and is not this 12,500 charge."""
        class Overstating(FakeProcessor):
            def settlement_for_charge(self, charge_id):
                settled = super().settlement_for_charge(charge_id)
                if settled is not None:
                    settled.update(gross_minor=25_000, fee_minor=750, net_minor=24_250)
                return settled

        processor = Overstating()
        store = Store(Ledger())
        SafeFlow(store, processor).pay_invoice(INVOICE, AMOUNT_MINOR, CURRENCY)
        processor.settle("ch_1", BATCH)
        before = len(store.ledger.entries())
        answer = SafeFlow(store, processor).handle_webhook(load_webhooks()[0])
        self.assertEqual(answer, "SETTLEMENT_AMOUNT")
        self.assertEqual(len(store.ledger.entries()), before, "no settlement posting")
        self.assertEqual(store.ledger.balance(CASH, CURRENCY), 0)

    def test_a_tampered_payload_posts_the_processors_numbers_and_not_its_own(self):
        self.assertEqual(self.deliver(amount_minor=999_999, fee_minor=0,
                                      net_minor=999_999), "APPLIED")
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12125)
        self.assertEqual(self.store.ledger.balance(FEES, CURRENCY), 375)
        self.assertEqual(self.store.ledger.balance(RECEIVABLE, CURRENCY), 0)

    def test_an_event_of_an_unexpected_type_never_posts(self):
        envelope = dict(self.first, type="payment.refunded")
        self.assertEqual(self.flow.handle_webhook(envelope), "UNEXPECTED_TYPE")
        self.assertEqual(len(self.store.ledger.entries()), 1)

    def test_an_event_that_disagrees_about_the_currency_never_posts(self):
        self.assertEqual(self.deliver(currency="EUR"), "CURRENCY_MISMATCH")
        self.assertEqual(len(self.store.ledger.entries()), 1)

    def test_an_event_for_a_charge_the_processor_does_not_know_never_posts(self):
        self.assertEqual(self.deliver(charge_id="ch_99"), "UNKNOWN_CHARGE")
        self.assertEqual(len(self.store.ledger.entries()), 1)

    def test_an_event_for_a_charge_this_store_never_captured_is_refused_not_queued(self):
        other = self.processor.charge(ChargeRequest("other:key", "INV-2", 500, CURRENCY,
                                                    self.processor.authority.fields()))
        self.processor.settle(other["charge_id"], BATCH)
        self.assertEqual(self.deliver(charge_id=other["charge_id"]), "NOT_CAPTURED_HERE")
        self.assertEqual(len(self.store.ledger.entries()), 1)

    def test_an_event_that_arrives_before_the_settlement_is_refused_not_queued(self):
        processor, store = paid(settle=False)
        self.assertEqual(SafeFlow(store, processor).handle_webhook(self.first),
                         "NOT_SETTLED_YET")
        self.assertEqual(len(store.ledger.entries()), 1)


class NothingIsMarkedSeenBeforeItsEntryExists(unittest.TestCase):
    def setUp(self):
        self.processor, self.store = paid()
        self.first = load_webhooks()[0]

    def test_a_fault_before_the_append_leaves_the_event_retryable(self):
        def boom():
            raise InjectedFailure("power lost inside the commit")

        with self.assertRaises(InjectedFailure):
            SafeFlow(self.store, self.processor).handle_webhook(self.first, fault=boom)
        self.assertIsNone(self.store.effect(settlement_effect(self.processor)),
                          "no entry, and so no identity claiming one exists")
        self.assertEqual(len(self.store.ledger.entries()), 1)
        self.assertEqual(SafeFlow(self.store, self.processor).handle_webhook(self.first),
                         "APPLIED")
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12125)

    def test_a_refused_posting_set_leaves_no_identity_behind(self):
        with self.assertRaises(Unbalanced):
            self.store.commit_once("effect:x", ("x",), kind="settlement", reference="st_x",
                                   postings=[Posting(CASH, CURRENCY, 1)])
        self.assertIsNone(self.store.effect("effect:x"))
        applied = self.store.commit_once("effect:x", ("x",), kind="settlement",
                                         reference="st_x",
                                         postings=[Posting(CASH, CURRENCY, 1),
                                                   Posting(REVENUE, CURRENCY, -1)])
        self.assertTrue(applied.applied)

    def test_one_effect_id_carrying_different_economic_fields_is_refused(self):
        postings = [Posting(CASH, CURRENCY, 1), Posting(REVENUE, CURRENCY, -1)]
        self.store.commit_once("effect:y", ("one",), kind="settlement", reference="st_y",
                               postings=postings)
        repeat = self.store.commit_once("effect:y", ("one",), kind="settlement",
                                        reference="st_y", postings=postings)
        self.assertFalse(repeat.applied, "the existing result came back and nothing moved")
        with self.assertRaises(EffectConflict):
            self.store.commit_once("effect:y", ("two",), kind="settlement",
                                   reference="st_y", postings=postings)

    def test_an_effect_whose_intent_update_names_an_unknown_key_writes_nothing(self):
        before = self.store.ledger.entries()
        with self.assertRaises(KeyError):
            self.store.commit_once(
                "effect:w", ("w",), kind="settlement", reference="st_w",
                postings=[Posting(CASH, CURRENCY, 1), Posting(REVENUE, CURRENCY, -1)],
                intent_updates={"a key this store never opened": {"state": "CAPTURED"}})
        self.assertIsNone(self.store.effect("effect:w"), "it refused before it wrote")
        self.assertEqual(self.store.ledger.entries(), before)


class ACorrectionClosesOneRealOpenBreak(unittest.TestCase):
    def setUp(self):
        self.processor, self.store, self.breaks = run_safe()
        self.flow = SafeFlow(self.store, self.processor)
        self.flow.breaks = self.breaks
        self.brk = self.breaks[0]
        self.before = self.store.ledger.entries()

    def test_reconciliation_records_the_break_it_found(self):
        record = self.store.break_record(self.brk.break_id)
        self.assertIsNotNone(record, "a correction has something real to close")
        self.assertEqual(record.state, "OPEN")
        self.assertEqual(self.store.open_breaks(), (self.brk,))

    def test_a_break_closes_only_by_an_approved_correction_that_adds_an_entry(self):
        with self.assertRaises(ValueError):
            self.flow.apply_correction(self.brk, approved_by="")
        self.assertEqual(self.store.ledger.entries(), self.before)
        applied = self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        self.assertTrue(applied.applied)
        self.assertEqual(self.store.ledger.entries()[:len(self.before)], self.before)
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12100)
        self.assertEqual(self.store.ledger.trial_balance(), {"USD": 0})
        self.assertEqual(self.flow.breaks, ())
        self.assertEqual(self.store.open_breaks(), ())

    def test_the_correction_entry_links_to_the_break_and_to_the_entry_it_corrects(self):
        original = self.store.ledger.entry_for_settlement("st_1")
        self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        entry = self.store.ledger.entries()[-1]
        self.assertEqual(entry.kind, "correction")
        self.assertEqual(entry.break_id, self.brk.break_id)
        self.assertEqual(entry.corrects, original.entry_id)
        self.assertEqual(entry.attribution, self.brk.attribution)
        self.assertEqual(self.store.break_record(self.brk.break_id).entry_id,
                         entry.entry_id)

    def test_the_correction_effect_is_keyed_by_the_break_and_not_the_settlement(self):
        applied = self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        self.assertEqual(applied.effect_id, f"correction:{self.brk.break_id}")
        self.assertNotIn("st_1", applied.effect_id.split(":")[1])

    def test_replaying_an_approved_correction_moves_nothing_a_second_time(self):
        first = self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        after = self.store.ledger.entries()
        again = SafeFlow(self.store, self.processor).apply_correction(
            self.brk, approved_by="a named reviewer")
        self.assertFalse(again.applied)
        self.assertEqual(again.entry_id, first.entry_id)
        self.assertEqual(self.store.ledger.entries(), after)

    def test_a_duplicate_entry_break_is_not_closed_by_a_correction(self):
        """Two entries claim the identity and nothing here knows which was the mistake.
        Netting the difference would leave both in the book and call the matter settled."""
        store = self.store
        settled = store.ledger.entry_for_settlement("st_1")
        store.commit_once(
            "settlement:st_1:again", ("st_1", "again"), kind="settlement",
            reference="st_1",
            attribution=settled.attribution,
            postings=[Posting(CASH, CURRENCY, 12125), Posting(FEES, CURRENCY, 375),
                      Posting(RECEIVABLE, CURRENCY, -12500)])
        breaks = SafeFlow(store, self.processor).run_reconciliation(
            load_settlement_report())
        duplicate = next(b for b in breaks if b.kind == "duplicate_entry")
        before = len(store.ledger.entries())
        with self.assertRaises(ValueError) as raised:
            SafeFlow(store, self.processor).apply_correction(duplicate, "a named reviewer")
        self.assertIn("explicit reversal", str(raised.exception))
        self.assertIn("exact duplicate entry", str(raised.exception))
        self.assertEqual(len(store.ledger.entries()), before, "nothing was posted")
        self.assertEqual(store.break_record(duplicate.break_id).state, "OPEN")

    def test_a_fabricated_break_has_nothing_to_close(self):
        invented = dataclasses.replace(self.brk, deltas=(("gross", 0, 0, 0),
                                                         ("fee", 400, 0, -400),
                                                         ("net", 12100, 12500, 400)))
        self.assertNotEqual(invented.break_id, self.brk.break_id)
        with self.assertRaises(UnknownBreak):
            self.flow.apply_correction(invented, approved_by="a named reviewer")
        self.assertEqual(self.store.ledger.entries(), self.before)

    def test_a_break_that_is_already_closed_is_refused_a_second_correction(self):
        self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        after = self.store.ledger.entries()
        with self.assertRaises(BreakAlreadyClosed):
            SafeFlow(self.store, self.processor).apply_correction(
                self.brk, approved_by="a second reviewer")
        self.assertEqual(self.store.ledger.entries(), after)

    def test_a_later_genuinely_different_break_is_its_own_identity_and_can_be_closed(self):
        self.flow.apply_correction(self.brk, approved_by="a named reviewer")
        later = self.flow.run_reconciliation(altered_report(fee_minor=450, net_minor=12050))
        self.assertEqual([b.kind for b in later], ["amount_mismatch"])
        self.assertEqual(later[0].settlement_id, "st_1")
        self.assertNotEqual(later[0].break_id, self.brk.break_id,
                            "a different disagreement is a different break to approve")
        self.flow.apply_correction(later[0], approved_by="a named reviewer")
        self.assertEqual(self.store.ledger.balance(CASH, CURRENCY), 12050)
        self.assertEqual(self.store.ledger.balance(FEES, CURRENCY), 450)
        self.assertEqual(self.store.ledger.trial_balance(), {"USD": 0})
        self.assertEqual([e.kind for e in self.store.ledger.entries()],
                         ["charge_captured", "settlement", "correction", "correction"])


class TheWholeRun(unittest.TestCase):
    def test_the_balances_are_what_the_demo_prints_and_the_trial_balance_is_zero(self):
        processor, store, breaks = run_safe()
        self.assertEqual(processor.charge_count(INVOICE), 1)
        self.assertEqual(store.ledger.balances(),
                         {(CASH, "USD"): 12125, (RECEIVABLE, "USD"): 0,
                          (FEES, "USD"): 375, (REVENUE, "USD"): -12500})
        self.assertEqual(store.ledger.trial_balance(), {"USD": 0})
        self.assertEqual([b.kind for b in breaks], ["amount_mismatch"])
        self.assertEqual(exposure(breaks), {"USD": 25})

    def test_five_consecutive_runs_are_identical(self):
        results = [(run_safe()[1].ledger.balances(),
                    [b.break_id for b in run_safe()[2]]) for _ in range(5)]
        self.assertEqual(results, [results[0]] * 5)
