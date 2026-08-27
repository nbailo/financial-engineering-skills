"""Sends vendor payouts through the provider client."""

from provider import PayoutDeclined


class PayoutWorker:
    def __init__(self, journal, provider):
        self.journal = journal
        self.provider = provider

    def submit(self, payout_id, account, amount):
        """Commit the intent, call the provider, then record the outcome.

        A stop between the call and the outcome write leaves the row open and
        resume_pending() finishes it on the next boot. A decline is the provider's
        final answer, so it closes the row here and the resume path never sees it.
        """
        row = self.journal.begin(payout_id, account, amount)
        try:
            receipt = self.provider.send_payout(
                row["idempotency_ref"], row["account"], row["amount"])
        except PayoutDeclined as declined:
            self.journal.decline(payout_id, declined.code)
            raise
        self.journal.complete(payout_id, receipt)
        return receipt
