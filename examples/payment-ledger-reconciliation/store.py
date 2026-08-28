"""One shared transactional state, so that "already done" means the same thing to everyone.

Every worker is constructed over one Store. The Store owns the lock, the intents table and
the open-break register; a worker owns no payment state and no dedupe state of its own,
which is why two workers over one Store are safe and two Stores would not be.

There is no separate effects table. The dedupe identity of an effect lives ON the ledger
entry it produced, and `Ledger.commit_once` writes both in one append, so an entry that
exists without its identity is not a state this code can reach. What this example does NOT
claim is durability: these are dicts and a list, not a database. The claim is co-location -
in this process the entry and its effect identity cannot diverge because they are the same
object. A production version needs the entry and its dedupe identity inside one database
transaction; a unique constraint on the effect id is what makes that true across crashes.

The intent transition is the one write that is still separate from the entry, so it is
written to be REDOABLE: a retry that finds the effect already committed finishes the missing
transition without posting again, and `after_entry` exists so a test can interrupt exactly
there.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, replace

from ledger import EffectConflict  # noqa: F401  (re-exported: callers refuse on it)
from money import AuthorityScope, validated_identifier


class IntentMismatch(ValueError):
    """One operation key, two different economic decisions. Refused, never executed."""


class ForeignAuthority(ValueError):
    """A second authority's operation offered to a book that already serves another."""


class UnknownBreak(ValueError):
    """A correction naming a break no reconciliation recorded. Refused, never posted."""


class BreakAlreadyClosed(ValueError):
    """A second, different correction against a break already closed. Refused."""


@dataclass(frozen=True)
class Applied:
    """What `commit_once` answered. `applied` is False when the effect was already there."""

    effect_id: str
    entry_id: str
    fields: tuple
    applied: bool = True


@dataclass(frozen=True)
class Intent:
    """A persisted economic decision, PINNED to the authority it will be sent to. The
    binding is what a replay is compared against, and the authority is part of it: the same
    key at another account, in another region, or at another backend carrying identical
    labels, is another counterparty that knows nothing about this key."""

    key: str
    invoice_id: str
    amount_minor: int
    currency: str
    provider: str
    account: str
    region: str
    backend_id: str         # which backend actually holds the key, not just its labels
    state: str = "PENDING"
    charge_id: str = ""

    @property
    def authority(self) -> tuple:
        return (self.provider, self.account, self.region, self.backend_id)

    @property
    def binding(self) -> tuple:
        return (self.invoice_id, self.amount_minor, self.currency) + self.authority


@dataclass(frozen=True)
class BreakRecord:
    """An open break, recorded by reconciliation so a correction has something real to
    close. A correction that cannot find its break here is a correction of nothing."""

    break_id: str
    brk: object
    state: str = "OPEN"
    entry_id: str = ""
    approved_by: str = ""


class Store:
    def __init__(self, ledger) -> None:
        self.ledger = ledger
        # This book serves ONE authority, fixed by the first intent it accepts. Counterparty
        # ids - charge ids, settlement ids - are unique only inside the counterparty that
        # minted them, so a book holding two authorities has two things called `ch_1` and
        # every index keyed on one of them is ambiguous: which intent owns it, which entry a
        # report line means, which of two correct entries a correction should attach to.
        # Scoping one index at a time only moves the ambiguity to the next one. A real book
        # that serves several authorities has to carry the authority in every one of those
        # identities; this one states the narrower thing it actually is, and enforces it.
        self._authority: tuple = ()
        self._lock = threading.RLock()
        self._intents: dict = {}
        self._breaks: dict = {}

    # ---- intents -----------------------------------------------------------------
    def open_intent(self, key: str, *, invoice_id: str, amount_minor: int, currency: str,
                    authority: AuthorityScope) -> Intent:
        """Create the intent, or return the stored one. Every bound field is compared on
        every replay, whatever state the intent is in: a mismatch is refused, not run."""
        # Everything that can refuse happens BEFORE the book is claimed. A call that is
        # then rejected must leave no trace: claiming on the way in would let one
        # misconfigured worker take the book with a call it was never allowed to make, and
        # lock the rightful worker out of it for ever with no way to clear it.
        validated_identifier(key, "intent key")
        validated_identifier(invoice_id, "invoice_id")
        for name, value in zip(("provider", "account", "region", "backend_id"),
                               authority.fields()):
            validated_identifier(value, f"authority.{name}")
        wanted = (invoice_id, amount_minor, currency) + authority.fields()
        with self._lock:
            if self._authority and self._authority != authority.fields():
                held = "/".join(self._authority[:3]) + f"#{self._authority[3]}"
                raise ForeignAuthority(
                    f"this book serves {held} and {key} is pinned to "
                    f"{authority.describe()}; one book, one authority")
            intent = self._intents.get(key)
            if intent is None:
                intent = Intent(key, invoice_id, amount_minor, currency, *authority.fields())
                self._intents[key] = intent
                self._authority = authority.fields()    # claimed with the intent, not before
                return intent
            if intent.binding != wanted:
                raise IntentMismatch(
                    f"{key} was opened for {intent.binding} and is now asked for {wanted}")
            return intent

    def get_intent(self, key: str):
        with self._lock:
            return self._intents.get(key)

    def intents(self) -> tuple:
        with self._lock:
            return tuple(v for _, v in sorted(self._intents.items()))

    def authority(self) -> tuple:
        """The one authority this book serves, or () before the first intent."""
        with self._lock:
            return self._authority

    def intent_for_charge(self, charge_id: str):
        with self._lock:
            for _, intent in sorted(self._intents.items()):
                if intent.charge_id and intent.charge_id == charge_id:
                    return intent
            return None

    # ---- effects -----------------------------------------------------------------
    def effect(self, effect_id: str):
        """The dedupe row IS the entry. There is no second place for it to be missing."""
        entry = self.ledger.entry_for_effect(effect_id)
        if entry is None:
            return None
        return Applied(effect_id, entry.entry_id, entry.fingerprint, False)

    def commit_once(self, effect_id: str, economic_fields, *, kind: str, reference: str,
                    postings, attribution=None, memo: str = "", corrects: str = "",
                    break_id: str = "", intent_updates=None, fault=None, seam=None,
                    after_entry=None) -> Applied:
        """Apply one effect at most once, and leave the intents consistent with it.

        The entry and its dedupe identity are one append inside the ledger's own lock. The
        intent transition follows, and is applied whether or not this call was the one that
        posted: a retry after an interruption between the two finishes the transition and
        posts nothing. `fault` fires before anything is written; `after_entry` fires once
        the entry is visible and before the transition, which is the one remaining window.
        """
        with self._lock:
            # An intent this store does not hold is refused here, with nothing written yet,
            # rather than after an entry has already been committed against it.
            updated = [(key, replace(self._intents[key], **updates))
                       for key, updates in (intent_updates or {}).items()]
            entry, applied = self.ledger.commit_once(
                effect_id, economic_fields, kind=kind, reference=reference,
                postings=postings, attribution=attribution, memo=memo, corrects=corrects,
                break_id=break_id, seam=seam, fault=fault)
            if after_entry is not None:
                after_entry()           # the entry is visible; the intent has not moved yet
            self._intents.update(updated)
            return Applied(effect_id, entry.entry_id, entry.fingerprint, applied)

    # ---- open breaks -------------------------------------------------------------
    def record_breaks(self, breaks) -> tuple:
        """Reconciliation records what it found. A break that was already closed stays
        closed; a genuinely different break on the same settlement has its own identity and
        is recorded on its own."""
        with self._lock:
            for brk in breaks:
                self._breaks.setdefault(brk.break_id, BreakRecord(brk.break_id, brk))
            return tuple(breaks)

    def break_record(self, break_id: str):
        with self._lock:
            return self._breaks.get(break_id)

    def open_breaks(self) -> tuple:
        with self._lock:
            return tuple(r.brk for _, r in sorted(self._breaks.items())
                         if r.state == "OPEN")

    def close_break(self, break_id: str, entry_id: str, approved_by: str) -> BreakRecord:
        with self._lock:
            record = self._breaks[break_id]
            closed = replace(record, state="CLOSED", entry_id=entry_id,
                             approved_by=approved_by)
            self._breaks[break_id] = closed
            return closed
