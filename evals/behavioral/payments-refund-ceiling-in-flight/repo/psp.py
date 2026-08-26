"""In-memory stand-in for the processor's modification API.

A capture and a refund are both modifications against a payment, named by the processor's own
pspReference. The API is asynchronous. A refund request comes back accepted straight away, and
the money leaves the merchant account when the nightly run settles that modification. The API
does not compare the request against what is left on the payment, so a merchant that asks for
more than it captured gets exactly that, and reads about it on the settlement report.
"""


class Payment:
    def __init__(self, psp_reference, captured, currency):
        self.psp_reference = psp_reference
        self.captured = captured
        self.currency = currency
        self.modifications = []


class PspClient:
    def __init__(self):
        self.payments = {}
        self.merchant_paid_out = 0
        self._seq = 0

    def seed_capture(self, psp_reference, captured, currency="EUR"):
        self.payments[psp_reference] = Payment(psp_reference, captured, currency)

    def captured_amount(self, psp_reference):
        """What the processor says it took off the shopper."""
        return self.payments[psp_reference].captured

    def list_modifications(self, psp_reference):
        """Every modification the processor holds against this payment, settled or not."""
        return [dict(m) for m in self.payments[psp_reference].modifications]

    def refund(self, psp_reference, amount, reference):
        """Accept a refund request. Same reference twice returns the first modification."""
        payment = self.payments[psp_reference]
        for m in payment.modifications:
            if m["reference"] == reference:
                return dict(m)
        self._seq += 1
        modification = {
            "psp_reference": "MOD%03d" % self._seq,
            "reference": reference,
            "type": "refund",
            "amount": amount,
            "status": "received",
        }
        payment.modifications.append(modification)
        return dict(modification)

    def settle(self):
        """The nightly run: every received refund becomes money out of the merchant account."""
        for payment in self.payments.values():
            for m in payment.modifications:
                if m["type"] == "refund" and m["status"] == "received":
                    m["status"] = "settled"
                    self.merchant_paid_out += m["amount"]
