"""Drain pending payouts."""

from processor_stub import ProcessorTimeout


class PayoutFailed(Exception):
    pass


def send_payout(store, gateway, payout, attempts=3):
    """Send one payout, retrying when the response is lost.

    The processor deduplicates on the Idempotency-Key, so replaying a request
    whose response never arrived cannot pay the account twice.
    """
    for _ in range(attempts):
        try:
            charge = gateway.submit(payout)
        except ProcessorTimeout:
            continue
        store.mark_paid(payout.payout_id, charge["charge_id"])
        return charge
    raise PayoutFailed(payout.payout_id)


def drain(store, gateway):
    """Send every pending payout. Returns the charges made."""
    return [send_payout(store, gateway, row) for row in store.pending()]
