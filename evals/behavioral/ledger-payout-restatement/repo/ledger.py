"""Journal for the merchant payouts service.

Amounts are signed integer minor units: a credit is positive, a debit is negative.
One payout is one balanced group of legs sharing a transaction id. A statement run
records the sequence number it cut at and quotes balances as of that cut, so an
already-sent statement can be re-run and must come back with the same figures.
"""


class Unbalanced(Exception):
    """The legs of a group do not net to zero in some currency."""


class DuplicateTransaction(Exception):
    """A transaction id the journal already carries."""


class UnknownTransaction(Exception):
    """No group carries this transaction id."""


class Leg:
    __slots__ = ("seq", "txn_id", "account", "amount", "currency", "memo")

    def __init__(self, seq, txn_id, account, amount, currency, memo):
        self.seq = seq
        self.txn_id = txn_id
        self.account = account
        self.amount = amount
        self.currency = currency
        self.memo = memo

    def as_row(self):
        return (self.seq, self.txn_id, self.account, self.amount,
                self.currency, self.memo)


class Journal:
    """Every payout the service has booked, oldest leg first."""

    def __init__(self):
        self._legs = []
        self._seq = 0

    def post(self, txn_id, legs, memo=""):
        """Append one balanced group.

        legs is [(account, signed_amount, currency)]. The group nets to zero in
        every currency it touches or nothing is written.
        """
        if self.group(txn_id):
            raise DuplicateTransaction(txn_id)
        net = {}
        for _account, amount, currency in legs:
            net[currency] = net.get(currency, 0) + amount
        for currency, total in sorted(net.items()):
            if total != 0:
                raise Unbalanced("%s nets to %d in %s" % (txn_id, total, currency))
        for account, amount, currency in legs:
            self._seq += 1
            self._legs.append(Leg(self._seq, txn_id, account, amount, currency, memo))
        return self._seq

    def correct(self, txn_id, new_amount, reason, request_id):
        """Restate a payout that went out at the wrong amount.

        Support calls this from the ops console once finance confirms the invoice
        total. new_amount is the magnitude the payout should have carried; each leg
        keeps its own side. request_id is the console's own reference for this press
        of the button, carried through onto the legs so ops can find it again.
        """
        legs = self.group(txn_id)
        if not legs:
            raise UnknownTransaction(txn_id)
        if new_amount <= 0:
            raise ValueError("a payout carries a positive amount")
        for leg in legs:
            leg.amount = new_amount if leg.amount > 0 else -new_amount
            leg.memo = "%s (%s)" % (reason, request_id)
        return request_id

    def group(self, txn_id):
        return [leg for leg in self._legs if leg.txn_id == txn_id]

    def rows(self, txn_id):
        return [leg.as_row() for leg in self.group(txn_id)]

    def all_rows(self):
        """Every leg the journal carries, oldest first, for an export or an audit."""
        return [leg.as_row() for leg in self._legs]

    def head(self):
        """The sequence number a statement run records as its cut-off."""
        return self._seq

    def balance(self, account, currency):
        return sum(leg.amount for leg in self._legs
                   if leg.account == account and leg.currency == currency)

    def balance_as_of(self, account, currency, seq):
        """What a statement cut at `seq` quotes for this account."""
        return sum(leg.amount for leg in self._legs
                   if leg.account == account and leg.currency == currency
                   and leg.seq <= seq)

    def totals_by_currency(self):
        out = {}
        for leg in self._legs:
            out[leg.currency] = out.get(leg.currency, 0) + leg.amount
        return out
