"""Wallet balances, holds and postings.

Amounts are signed integer minor units: a credit is positive, a debit is negative.
A group of legs nets to zero in every currency it touches. Accounts prefixed
`external:` are the counterparty legs for money entering or leaving the platform.

`today` is the business day counter the nightly batch advances. Holds expire on it,
so an abandoned reservation stops reserving without waiting for any callback.
"""


class Unbalanced(Exception):
    """The legs of a group do not net to zero in some currency."""


class DuplicateTransaction(Exception):
    """A transaction id that the ledger already carries."""


class DuplicateHold(Exception):
    """A hold id that the ledger already carries."""


class UnknownHold(Exception):
    """No live hold under this id."""


class Hold:
    __slots__ = ("hold_id", "account", "amount", "currency", "expires_on", "state")

    def __init__(self, hold_id, account, amount, currency, expires_on):
        self.hold_id = hold_id
        self.account = account
        self.amount = amount
        self.currency = currency
        self.expires_on = expires_on
        self.state = "live"


class Ledger:
    def __init__(self):
        self._legs = []
        self._holds = {}
        self.today = 0

    def post(self, txn_id, legs):
        """Append one balanced group. legs is [(account, signed_amount, currency)]."""
        if any(leg[0] == txn_id for leg in self._legs):
            raise DuplicateTransaction(txn_id)
        net = {}
        for _account, amount, currency in legs:
            net[currency] = net.get(currency, 0) + amount
        for currency, total in sorted(net.items()):
            if total != 0:
                raise Unbalanced("%s nets to %d in %s" % (txn_id, total, currency))
        for account, amount, currency in legs:
            self._legs.append((txn_id, account, amount, currency))

    def posted_balance(self, account, currency):
        """What has actually settled on the account. Statements quote this."""
        return sum(amount for (_txn, acct, amount, cur) in self._legs
                   if acct == account and cur == currency)

    def place_hold(self, hold_id, account, amount, currency, expires_on):
        if hold_id in self._holds:
            raise DuplicateHold(hold_id)
        if amount <= 0:
            raise ValueError("a hold reserves a positive amount")
        self._holds[hold_id] = Hold(hold_id, account, amount, currency, expires_on)

    def live_holds(self, account, currency):
        return [hold for hold in self._holds.values()
                if hold.account == account and hold.currency == currency
                and hold.state == "live" and hold.expires_on > self.today]

    def available(self, account, currency):
        """Posted minus everything already reserved.

        This is the number that decides whether the wallet can fund one more debit.
        """
        reserved = sum(hold.amount for hold in self.live_holds(account, currency))
        return self.posted_balance(account, currency) - reserved

    def capture_hold(self, hold_id, destination):
        """Turn a reservation into a posting, and drop the reservation with it."""
        hold = self._holds.get(hold_id)
        if hold is None or hold.state != "live":
            raise UnknownHold(hold_id)
        self.post("capture:" + hold_id,
                  [(hold.account, -hold.amount, hold.currency),
                   (destination, hold.amount, hold.currency)])
        hold.state = "captured"

    def void_hold(self, hold_id):
        hold = self._holds.get(hold_id)
        if hold is None or hold.state != "live":
            raise UnknownHold(hold_id)
        hold.state = "voided"

    def totals_by_currency(self):
        out = {}
        for (_txn, _acct, amount, currency) in self._legs:
            out[currency] = out.get(currency, 0) + amount
        return out
