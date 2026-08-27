"""The payout rows our own service writes, one per transfer we believe we made.

Stand-in for the payouts table. The transfer id is the identifier the processor
returned when the payout was created, so it is the processor's own key, not ours.
"""

from decimal import Decimal


class PayoutStore:
    def __init__(self):
        self._rows = {}

    def record(self, transfer_id, vendor, amount, book_date):
        self._rows[transfer_id] = {
            "transfer_id": transfer_id,
            "vendor": vendor,
            "amount": Decimal(amount),
            "book_date": book_date,
        }

    def rows_for(self, book_date):
        return [r for r in self._rows.values() if r["book_date"] == book_date]
