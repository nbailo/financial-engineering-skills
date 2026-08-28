"""In-memory stand-in for the payment processor's modification API.

A capture and a refund are both modifications against a payment, named by the processor's own
pspReference. The API is asynchronous. A refund request comes back accepted straight away with
status "received", and the money leaves the merchant account when the nightly run settles that
modification. The API does not compare the request against what is left on the payment, so a
merchant that asks for more than it captured gets exactly that, and reads about it later on the
settlement report.

There are two objects here because the real system has two. `Processor` is the processor's own
record: it lives at the processor, there is one of it, and every worker in the shop sees the same
one. `ProcessorClient` is the connection a worker opens to it, holding no record of its own. A
shop runs several workers - the admin panel, the webhook endpoint, a nightly job - and each of
them builds its own client:

    processor = Processor()                 # the processor's records, shared by everyone
    panel_client = ProcessorClient(processor)    # the admin panel's own connection
    webhook_client = ProcessorClient(processor)  # the webhook endpoint's own connection

The real call is a round trip over the wire and takes a few hundred milliseconds: long enough
that a second agent can press refund on the same order while the first request is still open.
That wait is spent in the connection, which is why `before_send` hangs off the client and not
off the processor.

A round trip has two legs and they fail differently. `before_send` runs on the outbound leg,
while the request is still inside the shop; a failure there is a PreSendRejected and the
processor never saw the request. `after_send` runs on the inbound leg, once the processor has
already done the work; a failure there - a timeout, a reset connection, an answer that never
arrives - is whatever the network raised, and it says nothing about whether the processor
accepted. The one thing that knows is the processor, asked about the merchant's own reference.
"""

import threading


class PreSendRejected(Exception):
    """A request the connection refused before it went out; nothing reached the processor.

    A client raises this only from the outbound leg. Any other failure out of a client call
    happens with the request already gone.
    """


class Payment:
    def __init__(self, psp_reference, captured, currency):
        self.psp_reference = psp_reference
        self.captured = captured
        self.currency = currency
        self.modifications = []


class Processor:
    """The processor's own record of every payment and every modification against it."""

    def __init__(self):
        self.payments = {}
        self.merchant_paid_out = 0
        self._seq = 0
        self._lock = threading.Lock()

    def seed_capture(self, psp_reference, captured, currency="EUR"):
        with self._lock:
            self.payments[psp_reference] = Payment(psp_reference, captured, currency)

    def captured_amount(self, psp_reference):
        """What the processor says it took off the shopper. It never changes after capture."""
        with self._lock:
            return self.payments[psp_reference].captured

    def currency_of(self, psp_reference):
        """The currency the payment was taken in. Like the capture, it never changes."""
        with self._lock:
            return self.payments[psp_reference].currency

    def list_modifications(self, psp_reference):
        """Every modification the processor holds against this payment, settled or not.

        This is where a merchant that lost an answer finds out what really happened: a refund it
        sent is in here under the reference it sent it with, whether or not the answer got home.
        """
        with self._lock:
            return [dict(m) for m in self.payments[psp_reference].modifications]

    def refund(self, psp_reference, amount, reference):
        """Accept a refund request. The same reference twice returns the first modification.

        `reference` is the merchant's own name for the refund and the only thing this API
        deduplicates on. It does not look at the amount, and it does not look at what is left on
        the payment: two refunds under two references are two refunds, and both will be paid.
        """
        with self._lock:
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
        with self._lock:
            for payment in self.payments.values():
                for m in payment.modifications:
                    if m["type"] == "refund" and m["status"] == "received":
                        m["status"] = "settled"
                        self.merchant_paid_out += m["amount"]


class ProcessorClient:
    """One worker's connection to the processor. Every worker builds its own.

    `before_send` is called with (operation, psp_reference) just before a request leaves for the
    processor, and is where latency instrumentation hangs. It is a property of this connection,
    not of the processor, because the round trip is this worker's wait and nobody else's.

    `after_send` is called with the same pair on the way back, after the processor has done the
    work and before this call returns. The two hooks are the two legs of the round trip, and a
    failure raised from one is not the same news as a failure raised from the other.
    """

    def __init__(self, processor, before_send=None, after_send=None):
        self._processor = processor
        self.before_send = before_send
        self.after_send = after_send

    def _wire(self, operation, psp_reference):
        """The outbound leg. Nothing has reached the processor by the time this returns."""
        hook = self.before_send
        if hook is not None:
            hook(operation, psp_reference)

    def _reply(self, operation, psp_reference):
        """The inbound leg. The processor has already done the work by the time this runs."""
        hook = self.after_send
        if hook is not None:
            hook(operation, psp_reference)

    def captured_amount(self, psp_reference):
        self._wire("captured_amount", psp_reference)
        captured = self._processor.captured_amount(psp_reference)
        self._reply("captured_amount", psp_reference)
        return captured

    def currency_of(self, psp_reference):
        self._wire("currency_of", psp_reference)
        currency = self._processor.currency_of(psp_reference)
        self._reply("currency_of", psp_reference)
        return currency

    def list_modifications(self, psp_reference):
        self._wire("list_modifications", psp_reference)
        modifications = self._processor.list_modifications(psp_reference)
        self._reply("list_modifications", psp_reference)
        return modifications

    def refund(self, psp_reference, amount, reference):
        self._wire("refund", psp_reference)
        modification = self._processor.refund(psp_reference, amount, reference)
        self._reply("refund", psp_reference)
        return modification
