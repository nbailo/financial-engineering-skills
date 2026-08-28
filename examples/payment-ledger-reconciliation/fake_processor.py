"""An in-process payment processor. No external or live processor, no network, no
credentials, no live mode.

A fake and not a mock, because an ambiguous timeout is only interesting if the request may
already have had its effect and an idempotency key is only interesting if something on the
other side remembers it: the counterparty has to keep state.

Its contract, which the safe flow relies on and states out loud: a charge is recorded before
an answer can be lost, so a lookup that misses means the request never arrived. `lossy_keys`
loses a request before recording it (definitely absent); `ambiguous_keys` records it and then
loses the answer (definitely present, unknown to the caller). The processor also owns the
settlement fact: `settle` creates it, `settlement_for_charge` answers about it by identity.

It has one authority scope and it is immutable. Provider, account and region are labels a
second, unrelated backend can carry too, so each instance also stamps a backend identity on
its scope: two clients over one processor share that identity, two independently created
processors never do. Every answer names the full scope, and a request addressed to another
one is refused rather than executed, because the same key at another backend is another
counterparty's state. Lookup and creation happen under one lock, so two callers racing on
one key cannot both create.

Not a model of any named processor. Amounts are exact integers in minor units, and the same
amount and currency rules that guard the ledger guard this boundary too.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field, replace
from pathlib import Path

from money import (AuthorityScope, validated_amount, validated_currency,
                   validated_identifier)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
# Fee in basis points of the gross, rounded up. The direction is stated because the residue
# belongs to somebody: a floor would hand it to the merchant, unrecorded.
FEE_BPS = 300
DEFAULT_AUTHORITY = AuthorityScope("fakeproc", "acct_main", "us-east-1")

# Backend identities are handed out in construction order, so a run is reproducible. They
# are never asserted on literally: what the tests compare is whether two scopes are the same
# identity, which is the only thing the identity is for.
_BACKEND_LOCK = threading.Lock()
_BACKENDS_MADE = 0


def _next_backend_id() -> str:
    global _BACKENDS_MADE
    with _BACKEND_LOCK:
        _BACKENDS_MADE += 1
        return f"be{_BACKENDS_MADE}"


class AmbiguousTimeout(RuntimeError):
    """UNKNOWN, never "did not happen": the charge may exist, so the only correct next step
    is to ask about the identity that was sent."""


class KeyMismatch(RuntimeError):
    """One key, two different economic fields. Refused rather than executed or replayed:
    the processor cannot tell which of the two was meant."""


class WrongAuthority(RuntimeError):
    """A request addressed to a provider, account or region this processor is not."""


@dataclass(frozen=True)
class ChargeRequest:
    idempotency_key: str
    invoice_id: str
    amount_minor: int
    currency: str
    authority: tuple = field(default_factory=DEFAULT_AUTHORITY.fields)

    def economic_fields(self) -> list:
        return [self.invoice_id, self.amount_minor, self.currency]


def fee_minor(amount_minor: int) -> int:
    """Ceiling division, so the fee never rounds down into the merchant's pocket."""
    return -(-amount_minor * FEE_BPS // 10_000)


class FakeProcessor:
    """Idempotent on the key, able to lose one answer per key, and able to lose one whole
    request per key before recording it."""

    def __init__(self, authority: AuthorityScope = DEFAULT_AUTHORITY, ambiguous_keys=(),
                 lossy_keys=()) -> None:
        # Every construction is a new backend and gets a new identity, whatever scope was
        # handed in. A caller chooses the labels; it cannot choose, supply or reuse the
        # identity, because that is the one field which says whose state this actually is.
        # Two workers over one backend share it by sharing this object, never by copying it.
        self._authority = replace(authority, backend_id=_next_backend_id())
        self._lock = threading.RLock()
        self._charges: dict = {}          # idempotency key -> charge
        self._by_charge_id: dict = {}     # charge id -> charge
        self._settlements: dict = {}      # charge id -> settlement
        self._ambiguous = set(ambiguous_keys)
        self._lossy = set(lossy_keys)
        self.lookups: list = []
        self.sends: list = []       # every key a caller actually put on the wire
        self.calls: list = []       # ("lookup"|"charge", key), in the order they happened

    @property
    def authority(self) -> AuthorityScope:
        """Read-only: an operation is bound to a scope that cannot be swapped underneath."""
        return self._authority

    def _addressed_to_me(self, authority) -> None:
        if tuple(authority) != self._authority.fields():
            raise WrongAuthority(f"this processor is {self._authority.describe()} and the "
                                 f"request names {'/'.join(str(f) for f in authority)}")

    def charge(self, req: ChargeRequest) -> dict:
        """Lookup and creation are one critical section, so two callers racing on one key
        cannot both decide it is absent."""
        self._addressed_to_me(req.authority)
        validated_identifier(req.idempotency_key, "idempotency_key")
        validated_identifier(req.invoice_id, "invoice_id")
        validated_amount(req.amount_minor)
        validated_currency(req.currency)
        with self._lock:
            self.sends.append(req.idempotency_key)
            self.calls.append(("charge", req.idempotency_key))
            if req.idempotency_key in self._lossy:
                # Lost on the way in: nothing is recorded, so a later lookup will miss and
                # the caller may replay this request under this key.
                self._lossy.discard(req.idempotency_key)
                raise AmbiguousTimeout(f"{req.idempotency_key} never arrived")
            existing = self._charges.get(req.idempotency_key)
            if existing is not None:
                if existing["economic_fields"] != req.economic_fields():
                    raise KeyMismatch(f"{req.idempotency_key} carried "
                                      f"{existing['economic_fields']} and now "
                                      f"{req.economic_fields()}")
                return dict(existing)
            fee = fee_minor(req.amount_minor)
            # Recorded before the answer can be lost, which is the whole point: the caller's
            # timeout says nothing about whether this happened.
            charge = {"charge_id": f"ch_{len(self._charges) + 1}",
                      "invoice_id": req.invoice_id,
                      "idempotency_key": req.idempotency_key, "currency": req.currency,
                      "amount_minor": req.amount_minor, "fee_minor": fee,
                      "net_minor": req.amount_minor - fee,
                      "authority": self._authority.fields(),
                      "economic_fields": req.economic_fields()}
            self._charges[req.idempotency_key] = charge
            self._by_charge_id[charge["charge_id"]] = charge
            if req.idempotency_key in self._ambiguous:
                self._ambiguous.discard(req.idempotency_key)
                raise AmbiguousTimeout(f"no answer came back for {req.idempotency_key}")
            return dict(charge)

    def lookup(self, idempotency_key: str):
        """Ask about an operation identity. None only if nothing was ever created for it."""
        with self._lock:
            self.lookups.append(idempotency_key)
            self.calls.append(("lookup", idempotency_key))
            charge = self._charges.get(idempotency_key)
            return dict(charge) if charge is not None else None

    def get_charge(self, charge_id: str):
        """Ask about a charge by its stable identity. This is the authority on its money."""
        with self._lock:
            charge = self._by_charge_id.get(charge_id)
            return dict(charge) if charge is not None else None

    def settle(self, charge_id: str, batch_id: str) -> dict:
        """The processor settles a charge into a batch. Idempotent on the charge."""
        validated_identifier(batch_id, "batch_id")
        validated_identifier(charge_id, "charge_id")
        with self._lock:
            charge = self._by_charge_id[charge_id]
            settled = self._settlements.get(charge_id)
            if settled is None:
                settled = {"settlement_id": f"st_{len(self._settlements) + 1}",
                           "charge_id": charge_id, "batch_id": batch_id,
                           "currency": charge["currency"],
                           "gross_minor": charge["amount_minor"],
                           "fee_minor": charge["fee_minor"],
                           "net_minor": charge["net_minor"],
                           "authority": self._authority.fields()}
                self._settlements[charge_id] = settled
            return dict(settled)

    def settlement_for_charge(self, charge_id: str):
        """The authoritative settlement fact, asked for by identity. None until settled."""
        with self._lock:
            settled = self._settlements.get(charge_id)
            return dict(settled) if settled is not None else None

    def charged_total_minor(self, invoice_id: str) -> int:
        """What the customer ended up owing, which is the number that matters."""
        return sum(c["amount_minor"] for c in self._charges.values()
                   if c["invoice_id"] == invoice_id)

    def charge_count(self, invoice_id: str) -> int:
        return sum(1 for c in self._charges.values() if c["invoice_id"] == invoice_id)


def _load(name: str):
    """Frozen inputs: one settlement event delivered twice, and a report whose fee disagrees
    with the processor's own by 25 minor units on purpose."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def load_webhooks() -> list:
    return _load("webhooks.json")


def load_settlement_report() -> dict:
    return _load("settlement_report.json")
