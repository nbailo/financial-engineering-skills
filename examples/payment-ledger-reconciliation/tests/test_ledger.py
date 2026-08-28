"""The ledger's own invariants: balanced, immutable, keyed by currency, carrying its own
dedupe identity and its structured attribution, and checked against an authority over the
full union of both sides' settlement identities."""
import dataclasses
import unittest

from fake_processor import load_settlement_report
from ledger import (Attribution, CASH, EffectConflict, FEES, Ledger, Posting, RECEIVABLE,
                    REVENUE, SUSPENSE, Unbalanced, exposure, reconcile)
from money import InvalidCurrency

REPORT_LINE = {"settlement_id": "st_1", "charge_id": "ch_1", "invoice_id": "INV-1001",
               "gross_minor": 12500, "fee_minor": 400, "net_minor": 12100}


def settled(ledger, net=12125, fee=375, gross=12500, reference="st_1", currency="USD",
            charge_id="ch_1", batch_id="SB-0001", effect=None):
    """One settlement entry, with the effect identity that caused it and the attribution it
    belongs to. Both are fields on the entry, never a second table and never memo prose."""
    entry, _applied = ledger.commit_once(
        effect or f"settlement:{reference}", (reference, gross, fee, net, currency),
        kind="settlement", reference=reference,
        attribution=Attribution(reference, charge_id, batch_id, currency),
        postings=[Posting(CASH, currency, net), Posting(FEES, currency, fee),
                  Posting(RECEIVABLE, currency, -gross)])
    return entry


def report(*lines, total=None, currency="USD", batch_id="SB-0001"):
    lines = list(lines) or [dict(REPORT_LINE)]
    if total is None:
        total = sum(line.get("net_minor") or 0 for line in lines
                    if isinstance(line, dict) and isinstance(line.get("net_minor"), int)
                    and not isinstance(line.get("net_minor"), bool))
    return {"batch_id": batch_id, "currency": currency, "lines": lines,
            "total_net_minor": total}


def kinds(breaks):
    return sorted(b.kind for b in breaks)


def only(breaks, kind):
    matching = [b for b in breaks if b.kind == kind]
    assert len(matching) == 1, f"expected one {kind}, got {kinds(breaks)}"
    return matching[0]


class APostingSetCommitsWholeOrNotAtAll(unittest.TestCase):
    def commit(self, *postings, effect="e"):
        return Ledger().commit_once(effect, (), kind="bad", reference="st_1",
                                    postings=list(postings))

    def test_a_set_that_does_not_sum_to_zero_is_refused_and_writes_nothing(self):
        ledger = Ledger()
        with self.assertRaises(Unbalanced):
            ledger.commit_once("e", (), kind="bad", reference="st_1",
                               postings=[Posting(CASH, "USD", 12125),
                                         Posting(REVENUE, "USD", -12000)])
        self.assertEqual(ledger.entries(), ())
        self.assertEqual(ledger.balance(CASH, "USD"), 0)
        self.assertIsNone(ledger.entry_for_effect("e"), "and no identity survived either")

    def test_an_empty_posting_set_is_refused(self):
        with self.assertRaises(Unbalanced):
            self.commit()

    def test_a_float_amount_is_refused_even_when_it_balances(self):
        with self.assertRaises(Unbalanced):
            self.commit(Posting(CASH, "USD", 121.25), Posting(REVENUE, "USD", -121.25))

    def test_each_currency_balances_on_its_own(self):
        with self.assertRaises(Unbalanced):
            self.commit(Posting(CASH, "USD", 100), Posting(REVENUE, "EUR", -100))

    def test_an_entry_with_no_effect_identity_cannot_be_committed(self):
        with self.assertRaises(ValueError):
            self.commit(Posting(CASH, "USD", 1), Posting(REVENUE, "USD", -1), effect="")


class OneAppendRecordsTheEntryAndItsIdentity(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()

    def test_every_entry_carries_the_effect_identity_that_caused_it(self):
        entry = settled(self.ledger)
        self.assertEqual(entry.effect_id, "settlement:st_1")
        self.assertIs(self.ledger.entry_for_effect("settlement:st_1"), entry)
        self.assertEqual([e.effect_id for e in self.ledger.entries()], [entry.effect_id])

    def test_the_same_identity_and_fingerprint_returns_the_entry_and_moves_nothing(self):
        first = settled(self.ledger)
        again, applied = self.ledger.commit_once(
            "settlement:st_1", ("st_1", 12500, 375, 12125, "USD"), kind="settlement",
            reference="st_1", attribution=Attribution("st_1", "ch_1", "SB-0001", "USD"),
            postings=[Posting(CASH, "USD", 12125),
                      Posting(FEES, "USD", 375),
                      Posting(RECEIVABLE, "USD", -12500)])
        self.assertFalse(applied)
        self.assertIs(again, first)
        self.assertEqual(len(self.ledger.entries()), 1)

    def test_the_same_identity_with_a_changed_fingerprint_is_refused(self):
        settled(self.ledger)
        before = self.ledger.entries()
        with self.assertRaises(EffectConflict):
            self.ledger.commit_once("settlement:st_1", ("st_1", 1, 1, 0, "USD"),
                                    kind="settlement", reference="st_1",
                                    postings=[Posting(CASH, "USD", 1),
                                              Posting(REVENUE, "USD", -1)])
        self.assertEqual(self.ledger.entries(), before)

    def test_the_dedupe_lookup_is_the_entries_themselves(self):
        """Every entry answers for its own identity, and the ledger keeps nowhere else to
        put one. The second assertion is the one that bites: a ledger that appended the
        entry and then registered the identity in its own table would satisfy every
        behavioural check above while still having a window between the two writes, so the
        shape of the ledger's state is asserted rather than assumed."""
        settled(self.ledger)
        settled(self.ledger, reference="st_2", gross=105, fee=5, net=100)
        self.assertIsNone(self.ledger.entry_for_effect("settlement:st_3"))
        for entry in self.ledger.entries():
            self.assertTrue(entry.effect_id, "an entry arrived carrying no identity")
            self.assertIs(self.ledger.entry_for_effect(entry.effect_id), entry)
        containers = [name for name, value in vars(self.ledger).items()
                      if isinstance(value, (dict, list, set, tuple))]
        self.assertEqual(containers, ["_entries"],
                         "a second container is a second place the identity can go missing")


class ABalanceNamesItsCurrency(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        settled(self.ledger, net=12125, fee=375, gross=12500, currency="USD")
        settled(self.ledger, net=9000, fee=300, gross=9300, reference="st_2",
                currency="EUR", charge_id="ch_2")

    def test_usd_and_eur_are_never_summed_into_one_balance(self):
        self.assertEqual(self.ledger.balance(CASH, "USD"), 12125)
        self.assertEqual(self.ledger.balance(CASH, "EUR"), 9000)
        self.assertEqual(self.ledger.balance(CASH, "GBP"), 0)

    def test_balances_are_keyed_by_account_and_currency(self):
        self.assertEqual(self.ledger.balances(),
                         {(CASH, "EUR"): 9000, (CASH, "USD"): 12125,
                          (FEES, "EUR"): 300, (FEES, "USD"): 375,
                          (RECEIVABLE, "EUR"): -9300, (RECEIVABLE, "USD"): -12500})

    def test_the_trial_balance_is_zero_per_currency_and_never_one_total(self):
        self.assertEqual(self.ledger.trial_balance(), {"EUR": 0, "USD": 0})

    def test_a_mixed_usd_and_eur_break_keeps_both_amounts_separate(self):
        breaks = reconcile(report(dict(REPORT_LINE, settlement_id="st_2",
                                       charge_id="ch_2")), self.ledger)
        mismatch = only(breaks, "currency_mismatch")
        self.assertEqual(mismatch.at_stake, (("EUR", 9000), ("USD", 12100)),
                         "two currencies are two exposures, never one integer")
        self.assertEqual(mismatch.amount("EUR"), 9000)
        self.assertEqual(mismatch.amount("USD"), 12100)
        self.assertIn("the ledger holds EUR net 9000", mismatch.detail)
        self.assertEqual(exposure(breaks), {"EUR": 9000, "USD": 12100 + 12125},
                         "the USD-only break adds to USD alone and to nothing else")

    def test_one_settlement_held_in_two_currencies_is_never_summed_into_one_figure(self):
        ledger = Ledger()
        ledger.commit_once("settlement:st_7", ("st_7",), kind="settlement", reference="st_7",
                           attribution=Attribution("st_7", "ch_7", "SB-0001", ""),
                           postings=[Posting(CASH, "USD", 700), Posting(FEES, "USD", 30),
                                     Posting(RECEIVABLE, "USD", -730),
                                     Posting(CASH, "EUR", 500), Posting(FEES, "EUR", 20),
                                     Posting(RECEIVABLE, "EUR", -520)])
        brk = only(reconcile(report(), ledger), "currency_mismatch")
        self.assertEqual(brk.settlement_id, "st_7")
        self.assertEqual(brk.at_stake, (("EUR", 500), ("USD", 700)),
                         "each currency's own net, stated separately, never added")
        self.assertIn("EUR net 500", brk.detail)
        self.assertIn("USD net 700", brk.detail)


class TheReportIsInputAndNotTruth(unittest.TestCase):
    """A detector that dies on the counterparty's file is a detector that is down."""

    def setUp(self):
        self.ledger = Ledger()
        settled(self.ledger, net=12100, fee=400)

    def test_an_amount_that_is_not_an_integer_is_a_break_and_is_never_compared(self):
        for name in ("gross_minor", "fee_minor", "net_minor"):
            for value in (12100.5, 12100.0, "12100", True, [12100]):
                with self.subTest(field=name, value=value):
                    breaks = reconcile(report(dict(REPORT_LINE, **{name: value}), total=0),
                                       self.ledger)
                    brk = only(breaks, "not_an_amount")
                    self.assertIn(name, brk.detail)
                    self.assertEqual(brk.at_stake, ())
                    self.assertEqual(kinds(breaks), ["not_an_amount"],
                                     "reported once, and not also as a ledger_only")
                    self.assertEqual(exposure(breaks), {},
                                     "an unreadable line puts no amount at stake")

    def test_a_line_that_is_not_a_record_at_all_is_a_break(self):
        brk = only(reconcile(report("st_1,12500,400,12100", total=0), self.ledger),
                   "malformed_line")
        self.assertEqual(brk.settlement_id, "unidentified_line_1")
        self.assertEqual(brk.at_stake, ())

    def test_a_malformed_line_beside_a_good_one_is_excluded_and_the_good_one_compared(self):
        breaks = reconcile(report(None, dict(REPORT_LINE), total=12100), self.ledger)
        self.assertEqual(kinds(breaks), ["malformed_line"],
                         "the readable line matched; the unreadable one was reported")

    def test_a_line_with_no_settlement_identity_is_a_break_and_is_never_compared(self):
        breaks = reconcile(report(dict(REPORT_LINE, settlement_id=""), total=0), self.ledger)
        self.assertEqual(kinds(breaks), ["ledger_only", "missing_field"])
        self.assertIn("settlement_id", only(breaks, "missing_field").detail)

    def test_lines_that_are_not_a_list_leave_nothing_to_compare(self):
        """Structural only. A report nobody can read is not a list of economic differences,
        and the ledger it could not be compared against is not `ledger_only`."""
        breaks = reconcile({"batch_id": "SB-0001", "currency": "USD",
                            "lines": {"settlement_id": "st_1"}, "total_net_minor": 12100},
                           self.ledger)
        self.assertEqual(kinds(breaks), ["report_envelope"])
        self.assertIn("not a list", only(breaks, "report_envelope").detail)
        self.assertEqual(exposure(breaks), {}, "an unreadable envelope puts nothing at stake")

    def test_a_declared_total_that_is_not_an_integer_is_a_break(self):
        brk = only(reconcile(report(total="12100"), self.ledger), "report_total")
        self.assertIn("not an integer minor amount", brk.detail)
        self.assertEqual(brk.at_stake, ())

    def test_a_report_with_no_declared_total_at_all_is_a_break(self):
        payload = report()
        del payload["total_net_minor"]
        brk = only(reconcile(payload, self.ledger), "report_total")
        self.assertIn("declares no total", brk.detail)


class AnEntryIsNeverEditedAndNeverDeleted(unittest.TestCase):
    def test_a_committed_entry_cannot_be_edited(self):
        entry = settled(Ledger())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.postings[0].amount_minor = 1
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.reference = "st_2"
        with self.assertRaises(dataclasses.FrozenInstanceError):
            entry.effect_id = "another"

    def test_entries_are_only_ever_appended(self):
        ledger = Ledger()
        first = settled(ledger)
        settled(ledger, reference="st_2")
        self.assertEqual([e.entry_id for e in ledger.entries()], ["e1", "e2"])
        self.assertIs(ledger.entries()[0], first)
        self.assertIsInstance(ledger.entries(), tuple)

    def test_a_correction_is_a_new_entry_naming_the_entry_it_corrects(self):
        ledger = Ledger()
        original = settled(ledger)
        before = ledger.entries()
        ledger.commit_once("correction:brk_1", ("brk_1",), kind="correction",
                           reference="st_1", corrects=original.entry_id, break_id="brk_1",
                           postings=[Posting(CASH, "USD", -25), Posting(FEES, "USD", 25)])
        self.assertEqual(ledger.entries()[0], before[0])
        self.assertEqual(len(ledger.entries()), 2)
        self.assertEqual(ledger.entries()[1].corrects, original.entry_id)
        self.assertEqual(ledger.balance(CASH, "USD"), 12100)
        self.assertEqual(ledger.trial_balance(), {"USD": 0})


class AttributionIsComparedAndNotAssumed(unittest.TestCase):
    def setUp(self):
        self.ledger = Ledger()
        settled(self.ledger, net=12100, fee=400, charge_id="ch_1", batch_id="SB-0001")

    def test_the_right_amount_on_the_wrong_charge_is_a_break(self):
        brk = only(reconcile(report(dict(REPORT_LINE, charge_id="ch_9")), self.ledger),
                   "attribution_mismatch")
        self.assertIn("charge 'ch_9'", brk.detail)
        self.assertEqual(brk.at_stake, (("USD", 12100),))
        self.assertNotIn("amount_mismatch", kinds(reconcile(
            report(dict(REPORT_LINE, charge_id="ch_9")), self.ledger)))

    def test_the_right_amount_in_the_wrong_batch_is_a_break(self):
        brk = only(reconcile(report(dict(REPORT_LINE, batch_id="SB-0009")), self.ledger),
                   "attribution_mismatch")
        self.assertIn("batch 'SB-0009'", brk.detail)
        self.assertEqual(brk.at_stake, (("USD", 12100),))

    def test_the_matching_attribution_is_compared_normally(self):
        self.assertEqual(reconcile(report(), self.ledger), (),
                         "same settlement, same charge, same batch, same amounts")

    def test_the_break_identity_covers_the_whole_attribution(self):
        wrong_charge = only(reconcile(report(dict(REPORT_LINE, charge_id="ch_9")),
                                      self.ledger), "attribution_mismatch")
        wrong_batch = only(reconcile(report(dict(REPORT_LINE, batch_id="SB-0009")),
                                     self.ledger), "attribution_mismatch")
        self.assertNotEqual(wrong_charge.break_id, wrong_batch.break_id)
        self.assertEqual(wrong_charge.break_id,
                         only(reconcile(report(dict(REPORT_LINE, charge_id="ch_9")),
                                        self.ledger), "attribution_mismatch").break_id,
                         "the same disagreement has the same identity every time")


class ReconciliationComparesTheFullUnion(unittest.TestCase):
    def test_the_planted_break_is_one_compound_row_with_one_amount_at_stake(self):
        ledger = Ledger()
        settled(ledger)
        breaks = reconcile(load_settlement_report(), ledger)
        self.assertEqual(kinds(breaks), ["amount_mismatch"])
        brk = breaks[0]
        self.assertEqual(brk.settlement_id, "st_1")
        self.assertEqual(brk.delta("fee"), -25)
        self.assertEqual(brk.delta("net"), 25)
        self.assertEqual(brk.delta("gross"), 0)
        self.assertEqual(brk.at_stake, (("USD", 25),))
        self.assertEqual(exposure(breaks), {"USD": 25},
                         "one disagreement, counted once, not 25 twice")
        self.assertIn("derived from the other two, not counted", brk.describe())

    def test_reconciliation_posts_nothing(self):
        ledger = Ledger()
        settled(ledger)
        before = ledger.entries()
        self.assertTrue(reconcile(load_settlement_report(), ledger))
        self.assertEqual(ledger.entries(), before)

    def test_a_ledger_that_matches_the_authority_reports_no_break(self):
        ledger = Ledger()
        settled(ledger, net=12100, fee=400)
        self.assertEqual(reconcile(load_settlement_report(), ledger), ())

    def test_a_settlement_only_the_report_claims_is_a_report_only_break(self):
        breaks = reconcile(report(dict(REPORT_LINE),
                                  dict(REPORT_LINE, settlement_id="st_9", charge_id="ch_9",
                                       net_minor=500, fee_minor=20, gross_minor=520)),
                           Ledger())
        self.assertEqual(kinds(breaks), ["report_only", "report_only"])
        self.assertEqual(exposure(breaks), {"USD": 12100 + 500})

    def test_a_settlement_only_the_ledger_holds_is_a_ledger_only_break(self):
        ledger = Ledger()
        settled(ledger)
        settled(ledger, net=700, fee=30, gross=730, reference="st_7", charge_id="ch_7")
        brk = only(reconcile(load_settlement_report(), ledger), "ledger_only")
        self.assertEqual((brk.settlement_id, brk.currency), ("st_7", "USD"))
        self.assertEqual(brk.at_stake, (("USD", 700),))

    def test_a_settlement_the_ledger_recorded_twice_is_a_duplicate_entry_break(self):
        ledger = Ledger()
        settled(ledger, net=12100, fee=400)
        # The reverse crash gap, planted by hand: the entry landed and the identity that
        # would have recognised it was lost, so the retry arrived under a fresh one.
        settled(ledger, net=12100, fee=400, effect="settlement:st_1:identity-lost")
        brk = only(reconcile(load_settlement_report(), ledger), "duplicate_entry")
        self.assertIn("2 settlement entries claim this identity", brk.detail)
        self.assertEqual(brk.at_stake, (("USD", 12500 + 400),))


class ReconciliationChecksTheReportsOwnArithmetic(unittest.TestCase):
    def test_one_duplicated_line_is_one_exposure_whatever_the_total_claims(self):
        """The duplicate is one underlying mistake. Whether the declared total counts the
        repeated line or ignores it, the money at stake is counted once: 12,100 and never
        24,200. Both problems may be described; only one of them carries the amount."""
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        for total, note in ((24200, "the total counts the duplicate as written"),
                            (12100, "the total counts only one of the two lines")):
            with self.subTest(total=total):
                breaks = reconcile(
                    report(dict(REPORT_LINE), dict(REPORT_LINE), total=total), ledger)
                self.assertIn("duplicate_report_line", kinds(breaks), note)
                self.assertEqual(only(breaks, "duplicate_report_line").amount("USD"), 12100)
                self.assertEqual(exposure(breaks), {"USD": 12100},
                                 f"{note}, and one duplicate is still one exposure")

    def test_a_line_whose_net_and_fee_do_not_make_its_gross_is_a_break(self):
        brk = only(reconcile(report(dict(REPORT_LINE, fee_minor=300)), Ledger()),
                   "line_arithmetic")
        self.assertEqual(brk.at_stake, (("USD", 100),))

    def test_a_declared_total_the_lines_do_not_add_up_to_is_a_break(self):
        brk = only(reconcile(report(dict(REPORT_LINE), total=99_999), Ledger()),
                   "report_total")
        self.assertEqual(brk.at_stake, (("USD", 99_999 - 12100),))

    def test_one_declared_total_cannot_answer_for_two_currencies(self):
        breaks = reconcile(report(dict(REPORT_LINE),
                                  dict(REPORT_LINE, settlement_id="st_2", charge_id="ch_2",
                                       currency="EUR", net_minor=9000, fee_minor=300,
                                       gross_minor=9300), total=12100), Ledger())
        brk = only(breaks, "report_total")
        self.assertEqual(brk.at_stake, (("EUR", 9000), ("USD", 12100)))
        self.assertIn("cannot be all of them", brk.detail)

    def test_a_line_missing_a_required_field_is_a_break_and_is_not_compared(self):
        line = {k: v for k, v in REPORT_LINE.items() if k != "net_minor"}
        breaks = reconcile(report(line, total=0), Ledger())
        self.assertEqual(kinds(breaks), ["missing_field"])
        self.assertIn("net_minor", breaks[0].detail)


class ReconciliationIsTotal(unittest.TestCase):
    """The report comes from outside and can be anything. A detector that raises on the
    counterparty's file is a detector that is down on the morning somebody sends a bad one.
    Every unreadable shape is a break, and none of them is compared as if it were money."""

    STRUCTURAL = ("report_envelope", "malformed_line", "missing_field", "not_an_amount",
                  "unidentified_line", "report_total", "line_arithmetic")

    def test_no_shape_of_report_makes_reconciliation_raise(self):
        ledger = Ledger()
        settled(ledger)
        cases = {
            "report is None": None,
            "report is a list": [dict(REPORT_LINE)],
            "report is a string": "SB-0001,12500,400,12100",
            "report is a number": 12100,
            "lines is not a list": {"batch_id": "SB-0001", "currency": "USD",
                                    "lines": {"st_1": dict(REPORT_LINE)},
                                    "total_net_minor": 12100},
            "lines is a string": {"batch_id": "SB-0001", "currency": "USD",
                                  "lines": "st_1", "total_net_minor": 12100},
            "batch id is a list": report(dict(REPORT_LINE), batch_id=["SB-0001"]),
            "currency is a dict": report(dict(REPORT_LINE), currency={"code": "USD"}),
            "settlement id is a list": report(dict(REPORT_LINE, settlement_id=["st_1"])),
            "charge id is a dict": report(dict(REPORT_LINE, charge_id={"id": "ch_1"})),
            "batch id on the line is a list": report(dict(REPORT_LINE,
                                                          batch_id=["SB-0001"])),
            "line currency is a number": report(dict(REPORT_LINE, currency=840)),
            "total is missing": {"batch_id": "SB-0001", "currency": "USD",
                                 "lines": [dict(REPORT_LINE)]},
            "total is a float": report(dict(REPORT_LINE), total=12100.0),
            "total is a bool": report(dict(REPORT_LINE), total=True),
            "total is a string": report(dict(REPORT_LINE), total="12100"),
            "line arithmetic does not add up": report(dict(REPORT_LINE, fee_minor=1)),
            "a line is not a record": report("st_1,12500"),
            "an amount is a float": report(dict(REPORT_LINE, net_minor=12100.0)),
            "an amount is a bool": report(dict(REPORT_LINE, fee_minor=True)),
        }
        for name, payload in cases.items():
            with self.subTest(name):
                breaks = reconcile(payload, ledger)
                self.assertTrue(breaks, "an unreadable report is a finding, not silence")
                self.assertTrue(
                    any(b.kind in self.STRUCTURAL for b in breaks),
                    f"{name} produced only {kinds(breaks)}")

    def test_an_unreadable_line_is_never_compared_as_money(self):
        """A structural break and an amount_mismatch built from the same unreadable line
        would report one problem twice, the second time as if the numbers meant something."""
        ledger = Ledger()
        settled(ledger)
        for name, line in (("settlement id is a list", dict(REPORT_LINE,
                                                            settlement_id=["st_1"])),
                           ("charge id is a dict", dict(REPORT_LINE,
                                                        charge_id={"id": "ch_1"})),
                           ("an amount is a float", dict(REPORT_LINE, net_minor=12100.0))):
            with self.subTest(name):
                breaks = reconcile(report(line), ledger)
                self.assertNotIn("amount_mismatch", kinds(breaks),
                                 "an unreadable line is excluded from comparison")


class AttributionIsComparedExactly(unittest.TestCase):
    """`{correct, wrong}` is not correct, and an empty local set is not a match."""

    def setUp(self):
        self.ledger = Ledger()

    def breaks_for(self, **entry):
        settled(self.ledger, net=12100, fee=400, gross=12500, **entry)
        return reconcile(report(dict(REPORT_LINE, batch_id="SB-0001")), self.ledger)

    def test_a_local_entry_with_no_charge_or_batch_is_a_mismatch(self):
        for missing in ({"charge_id": ""}, {"batch_id": ""}):
            with self.subTest(**missing):
                self.ledger = Ledger()
                breaks = self.breaks_for(**missing)
                self.assertIn("attribution_mismatch", kinds(breaks))
                self.assertIn("no attribution at all",
                              only(breaks, "attribution_mismatch").detail)

    def test_the_right_identity_beside_a_wrong_one_is_not_a_match(self):
        settled(self.ledger, net=12100, fee=400, gross=12500)
        # A correction entry attributes the same settlement to a second, different charge.
        self.ledger.commit_once(
            "correction:stray", ("stray",), kind="correction", reference="st_1",
            attribution=Attribution("st_1", "ch_9", "SB-0001", "USD"),
            postings=[Posting(CASH, "USD", 1), Posting(RECEIVABLE, "USD", -1)])
        breaks = reconcile(report(dict(REPORT_LINE)), self.ledger)
        self.assertIn("attribution_mismatch", kinds(breaks))
        self.assertIn("ch_1", only(breaks, "attribution_mismatch").detail)

    def test_a_correct_amount_on_the_wrong_charge_or_batch_is_a_mismatch(self):
        for name, line in (("wrong charge", dict(REPORT_LINE, charge_id="ch_9")),
                           ("wrong batch", dict(REPORT_LINE, batch_id="SB-9999"))):
            with self.subTest(name):
                self.ledger = Ledger()
                settled(self.ledger, net=12100, fee=400, gross=12500)
                breaks = reconcile(report(line), self.ledger)
                self.assertEqual(kinds(breaks), ["attribution_mismatch"],
                                 "the amounts agree; the identity does not")


class OneEffectIdIsAnsweredForItsWholeContent(unittest.TestCase):
    """Returning the stored entry for an id whose content changed would answer a question
    nobody asked. Every component of the entry is part of what the id promised."""

    BASE = dict(kind="settlement", reference="st_1",
                attribution=Attribution("st_1", "ch_1", "SB-0001", "USD"),
                corrects="", break_id="")

    def postings(self, net=12125):
        return [Posting(CASH, "USD", net), Posting(FEES, "USD", 12500 - net),
                Posting(RECEIVABLE, "USD", -12500)]

    def commit(self, ledger, fingerprint=("st_1", 12500), **over):
        fields = dict(self.BASE, postings=self.postings())
        fields.update(over)
        return ledger.commit_once("settlement:st_1", fingerprint, **fields)

    def test_the_same_id_with_the_same_whole_content_returns_the_entry(self):
        ledger = Ledger()
        first, applied = self.commit(ledger)
        again, applied_again = self.commit(ledger)
        self.assertTrue(applied)
        self.assertFalse(applied_again)
        self.assertIs(again, first)
        self.assertEqual(len(ledger.entries()), 1)

    def test_changing_any_component_under_one_id_is_refused(self):
        changes = {
            "economic fingerprint": {"fingerprint": ("st_1", 99)},
            "kind": {"kind": "suspense"},
            "reference": {"reference": "st_2"},
            "postings": {"postings": self.postings(net=12000)},
            "attribution": {"attribution": Attribution("st_1", "ch_9", "SB-0001", "USD")},
            "corrects": {"corrects": "e1"},
            "break id": {"break_id": "brk_1"},
        }
        for name, change in changes.items():
            with self.subTest(name):
                ledger = Ledger()
                self.commit(ledger)
                with self.assertRaises(EffectConflict):
                    self.commit(ledger, **change)
                self.assertEqual(len(ledger.entries()), 1, "and nothing was appended")


class StructuralFailureIsNeverEconomic(unittest.TestCase):
    """A report nobody can read is a structural finding. It is not a list of differences in
    money, and nothing in it may become a break somebody approves a correction against."""

    def matching_ledger(self):
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        return ledger

    def test_a_line_that_does_not_add_up_is_one_finding_and_one_exposure(self):
        ledger = self.matching_ledger()
        breaks = reconcile(report(dict(REPORT_LINE, fee_minor=1), total=12100), ledger)
        self.assertEqual(kinds(breaks), ["line_arithmetic"],
                         "not also an amount_mismatch, a ledger_only or a total break")
        self.assertEqual(exposure(breaks), {"USD": 399}, "one exposure, never twice")
        self.assertEqual([b for b in breaks if b.deltas], [],
                         "and nothing correctable came out of an unreadable line")

    def test_a_total_that_cannot_be_read_leaves_nothing_correctable(self):
        ledger = self.matching_ledger()
        for name, payload in (
                ("missing", {"batch_id": "SB-0001", "currency": "USD",
                             "lines": [dict(REPORT_LINE)]}),
                ("a string", report(dict(REPORT_LINE), total="12100")),
                ("a float", report(dict(REPORT_LINE), total=12100.0)),
                ("a bool", report(dict(REPORT_LINE), total=True))):
            with self.subTest(total=name):
                breaks = reconcile(payload, ledger)
                self.assertEqual(kinds(breaks), ["report_total"])
                self.assertEqual(exposure(breaks), {})
                self.assertEqual([b for b in breaks if b.deltas], [],
                                 "an unreadable envelope produces nothing to correct")

    def test_a_sound_envelope_still_compares_its_lines(self):
        """The rule above must not have turned the detector off for a readable report."""
        ledger = self.matching_ledger()
        breaks = reconcile(report(dict(REPORT_LINE, fee_minor=375, net_minor=12125),
                                  total=12125), ledger)
        self.assertEqual(kinds(breaks), ["amount_mismatch"])
        self.assertEqual(exposure(breaks), {"USD": 25},
                         "gross agrees, fee is out by 25, and the derived net is not "
                         "counted a second time")


class EveryRealDifferenceCarriesItsAmount(unittest.TestCase):
    """A difference reported at zero is a difference nobody triages."""

    def test_a_ledger_row_that_does_not_add_up_values_its_cash_gap(self):
        """Net is derived only while gross - fee == net. A row whose cash alone diverges has
        gross and fee agreeing, so counting only those would value 1.00 of real cash at
        nothing and drop it out of exposure() entirely."""
        ledger = Ledger()
        ledger.commit_once("settlement:st_1", ("st_1",), kind="settlement", reference="st_1",
                           attribution=Attribution("st_1", "ch_1", "SB-0001", "USD"),
                           postings=[Posting(CASH, "USD", 12200), Posting(FEES, "USD", 400),
                                     Posting(RECEIVABLE, "USD", -12500),
                                     Posting(SUSPENSE, "USD", -100)])
        breaks = reconcile(report(dict(REPORT_LINE)), ledger)
        self.assertEqual(kinds(breaks), ["amount_mismatch"])
        self.assertEqual(exposure(breaks), {"USD": 100}, "the cash gap is the exposure")
        self.assertIn("does not add up", only(breaks, "amount_mismatch").detail)

    def test_a_total_gap_the_duplicate_does_not_explain_survives(self):
        """Subtracting a duplicate's amount from an unrelated gap nets two different
        figures and values a real disagreement at nothing."""
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        other = {"settlement_id": "st_2", "charge_id": "ch_2", "batch_id": "SB-0001",
                 "currency": "USD", "gross_minor": 5000, "fee_minor": 0, "net_minor": 5000}
        breaks = reconcile(report(dict(REPORT_LINE), dict(REPORT_LINE), other, total=22450),
                           ledger)
        self.assertIn("report_total", kinds(breaks))
        self.assertEqual(only(breaks, "report_total").amount("USD"), 5350,
                         "the declared total is out by its own amount, not the duplicate's")
        self.assertEqual(only(breaks, "duplicate_report_line").amount("USD"), 12100)

    def test_a_refund_batch_duplicate_is_not_counted_twice(self):
        """The duplicate's net is accumulated signed. Held as an absolute it could never
        match a negative declared total, so every refund batch double-counted its duplicate
        in exposure() - the one case the guard exists for."""
        ledger = Ledger()
        settled(ledger, net=-5000, fee=0, gross=-5000)
        refund = dict(REPORT_LINE, gross_minor=-5000, fee_minor=0, net_minor=-5000)
        breaks = reconcile(report(dict(refund), dict(refund), total=-10000), ledger)
        self.assertEqual(kinds(breaks), ["duplicate_report_line"])
        self.assertEqual(exposure(breaks), {"USD": 5000}, "5000 once, not 10000")

    def test_a_total_error_beside_a_duplicate_is_charged_only_for_the_extra(self):
        """Exact equality made any additional total error re-count the duplicate's whole
        amount. The total is independently wrong only by what it misses both readings by."""
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        breaks = reconcile(report(dict(REPORT_LINE), dict(REPORT_LINE), total=24500), ledger)
        self.assertEqual(only(breaks, "duplicate_report_line").amount("USD"), 12100)
        self.assertEqual(only(breaks, "report_total").amount("USD"), 300,
                         "the extra 300, not the duplicate's 12100 all over again")
        self.assertEqual(exposure(breaks), {"USD": 12400})

    def test_a_duplicate_the_total_does_explain_is_still_one_exposure(self):
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        breaks = reconcile(report(dict(REPORT_LINE), dict(REPORT_LINE), total=24200), ledger)
        self.assertEqual(kinds(breaks), ["duplicate_report_line"])
        self.assertEqual(exposure(breaks), {"USD": 12100})


class NothingUnnamedIsCompared(unittest.TestCase):
    def test_a_whitespace_identifier_names_nothing(self):
        """The ledger's own rule already says a whitespace-only string names nothing. The
        reconciler has to agree, or one unnamed line becomes two economic breaks."""
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        breaks = reconcile(report(dict(REPORT_LINE, settlement_id="   ")), ledger)
        self.assertIn("unidentified_line", kinds(breaks))
        self.assertNotIn("report_only", kinds(breaks))
        self.assertEqual(exposure(breaks), {"USD": 12100}, "one settlement, one exposure")

    def test_a_whitespace_envelope_currency_puts_nothing_at_stake(self):
        ledger = Ledger()
        settled(ledger, net=12100, fee=400, gross=12500)
        breaks = reconcile(report(dict(REPORT_LINE), currency="   "), ledger)
        self.assertEqual(kinds(breaks), ["report_envelope", "unidentified_line"],
                         "the envelope and the line it defaults into are both unnamed")
        self.assertTrue(all(not b.deltas for b in breaks), "structural, nothing to correct")
        self.assertEqual(exposure(breaks), {},
                         "no figure keyed on a currency that is not one")

    def test_a_posting_currency_is_held_to_the_same_rule_as_the_money_it_names(self):
        for currency in (None, ["USD"], "", "   ", 7, True, "usd", "US"):
            with self.subTest(currency=repr(currency)):
                ledger = Ledger()
                with self.assertRaises((InvalidCurrency, Unbalanced)):
                    ledger.commit_once("e", (), kind="settlement", reference="st_x",
                                       postings=[Posting(CASH, currency, 1),
                                                 Posting(RECEIVABLE, currency, -1)])
                self.assertEqual(ledger.entries(), ())
