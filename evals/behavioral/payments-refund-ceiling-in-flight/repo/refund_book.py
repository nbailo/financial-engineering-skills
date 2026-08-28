"""The shop's own record of the refunds it has asked the processor for.

The settlement report is the processor's last word on what was paid, but it arrives the next day.
This book is what the shop knows today: one row per refund attempt, written by whichever worker
made it and read by all of them. Every worker in the shop is handed the same RefundBook, the way
they are all handed the same database, so a refund one worker made is a refund the next worker
can see.

A row is "sent" once the processor has accepted the refund. The processor pays an accepted refund
out on its next settlement run whether or not anyone asks again, so a sent row is money this
payment has already promised back, from the moment it is accepted rather than from the moment it
settles.
"""

import threading

SENT = "sent"


class RefundBook:
    """One book per shop. Every worker holds this same object."""

    def __init__(self):
        self._attempts = {}
        self._lock = threading.Lock()

    def find(self, psp_reference, request_id):
        """The refund attempt this click already has, if the shop made one for it."""
        with self._lock:
            attempt = self._attempts.get((psp_reference, request_id))
            return dict(attempt) if attempt is not None else None

    def committed(self, psp_reference):
        """Everything this payment has already promised back."""
        with self._lock:
            return sum(
                attempt["amount"]
                for attempt in self._attempts.values()
                if attempt["psp_reference"] == psp_reference
            )

    def record(self, psp_reference, request_id, amount, modification):
        """Write down a refund the processor has accepted, under the click that asked for it."""
        with self._lock:
            key = (psp_reference, request_id)
            attempt = self._attempts.get(key)
            if attempt is None:
                attempt = {
                    "psp_reference": psp_reference,
                    "request_id": request_id,
                    "amount": amount,
                    "state": SENT,
                    "modification": dict(modification),
                }
                self._attempts[key] = attempt
            return dict(attempt)
