"""The counter-example. Do not copy any of it.

Six design notes, all false, and none of them raises. Each is refuted by a named test in
tests/test_unsafe_flow_is_wrong.py: (1) "a timeout means it did not go through, so send it
again under a fresh key"; (2) "nothing durable is needed before the call"; (3) "every
delivery has an id, so dedupe on the delivery id"; (4) "dedupe state lives on the worker",
so two workers share nothing; (5) "the webhook body says what the money was, so post from
it"; (6) "a break is a bookkeeping gap: post it to suspense". The attempt counter stands in
for the uuid4 note 1 would really mint: a counter so the tests stay deterministic, on the
instance because nothing here is durable, which is note 2.

Note 3 and note 4 are written into the effect identity this flow hands the ledger:
`{worker}:delivery:{delivery_id}`. The ledger will refuse the same identity twice, and this
flow never produces the same identity twice - a redelivery carries a new delivery id, and a
second worker carries a different name. That is what "the dedupe key is whatever the worker
happened to have" costs.
"""
from __future__ import annotations

from fake_processor import AmbiguousTimeout, ChargeRequest
from ledger import CASH, FEES, Posting, RECEIVABLE, REVENUE, SUSPENSE, reconcile


class UnsafeFlow:
    def __init__(self, ledger, worker: str = "worker") -> None:
        self.ledger = ledger
        self.worker = worker
        self.charges: dict = {}
        self._seen_deliveries: set = set()
        self.attempt = 0

    def _next_key(self, invoice_id: str) -> str:
        self.attempt += 1
        return f"{invoice_id}-attempt{self.attempt}"

    def pay_invoice(self, processor, invoice_id, amount_minor, currency,
                    fail_after_send=None):
        # Nothing is committed before the call, so a lost answer has no identity to ask
        # about and a fresh key looks like the only way forward.
        request = ChargeRequest(self._next_key(invoice_id), invoice_id, amount_minor,
                                currency, processor.authority.fields())
        try:
            charge = processor.charge(request)
        except AmbiguousTimeout:
            charge = processor.charge(ChargeRequest(self._next_key(invoice_id), invoice_id,
                                                    amount_minor, currency,
                                                    processor.authority.fields()))
        if fail_after_send is not None:
            fail_after_send()
        self.charges[charge["charge_id"]] = charge
        self.ledger.commit_once(f"{self.worker}:capture:{charge['charge_id']}",
                                (charge["charge_id"],), kind="charge_captured",
                                reference=charge["charge_id"],
                                postings=[Posting(RECEIVABLE, currency, amount_minor),
                                          Posting(REVENUE, currency, -amount_minor)])
        return charge

    def handle_webhook(self, envelope, seam=None) -> str:
        # Deduped on the delivery, which a redelivery does not repeat, with no guard on the
        # charge's own state and no lock, so two threads read this set before either writes.
        # The type is never checked and the processor is never asked: every figure below is
        # lifted straight out of a notification body that anyone could have written.
        if envelope["delivery_id"] in self._seen_deliveries:
            return "DUPLICATE"
        if seam is not None:
            seam()
        d = envelope["data"]
        self._seen_deliveries.add(envelope["delivery_id"])
        self.ledger.commit_once(f"{self.worker}:delivery:{envelope['delivery_id']}",
                                (envelope["delivery_id"],), kind="settlement",
                                reference=d["settlement_id"],
                                postings=[Posting(CASH, d["currency"], d["net_minor"]),
                                          Posting(FEES, d["currency"], d["fee_minor"]),
                                          Posting(RECEIVABLE, d["currency"],
                                                  -d["amount_minor"])])
        return "APPLIED"

    def run_reconciliation(self, report) -> tuple:
        # Every break is plugged: cash and fee are forced to whatever the report says and
        # the balancing figure is dropped into suspense, so the accounts an operator reads
        # agree with the report and the difference sits where nobody looks.
        for brk in reconcile(report, self.ledger):
            if not brk.deltas:
                continue
            postings = [p for p in (Posting(CASH, brk.currency, -brk.delta("net")),
                                    Posting(FEES, brk.currency, -brk.delta("fee")))
                        if p.amount_minor]
            plug = -sum(p.amount_minor for p in postings)
            if plug:
                postings.append(Posting(SUSPENSE, brk.currency, plug))
            if postings:
                self.ledger.commit_once(f"{self.worker}:plug:{brk.settlement_id}",
                                        (brk.settlement_id,), kind="suspense",
                                        reference=brk.settlement_id, postings=postings)
        return reconcile(report, self.ledger)
