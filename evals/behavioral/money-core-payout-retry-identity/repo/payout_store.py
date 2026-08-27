"""Payout rows. A row is written before anything leaves the process."""

from dataclasses import dataclass


@dataclass
class Payout:
    payout_id: str
    account: str
    amount_minor: int
    idempotency_key: str
    status: str = "PENDING"
    charge_id: str = ""


class PayoutStore:
    def __init__(self):
        self._rows = {}
        self._next = 1

    def create(self, account, amount_minor):
        """Record the decision to pay, with the identity that decision keeps."""
        payout_id = "po-%04d" % self._next
        self._next += 1
        row = Payout(payout_id=payout_id, account=account, amount_minor=amount_minor,
                     idempotency_key="payout-" + payout_id)
        self._rows[payout_id] = row
        return row

    def mark_paid(self, payout_id, charge_id):
        row = self._rows[payout_id]
        row.status = "PAID"
        row.charge_id = charge_id
        return row

    def pending(self):
        return [r for r in self._rows.values() if r.status == "PENDING"]
