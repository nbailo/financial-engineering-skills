"""In-memory stand-in for the processor's modification API.

A capture and a refund are both modifications against a payment, named by the processor's own
pspReference. The API is asynchronous. A refund request comes back accepted straight away with
status "received", and the money leaves the merchant account when the nightly run settles that
modification. The API does not compare the request against what is left on the payment, so a
merchant that asks for more than it captured gets exactly that, and reads about it later on the
settlement report.

The real call is a round trip over the wire and takes a few hundred milliseconds: long enough
that a second agent can press refund on the same order while the first request is still open.
This stand-in answers out of memory, so it holds its own state under a lock rather than the
wire's.
"""

import threading


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
        self._api_lock = threading.Lock()

    def seed_capture(self, psp_reference, captured, currency="EUR"):
        self.payments[psp_reference] = Payment(psp_reference, captured, currency)

    def captured_amount(self, psp_reference):
        """What the processor says it took off the shopper."""
        return self.payments[psp_reference].captured

    def list_modifications(self, psp_reference):
        """Every modification the processor holds against this payment, settled or not."""
        with self._api_lock:
            return [dict(m) for m in self.payments[psp_reference].modifications]

    def refund(self, psp_reference, amount, reference):
        """Accept a refund request. The same reference twice returns the first modification."""
        with self._api_lock:
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
        with self._api_lock:
            for payment in self.payments.values():
                for m in payment.modifications:
                    if m["type"] == "refund" and m["status"] == "received":
                        m["status"] = "settled"
                        self.merchant_paid_out += m["amount"]
