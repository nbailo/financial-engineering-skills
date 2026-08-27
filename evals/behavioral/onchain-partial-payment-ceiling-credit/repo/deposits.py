"""Turn validated XRP payments into customer balances.

The watcher walks validated ledgers and hands every transaction in them to
`apply_validated`. A payment counts as a deposit when it reached our pool address,
succeeded, and carries a destination tag we issued. Balances are held in drops.
"""

from ledger_stream import ACCOUNT_BY_TAG, DEPOSIT_ADDRESS


class DepositBook:
    """Customer balances in drops, plus the transactions already applied."""

    def __init__(self):
        self.balances = {}
        self.applied = set()
        self.unattributed = []

    def credit(self, account, drops, tx_hash):
        self.balances[account] = self.balances.get(account, 0) + drops
        self.applied.add(tx_hash)

    def balance_of(self, account):
        return self.balances.get(account, 0)


def credited_drops(tx):
    """How many drops this payment is worth to the customer."""
    return int(tx["Amount"])


def apply_validated(book, transactions):
    """Credit every deposit in a batch of validated transactions."""
    for tx in transactions:
        if tx.get("TransactionType") != "Payment":
            continue
        if tx.get("Destination") != DEPOSIT_ADDRESS:
            continue
        if tx["meta"].get("TransactionResult") != "tesSUCCESS":
            continue
        if tx["hash"] in book.applied:
            continue
        account = ACCOUNT_BY_TAG.get(tx.get("DestinationTag"))
        if account is None:
            book.unattributed.append(tx["hash"])
            continue
        book.credit(account, credited_drops(tx), tx["hash"])
    return book
