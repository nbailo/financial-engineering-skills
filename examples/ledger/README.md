# A transfer ledger, and the reversal that has to undo it

An internal double-entry ledger: move money between two customer accounts, take a 0.25% fee, and be able to
reverse the whole thing when fraud is confirmed. This is the code that gets written once, early, by whoever
is fastest, and then everything else in the company is built on top of it. The arithmetic is rarely the
problem, because transfer and reversal maths is usually correct. What goes wrong is the journal that does not
balance, because a leg is missing.

---

## Before

```sql
-- migrations/001_ledger.sql

CREATE TABLE accounts (
    id            bigserial   PRIMARY KEY,
    name          text        NOT NULL,
    currency      char(3)     NOT NULL,
    balance_cents bigint      NOT NULL DEFAULT 0 CHECK (balance_cents >= 0),
    status        text        NOT NULL DEFAULT 'active'
);

CREATE TABLE ledger_transactions (
    id              uuid        PRIMARY KEY,
    kind            text        NOT NULL,
    idempotency_key text        UNIQUE,
    reverses        uuid        REFERENCES ledger_transactions(id),
    created_at      timestamptz NOT NULL DEFAULT now()
);

-- Append-only double-entry journal. Entries are never updated or deleted.
CREATE TABLE entries (
    id             bigserial   PRIMARY KEY,
    transaction_id uuid        NOT NULL REFERENCES ledger_transactions(id),
    account_id     bigint      NOT NULL REFERENCES accounts(id),
    currency       char(3)     NOT NULL,
    amount_cents   bigint      NOT NULL,     -- signed: debit negative, credit positive
    created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX entries_txn_idx  ON entries (transaction_id);
CREATE INDEX entries_acct_idx ON entries (account_id);

-- Invariants. Both of these must return zero rows at all times:
--
--   -- 1. every transaction's entries net to zero
--   SELECT transaction_id, SUM(amount_cents)
--     FROM entries GROUP BY transaction_id HAVING SUM(amount_cents) <> 0;
--
--   -- 2. every materialised balance equals the sum of its entries
--   SELECT a.id FROM accounts a LEFT JOIN entries e ON e.account_id = a.id
--    GROUP BY a.id, a.balance_cents
--   HAVING COALESCE(SUM(e.amount_cents), 0) <> a.balance_cents;
--
-- Worth running as a cron.
```

```python
# ledger.py
"""Internal transfer ledger. Double-entry, append-only, integer minor units."""
import uuid
from typing import Optional

from psycopg.errors import CheckViolation

FEE_BPS = 25  # 0.25%, charged to the sender


class TransferError(Exception): ...
class InsufficientFunds(TransferError): ...
class AccountNotActive(TransferError): ...
class AlreadyReversed(TransferError): ...


def transfer(conn, from_id, to_id, amount_cents, currency,
             idempotency_key: Optional[str] = None):
    """Move `amount_cents` between accounts, less a 0.25% fee.

    `idempotency_key` is optional here; it should be required at the API layer.
    """
    fee = amount_cents * FEE_BPS // 10_000
    net = amount_cents - fee
    txid = uuid.uuid4()

    with conn.transaction():
        if idempotency_key is not None:
            row = conn.execute(
                "SELECT id FROM ledger_transactions WHERE idempotency_key = %s",
                (idempotency_key,),
            ).fetchone()
            if row:
                return row[0]

        # deterministic lock order, so two concurrent transfers cannot deadlock
        for acct in sorted((from_id, to_id)):
            r = conn.execute(
                "SELECT status FROM accounts WHERE id = %s FOR UPDATE", (acct,)
            ).fetchone()
            if r is None or r[0] != "active":
                raise AccountNotActive(acct)

        conn.execute(
            "INSERT INTO ledger_transactions (id, kind, idempotency_key)"
            " VALUES (%s, 'transfer', %s)",
            (txid, idempotency_key),
        )
        conn.execute(
            "INSERT INTO entries (transaction_id, account_id, currency, amount_cents)"
            " VALUES (%s,%s,%s,%s)",
            (txid, from_id, currency, -amount_cents),
        )
        conn.execute(
            "INSERT INTO entries (transaction_id, account_id, currency, amount_cents)"
            " VALUES (%s,%s,%s,%s)",
            (txid, to_id, currency, net),
        )
        try:
            conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents - %s WHERE id = %s",
                (amount_cents, from_id),
            )
        except CheckViolation:
            raise InsufficientFunds(from_id)
        conn.execute(
            "UPDATE accounts SET balance_cents = balance_cents + %s WHERE id = %s",
            (net, to_id),
        )
    return txid


def reverse_transfer(conn, txid, allow_overdraft=False):
    """Reverse a transfer with compensating entries. Never mutates the original."""
    rid = uuid.uuid4()
    with conn.transaction():
        if conn.execute(
            "SELECT id FROM ledger_transactions WHERE reverses = %s", (txid,)
        ).fetchone():
            raise AlreadyReversed(txid)

        legs = conn.execute(
            "SELECT account_id, currency, amount_cents FROM entries"
            " WHERE transaction_id = %s ORDER BY id",
            (txid,),
        ).fetchall()
        if not legs:
            raise TransferError(f"unknown transaction {txid}")

        conn.execute(
            "INSERT INTO ledger_transactions (id, kind, reverses)"
            " VALUES (%s, 'reversal', %s)",
            (rid, txid),
        )
        for account_id, currency, amount in legs:
            conn.execute(
                "INSERT INTO entries (transaction_id, account_id, currency, amount_cents)"
                " VALUES (%s,%s,%s,%s)",
                (rid, account_id, currency, -amount),
            )
            conn.execute(
                "UPDATE accounts SET balance_cents = balance_cents - %s WHERE id = %s",
                (amount, account_id),
            )
    return rid
```

---

## What the suite catches

| Defect | Rule | What actually happens | Loss shape |
|---|---|---|---|
| Two legs for a three-leg transaction | `fin-ledger`: *The balanced set commits whole, and a posted entry is immutable* | The group is `−amount` and `+net`. It sums to `−fee`, not zero. The fee is debited from the sender and credited to **nobody**: there is no revenue account leg and no revenue account. The materialised balances agree with the entries, so nothing in the system disagrees with anything else. The money is simply not there. | Silent, per-transfer, permanently unattributed. The trial balance is off by the cumulative fee take from the first transfer onwards, and the discrepancy grows with volume. |
| `idempotency_key text UNIQUE` + `Optional[str] = None` | `fin-money-core`: *operation identity*. `fin-ledger`: *A repeat of an identity returns the original outcome only if it is the same request* | The mechanism is built and then made optional, with enforcement deferred to prose about the API layer, which is the usual shape. Callers that omit it get no protection at all. Callers that supply it hit a `SELECT`-then-`INSERT` race with no `ON CONFLICT`, so an honest concurrent retry gets a raw `UniqueViolation` through the `TransferError` hierarchy. Nothing compares the stored row's `from`/`to`/`amount` to the request, so a replayed key with a different body is accepted. | Duplicate transfer under concurrency; and an unvalidated replay lets `reverse_transfer` mark a transfer reversed **while writing zero compensating entries**. |
| Invariants as SQL in a schema comment | `fin-ledger`: *A stored balance is a cache, and a cache with no drift detector is a rumour*. `fin-money-core`: *reconciliation*, *Implemented, not described*. `fin-verification`: *A detector that has never detected is not known to detect* | The two queries are correct. They run nowhere. There is no scheduled entrypoint, no alert destination, no break record, and no owner. The usual version of this ships annotated *"worth running as a cron"*. Zero of the reps that were probed on conservation passed, in either domain where it was probed. | This is the control that would have caught the row above. Its absence converts a one-line bug into an unbounded, undetected one. |
| `CHECK (balance_cents >= 0)` on the accounts table | `fin-ledger`: *Corrections are reversals, never mutations, and the remedy has to be able to land* | The clawback path is structurally impossible. When the recipient has spent the money, `reverse_transfer`'s debit drives the balance negative and Postgres rejects it, so the fraud reversal aborts. `allow_overdraft` is accepted and never read: dead code. Only `transfer()` catches `CheckViolation`, so the raw `psycopg` error escapes the typed hierarchy mid-clawback. And `AccountNotActive` means the standard flow (freeze the recipient, then claw back) is blocked by the code, forcing an unfreeze that reopens exactly the drain window the freeze existed to close. | The safety operation cannot be performed at the moment it is needed. Full loss of the disputed amount, plus whatever drains during the unfreeze. |

The first and fourth rows are the same shape in opposite directions: a constraint that was not written
where it was needed, and a constraint that was written where it did harm. *Corrections are reversals,
never mutations, and the remedy has to be able to land* exists specifically so that fixing the second does
not produce `DROP CONSTRAINT`.

---

## After

```sql
-- migrations/002_conservation.sql

ALTER TABLE ledger_transactions
    ADD COLUMN posting_type text NOT NULL DEFAULT 'ordinary'
        CHECK (posting_type IN ('ordinary', 'reversal', 'clawback')),
    ADD COLUMN request_fingerprint jsonb NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN reversed_by uuid REFERENCES ledger_transactions(id),
    ALTER COLUMN idempotency_key SET NOT NULL;

-- One transaction cannot be reversed twice: not by two operators, not by a retry.
CREATE UNIQUE INDEX ledger_transactions_reverses_uniq
    ON ledger_transactions (reverses) WHERE reverses IS NOT NULL;

-- Conservation belongs to the write path ------------------------------------------
-- It is a property of the write API, not a check somebody runs later. DEFERRABLE
-- INITIALLY DEFERRED so leg order inside the transaction does not matter, and there is
-- no code path (ORM, psql, migration, admin script) that can bypass it.
CREATE FUNCTION assert_group_balances() RETURNS trigger AS $$
DECLARE bad record;
BEGIN
    SELECT currency, SUM(amount_cents) AS delta INTO bad
      FROM entries WHERE transaction_id = NEW.transaction_id
     GROUP BY currency HAVING SUM(amount_cents) <> 0 LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'unbalanced group %: % %',
              NEW.transaction_id, bad.delta, bad.currency
              USING ERRCODE = 'integrity_constraint_violation';
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER entries_group_balances
    AFTER INSERT ON entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_group_balances();

-- The remedy must still post ------------------------------------------------------
-- The floor applies to ordinary debits and NOT to compensating entries. The constraint
-- is conditioned on posting_type, not deleted; an ordinary debit still cannot overdraw.
ALTER TABLE accounts DROP CONSTRAINT accounts_balance_cents_check;

CREATE FUNCTION assert_balance_floor() RETURNS trigger AS $$
DECLARE ptype text; bal bigint;
BEGIN
    SELECT posting_type INTO ptype
      FROM ledger_transactions WHERE id = NEW.transaction_id;
    IF ptype <> 'ordinary' THEN
        RETURN NULL;   -- a clawback of already-spent funds MUST be able to post
    END IF;
    SELECT balance_cents INTO bal FROM accounts WHERE id = NEW.account_id;
    IF bal < 0 THEN
        RAISE EXCEPTION 'balance_floor: account % would be %', NEW.account_id, bal
              USING ERRCODE = 'check_violation', CONSTRAINT = 'balance_floor';
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER entries_balance_floor
    AFTER INSERT ON entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION assert_balance_floor();

-- Immutability is a permission the database enforces, not a comment. A test asserts
-- these grants are absent.
REVOKE UPDATE, DELETE ON entries FROM ledger_app;

-- The suspense account and the break log live in the chart of accounts, so a
-- discrepancy still leaves the trial balance balanced.
INSERT INTO accounts (id, name, currency) VALUES
    (2, 'revenue:transfer_fees', 'USD'),
    (3, 'suspense:reconciliation', 'USD');

CREATE TABLE breaks (
    id           bigserial   PRIMARY KEY,
    detected_at  timestamptz NOT NULL DEFAULT now(),
    source_a     text        NOT NULL,
    source_b     text        NOT NULL,
    account_id   bigint      REFERENCES accounts(id),
    currency     char(3)     NOT NULL,
    amount_cents bigint      NOT NULL,
    status       text        NOT NULL DEFAULT 'open'
);

-- Opening balances are backfilled BEFORE the first reconciliation run, or the
-- per-account comparison is broken on day one and the alert gets muted.
INSERT INTO ledger_transactions (id, kind, posting_type, idempotency_key)
VALUES ('00000000-0000-0000-0000-000000000001', 'opening', 'ordinary', 'opening-balances');
INSERT INTO entries (transaction_id, account_id, currency, amount_cents)
SELECT '00000000-0000-0000-0000-000000000001', a.id, a.currency,
       a.balance_cents - COALESCE(
           (SELECT SUM(e.amount_cents) FROM entries e WHERE e.account_id = a.id), 0)
  FROM accounts a;
-- (the opening group is balanced by an equal and opposite leg on 'equity:opening')
```

```python
# ledger.py
"""Internal transfer ledger. One posting entrypoint; every group balances per currency."""
import uuid
from dataclasses import dataclass

from psycopg.errors import CheckViolation, UniqueViolation
from psycopg.types.json import Jsonb

FEE_BPS = 25
FEE_REVENUE_ACCOUNT = 2


@dataclass(frozen=True)
class Leg:
    account_id: int
    currency: str
    amount_cents: int


class LedgerError(Exception): ...
class InsufficientFunds(LedgerError): ...
class UnbalancedGroup(LedgerError): ...
class AlreadyReversed(LedgerError): ...
class IdempotencyMismatch(LedgerError): ...


def post(conn, *, legs, kind, idempotency_key, posting_type="ordinary", reverses=None):
    """The single write path into the journal.

    Operation identity: `idempotency_key` is a required keyword argument. There is no
    default and no `Optional`, so a caller that has not thought about replay does not
    compile.

    Nets to zero in every unit of account: "per currency" is inside the assert.
    `sum(legs) == 0` passes on +100 JPY / -100 USD; this does not.

    Conservation belongs to the write path: the group is rejected before it can exist.
    The database trigger is the backstop that no caller can bypass; this is the typed
    error callers handle.
    """
    by_currency = {}
    for leg in legs:
        by_currency[leg.currency] = by_currency.get(leg.currency, 0) + leg.amount_cents
    if any(delta != 0 for delta in by_currency.values()):
        raise UnbalancedGroup(by_currency)

    fingerprint = sorted([l.account_id, l.currency, l.amount_cents] for l in legs)
    txid = uuid.uuid4()

    try:
        with conn.transaction():
            try:
                with conn.transaction():  # savepoint: a conflict here is recoverable
                    conn.execute(
                        "INSERT INTO ledger_transactions (id, kind, posting_type,"
                        " idempotency_key, request_fingerprint, reverses)"
                        " VALUES (%s,%s,%s,%s,%s,%s)",
                        (txid, kind, posting_type, idempotency_key,
                         Jsonb(fingerprint), reverses),
                    )
            except UniqueViolation as e:
                if e.diag.constraint_name == "ledger_transactions_reverses_uniq":
                    raise AlreadyReversed(reverses) from e
                # The unique-violation path resolves to the winner's result. No
                # SELECT-then-INSERT: the check and the claim are one statement.
                row = conn.execute(
                    "SELECT id, request_fingerprint FROM ledger_transactions"
                    " WHERE idempotency_key = %s",
                    (idempotency_key,),
                ).fetchone()
                if row[1] != fingerprint:
                    raise IdempotencyMismatch(idempotency_key) from e
                return row[0]

            for leg in legs:
                conn.execute(
                    "INSERT INTO entries (transaction_id, account_id, currency,"
                    " amount_cents) VALUES (%s,%s,%s,%s)",
                    (txid, leg.account_id, leg.currency, leg.amount_cents),
                )
                # A stored balance is a cache: it moves in the same transaction as
                # the entry that justifies it, never in a statement after COMMIT.
                conn.execute(
                    "UPDATE accounts SET balance_cents = balance_cents + %s"
                    " WHERE id = %s AND currency = %s",
                    (leg.amount_cents, leg.account_id, leg.currency),
                )
            # Fire the deferred triggers HERE, inside our own error handling. Without
            # this they fire at whatever outermost COMMIT encloses us, and a caller who
            # wraps `post` in their own transaction gets the raw CheckViolation at a
            # point where `except` below can no longer see it.
            conn.execute("SET CONSTRAINTS entries_group_balances,"
                         " entries_balance_floor IMMEDIATE")
    except CheckViolation as e:
        # The raw psycopg error does not escape the typed hierarchy mid-clawback.
        if e.diag.constraint_name == "balance_floor":
            raise InsufficientFunds(str(e)) from e
        raise
    return txid


def transfer(conn, *, from_id, to_id, amount_cents, currency, idempotency_key):
    """Enumerate the legs before writing the insert. Three, not two."""
    fee = -(-amount_cents * FEE_BPS // 10_000)  # ceil what the system collects
    net = amount_cents - fee
    return post(
        conn,
        kind="transfer",
        idempotency_key=idempotency_key,
        legs=[
            Leg(from_id, currency, -amount_cents),
            Leg(to_id, currency, net),
            Leg(FEE_REVENUE_ACCOUNT, currency, fee),   # the leg that was missing
        ],
    )


def reverse(conn, txid, *, idempotency_key):
    """A reversal is a compensating group that is allowed to overdraw.

    `posting_type='reversal'` is what exempts it from the balance floor. That is why
    the floor could stay in place instead of being dropped, and why a clawback works
    against a frozen account: freeze first, then claw back, in that order.
    """
    with conn.transaction():
        legs = conn.execute(
            "SELECT account_id, currency, amount_cents FROM entries"
            " WHERE transaction_id = %s ORDER BY id",
            (txid,),
        ).fetchall()
        if not legs:
            raise LedgerError(f"unknown transaction {txid}")

        rid = post(
            conn,
            kind="reversal",
            posting_type="reversal",
            reverses=txid,
            idempotency_key=idempotency_key,
            legs=[Leg(a, c, -amount) for a, c, amount in legs],
        )
        conn.execute(
            "UPDATE ledger_transactions SET reversed_by = %s WHERE id = %s", (rid, txid)
        )
    return rid
```

```python
# reconcile.py: the scheduled entrypoint. Reconciliation runs in production, or it
# does not exist.
#
#   */15 * * * *  python -m ledger.reconcile
#
"""Reads through a path independent of the writer: the writer maintains
accounts.balance_cents; this recomputes from entries and from the custodian's own
statement, and compares all three."""
import os

import httpx

from .ledger import Leg, post

# No default. Import fails rather than alerting into a void.
ALERT_SINK = os.environ["LEDGER_ALERT_SINK"]
SUSPENSE_ACCOUNT = int(os.environ["LEDGER_SUSPENSE_ACCOUNT"])
CUSTODY_ACCOUNT = int(os.environ["LEDGER_CUSTODY_ACCOUNT"])
ESCALATE_AFTER_HOURS = int(os.environ["LEDGER_BREAK_ESCALATION_HOURS"])


def run(conn):
    found = []

    # Axis 1, balance. Materialised against journal: a stored balance is a cache.
    for account_id, currency, drift in conn.execute("""
        SELECT a.id, a.currency,
               a.balance_cents - COALESCE(SUM(e.amount_cents), 0)
          FROM accounts a LEFT JOIN entries e ON e.account_id = a.id
         GROUP BY a.id, a.currency, a.balance_cents
        HAVING a.balance_cents <> COALESCE(SUM(e.amount_cents), 0)
    """):
        found.append(("accounts.balance_cents", "entries", account_id, currency, drift))

    # Axis 2, clearing. Every clearing account returns to zero.
    for account_id, currency, bal in conn.execute("""
        SELECT id, currency, balance_cents FROM accounts
         WHERE name LIKE 'clearing:%' AND balance_cents <> 0
    """):
        found.append(("clearing", "zero", account_id, currency, bal))

    # Axis 3, completeness, against the external authority named for this quantity.
    # `custodian_balances` is written by a separate importer that never touches `entries`.
    for currency, ours, theirs in conn.execute("""
        SELECT c.currency, COALESCE(SUM(a.balance_cents), 0), c.amount_cents
          FROM custodian_balances c
          LEFT JOIN accounts a
                 ON a.currency = c.currency AND a.name LIKE 'customer:%'
         GROUP BY c.currency, c.amount_cents
        HAVING COALESCE(SUM(a.balance_cents), 0) <> c.amount_cents
    """):
        found.append(("customer_balances", "custodian", None, currency, ours - theirs))

    for source_a, source_b, account_id, currency, delta in found:
        conn.execute(
            "INSERT INTO breaks (source_a, source_b, account_id, currency, amount_cents)"
            " VALUES (%s,%s,%s,%s,%s)",
            (source_a, source_b, account_id, currency, delta),
        )
        if source_b == "custodian":
            # A disagreement with an EXTERNAL record posts to suspense, in the chart
            # of accounts, so the trial balance still balances while a human works it.
            # Not a nullable column, not a log line.
            post(conn, kind="reconciliation_break", posting_type="clawback",
                 idempotency_key=f"break-custodian-{currency}-{delta}-{run_id()}",
                 legs=[Leg(CUSTODY_ACCOUNT, currency, -delta),
                       Leg(SUSPENSE_ACCOUNT, currency, delta)])
        # A materialised-against-journal drift gets NO auto-posting. The journal is
        # the authority; writing a correcting entry would launder a bug into history.

    aged = conn.execute(
        "SELECT count(*) FROM breaks WHERE status = 'open'"
        " AND detected_at < now() - make_interval(hours => %s)",
        (ESCALATE_AFTER_HOURS,),
    ).fetchone()[0]

    if found or aged:
        httpx.post(ALERT_SINK, json={
            "text": f"ledger reconciliation: {len(found)} new breaks, {aged} aged past "
                    f"{ESCALATE_AFTER_HOURS}h"
        }, timeout=10)
    return found
```

```python
# tests/test_reconcile_detects.py: a detector that has never detected is not known
# to detect. The cheapest test in the suite.
def test_reconciliation_detects_an_external_break(fresh_db, alert_sink):
    """Feed it a known discrepancy and assert it produces the break AND the alert.

    Running the job proves it runs. Nothing else proves it detects, and every broken
    reconciliation anyone has watched ship, shipped green. `fresh_db` is a freshly
    migrated database, so an un-backfilled opening balance fails here rather than
    muting production.
    """
    conn = fresh_db
    alice, bob = seed_accounts(conn, usd=100_00)
    transfer(conn, from_id=alice, to_id=bob, amount_cents=10_00,
             currency="USD", idempotency_key="t1")
    set_custodian_statement(conn, currency="USD", amount_cents=200_00 - 13)

    found = reconcile.run(conn)

    assert [(f[1], f[3], f[4]) for f in found] == [("custodian", "USD", 13)]
    row = conn.execute(
        "SELECT source_a, source_b, amount_cents, status FROM breaks"
    ).fetchone()
    assert row == ("customer_balances", "custodian", 13, "open")
    assert trial_balance(conn) == 0            # suspense keeps the books balanced
    assert balance(conn, SUSPENSE_ACCOUNT) == 13
    assert len(alert_sink.messages) == 1       # exactly one, to the routed channel


def test_materialised_drift_breaks_but_does_not_self_correct(fresh_db, alert_sink):
    """The recompute raises a break; it does not fix the balance in place."""
    conn = fresh_db
    alice, bob = seed_accounts(conn, usd=100_00)
    before = journal_sum(conn, bob)
    conn.execute("UPDATE accounts SET balance_cents = balance_cents + 7 WHERE id = %s",
                 (bob,))

    found = reconcile.run(conn)

    assert [(f[2], f[4]) for f in found] == [(bob, 7)]
    assert journal_sum(conn, bob) == before    # nothing was posted to paper over it
    assert len(alert_sink.messages) == 1


def test_entries_are_append_only(fresh_db):
    """The grant is the artifact, not the comment."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        fresh_db.execute("UPDATE entries SET amount_cents = 0")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        fresh_db.execute("DELETE FROM entries")


def test_clawback_can_overdraw_and_an_ordinary_debit_cannot(fresh_db):
    """The floor is conditioned on posting_type, not deleted."""
    conn = fresh_db
    alice, bob = seed_accounts(conn, usd=100_00)
    txid = transfer(conn, from_id=alice, to_id=bob, amount_cents=100_00,
                    currency="USD", idempotency_key="t1")
    spend_everything(conn, bob)                       # bob's balance is now 0

    with pytest.raises(InsufficientFunds):            # ordinary debit: still refused
        transfer(conn, from_id=bob, to_id=alice, amount_cents=1,
                 currency="USD", idempotency_key="t2")

    reverse(conn, txid, idempotency_key="r1")         # clawback: must succeed
    assert balance(conn, bob) < 0
```

---

## What changed, and what did not

**Changed.** Four things and one of their consequences. The fee got its counterparty leg, and the posting
API now takes a set of legs and rejects the group rather than trusting the caller. `idempotency_key`
became a required keyword with a stored fingerprint and a unique-violation path that resolves to the
winner instead of raising `23505`. The two SQL comments became `reconcile.py`, a scheduled entrypoint
with an alert destination that has no default, a break table, an aging threshold, and a test that seeds a
known discrepancy and asserts the alert fired. And the balance floor moved from an unconditional table
`CHECK` to a deferred trigger conditioned on `posting_type`, so the clawback can post and an ordinary
debit still cannot.

**Not changed, deliberately.** The transfer and reversal *arithmetic* is untouched. It was correct, and it
is the part most implementations get right. The deterministic lock ordering was correct; so was
"a reversal is a compensating entry, never a mutation of the original"; so was the double-reversal guard,
which only gained a unique index because a guard implemented as a `SELECT` is not a guard under
concurrency. Integer minor units stayed. `entries` is still one table with a currency dimension. It was
not split into one ledger per currency, which is a schema people spend a year unwinding.

**One thing was deleted, not moved.** The first SQL comment, *"every transaction's entries net to zero"*,
does not appear anywhere in the corrected code. It is not in `reconcile.py`. Under *The balanced set commits
whole, and a posted entry is immutable*, a runtime
"do the books balance" check that can fire means either a bypass path exists, in which case close the
bypass, or it is not a conservation breach at all but a disagreement with an external record, which is
what axis 3 is for. Once the posting API and the deferred trigger make an unbalanced group unrepresentable,
a job that looks for one is checking that Postgres works.

**Still absent, and named.** `REVOKE UPDATE, DELETE ON accounts` with the chokepoint as a `SECURITY DEFINER`
function is the stronger form of `fin-ledger`'s *One entrypoint, and the solvency check lives inside it*, and
it is not implemented here; the application role still writes `accounts.balance_cents` directly, and axis 1
is what makes that survivable. Bitemporality, which `fin-ledger` carries in
[bitemporality-and-close.md](../../skills/fin-ledger/references/bitemporality-and-close.md), is
absent because this ledger has no reporting period and no back-dating. Both become required the moment
this system has an accounting close.

---

## The output the review ends with

This ledger is the system of record for almost everything it holds. It assigns the transaction ids other
systems consume, it decides what a customer's balance is, and no external oracle holds the same numbers,
so nothing outside it can tell it that it is wrong. One quantity is different: the aggregate customer
position, which the custodian also holds and which axis 3 compares against. Authority is a property of a
quantity rather than of the codebase, so the line says MIXED and qualifies the one that differs.

For the SELF quantities `fin-verification`'s *When authority is SELF there is nothing to reconcile
against* sets the evidence: replay, conservation and a planted break rather than a comparison against
somebody else's copy. For the aggregate the custodian statement is a real oracle and the evidence is the
comparison. Exposure is `customer`, because the money at risk is the customer's, and this ledger is also
the record other systems consume, which raises the bar rather than lowering it.

`fin-ledger` asks for its per-verb contract table only when a quantity in scope is SELF **and** the change
adds, routes or reshapes a write to the entries. This change does exactly that, so the evidence anchors
below are the ones that table would have carried.

`EVIDENCE` names functions rather than lines, because the code under review is a listing in this file. In a
real response every one of them is a `file:line`.

```
authority: MIXED · exposure: customer
  balances, postings and transaction ids  SELF
  aggregate customer position             EXTERNAL (custodian statement)

FINDING   Every transfer debits a fee from the sender and credits it to nobody. The trial balance is off by
          the cumulative fee take from the first transfer onwards, and the discrepancy grows with volume.
WHY       The group is `−amount` and `+net`, which sums to `−fee`, not zero. There is no revenue account
          leg and no revenue account. The materialised balances agree with the entries, so nothing in the
          system disagrees with anything else. The money is simply not there.
EVIDENCE  ledger.py transfer(), the two INSERTs into entries
FIX       ledger.py post() takes the set of legs and rejects any group that does not net to zero in every
          unit of account separately; ledger.py transfer() supplies the third leg to FEE_REVENUE_ACCOUNT,
          and migrations/002_conservation.sql assert_group_balances is the deferred backstop no caller,
          ORM, psql session or migration can bypass.
TEST      A transfer's entries sum to zero per currency, and a two-leg group carrying a fee is rejected by
          both the typed error and the trigger.

FINDING   Two callers, or one caller retrying, produce two real transfers; and a replayed key with a
          different body lets reverse_transfer mark a transfer reversed while writing zero compensating
          entries.
WHY       The mechanism is built and then made optional: the column is `UNIQUE` and the parameter is
          `Optional[str] = None`, with enforcement deferred to prose about the API layer. Callers that omit
          it get no protection. Callers that supply it hit a SELECT-then-INSERT race with no `ON CONFLICT`,
          so an honest concurrent retry raises a raw `UniqueViolation` outside the `TransferError`
          hierarchy, and nothing compares the stored `from`/`to`/`amount` against the request.
EVIDENCE  ledger.py transfer() signature and its SELECT on idempotency_key;
          migrations/001_ledger.sql idempotency_key text UNIQUE
FIX       A required keyword argument with no default at ledger.py post(), with
          `ALTER COLUMN idempotency_key SET NOT NULL` behind it. The INSERT is the claim, and a
          UniqueViolation resolves to the winner's row only after comparing request_fingerprint field by
          field, raising IdempotencyMismatch when it differs.
TEST      A concurrent replay of one key returns one transaction id and posts one group; the same key with
          a different body raises rather than reporting work done that was never done.

FINDING   The control that would have caught the missing fee leg does not run, which turns a one-line bug
          into an unbounded, undetected one.
WHY       The two invariant queries are correct and they run nowhere. There is no scheduled entrypoint, no
          alert destination, no break record and no owner. The usual version of this ships annotated
          "worth running as a cron". Zero of the reps that were probed on conservation passed, in either
          domain where it was probed.
EVIDENCE  migrations/001_ledger.sql, the invariant comment block
FIX       reconcile.py run(), scheduled at `*/15 * * * *`, reading through a path the writer does not
          share: three axes, a breaks table, an aging threshold at ESCALATE_AFTER_HOURS, and a custodian
          disagreement posted to a real suspense account so the trial balance still balances while a human
          works it. ALERT_SINK and its siblings are environment reads with no default, so import fails
          rather than alerting into a void.
TEST      tests/test_reconcile_detects.py test_reconciliation_detects_an_external_break plants a 13-cent
          discrepancy against a freshly migrated database and asserts the break record, the suspense
          posting and exactly one alert; test_materialised_drift_breaks_but_does_not_self_correct asserts
          the recompute raises rather than papering over the drift.

FINDING   When the recipient has spent the money, the fraud reversal aborts, so the safety operation cannot
          be performed at the moment it is needed. Full loss of the disputed amount, plus whatever drains
          during the unfreeze the standard flow is forced into.
WHY       `CHECK (balance_cents >= 0)` is unconditional, so reverse_transfer's debit drives the balance
          negative and Postgres rejects it. `allow_overdraft` is accepted and never read: dead code. Only
          transfer() catches CheckViolation, so the raw psycopg error escapes the typed hierarchy
          mid-clawback. And AccountNotActive blocks the standard flow of freeze first, then claw back,
          forcing an unfreeze that reopens exactly the drain window the freeze existed to close.
EVIDENCE  migrations/001_ledger.sql accounts.balance_cents CHECK; ledger.py reverse_transfer(), the
          allow_overdraft parameter
FIX       The floor becomes migrations/002_conservation.sql assert_balance_floor, a deferred trigger
          conditioned on posting_type, so an ordinary debit still cannot overdraw and a reversal or
          clawback can. ledger.py reverse() posts with posting_type='reversal', and ledger.py post() fires
          the deferred triggers inside its own error handling so CheckViolation becomes InsufficientFunds
          instead of surfacing at a caller's COMMIT. The constraint is conditioned, not dropped.
TEST      tests/test_reconcile_detects.py test_clawback_can_overdraw_and_an_ordinary_debit_cannot; and
          test_entries_are_append_only, because immutability is a grant the database enforces rather than a
          review convention.

UNRESOLVED: REVOKE UPDATE, DELETE ON accounts with the chokepoint as a SECURITY DEFINER function, the
stronger form of fin-ledger's One entrypoint, and the solvency check lives inside it (the application role
still writes accounts.balance_cents directly; axis 1 of reconcile.py is the compensating control)

VERDICT   SHIP
```

The one control still named `UNRESOLVED` is the revoked grant on `accounts`, and it is on the page rather
than deleted from it, which is the whole difference between a named risk and a comment about one.
