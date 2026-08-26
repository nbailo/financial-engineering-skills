"""The payout journal: the intent row committed before the provider is called.

Stand-in for the payouts table. The row is written first so that a worker which
stops between the call and the outcome write leaves something for the resume path
to find.
"""

from decimal import Decimal

PENDING = "pending"
DONE = "done"


class Journal:
    def __init__(self):
        self._rows = {}
        self._attempts = {}

    def mint_ref(self, payout_id):
        """A fresh idempotency reference, one per attempt at one payout."""
        n = self._attempts.get(payout_id, 0) + 1
        self._attempts[payout_id] = n
        return "ik-%s-%d" % (payout_id, n)

    def begin(self, payout_id, account, amount):
        """Commit the intent, including the reference the provider will see."""
        row = {
            "payout_id": payout_id,
            "account": account,
            "amount": Decimal(amount),
            "idempotency_ref": self.mint_ref(payout_id),
            "status": PENDING,
            "receipt": None,
        }
        self._rows[payout_id] = row
        return row

    def complete(self, payout_id, receipt):
        row = self._rows[payout_id]
        row["receipt"] = receipt
        row["status"] = DONE

    def pending(self):
        return [r for r in self._rows.values() if r["status"] == PENDING]

    def row(self, payout_id):
        return self._rows[payout_id]
