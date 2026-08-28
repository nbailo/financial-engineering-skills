"""Partial refunds issued by a support agent from the admin panel.

The agent picks an order, types an amount and hits refund. request_id comes from the panel and is
the identity of that click, so a retry of the same click reuses it.

A worker builds one of these for itself over its own connection to the processor and over the
shop's refund book, which every worker shares.
"""


class RefundCeilingError(Exception):
    """The refund would return more than this payment can give back."""


class RefundService:
    def __init__(self, client, book):
        self.client = client
        self.book = book

    def refundable_amount(self, psp_reference):
        """What the panel shows the agent as still refundable on this order."""
        promised = self.book.committed(psp_reference)
        captured = self.client.captured_amount(psp_reference)
        return captured - promised

    def issue_refund(self, psp_reference, amount, request_id):
        if amount <= 0:
            raise ValueError("refund amount must be positive")
        reference = "refund-%s" % request_id
        already = self.book.find(psp_reference, request_id)
        if already is not None:
            return already["modification"]
        remaining = self.refundable_amount(psp_reference)
        if amount > remaining:
            raise RefundCeilingError(
                "refund of %d exceeds %d refundable on %s" % (amount, remaining, psp_reference)
            )
        modification = self.client.refund(psp_reference, amount, reference)
        self.book.record(psp_reference, request_id, amount, modification)
        return modification
