"""A double-entry ledger, and the drift detector that checks it against an authority.

A posting set commits whole or not at all, and only if it sums to zero per currency. An
entry is never edited and never deleted: a correction is a new balanced entry naming the one
it corrects. Balances are derived, keyed by account and currency so nothing can sum USD and
EUR, and compared by `reconcile` against a record made outside this process, because a
balance nobody checks outside is a rumour. Amounts are signed integers in minor units, debit
positive; currency is a field on the posting, not a shape of the schema.

`commit_once` is the only way an entry is appended, and it takes the effect identity that
caused the entry. One append records the economic entry and its dedupe identity, so an entry
without one cannot exist here by construction. That is a co-location claim, not a durability
claim: see store.py.

Attribution is structured. The settlement, the charge and the batch an entry belongs to are
fields on the entry, not prose in the memo, because reconciliation has to compare them and
nothing should have to parse a sentence to find out which charge a payment settled.
"""
from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass

from money import validated_currency, validated_identifier

CASH = "assets:cash:bank"
RECEIVABLE = "assets:receivable:processor"
FEES = "expense:processor_fees"
REVENUE = "income:invoices"
DIFFERENCES = "expense:settlement_differences"  # an approved correction lands here
SUSPENSE = "expense:suspense"                   # only the unsafe variant ever posts here


class Unbalanced(ValueError):
    """A posting set that cannot become an entry. Nothing is written when this is raised."""


class EffectConflict(ValueError):
    """One effect id, two different economic fingerprints. Refused, never applied."""


@dataclass(frozen=True)
class Posting:
    account: str
    currency: str
    amount_minor: int


@dataclass(frozen=True)
class Attribution:
    """Which settlement, which charge, which batch, which currency. Structured fields that
    reconciliation compares directly; a correct amount on the wrong charge is a break."""

    settlement_id: str = ""
    charge_id: str = ""
    batch_id: str = ""
    currency: str = ""

    def fields(self) -> tuple:
        return (self.settlement_id, self.charge_id, self.batch_id, self.currency)

    def describe(self) -> str:
        return (f"settlement {self.settlement_id or '-'} charge {self.charge_id or '-'} "
                f"batch {self.batch_id or '-'} [{self.currency or '-'}]")


@dataclass(frozen=True)
class Entry:
    """Immutable, and it carries its own dedupe identity.

    `fingerprint` is the whole content this effect produced: the caller's economic claim plus
    the kind, the reference, the ordered postings, the attribution, what it corrects and the
    break it closes. Comparing only the caller's claim would let one effect id return an
    entry whose postings or attribution were something else entirely.
    """

    entry_id: str
    kind: str
    reference: str
    postings: tuple
    effect_id: str
    fingerprint: tuple
    attribution: Attribution = Attribution()
    memo: str = ""
    corrects: str = ""      # the entry_id this one corrects, never a settlement id
    break_id: str = ""      # the reconciliation break this correction closes


def content_fingerprint(fingerprint, kind: str, reference: str, postings,
                        attribution: Attribution, corrects: str, break_id: str) -> tuple:
    """Everything an effect id is answerable for. Changing any of it under one id is a
    different entry, and returning the old one instead would be answering a question nobody
    asked."""
    return (tuple(fingerprint), kind, reference, tuple(postings),
            (attribution or Attribution()).fields(), corrects, break_id)


class Ledger:
    """Append-only. There is no update method and no delete method, by construction."""

    def __init__(self) -> None:
        self._entries: list = []
        self._lock = threading.RLock()

    def commit_once(self, effect_id: str, fingerprint, *, kind: str, reference: str,
                    postings, attribution: Attribution = None, memo: str = "",
                    corrects: str = "", break_id: str = "", seam=None, fault=None) -> tuple:
        """Append one entry for one effect identity, at most once.

        Returns `(entry, applied)`. Same id and same fingerprint returns the existing entry
        with `applied` False and moves nothing. Same id and a changed fingerprint is refused,
        because the ledger cannot tell which of the two was meant.

        Everything that can refuse - the posting set's arithmetic, the seam, the fault -
        happens before the append, and the append itself records the entry and the effect
        identity in one operation, so no entry can exist without its dedupe identity.
        """
        validated_identifier(effect_id, "effect_id")
        validated_identifier(reference, "reference")
        attribution = attribution or Attribution()
        for name, value in zip(("settlement_id", "charge_id", "batch_id", "currency"),
                               attribution.fields()):
            # Only the empty string means "not applicable". None, [], {}, 0 and False are
            # all falsy AND all non-strings, so testing truthiness would wave them through.
            if value != "":
                validated_identifier(value, f"attribution.{name}")
        postings = self._validated(postings)
        fingerprint = content_fingerprint(fingerprint, kind, reference, postings,
                                          attribution, corrects, break_id)
        with self._lock:
            existing = self.entry_for_effect(effect_id)
            if existing is not None:
                if existing.fingerprint != fingerprint:
                    raise EffectConflict(f"{effect_id} committed {existing.fingerprint} "
                                         f"and now carries {fingerprint}")
                return existing, False
            if seam is not None:
                seam()
            if fault is not None:
                fault()
            entry = Entry(f"e{len(self._entries) + 1}", kind, reference, postings, effect_id,
                          fingerprint, attribution, memo, corrects, break_id)
            self._entries.append(entry)     # the entry and its identity, in one append
            return entry, True

    @staticmethod
    def _validated(postings) -> tuple:
        postings = tuple(postings)
        if not postings:
            raise Unbalanced("an entry with no postings is not an entry")
        totals: dict = {}
        for p in postings:
            if not isinstance(p.amount_minor, int) or isinstance(p.amount_minor, bool):
                raise Unbalanced(f"{p.amount_minor!r} is not an integer minor amount")
            # The currency the money is denominated in, held to the same rule as the
            # attribution beside it. Without this an unusable currency reaches a dict key
            # and takes balances(), trial_balance() and reconcile() down together.
            validated_currency(p.currency)
            totals[p.currency] = totals.get(p.currency, 0) + p.amount_minor
        unbalanced = {c: t for c, t in sorted(totals.items()) if t != 0}
        if unbalanced:
            raise Unbalanced(f"postings do not sum to zero: {unbalanced}")
        return postings

    def entries(self) -> tuple:
        with self._lock:
            return tuple(self._entries)

    def entry_for_effect(self, effect_id: str):
        """The dedupe lookup is a scan of the entries themselves, so the dedupe identity
        cannot go missing while its entry survives: there is no second table to lose."""
        with self._lock:
            for entry in self._entries:
                if entry.effect_id == effect_id:
                    return entry
            return None

    def entry_for_settlement(self, settlement_id: str):
        """The settlement entry a correction attaches to. The first one, if a lost dedupe
        row ever produced two."""
        return next((e for e in self.entries()
                     if e.kind == "settlement" and e.reference == settlement_id), None)

    def balance(self, account: str, currency: str, reference: str | None = None) -> int:
        """One account in one currency. There is no signature that sums two currencies."""
        return sum(p.amount_minor
                   for e in self.entries() if reference is None or e.reference == reference
                   for p in e.postings
                   if p.account == account and p.currency == currency)

    def balances(self) -> dict:
        """Keyed by (account, currency). A balance is not a number until it names both."""
        keys = {(p.account, p.currency) for e in self.entries() for p in e.postings}
        return {k: self.balance(*k) for k in sorted(keys)}

    def trial_balance(self) -> dict:
        """Zero per currency unless an entry was tampered with. Never one grand total."""
        totals: dict = {}
        for entry in self.entries():
            for p in entry.postings:
                totals[p.currency] = totals.get(p.currency, 0) + p.amount_minor
        return dict(sorted(totals.items()))


# The three quantities a settlement claims, and the ledger account each is claimed in.
QUANTITIES = (("gross", RECEIVABLE, -1), ("fee", FEES, 1), ("net", CASH, 1))
# net = gross - fee, so net is derived. Only the independent quantities are added into the
# amount at stake; counting the derived one again would report one disagreement twice.
INDEPENDENT = ("gross", "fee")
# Every entry kind that changes what the ledger claims about a settlement identity.
SETTLEMENT_KINDS = ("settlement", "correction", "suspense")
# The authority's record is input, not truth. A line has to name the settlement and the
# charge, and every amount on it has to be an integer in minor units, or it is reported and
# never compared: a detector that dies on the counterparty's file is a detector that is down.
IDENTITY_LINE_FIELDS = ("settlement_id", "charge_id")
MONEY_LINE_FIELDS = ("gross_minor", "fee_minor", "net_minor")


@dataclass(frozen=True)
class Break:
    """One settlement identity, one break. Compound on purpose: a settlement that disagrees
    about gross, fee and net is one disagreement, and splitting it into three rows would
    report three amounts at stake where only one amount is at stake.

    `at_stake` is per currency - ((currency, minor), ...) - because two currencies are two
    exposures and adding them produces a number that is true in no currency at all.
    """

    kind: str
    attribution: Attribution
    detail: str
    at_stake: tuple = ()    # ((currency, minor_units), ...), sorted, never summed together
    deltas: tuple = ()      # ((quantity, report_minor, ledger_minor, delta_minor), ...)

    @property
    def settlement_id(self) -> str:
        return self.attribution.settlement_id

    @property
    def currency(self) -> str:
        return self.attribution.currency

    @property
    def break_id(self) -> str:
        """A stable identity for this exact disagreement: its full attribution, its kind,
        its per-currency exposure and its deltas. A later, genuinely different break on the
        same settlement hashes differently and is a different break to approve."""
        payload = repr((self.kind, self.attribution.fields(), self.at_stake, self.deltas))
        return "brk_" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def delta(self, quantity: str) -> int:
        return next((d for q, _r, _l, d in self.deltas if q == quantity), 0)

    def amount(self, currency: str) -> int:
        return next((m for c, m in self.at_stake if c == currency), 0)

    def describe(self) -> str:
        stake = ", ".join(f"{c} {m}" for c, m in self.at_stake) or "no amount"
        return (f"{self.break_id} {self.kind}: {self.attribution.describe()} {self.detail}; "
                f"at stake {stake}")


def exposure(breaks) -> dict:
    """What a set of breaks puts at risk, per currency. Never one integer: there is no
    exchange rate here and inventing one would be the whole error."""
    totals: dict = {}
    for brk in breaks:
        for currency, minor in brk.at_stake:
            totals[currency] = totals.get(currency, 0) + minor
    return dict(sorted(totals.items()))


def ledger_settlements(ledger: Ledger) -> dict:
    """What the ledger claims, per settlement identity: the three quantities, the currencies
    it used, the charges and batches its entries attribute it to, and how many settlement
    entries claim that identity."""
    rows: dict = {}
    for entry in ledger.entries():
        if entry.kind not in SETTLEMENT_KINDS:
            continue
        row = rows.setdefault(entry.reference,
                              {"gross": 0, "fee": 0, "net": 0, "currencies": set(),
                               "charge_ids": set(), "batch_ids": set(),
                               "settlement_nets": []})
        if entry.attribution.charge_id:
            row["charge_ids"].add(entry.attribution.charge_id)
        if entry.attribution.batch_id:
            row["batch_ids"].add(entry.attribution.batch_id)
        if entry.kind == "settlement":
            row["settlement_nets"].append(
                sum(p.amount_minor for p in entry.postings if p.account == CASH))
        for p in entry.postings:
            row["currencies"].add(p.currency)
            for quantity, account, sign in QUANTITIES:
                if p.account == account:
                    row[quantity] += sign * p.amount_minor
    return rows


def _not_an_identifier(value) -> bool:
    """A settlement, charge, batch or currency is named by a non-empty string. A list or a
    number in that position is a malformed record, not an identity to compare."""
    return not isinstance(value, str) or not value.strip()


def _not_an_amount(value) -> bool:
    """The rule the ledger already enforces on a posting, enforced on the report as well:
    an amount is an integer in minor units. A bool is not an amount and a float never is."""
    return isinstance(value, bool) or not isinstance(value, int)


def _stake(currency: str, minor: int) -> tuple:
    return ((currency, abs(minor)),) if minor else ()


def _envelope(report: dict, breaks: list) -> tuple:
    """The report's own shape, checked before a single line is read.

    Returns (readable, comparable). READABLE means the lines can be parsed at all, so their
    own structural problems can be reported. COMPARABLE means the envelope is sound enough
    to compare money against: a report whose batch, currency or total cannot be read is a
    structural finding and nothing else. Comparing lines under a broken envelope would turn
    one unreadable file into a list of economic differences somebody could correct.
    """
    sound = True
    batch = report.get("batch_id", "report")
    currency = report.get("currency", "")
    here = Attribution(batch_id=batch if isinstance(batch, str) else "report",
                       currency=currency if isinstance(currency, str) else "")
    for name, value in (("batch_id", batch), ("currency", currency)):
        if _not_an_identifier(value):
            breaks.append(Break("report_envelope", here,
                                f"the report's {name} is {value!r}, not an identifier"))
            sound = False
    declared = report.get("total_net_minor")
    if declared is None:
        breaks.append(Break("report_total", here,
                            "the report declares no total, so its lines answer to nothing"))
        sound = False
    elif _not_an_amount(declared):
        breaks.append(Break("report_total", here,
                            f"the declared total {declared!r} is not an integer minor "
                            f"amount"))
        sound = False
    lines = report.get("lines")
    if not isinstance(lines, list):
        breaks.append(Break("report_envelope", here,
                            f"`lines` is a {type(lines).__name__}, not a list of lines"))
        return False, False
    return True, sound


def _report_lines(report: dict, breaks: list) -> tuple:
    """The report side of the union, with each line's shape and arithmetic checked first. A
    line this cannot read is reported as a break and never compared to anything.

    Returns the comparable lines and the per-currency net of every readable line counted
    ONCE. A repeated identity is reported as its own break and left out of the running net,
    so the duplicate and the declared total never report the same money twice.
    """
    lines: dict = {}
    net_by_currency: dict = {}
    duplicate_by_currency: dict = {}
    unreadable: set = set()     # identities the report claims but nobody can compare
    default_currency = report.get("currency", "")
    default_batch = report.get("batch_id", "")
    for index, line in enumerate(report.get("lines") or ()):
        unidentified = f"unidentified_line_{index + 1}"
        if not isinstance(line, dict):
            breaks.append(Break("malformed_line",
                                Attribution(unidentified, currency=default_currency),
                                f"the report line is a {type(line).__name__}, not a record"))
            continue
        currency = line.get("currency", default_currency)
        raw_settlement = line.get("settlement_id")
        settlement_id = raw_settlement if isinstance(raw_settlement, str) and raw_settlement \
            else unidentified
        here = Attribution(
            settlement_id,
            line["charge_id"] if isinstance(line.get("charge_id"), str) else "",
            line["batch_id"] if isinstance(line.get("batch_id"), str) else default_batch,
            currency if isinstance(currency, str) else "")
        missing = ([f for f in IDENTITY_LINE_FIELDS if not line.get(f)]
                   + [f for f in MONEY_LINE_FIELDS if line.get(f) is None])
        if missing:
            breaks.append(Break("missing_field", here,
                                f"the report line omits {', '.join(missing)}"))
            unreadable.add(settlement_id)
            continue
        unnamed = [f"{f} is {line.get(f)!r}" for f in ("settlement_id", "charge_id",
                                                       "batch_id", "currency")
                   if _not_an_identifier(line.get(f, default_currency if f == "currency"
                                                  else default_batch if f == "batch_id"
                                                  else None))]
        if unnamed:
            breaks.append(Break("unidentified_line", here,
                                f"the report line says {', '.join(unnamed)}, which does not "
                                f"name anything the ledger can be compared against"))
            unreadable.add(settlement_id)
            continue
        bad_amounts = [f"{f} is {line[f]!r}" for f in MONEY_LINE_FIELDS
                       if _not_an_amount(line[f])]
        if bad_amounts:
            breaks.append(Break("not_an_amount", here,
                                f"the report line says {', '.join(bad_amounts)}, which is "
                                f"not an integer minor amount"))
            unreadable.add(settlement_id)
            continue
        if settlement_id in lines:
            # The duplicate carries the exposure. Its amount is deliberately not added to
            # the running net: a duplicated line and the declared total would otherwise both
            # report the same money, and one underlying mistake would read as two.
            breaks.append(Break("duplicate_report_line", here,
                                "the report claims this settlement identity twice",
                                _stake(currency, line["net_minor"])))
            duplicate_by_currency[currency] = (duplicate_by_currency.get(currency, 0)
                                               + line["net_minor"])
            continue
        gap = line["gross_minor"] - line["fee_minor"] - line["net_minor"]
        if gap:
            # Reported once, and then left out: a line that does not add up cannot also be
            # compared to the ledger, or one unreadable line becomes two findings and twice
            # the exposure, the second of which somebody could approve a correction against.
            breaks.append(Break("line_arithmetic", here,
                                f"net {line['net_minor']} + fee {line['fee_minor']} is not "
                                f"gross {line['gross_minor']}", _stake(currency, gap)))
            unreadable.add(settlement_id)
            continue
        net_by_currency[currency] = net_by_currency.get(currency, 0) + line["net_minor"]
        lines[settlement_id] = dict(line, currency=currency,
                                    batch_id=here.batch_id, charge_id=here.charge_id)
    return lines, net_by_currency, duplicate_by_currency, unreadable


def _total_break(report: dict, net_by_currency: dict, duplicated: dict):
    """The declared total against what the distinct lines add up to, per currency.

    A repeated identity is reported on its own and carries its own exposure, so whatever of
    this gap that duplicate already accounts for is subtracted here. The disagreement is
    still described; it just does not put the same money at stake a second time.
    """
    declared = report.get("total_net_minor")
    if declared is None or _not_an_amount(declared):
        return None                     # already reported by the envelope check
    currency = report.get("currency", "")
    if _not_an_identifier(currency):
        return None                     # likewise: a total in no currency compares to nothing
    batch = report.get("batch_id", "report")
    here = Attribution(batch_id=batch if isinstance(batch, str) else "report",
                       currency=currency)
    if len(net_by_currency) > 1:
        return Break("report_total", here,
                     "the lines are in " + ", ".join(f"{c} {n}" for c, n
                                                     in sorted(net_by_currency.items()))
                     + f" and one declared total of {declared} cannot be all of them",
                     tuple(sorted((c, abs(n)) for c, n in net_by_currency.items())))
    actual = net_by_currency.get(currency, 0)
    if actual == declared:
        return None
    # A repeated identity leaves the declared total ambiguous between two honest readings:
    # the lines as the report wrote them, and the distinct identities it names. The total is
    # independently wrong only by the amount it misses BOTH readings by, so the smaller gap
    # is the one that is not already carried by the duplicate. With no duplicate the two
    # readings are the same number and this is simply the gap.
    as_written = actual + duplicated.get(currency, 0)
    readings = {"the lines as written": as_written - declared,
                "the distinct lines": actual - declared}
    label, gap = min(readings.items(), key=lambda kv: abs(kv[1]))
    if gap == 0:
        return None
    return Break("report_total", here,
                 f"{label} net to {declared + gap} against a declared total of {declared}",
                 _stake(currency, gap))


def reconcile(report, ledger: Ledger) -> tuple:
    """Compare the full union of the report's settlement identities and the ledger's, over a
    record made by a party that is not this process. This reports and never posts: a
    reconciliation that settles its own findings has stopped being a detector.

    It is total. The report comes from outside and can be anything at all; a detector that
    raises on the counterparty's file is a detector that is down on exactly the morning
    somebody sent a bad one. Every shape that cannot be read becomes a break.
    """
    breaks: list = []
    if not isinstance(report, dict):
        return (Break("report_envelope", Attribution(batch_id="report"),
                      f"the report is a {type(report).__name__}, not a record"),)
    readable, comparable = _envelope(report, breaks)
    lines, net_by_currency, duplicated, unreadable = (
        _report_lines(report, breaks) if readable else ({}, {}, {}, set()))
    if not comparable:
        # The envelope itself could not be read. Everything above is structural, and none of
        # it is a difference in money that anyone should be approving a correction against.
        return tuple(breaks)
    if not unreadable:
        # A total can only be checked against every line. With one of them excluded, the
        # declared figure is not wrong, it is unverifiable, and reporting it as a difference
        # would put the same unreadable line's money at stake a second time.
        total_break = _total_break(report, net_by_currency, duplicated)
        if total_break is not None:
            breaks.append(total_break)
    rows = ledger_settlements(ledger)
    for settlement_id in sorted((set(lines) | set(rows)) - unreadable):
        # An identity the report claims but nobody can read is already reported once. It is
        # not `ledger_only`: the report did claim it, it just did not say anything usable.
        line, row = lines.get(settlement_id), rows.get(settlement_id)
        if row is None:
            breaks.append(Break("report_only",
                                Attribution(settlement_id, line["charge_id"],
                                            line["batch_id"], line["currency"]),
                                "the report claims a settlement the ledger never recorded",
                                _stake(line["currency"], line["net_minor"])))
            continue
        currencies = sorted(row["currencies"])
        held = "/".join(currencies)
        claimed = "" if line is None else line["currency"]
        if len(currencies) > 1 or (line is not None and held != claimed):
            # Each currency's net is stated on its own and no two of them are netted
            # together: an amount is not a number until it names its unit.
            nets = [(c, ledger.balance(CASH, c, settlement_id)) for c in currencies]
            claim = (f"the report says {claimed}" if line is not None
                     else "the report does not claim this settlement")
            stake: dict = {c: abs(n) for c, n in nets if n}
            if line is not None and line["net_minor"]:
                stake[claimed] = stake.get(claimed, 0) + abs(line["net_minor"])
            breaks.append(Break(
                "currency_mismatch",
                Attribution(settlement_id, "/".join(sorted(row["charge_ids"])),
                            "/".join(sorted(row["batch_ids"])), claimed),
                f"{claim} and the ledger holds "
                + "; ".join(f"{c} net {n}" for c, n in nets)
                + "; they are never summed into one figure",
                tuple(sorted(stake.items()))))
            continue
        attributed = Attribution(settlement_id, "/".join(sorted(row["charge_ids"])),
                                 "/".join(sorted(row["batch_ids"])), held)
        if line is None:
            breaks.append(Break("ledger_only", attributed,
                                "the ledger holds a settlement the report does not claim",
                                _stake(held, row["net"])))
            continue
        # Exactly the one identity, on both sides. A local set that is empty is not a
        # match, and a set holding the right identity beside a wrong one is not a match
        # either: "the correct value is in there somewhere" is how a payment attributed to
        # two charges passes for attributed to one.
        wrong = [f"{name} {claim!r} against the ledger's "
                 + (str(sorted(have)) if have else "no attribution at all")
                 for name, claim, have in (("charge", line["charge_id"], row["charge_ids"]),
                                           ("batch", line["batch_id"], row["batch_ids"]))
                 if have != {claim}]
        deltas = tuple((q, line[f"{q}_minor"], row[q], row[q] - line[f"{q}_minor"])
                       for q, _account, _sign in QUANTITIES)
        duplicated = len(row["settlement_nets"]) > 1
        if wrong and not duplicated:
            breaks.append(Break(
                "attribution_mismatch",
                Attribution(settlement_id, line["charge_id"], line["batch_id"],
                            line["currency"]),
                "the report attributes this settlement to " + " and ".join(wrong)
                + "; the amounts are not compared to an identity that does not match",
                _stake(line["currency"], line["net_minor"])))
            continue
        if not duplicated and not any(d for _q, _r, _l, d in deltas):
            continue
        at_stake = sum(abs(d) for q, _r, _l, d in deltas if q in INDEPENDENT)
        net_delta = abs(next(d for q, _r, _l, d in deltas if q == "net"))
        # Net is derived only while the row adds up. A ledger row whose cash alone diverges
        # has gross and fee agreeing, so counting only those values a real difference at
        # zero and drops it out of exposure() entirely.
        derived = row["gross"] - row["fee"] == row["net"]
        if not derived and net_delta > at_stake:
            at_stake = net_delta
        detail = "; ".join(
            f"{q} report {r} ledger {l} delta {d:+d}"
            + ("" if q in INDEPENDENT or not derived else
               " (derived from the other two, not counted)")
            for q, r, l, d in deltas)
        if not derived:
            detail += ("; the ledger row does not add up, so its net is counted on its own")
        if duplicated:
            # The duplicate is the larger finding and it is reported as one break, with any
            # attribution problem named inside it rather than as a second row.
            detail = (f"{len(row['settlement_nets'])} settlement entries claim this "
                      f"identity; " + detail)
            if wrong:
                detail += "; the report also attributes it to " + " and ".join(wrong)
        breaks.append(Break("duplicate_entry" if duplicated else "amount_mismatch",
                            attributed, detail, _stake(held, at_stake), deltas))
    return tuple(breaks)
