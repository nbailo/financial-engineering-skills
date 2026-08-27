"""Sends vendor payouts through the provider client."""


class PayoutWorker:
    def __init__(self, journal, provider):
        self.journal = journal
        self.provider = provider

    def submit(self, payout_id, account, amount):
        """Commit the intent, call the provider, then record the outcome.

        A stop between the call and the outcome write leaves the row pending and
        resume_pending() finishes it on the next boot.
        """
        row = self.journal.begin(payout_id, account, amount)
        receipt = self.provider.send_payout(
            row["idempotency_ref"], row["account"], row["amount"])
        self.journal.complete(payout_id, receipt)
        return receipt
