"""The two answers a payout provider can give that are not a receipt.

They are not interchangeable and the difference is the whole of the recovery
problem. A decline is the provider's final word: no money moved, and sending the
same request again will not make any move. An outage is silence: the request may
have been booked a moment before the connection went away, so the payout is
still open and whoever finishes it has to finish the attempt that was already
made rather than start a new one.
"""


class PayoutDeclined(Exception):
    """Definitive refusal. The payout is closed and must never be re-sent."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class ProviderUnavailable(Exception):
    """The call did not complete. Whether the provider booked it is unknown."""
