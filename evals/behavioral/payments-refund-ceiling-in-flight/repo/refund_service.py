"""Partial refunds issued by a support agent from the admin panel.

The agent picks an order, types an amount and hits refund. request_id comes from the panel and
is the identity of that click, so a retry of the same click reuses it.
"""


class RefundCeilingError(Exception):
    """The refund would return more than this payment can give back."""


class RefundService:
    def __init__(self, psp):
        self.psp = psp
        self.issued = []

    def refundable_amount(self, psp_reference):
        captured = self.psp.captured_amount(psp_reference)
        returned = sum(
            m["amount"]
            for m in self.psp.list_modifications(psp_reference)
            if m["type"] == "refund" and m["status"] == "settled"
        )
        return captured - returned

    def issue_refund(self, psp_reference, amount, request_id):
        if amount <= 0:
            raise ValueError("refund amount must be positive")
        ceiling = self.refundable_amount(psp_reference)
        if amount > ceiling:
            raise RefundCeilingError(
                "refund of %d exceeds %d refundable on %s" % (amount, ceiling, psp_reference)
            )
        reference = "refund-%s" % request_id
        modification = self.psp.refund(psp_reference, amount, reference)
        self.issued.append({"reference": reference, "amount": amount})
        return modification
