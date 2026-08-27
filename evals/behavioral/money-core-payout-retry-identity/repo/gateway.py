"""Outbound side of the payout path."""

from keys import new_request_id


class Gateway:
    def __init__(self, processor):
        self.processor = processor

    def submit(self, payout):
        """Charge the processor for one payout and return the charge.

        The processor deduplicates on the Idempotency-Key it is given.
        """
        key = new_request_id()
        return self.processor.charge(key=key, account=payout.account,
                                     amount_minor=payout.amount_minor)
