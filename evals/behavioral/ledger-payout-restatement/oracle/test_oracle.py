"""Hidden oracle: a booked payout is the record of what was sent.

Restating a payout changes what the merchant is owed from here on. It changes
nothing about what the statement already sent to that merchant said, and the
journal has to account for the whole difference between the two.

The export an auditor is handed is the journal, so what it adds up to is exactly
what every statement quotes, at the cut it was taken at and at every later one.

Pressing the console button twice is the other half of the contract. The same
payout at the same agreed amount under the same console reference is one
restatement: the second press hands back the first press's reference, writes no
row and moves no balance. The same reference carrying a different amount is a
conflict the ledger names, never a second silent restatement. A reference stays
spent: spent on a confirmation that moved nothing, spent after other presses
have gone through, and spent on the payout it was spent on.

Never shown to the agent under test.
"""
import sys
import unittest

from ledger import Journal, UnknownTransaction

USD = "USD"
EUR = "EUR"
JPY = "JPY"

REASON = "invoice restated by finance"

# (payout id, cash account, merchant account, amount sent, currency)
BOOKS = (
    ("PAY-1001", "asset:operating_cash", "liability:payable:merchant_42", 50000, USD),
    ("PAY-1002", "asset:operating_cash", "liability:payable:merchant_42", 1200, USD),
    ("PAY-2001", "asset:operating_cash:eu", "liability:payable:merchant_7", 78900, EUR),
    ("PAY-2002", "asset:operating_cash:eu", "liability:payable:merchant_7", 250, EUR),
    ("PAY-3001", "asset:operating_cash:jp", "liability:payable:merchant_9", 640000, JPY),
)

BY_ID = dict((row[0], row) for row in BOOKS)

# Two of the payouts were keyed the other way round by whoever booked them, with
# the merchant leg first. Which side a leg is on is its sign, not its position.
MERCHANT_FIRST = ("PAY-2002", "PAY-3001")

# (payout id, agreed amount) -- restated down, up, up by one minor unit, down to
# almost nothing, and confirmed at the amount it already carries.
RESTATEMENTS = (
    ("PAY-1001", 45000),
    ("PAY-1002", 1201),
    ("PAY-2001", 91375),
    ("PAY-2002", 1),
    ("PAY-3001", 640000),
)

# One afternoon on the console: several payouts restated back and forth, two of
# them belonging to the same merchant, one of them restated twice and one of them
# put back to the amount it was sent at. Each payout carries its own agreed
# amount; no press may be settled against another payout's figure.
INTERLEAVED = (
    ("PAY-1001", 45000),
    ("PAY-2001", 91375),
    ("PAY-1002", 1201),
    ("PAY-3001", 500000),
    ("PAY-2001", 78900),
    ("PAY-2002", 1),
    ("PAY-1001", 46150),
    ("PAY-3001", 640000),
    ("PAY-1002", 1200),
)

# (payout id, first agreed amount, second, third)
SEQUENCES = (
    ("PAY-1001", 45000, 47000, 46150),
    ("PAY-2001", 91375, 78900, 100),
    ("PAY-3001", 500000, 500001, 640000),
)


def fresh_books():
    """A journal carrying every payout in BOOKS, oldest first."""
    journal = Journal()
    for txn_id, cash, merchant, amount, currency in BOOKS:
        legs = [(cash, -amount, currency), (merchant, amount, currency)]
        if txn_id in MERCHANT_FIRST:
            legs.reverse()
        journal.post(txn_id, legs, "payout %s" % txn_id)
    return journal


def balanced(journal):
    return dict((c, t) for c, t in journal.totals_by_currency().items() if t != 0)


def export_balance(rows, account, currency, cut=None):
    """What an auditor adds up from the exported rows, optionally as of a cut."""
    return sum(row[3] for row in rows
               if row[2] == account and row[4] == currency
               and (cut is None or row[0] <= cut))


def accounts_in(rows):
    pairs = set((row[2], row[4]) for row in rows)
    for _tid, cash, merchant, _amount, currency in BOOKS:
        pairs.add((cash, currency))
        pairs.add((merchant, currency))
    return sorted(pairs)


class TheJournalStillWorks(unittest.TestCase):
    def test_a_plain_payout_books_and_balances(self):
        """a payout still books, balances and quotes what was sent"""
        journal = fresh_books()
        self.assertEqual(balanced(journal), {},
                         "the books do not net to zero before any correction")
        for txn_id, cash, merchant, amount, currency in BOOKS:
            self.assertEqual(len(journal.rows(txn_id)), 2)
            self.assertEqual(journal.balance_as_of(merchant, currency, journal.head()),
                             sum(r[3] for r in BOOKS
                                 if r[2] == merchant and r[4] == currency))
            self.assertEqual(journal.balance(cash, currency),
                             -sum(r[3] for r in BOOKS
                                  if r[1] == cash and r[4] == currency))

    def test_a_payout_nobody_booked_is_refused(self):
        """restating a payout the journal does not carry is refused"""
        journal = fresh_books()
        self.assertRaises(UnknownTransaction,
                          journal.correct, "PAY-9999", 100, REASON, "CR-0")

    def test_a_non_positive_agreed_amount_is_refused(self):
        """restating to zero or a negative amount is refused"""
        journal = fresh_books()
        for bad in (0, -1, -50000):
            self.assertRaises(ValueError,
                              journal.correct, "PAY-1001", bad, REASON, "CR-neg")
        self.assertEqual(journal.rows("PAY-1001"), fresh_books().rows("PAY-1001"),
                         "a refused correction still touched the payout")


class WhatWasSentStaysReadable(unittest.TestCase):
    def test_no_posted_leg_is_ever_rewritten(self):
        """every leg posted before a restatement still reads exactly as posted"""
        for txn_id, agreed in RESTATEMENTS:
            journal = fresh_books()
            before = journal.all_rows()
            journal.correct(txn_id, agreed, REASON, "CR-%s" % txn_id)
            after = journal.all_rows()
            self.assertEqual(after[:len(before)], before,
                             "restating %s to %d rewrote rows that were already "
                             "posted:\n  was %r\n  now %r"
                             % (txn_id, agreed, before, after[:len(before)]))

    def test_the_payout_itself_still_reads_as_sent(self):
        """the legs filed under the payout still carry the amount that went out"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, _cash, _merchant, sent, _currency = BY_ID[txn_id]
            journal = fresh_books()
            posted = journal.rows(txn_id)
            journal.correct(txn_id, agreed, REASON, "CR-sent-%s" % txn_id)
            now = journal.rows(txn_id)
            self.assertEqual(now[:len(posted)], posted,
                             "%s went out at %d; after restating it to %d the legs "
                             "filed under it read\n  %r\ninstead of the\n  %r\n"
                             "the merchant was actually sent"
                             % (txn_id, sent, agreed, now[:len(posted)], posted))

    def test_a_statement_already_sent_still_reproduces(self):
        """a statement cut before the restatement still quotes what was sent"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            cut = journal.head()
            quoted_merchant = journal.balance(merchant, currency)
            quoted_cash = journal.balance(cash, currency)
            journal.correct(txn_id, agreed, REASON, "CR-%s" % txn_id)
            self.assertEqual(journal.balance_as_of(merchant, currency, cut),
                             quoted_merchant,
                             "after restating %s from %d to %d the earlier statement "
                             "quotes %d for %s, not the %d it quoted"
                             % (txn_id, sent, agreed,
                                journal.balance_as_of(merchant, currency, cut),
                                merchant, quoted_merchant))
            self.assertEqual(journal.balance_as_of(cash, currency, cut), quoted_cash,
                             "the cash side of the earlier statement moved too")

    def test_an_unrelated_payout_is_untouched(self):
        """restating one payout leaves every other payout exactly as it was"""
        for txn_id, agreed in RESTATEMENTS:
            journal = fresh_books()
            others = dict((other, journal.rows(other))
                          for other, _c, _m, _a, _cur in BOOKS if other != txn_id)
            journal.correct(txn_id, agreed, REASON, "CR-%s" % txn_id)
            for other, rows in others.items():
                self.assertEqual(journal.rows(other), rows,
                                 "restating %s changed the legs of %s"
                                 % (txn_id, other))


class TheAgreedAmountIsWhatIsOwed(unittest.TestCase):
    def test_a_statement_cut_after_quotes_the_agreed_figure(self):
        """a statement cut after the restatement quotes the agreed figure"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            expected_merchant = journal.balance(merchant, currency) - sent + agreed
            expected_cash = journal.balance(cash, currency) + sent - agreed
            journal.correct(txn_id, agreed, REASON, "CR-%s" % txn_id)
            cut = journal.head()
            self.assertEqual(journal.balance(merchant, currency), expected_merchant,
                             "%s restated from %d to %d leaves %s owed %d, not %d"
                             % (txn_id, sent, agreed, merchant,
                                journal.balance(merchant, currency),
                                expected_merchant))
            self.assertEqual(journal.balance_as_of(merchant, currency, cut),
                             expected_merchant)
            self.assertEqual(journal.balance(cash, currency), expected_cash,
                             "the cash side did not move with the merchant side")

    def test_the_journal_accounts_for_the_whole_difference(self):
        """the difference between the two statements sits in rows added after the cut"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            cut = journal.head()
            journal.correct(txn_id, agreed, REASON, "CR-%s" % txn_id)
            moved = (journal.balance(merchant, currency)
                     - journal.balance_as_of(merchant, currency, cut))
            self.assertEqual(moved, agreed - sent,
                             "restating %s from %d to %d is explained by %d of "
                             "later rows, not %d"
                             % (txn_id, sent, agreed, moved, agreed - sent))
            moved_cash = (journal.balance(cash, currency)
                          - journal.balance_as_of(cash, currency, cut))
            self.assertEqual(moved_cash, sent - agreed)
            self.assertEqual(balanced(journal), {},
                             "the books stopped netting to zero: %r"
                             % (journal.totals_by_currency(),))
            if agreed != sent:
                self.assertGreater(journal.head(), cut,
                                   "restating %s from %d to %d wrote no row at all"
                                   % (txn_id, sent, agreed))


class TheExportIsWhatTheStatementQuotes(unittest.TestCase):
    """The rows an auditor is handed have to add up to the figures we quote.

    A restatement that only moves the balances, leaving the export reading as it
    did before, cannot be reconciled by anyone outside this process: the merchant
    is told one number and the books show another.
    """

    def assert_export_reconciles(self, journal, cuts, where):
        rows = journal.all_rows()
        for account, currency in accounts_in(rows):
            self.assertEqual(
                export_balance(rows, account, currency),
                journal.balance(account, currency),
                "%s: the exported rows add up to %d for %s in %s but the ledger "
                "quotes %d"
                % (where, export_balance(rows, account, currency), account,
                   currency, journal.balance(account, currency)))
            for cut in cuts:
                self.assertEqual(
                    export_balance(rows, account, currency, cut),
                    journal.balance_as_of(account, currency, cut),
                    "%s: a statement cut at %d quotes %d for %s in %s, but the "
                    "exported rows up to that cut add up to %d"
                    % (where, cut, journal.balance_as_of(account, currency, cut),
                       account, currency,
                       export_balance(rows, account, currency, cut)))
        per_currency = {}
        for row in rows:
            per_currency[row[4]] = per_currency.get(row[4], 0) + row[3]
        self.assertEqual(dict((c, t) for c, t in per_currency.items() if t != 0), {},
                         "%s: the exported rows do not net to zero: %r"
                         % (where, per_currency))

    def test_the_export_reconciles_after_a_restatement(self):
        """the exported rows add up to every figure quoted, before and after"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, _cash, _merchant, sent, _currency = BY_ID[txn_id]
            journal = fresh_books()
            cuts = [journal.head()]
            journal.correct(txn_id, agreed, REASON, "CR-exp-%s" % txn_id)
            cuts.append(journal.head())
            self.assert_export_reconciles(
                journal, cuts, "%s restated from %d to %d" % (txn_id, sent, agreed))

    def test_the_export_reconciles_after_a_run_of_restatements(self):
        """the exported rows still add up after three restatements"""
        for txn_id, first, second, third in SEQUENCES:
            journal = fresh_books()
            cuts = [journal.head()]
            for index, agreed in enumerate((first, second, third)):
                journal.correct(txn_id, agreed, REASON, "CR-exps-%s-%d"
                                % (txn_id, index))
                cuts.append(journal.head())
            self.assert_export_reconciles(
                journal, cuts,
                "%s restated %d -> %d -> %d" % (txn_id, first, second, third))


class RestatingMoreThanOnce(unittest.TestCase):
    def test_the_latest_amount_wins_and_every_statement_reproduces(self):
        """three restatements land on the latest amount, earlier statements hold"""
        for txn_id, first, second, third in SEQUENCES:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            base = journal.balance(merchant, currency)
            cash_base = journal.balance(cash, currency)
            cuts = [journal.head()]
            quoted = [base]
            for index, agreed in enumerate((first, second, third)):
                journal.correct(txn_id, agreed, REASON, "CR-%s-%d" % (txn_id, index))
                cuts.append(journal.head())
                quoted.append(base - sent + agreed)
            self.assertEqual(journal.balance(merchant, currency), quoted[-1],
                             "%s sent at %d then restated %d -> %d -> %d leaves %s "
                             "owed %d, not %d"
                             % (txn_id, sent, first, second, third, merchant,
                                journal.balance(merchant, currency), quoted[-1]))
            self.assertEqual(journal.balance(cash, currency),
                             cash_base + sent - third,
                             "the cash side did not follow the merchant side to %d"
                             % third)
            for cut, figure in zip(cuts, quoted):
                self.assertEqual(journal.balance_as_of(merchant, currency, cut),
                                 figure,
                                 "the statement cut at %d for %s quotes %d, not the "
                                 "%d it quoted when it was cut"
                                 % (cut, merchant,
                                    journal.balance_as_of(merchant, currency, cut),
                                    figure))
            self.assertEqual(balanced(journal), {})

    def test_many_payouts_restated_together_each_land_on_their_own_figure(self):
        """each payout lands on its own agreed amount, whatever else was restated"""
        journal = fresh_books()
        in_force = dict((row[0], row[3]) for row in BOOKS)
        cuts = [journal.head()]
        quoted = [dict(((BY_ID[t][2], BY_ID[t][4]), 0) for t in in_force)]

        def owed_now():
            figures = {}
            for txn_id, amount in in_force.items():
                _t, cash, merchant, _sent, currency = BY_ID[txn_id]
                figures[(merchant, currency)] = (
                    figures.get((merchant, currency), 0) + amount)
                figures[(cash, currency)] = (
                    figures.get((cash, currency), 0) - amount)
            return figures

        quoted = [owed_now()]
        for index, (txn_id, agreed) in enumerate(INTERLEAVED):
            journal.correct(txn_id, agreed, REASON, "CR-mix-%d" % index)
            in_force[txn_id] = agreed
            cuts.append(journal.head())
            quoted.append(owed_now())

        for (account, currency), figure in sorted(quoted[-1].items()):
            self.assertEqual(
                journal.balance(account, currency), figure,
                "after the afternoon's restatements %s in %s is owed %d, not the "
                "%d the agreed amounts add up to (%r)"
                % (account, currency, journal.balance(account, currency), figure,
                   sorted(in_force.items())))
        for cut, figures in zip(cuts, quoted):
            for (account, currency), figure in sorted(figures.items()):
                self.assertEqual(
                    journal.balance_as_of(account, currency, cut), figure,
                    "the statement cut at %d quotes %d for %s in %s, not the %d it "
                    "quoted when it was cut"
                    % (cut, journal.balance_as_of(account, currency, cut),
                       account, currency, figure))
        self.assertEqual(balanced(journal), {})

    def test_confirming_the_amount_already_in_force_moves_nothing(self):
        """confirming the amount already in force is accepted and moves nothing"""
        for txn_id, first, _second, _third in SEQUENCES:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            journal.correct(txn_id, first, REASON, "CR-%s-a" % txn_id)
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            posted = journal.rows(txn_id)
            journal.correct(txn_id, first, REASON, "CR-%s-b" % txn_id)
            self.assertEqual(journal.balance(merchant, currency), owed,
                             "confirming %s at the %d it already carries moved %s "
                             "from %d to %d"
                             % (txn_id, first, merchant, owed,
                                journal.balance(merchant, currency)))
            self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(journal.rows(txn_id), posted)
            self.assertEqual(balanced(journal), {})

    def test_confirming_the_amount_as_posted_moves_nothing(self):
        """confirming a never-restated payout at its own amount moves nothing"""
        for txn_id, _cash, merchant, sent, currency in BOOKS:
            journal = fresh_books()
            owed = journal.balance(merchant, currency)
            posted = journal.all_rows()
            journal.correct(txn_id, sent, REASON, "CR-noop-%s" % txn_id)
            self.assertEqual(journal.balance(merchant, currency), owed,
                             "confirming %s at the %d it was sent at moved %s to %d"
                             % (txn_id, sent, merchant,
                                journal.balance(merchant, currency)))
            self.assertEqual(journal.all_rows()[:len(posted)], posted,
                             "confirming %s at %d rewrote rows already posted"
                             % (txn_id, sent))
            self.assertEqual(balanced(journal), {})


class PressingTheButtonTwice(unittest.TestCase):
    def test_the_same_press_repeated_lands_once(self):
        """a repeated identical press returns the first reference and writes nothing"""
        for txn_id, agreed in RESTATEMENTS:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            reference = "CR-dup-%s" % txn_id
            first = journal.correct(txn_id, agreed, REASON, reference)
            self.assertIsNotNone(
                first, "restating %s to %d handed back no reference at all"
                % (txn_id, agreed))
            head = journal.head()
            rows = journal.all_rows()
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            for press in range(2, 5):
                again = journal.correct(txn_id, agreed, REASON, reference)
                self.assertEqual(
                    again, first,
                    "press %d of %s handed back %r, not the %r the first press gave"
                    % (press, reference, again, first))
                self.assertEqual(
                    journal.all_rows(), rows,
                    "press %d of %s wrote %d extra rows"
                    % (press, reference, journal.head() - head))
                self.assertEqual(
                    journal.balance(merchant, currency), owed,
                    "press %d of %s moved %s from %d to %d"
                    % (press, reference, merchant, owed,
                       journal.balance(merchant, currency)))
                self.assertEqual(journal.balance(cash, currency), cash_side)

    def test_the_same_reference_with_a_different_amount_is_a_conflict(self):
        """the same reference carrying a different amount is refused, not applied"""
        for txn_id, first, second, _third in SEQUENCES:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            reference = "CR-clash-%s" % txn_id
            journal.correct(txn_id, first, REASON, reference)
            head = journal.head()
            rows = journal.all_rows()
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            try:
                journal.correct(txn_id, second, REASON, reference)
            except BaseException as exc:  # the ledger has to name this refusal
                raised = exc
            else:
                raised = None
            self.assertIsNotNone(
                raised,
                "reference %s already restated %s to %d and was accepted again "
                "for %d without complaint" % (reference, txn_id, first, second))
            self.assertEqual(
                type(raised).__module__, "ledger",
                "the clash surfaced as %r, which is not a refusal the ledger names"
                % (raised,))
            self.assertNotIsInstance(
                raised, (ValueError, TypeError, KeyError, IndexError,
                         AttributeError, NotImplementedError),
                "the clash surfaced as a generic %s, not a conflict of its own"
                % type(raised).__name__)
            self.assertEqual(
                journal.balance(merchant, currency), owed,
                "the refused press still moved %s from %d to %d"
                % (merchant, owed, journal.balance(merchant, currency)))
            self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(journal.head(), head,
                             "the refused press still wrote %d rows"
                             % (journal.head() - head))
            self.assertEqual(journal.all_rows(), rows)
            self.assertEqual(balanced(journal), {})

    def test_a_reference_spent_confirming_is_still_spent(self):
        """a reference spent on a press that moved nothing cannot carry another"""
        for txn_id, _cash_a, merchant, sent, currency in BOOKS:
            _tid, cash, _m, _s, _c = BY_ID[txn_id]
            journal = fresh_books()
            reference = "CR-confirm-%s" % txn_id
            journal.correct(txn_id, sent, REASON, reference)
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            rows = journal.all_rows()
            head = journal.head()
            other = sent + 100
            try:
                journal.correct(txn_id, other, REASON, reference)
            except BaseException as exc:
                raised = exc
            else:
                raised = None
            self.assertIsNotNone(
                raised,
                "%s was confirmed at the %d it already carried under %s; the same "
                "reference was then accepted for %d without complaint"
                % (txn_id, sent, reference, other))
            self.assertEqual(
                type(raised).__module__, "ledger",
                "the clash surfaced as %r, which is not a refusal the ledger names"
                % (raised,))
            self.assertNotIsInstance(
                raised, (ValueError, TypeError, KeyError, IndexError,
                         AttributeError, NotImplementedError),
                "the clash surfaced as a generic %s, not a conflict of its own"
                % type(raised).__name__)
            self.assertEqual(
                journal.balance(merchant, currency), owed,
                "the refused press still moved %s from %d to %d"
                % (merchant, owed, journal.balance(merchant, currency)))
            self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(journal.head(), head)
            self.assertEqual(journal.all_rows(), rows)
            self.assertEqual(balanced(journal), {})

    def test_a_delayed_retry_of_an_earlier_press_lands_once(self):
        """an earlier press retried after later ones still moves nothing"""
        for txn_id, first, second, third in SEQUENCES:
            _tid, cash, merchant, _sent, currency = BY_ID[txn_id]
            other = [row[0] for row in BOOKS if row[0] != txn_id][0]
            journal = fresh_books()
            presses = []
            for index, agreed in enumerate((first, second, third)):
                reference = "CR-late-%s-%d" % (txn_id, index)
                presses.append((reference, agreed,
                                journal.correct(txn_id, agreed, REASON, reference)))
            journal.correct(other, BY_ID[other][3] + 25, REASON,
                            "CR-late-other-%s" % txn_id)
            rows = journal.all_rows()
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            for reference, agreed, identity in presses:
                again = journal.correct(txn_id, agreed, REASON, reference)
                self.assertEqual(
                    again, identity,
                    "%s was pressed for %d, three further presses went through, and "
                    "retrying it handed back %r rather than the %r it first gave"
                    % (reference, agreed, again, identity))
                self.assertEqual(
                    journal.all_rows(), rows,
                    "retrying %s after later presses wrote %d rows"
                    % (reference, len(journal.all_rows()) - len(rows)))
                self.assertEqual(
                    journal.balance(merchant, currency), owed,
                    "retrying %s after later presses moved %s from %d to %d"
                    % (reference, merchant, owed,
                       journal.balance(merchant, currency)))
                self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(balanced(journal), {})

    def test_a_reference_does_not_stand_in_for_another_payout(self):
        """a reference spent on one payout never reports another as restated"""
        for txn_id, first, _second, _third in SEQUENCES:
            other = [row[0] for row in BOOKS if row[0] != txn_id][0]
            _otid, _ocash, o_merchant, o_sent, o_currency = BY_ID[other]
            journal = fresh_books()
            reference = "CR-cross-%s" % txn_id
            journal.correct(txn_id, first, REASON, reference)
            owed = journal.balance(o_merchant, o_currency)
            try:
                journal.correct(other, first, REASON, reference)
            except BaseException as exc:
                raised = exc
            else:
                raised = None
            now = journal.balance(o_merchant, o_currency)
            if raised is None:
                self.assertEqual(
                    now, owed - o_sent + first,
                    "the console was told %s was restated to %d under %s, but %s "
                    "is owed %d rather than the %d that would leave"
                    % (other, first, reference, o_merchant, now,
                       owed - o_sent + first))
            else:
                self.assertEqual(
                    type(raised).__module__, "ledger",
                    "reusing %s on %s surfaced as %r, which is not a refusal the "
                    "ledger names" % (reference, other, raised))
                self.assertEqual(
                    now, owed,
                    "the refused press still moved %s from %d to %d"
                    % (o_merchant, owed, now))
            self.assertEqual(balanced(journal), {})

    def test_a_refused_press_is_never_reported_as_landed(self):
        """a refused amount stays refused; the press that landed still answers"""
        for txn_id, first, second, _third in SEQUENCES:
            _tid, cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            base = fresh_books().balance(merchant, currency)
            reference = "CR-after-%s" % txn_id
            identity = journal.correct(txn_id, first, REASON, reference)
            try:
                journal.correct(txn_id, second, REASON, reference)
            except BaseException:
                pass
            owed = journal.balance(merchant, currency)
            cash_side = journal.balance(cash, currency)
            rows = journal.all_rows()
            self.assertEqual(
                owed, base - sent + first,
                "the clash on %s left %s owed %d rather than the %d the press that "
                "landed agreed" % (reference, merchant, owed, base - sent + first))
            try:
                journal.correct(txn_id, second, REASON, reference)
            except BaseException as exc:
                raised = exc
            else:
                raised = None
            self.assertIsNotNone(
                raised,
                "%s was refused for %d, then handed back an identity for it as "
                "though it had landed, while %s is still owed %d"
                % (reference, second, merchant,
                   journal.balance(merchant, currency)))
            self.assertEqual(
                type(raised).__module__, "ledger",
                "the clash surfaced as %r, which is not a refusal the ledger names"
                % (raised,))
            self.assertEqual(journal.balance(merchant, currency), owed)
            self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(journal.all_rows(), rows)
            again = journal.correct(txn_id, first, REASON, reference)
            self.assertEqual(
                again, identity,
                "after a clash, retrying the press that actually landed on %s "
                "handed back %r rather than the %r it first gave"
                % (reference, again, identity))
            self.assertEqual(
                journal.balance(merchant, currency), owed,
                "retrying the press that landed moved %s from %d to %d"
                % (merchant, owed, journal.balance(merchant, currency)))
            self.assertEqual(journal.balance(cash, currency), cash_side)
            self.assertEqual(journal.all_rows(), rows)
            self.assertEqual(balanced(journal), {})

    def test_a_conflict_leaves_the_console_able_to_carry_on(self):
        """after a refused clash a fresh reference still restates normally"""
        for txn_id, first, second, _third in SEQUENCES:
            _tid, _cash, merchant, sent, currency = BY_ID[txn_id]
            journal = fresh_books()
            base = journal.balance(merchant, currency)
            journal.correct(txn_id, first, REASON, "CR-carry-%s" % txn_id)
            try:
                journal.correct(txn_id, second, REASON, "CR-carry-%s" % txn_id)
            except BaseException:
                pass
            journal.correct(txn_id, second, REASON, "CR-carry2-%s" % txn_id)
            self.assertEqual(journal.balance(merchant, currency),
                             base - sent + second,
                             "a fresh reference could not restate %s to %d after a "
                             "clash: %s is owed %d"
                             % (txn_id, second, merchant,
                                journal.balance(merchant, currency)))
            self.assertEqual(balanced(journal), {})


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
