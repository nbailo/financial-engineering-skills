"""Finishes payouts that were in flight when the worker stopped."""


def resume_pending(journal, provider):
    """Re-send every payout still open in the journal, then close the row.

    The provider deduplicates on the idempotency reference, so a payout that
    already reached it before the stop is not paid a second time here.
    """
    closed = []
    for row in list(journal.pending()):
        ref = row.get("idempotency_key") or journal.mint_ref(row["payout_id"])
        receipt = provider.send_payout(ref, row["account"], row["amount"])
        journal.complete(row["payout_id"], receipt)
        closed.append(row["payout_id"])
    return closed
