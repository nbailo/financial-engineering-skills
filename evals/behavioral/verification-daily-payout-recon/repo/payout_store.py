"""The payout rows our own service writes, one per transfer we believe we made.

Stand-in for the payouts table. The transfer id is the identifier the processor
returned when the payout was created, so it is the processor's own key, not ours,
and nothing here enforces that it appears only once: an outcome write that lands
twice leaves two rows carrying one transfer id. This table stores what was written
and does not decide what any of it means.
"""

from decimal import Decimal


class PayoutStore:
    def __init__(self):
        self._rows = []

    def record(self, transfer_id, vendor, amount, book_date):
        self._rows.append({
            "transfer_id": transfer_id,
            "vendor": vendor,
            "amount": Decimal(amount),
            "book_date": book_date,
        })

    def rows_for(self, book_date):
        return [row for row in self._rows if row["book_date"] == book_date]
