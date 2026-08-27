"""Credit token deposits from chain logs.

The watermark is the last block this service has processed. Each pass reads the logs
above it, credits the deposits, and moves it up. Blocks nearer the head than
CONFIRMATIONS are left for a later pass.
"""

from chain import ACCOUNT_BY_DEPOSIT_ADDRESS, INTERNAL_ADDRESSES, TRACKED_TOKEN

CONFIRMATIONS = 12
RANGE_SPAN = 50


class DepositStore:
    """Customer balances in base units, the watermark, and what has been applied."""

    def __init__(self, cursor):
        self.cursor = cursor
        self.balances = {}
        self.applied = set()

    def credit(self, log, account):
        key = (log["block_hash"], log["tx_hash"], log["log_index"])
        if key in self.applied:
            return
        self.applied.add(key)
        self.balances[account] = self.balances.get(account, 0) + log["amount"]

    def balance_of(self, account):
        return self.balances.get(account, 0)


def is_deposit(log):
    """A log is a customer deposit when outside money reaches an address we issued."""
    if log["token"] != TRACKED_TOKEN:
        return False
    if log["from"] in INTERNAL_ADDRESSES:
        return False
    return log["to"] in ACCOUNT_BY_DEPOSIT_ADDRESS


def sync(node, store):
    """Credit every deposit between the watermark and the confirmed head."""
    safe_head = node.block_number() - CONFIRMATIONS
    while store.cursor < safe_head:
        from_block = store.cursor + 1
        to_block = min(from_block + RANGE_SPAN - 1, safe_head)
        logs = node.get_logs(from_block, to_block)
        for log in logs:
            if is_deposit(log):
                store.credit(log, ACCOUNT_BY_DEPOSIT_ADDRESS[log["to"]])
        store.cursor = to_block
    return store
