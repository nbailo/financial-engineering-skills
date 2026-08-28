"""The safe path: one invoice, one charge, one settlement, whatever the network does.

A SafeFlow is a worker, bound at construction to one processor authority:
provider, account, region. It holds no payment state and no dedupe state; the intents, the
open breaks and the effect identities live in the shared `Store` and its ledger, so two
workers over one store agree about what has already happened. No method takes a processor,
so no retry can arrive through a different one, and an intent pins the authority it was
opened for: a worker bound elsewhere is refused before anything is sent.

The ordering is the point. The identity is committed before the send, so a lost answer has
something to ask about. An intent that already exists is asked about at its pinned authority
before anything is resent, and a replay happens only where that authority's contract proves
the request never arrived - the exact stored request, under the same key. Every answer is
checked before it is believed: its shape first, because a `None`, a list or a number is not a
response and reading a field out of one raises instead of refusing; then its names, because
each becomes an effect id, a ledger reference or an attribution field; then what it describes,
because a capture must carry the key, invoice, amount, currency and authority scope this
worker asked about, and a settlement must name the exact charge it was asked about. A
misrouted or malformed answer posts nothing.

What this example does NOT have: a durable queue for an event that arrives before the thing
it describes. An event for a charge this store has not captured, or for a charge the
processor has not settled, is REFUSED and reported to the caller to alert on. It is not
parked and not retried later. A real integration needs that queue; saying so is cheaper than
implying one exists.
"""
from __future__ import annotations

from fake_processor import AmbiguousTimeout, ChargeRequest
from ledger import (Attribution, CASH, DIFFERENCES, FEES, Posting, RECEIVABLE, REVENUE,
                    content_fingerprint, reconcile)
from money import (InvalidIdentifier, validated_amount, validated_component,
                   validated_currency, validated_identifier)
from store import BreakAlreadyClosed, UnknownBreak

SETTLEMENT_EVENT = "payment.settled"


def claimed_scope(value) -> tuple:
    """An authority claim exactly as it arrived. Anything that is not a sequence of labels
    is not a scope at all, and the empty tuple it becomes here matches no worker's, so it is
    refused by the same comparison instead of raising inside one."""
    return tuple(value) if isinstance(value, (list, tuple)) else ()


class InjectedFailure(BaseException):
    """Stops the process between two writes. A BaseException, not an Exception, so no
    `except Exception` anywhere can swallow it and turn a recovery test into a pass."""


class AuthorityMismatch(ValueError):
    """This worker is bound to one authority and the operation is pinned to another."""


class ResponseMismatch(ValueError):
    """An answer that does not describe the operation that was asked about. Posts nothing."""


def intent_key(invoice_id: str, attempt: int = 1) -> str:
    """One identity per economic decision, stable across every retry of it, derived from the
    decision itself: never from the request body, never from a clock. The authority is not
    in the key, so pointing a retry at another account cannot quietly mint a second identity;
    it collides with the pinned one and is refused."""
    return f"pay:{invoice_id}:{attempt}"


class SafeFlow:
    def __init__(self, store, processor) -> None:
        self.store = store
        self.processor = processor
        self.authority = processor.authority
        self.breaks: tuple = ()

    # ---- paying ------------------------------------------------------------------
    def pay_invoice(self, invoice_id, amount_minor, currency, fail_after_send=None,
                    seam=None, after_entry=None):
        """Validate, pin, ask, then send. Nothing reaches the wire before all four."""
        validated_amount(amount_minor)
        validated_currency(currency)
        key = intent_key(invoice_id)
        known = self.store.get_intent(key)
        if known is not None:
            self._bound(known)          # pinned elsewhere: refused before any send
        intent = self.store.open_intent(key, invoice_id=invoice_id,
                                        amount_minor=amount_minor, currency=currency,
                                        authority=self.authority)
        if intent.state == "CAPTURED":
            return intent               # the decision was already carried out
        request = self.stored_request(key)      # built from the intent, not from the caller
        charge = None
        if known is not None:
            # This intent was already on file, so a send may already have happened. Ask the
            # pinned authority about it before anything goes out a second time.
            charge = self.processor.lookup(request.idempotency_key)
        if charge is None:
            try:
                charge = self.processor.charge(request)
            except AmbiguousTimeout:
                charge = self._resolve(request)
                if charge is None:
                    return self.store.get_intent(key)   # PENDING; recovery asks again
        if fail_after_send is not None:
            fail_after_send()          # the external effect landed, the local write has not
        self._capture(intent, charge, seam=seam, after_entry=after_entry)
        return self.store.get_intent(key)

    def effect_id(self, kind: str, external_id: str) -> str:
        """Our own dedupe identity for one external effect.

        The counterparty's id is only unique inside the counterparty. Two backends both
        call their first charge `ch_1`, so an effect id built from that alone collides in
        one book: the second capture is a different effect with a different fingerprint,
        and the ledger would refuse it for ever. The authority is part of the identity.
        """
        return f"{kind}:{self.authority.describe()}:{external_id}"

    def stored_request(self, key: str) -> ChargeRequest:
        """Every send and every replay is built from the persisted intent, addressed to the
        persisted authority. A caller that passes different arguments the second time cannot
        reach the wire."""
        intent = self.store.get_intent(key)
        return ChargeRequest(intent.key, intent.invoice_id, intent.amount_minor,
                             intent.currency, intent.authority)

    def _bound(self, intent) -> None:
        if intent.authority != self.authority.fields():
            raise AuthorityMismatch(
                f"{intent.key} is pinned to {'/'.join(intent.authority)} and this worker is "
                f"bound to {self.authority.describe()}")

    def _resolve(self, request):
        """UNKNOWN has two shapes and both start with a question.

        The effect already happened: the lookup finds it. The effect is definitely absent:
        this processor records a charge before it can lose an answer, so a lookup miss means
        the request never arrived, and only then is a replay safe - the exact stored request,
        the same key, identical economic fields, the same authority. Query first, then
        follow the contract.
        """
        charge = self.processor.lookup(request.idempotency_key)
        if charge is not None:
            return charge
        try:
            return self.processor.charge(request)
        except AmbiguousTimeout:
            return self.processor.lookup(request.idempotency_key)

    def _validated_charge(self, charge, intent) -> dict:
        """An answer is believed only if it is an answer, is well formed, and describes the
        operation that was asked about - in that order.

        The shape comes first because everything after it reads fields: a `None`, a list, a
        string or a number is not a response, and `.get` on one raises out of the caller
        instead of refusing. The names come next because `charge_id` becomes an effect id,
        a ledger reference and an attribution field, and a list or a number in any of those
        would compare equal to something it is not. Only then is the answer compared to the
        question, and every failure is one `ResponseMismatch`, before any local posting.
        """
        if not isinstance(charge, dict):
            raise ResponseMismatch(f"{intent.key} asked about a capture and the answer is "
                                   f"{charge!r}, which is not a response; nothing is posted")
        try:
            key = validated_identifier(charge.get("idempotency_key"), "idempotency_key")
            invoice_id = validated_identifier(charge.get("invoice_id"), "invoice_id")
            validated_identifier(charge.get("charge_id"), "charge_id")
            amount = validated_amount(charge.get("amount_minor"))
            currency = validated_currency(charge.get("currency"))
        except ValueError as exc:
            raise ResponseMismatch(f"{intent.key} got an answer that is not a well-formed "
                                   f"capture: {exc}; nothing is posted") from exc
        wanted = (intent.key, intent.invoice_id, intent.amount_minor, intent.currency,
                  self.authority.fields())
        got = (key, invoice_id, amount, currency, claimed_scope(charge.get("authority")))
        if got != wanted:
            raise ResponseMismatch(f"{intent.key} asked about {wanted} and the answer "
                                   f"describes {got}; nothing is posted")
        return charge

    def _capture(self, intent, charge, seam=None, after_entry=None):
        """One append records the capture entry and its effect identity; the intent
        transition follows and is redone by any retry that finds the effect already there."""
        self._bound(intent)
        charge = self._validated_charge(charge, intent)
        return self.store.commit_once(
            self.effect_id("capture", charge["charge_id"]),
            (charge["charge_id"], charge["idempotency_key"], charge["invoice_id"],
             charge["amount_minor"], charge["currency"]) + self.authority.fields(),
            kind="charge_captured", reference=charge["charge_id"],
            attribution=Attribution(charge_id=charge["charge_id"],
                                    currency=charge["currency"]),
            postings=[Posting(RECEIVABLE, charge["currency"], charge["amount_minor"]),
                      Posting(REVENUE, charge["currency"], -charge["amount_minor"])],
            memo=f"invoice {charge['invoice_id']} captured",
            intent_updates={intent.key: {"state": "CAPTURED",
                                         "charge_id": charge["charge_id"]}},
            seam=seam, after_entry=after_entry)

    def recover(self, seam=None) -> tuple:
        """A new worker over the same store: finish every intent with no local completion by
        asking about its key at the authority it is pinned to. Moves no money."""
        done = []
        for intent in self.store.intents():
            if intent.state == "CAPTURED" or intent.authority != self.authority.fields():
                continue
            charge = self.processor.lookup(intent.key)
            if charge is None:
                continue
            if self._capture(intent, charge, seam=seam).applied:
                done.append(charge["charge_id"])
        return tuple(done)

    # ---- settlement --------------------------------------------------------------
    def handle_webhook(self, envelope, seam=None, fault=None) -> str:
        """Apply one settlement. The envelope is a notification, not authority. Two things
        are read out of it: the charge identity, which is then verified against the
        processor, and a currency claim that can only ever cause a refusal. Every posted
        figure comes from the processor's own answer about that charge and its settlement,
        and every answer is checked to name the thing that was asked about before it is
        believed. A repeat is answered, never raised on; anything unexpected posts nothing.
        """
        # The envelope arrives from outside and can be anything at all. A handler that
        # raises on a partner's payload stops the delivery loop, and every settlement behind
        # the bad one goes unrecorded; reconcile() is total for the same reason.
        if not isinstance(envelope, dict):
            return "MALFORMED_EVENT"
        if envelope.get("type") != SETTLEMENT_EVENT:
            return "UNEXPECTED_TYPE"
        data = envelope.get("data")
        if not isinstance(data, dict):
            return "MALFORMED_EVENT"
        claimed = data.get("charge_id")
        if not isinstance(claimed, str) or not claimed.strip():
            return "UNIDENTIFIED_CHARGE"    # never a dict key, never a lookup argument
        charge = self.processor.get_charge(claimed)
        if charge is None:
            return "UNKNOWN_CHARGE"
        # The processor is trusted for the figures, not for the shape of its own answers: a
        # proxy, a decoder or a stub can hand back something that is not a response, or a
        # response whose identity and money are not a name and a number. Refused by
        # answering, like every other bad delivery, because raising here stops the loop.
        if not isinstance(charge, dict):
            return "MALFORMED_CHARGE"
        try:
            charge_id = validated_identifier(charge.get("charge_id"), "charge_id")
            validated_amount(charge.get("amount_minor"))
            currency = validated_currency(charge.get("currency"))
        except ValueError:
            return "MALFORMED_CHARGE"
        if charge_id != claimed:
            return "MISROUTED_CHARGE"   # an answer about some other charge posts nothing
        if data.get("currency") not in (None, currency):
            return "CURRENCY_MISMATCH"  # a notification that disagrees about the currency
        intent = self.store.intent_for_charge(charge_id)
        if intent is None or intent.state != "CAPTURED":
            return "NOT_CAPTURED_HERE"  # refused and alerted on, not queued for later
        if intent.authority != self.authority.fields():
            return "WRONG_AUTHORITY"
        if claimed_scope(charge.get("authority")) != self.authority.fields():
            return "WRONG_AUTHORITY"    # the charge answers for a scope this worker is not
        settled = self.processor.settlement_for_charge(charge_id)
        if settled is None:
            return "NOT_SETTLED_YET"    # refused and alerted on, not queued for later
        refusal = self._settlement_refusal(settled, charge, intent)
        if refusal:
            return refusal
        fields = (settled["settlement_id"], settled["charge_id"], settled["batch_id"],
                  settled["currency"], settled["gross_minor"], settled["fee_minor"],
                  settled["net_minor"]) + self.authority.fields()
        applied = self.store.commit_once(
            self.effect_id("settlement", settled["settlement_id"]), fields,
            kind="settlement",
            reference=settled["settlement_id"],
            attribution=Attribution(settled["settlement_id"], settled["charge_id"],
                                    settled["batch_id"], settled["currency"]),
            postings=[Posting(CASH, settled["currency"], settled["net_minor"]),
                      Posting(FEES, settled["currency"], settled["fee_minor"]),
                      Posting(RECEIVABLE, settled["currency"], -settled["gross_minor"])],
            memo=f"invoice {intent.invoice_id} settled", fault=fault, seam=seam)
        return "APPLIED" if applied.applied else "DUPLICATE"

    def _settlement_refusal(self, settled, charge, intent) -> str:
        """The settlement has to be a response, has to name itself, its batch and the exact
        charge that was asked about, in the scope the charge, the intent and this worker all
        agree on, with components that are exact integers and add up.

        The names are checked before anything is compared, because `settlement_id` becomes
        an effect id and a ledger reference and `batch_id` becomes an attribution field: a
        list, a number or a blank string in any of them names nothing, and a settlement that
        cannot name itself is `UNIDENTIFIED_SETTLEMENT` rather than an exception in a caller.

        Gross is strictly positive: a settlement of nothing is not a settlement. Fee and net
        are only non-negative, because a fee can consume the whole gross and leave a net of
        zero, and refusing that would refuse a true answer. The two still have to reconstruct
        the gross, and a fee can never be larger than the gross it came out of. Finally the
        gross has to be the amount this charge captured and the amount the intent decided:
        one charge settles in full here, so any other figure is about something else.
        """
        if not isinstance(settled, dict):
            return "UNIDENTIFIED_SETTLEMENT"
        try:
            validated_identifier(settled.get("settlement_id"), "settlement_id")
            validated_identifier(settled.get("batch_id"), "batch_id")
            settled_charge = validated_identifier(settled.get("charge_id"), "charge_id")
        except InvalidIdentifier:
            return "UNIDENTIFIED_SETTLEMENT"
        if settled_charge != charge["charge_id"]:
            return "MISROUTED_SETTLEMENT"
        scope = claimed_scope(settled.get("authority"))
        if (scope != claimed_scope(charge.get("authority"))
                or scope != tuple(intent.authority) or scope != self.authority.fields()):
            return "WRONG_AUTHORITY"
        if settled.get("currency") != intent.currency:
            return "CURRENCY_MISMATCH"
        try:
            gross = validated_amount(settled.get("gross_minor"), "gross_minor")
            fee = validated_component(settled.get("fee_minor"), "fee_minor")
            net = validated_component(settled.get("net_minor"), "net_minor")
            validated_currency(settled["currency"])
        except ValueError:
            return "NOT_AN_AMOUNT"
        if fee > gross or gross - fee != net:
            return "SETTLEMENT_ARITHMETIC"
        # In this example one charge settles in full, so the gross is not merely a
        # well-formed number: it is THIS charge's amount, and the intent's. A settlement
        # that is coherent with itself and describes a different sum is still not ours.
        if gross != charge["amount_minor"] or gross != intent.amount_minor:
            return "SETTLEMENT_AMOUNT"
        return ""

    # ---- reconciliation ----------------------------------------------------------
    def run_reconciliation(self, report) -> tuple:
        """Detect, report, and record what is open in the shared store so a correction has
        something real to close. Nothing here posts, and nothing here closes a break."""
        self.breaks = self.store.record_breaks(reconcile(report, self.store.ledger))
        return self.breaks

    def apply_correction(self, brk, approved_by: str):
        """Close one real, open break by adding an entry, never by editing one.

        The effect identity is the break's identity, so replaying an approved correction is
        idempotent, a fabricated break has nothing to close, and a later, genuinely different
        break on the same settlement is a different identity that can be corrected on its
        own. A duplicate_entry break is refused outright: see below.
        """
        if not approved_by:
            raise ValueError("a correction with no named approver is not a correction")
        if not brk.deltas:
            raise ValueError(f"a {brk.kind} break does not close by a posting")
        if brk.kind == "duplicate_entry":
            # Two entries claim this identity and nothing here knows which one was the
            # mistake. Netting the difference would leave both in the book and call it
            # settled. The repair is an explicit reversal naming the exact duplicate entry,
            # which is an operator's decision and not this function's to take.
            raise ValueError(
                f"{brk.break_id} is a duplicate_entry: two entries claim "
                f"{brk.settlement_id}, so it closes by an explicit reversal naming the "
                f"exact duplicate entry, never by a correction that nets the difference")
        record = self.store.break_record(brk.break_id)
        if record is None or record.brk != brk:
            raise UnknownBreak(f"{brk.break_id} is not a break this reconciliation "
                               f"recorded; there is nothing to close")
        original = self.store.ledger.entry_for_settlement(brk.settlement_id)
        if original is None:
            raise UnknownBreak(f"{brk.settlement_id} has no settlement entry to correct")
        effect_id = f"correction:{brk.break_id}"
        fingerprint = ((brk.break_id,) + brk.attribution.fields() + brk.deltas
                       + (approved_by,))
        moves = {CASH: -brk.delta("net"), FEES: -brk.delta("fee"),
                 RECEIVABLE: brk.delta("gross")}
        postings = [Posting(a, brk.currency, v) for a, v in sorted(moves.items()) if v]
        residue = -sum(moves.values())
        if residue:
            postings.append(Posting(DIFFERENCES, brk.currency, residue))
        if record.state == "CLOSED":
            # A replay is the same correction only if the WHOLE entry it would write is the
            # same one, which is what the ledger stores against the effect identity.
            existing = self.store.effect(effect_id)
            wanted = content_fingerprint(fingerprint, "correction", brk.settlement_id,
                                         postings, brk.attribution, original.entry_id,
                                         brk.break_id)
            if existing is not None and existing.fields == wanted:
                return existing         # the same approved correction, replayed; no move
            raise BreakAlreadyClosed(f"{brk.break_id} was closed by {record.approved_by} "
                                     f"in {record.entry_id}")
        applied = self.store.commit_once(
            effect_id, fingerprint, kind="correction", reference=brk.settlement_id,
            attribution=brk.attribution, postings=postings, break_id=brk.break_id,
            memo=f"{brk.describe()}; approved by {approved_by}", corrects=original.entry_id)
        self.store.close_break(brk.break_id, applied.entry_id, approved_by)
        self.breaks = tuple(b for b in self.breaks if b.break_id != brk.break_id)
        return applied
