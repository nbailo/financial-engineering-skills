"""Card authorisation.

The card processor calls authorize() inline and treats the answer as final. There is
no second look: an approval here is money that leaves the wallet.
"""
from collections import namedtuple

Decision = namedtuple("Decision", "approved reason")


def authorize(ledger, account, amount, currency, txn_id, merchant_account):
    """Approve or decline one card purchase against the customer's wallet."""
    if amount <= 0:
        return Decision(False, "invalid_amount")
    if ledger.posted_balance(account, currency) < amount:
        return Decision(False, "insufficient_funds")
    ledger.post(txn_id, [(account, -amount, currency),
                         (merchant_account, amount, currency)])
    return Decision(True, "approved")
