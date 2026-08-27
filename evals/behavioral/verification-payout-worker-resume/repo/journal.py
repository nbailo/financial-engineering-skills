"""The payout journal: the intent row committed before the provider is called.

Stand-in for the payouts table. The row is written first so that a worker which
stops between the call and the outcome write leaves something for the resume path
to find, and the reference the provider will be shown is part of that first
commit rather than something a later attempt makes up.

A worker that is killed keeps nothing in memory. The next boot constructs the
journal from the committed rows and that is all it knows, so anything a payout
needs in order to be finished safely has to live on the row itself.
"""

from decimal import Decimal

PENDING = "pending"
DONE = "done"
DECLINED = "declined"


class Journal:
    def __init__(self, rows=None):
        """Boot the table. `rows` is what a previous process left committed."""
        self._rows = {}
        self._attempts = {}
        for row in rows or ():
            committed = dict(row)
            payout_id = committed["payout_id"]
            self._rows[payout_id] = committed
            self._attempts[payout_id] = committed.get("attempt", 0)

    def mint_ref(self, payout_id):
        """A fresh idempotency reference, one per attempt at one payout.

        A fresh reference is a new payment as far as the provider is concerned,
        so this belongs to starting a payout and to nothing else.
        """
        n = self._attempts.get(payout_id, 0) + 1
        self._attempts[payout_id] = n
        return "ik-%s-%d" % (payout_id, n)

    def begin(self, payout_id, account, amount):
        """Commit the intent, including the reference the provider will see."""
        ref = self.mint_ref(payout_id)
        row = {
            "payout_id": payout_id,
            "account": account,
            "amount": Decimal(amount),
            "attempt": self._attempts[payout_id],
            "idempotency_ref": ref,
            "status": PENDING,
            "receipt": None,
            "decline_code": None,
        }
        self._rows[payout_id] = row
        return row

    def complete(self, payout_id, receipt):
        """Close the row against the receipt the provider handed back."""
        row = self._rows[payout_id]
        row["receipt"] = receipt
        row["status"] = DONE

    def decline(self, payout_id, code):
        """Close the row against the provider's refusal. Nothing was paid."""
        row = self._rows[payout_id]
        row["decline_code"] = code
        row["status"] = DECLINED

    def pending(self):
        return [r for r in self._rows.values() if r["status"] == PENDING]

    def rows(self):
        return list(self._rows.values())

    def row(self, payout_id):
        return self._rows[payout_id]
