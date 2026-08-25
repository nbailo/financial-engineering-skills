---
name: fin-ledger
description: >-
  Financial correctness for authoritative balances and books: double-entry postings, balanced
  sets, immutable entries, holds and available versus posted balance, reversals and corrections,
  currency as a dimension, solvency chokepoints, accruals and period close. Use when your code
  writes or reads a balance other systems trust, including TigerBeetle and Formance. For
  processor lifecycles use fin-payments.
license: MIT
---

# The balance other systems trust

You are editing the system of record. Nothing downstream re-derives your arithmetic; it quotes you, and the
number this code writes becomes what someone is owed. A ledger that is not a mirror of an external processor
has no authority above it to reconcile against, which makes it T3 by definition. Processor lifecycles belong
to `fin-payments`, chain mechanics to `fin-onchain`, matching and allocation to
`fin-matching-and-settlement`, and rounding direction plus outbound call identity to `fin-money-core`. At
T3, which is most real ledgers, load `fin-verification` alongside this skill: it owns the evidence a tier
requires.

## Workflow

1. Name the economic fact this posting records, and state who is owed what once it lands.
2. Fix the account model and the sign convention before writing a single leg.
3. Make the balanced set atomic and the entry immutable: one entrypoint, no update, no delete.
4. Separate provisional, available and posted. A hold is a state, not a subtraction.
5. Establish the posting's identity, and decide what a duplicate request returns.
6. Handle corrections by reversal, never by mutation, and decide whether the question is bitemporal.
7. Name the solvency chokepoint and prove no write path bypasses it.
8. Assert conservation, load only the references this change needs, and implement the controls and their
   tests before calling the path complete.

## When this applies

Your code holds the number that decides what someone is owed, and other systems read it rather than
recompute it. That includes anything that records an economic fact as accounting entries, anything that
increments or overwrites a stored balance, anything that reserves funds against a future spend, and anything
that corrects a balance after the fact. If a wrong value here would be believed by a statement, an
authorisation decision or a payout, you are in this skill.

Routing hints, not the definition: a table named `entries`, `postings`, `journal`, `ledger_entries`,
`ledger_transactions`, `accounts`, `balances`, `holds`; an import of `tigerbeetle` or a Formance/Numscript
client; a `debit`/`credit` column or a `direction` enum; an `amount` alongside an `account_id`; the
identifiers `available`, `pending`, `posted`, `hold`, `reserve`, `reversal`, `clawback`, `suspense`,
`trial_balance`, `period_close`, `effective_at`; or a path that credits an account from a deposit, fill,
capture or payout.

Not this skill: the processor's own state machine and its capture, refund and dispute windows go to
`fin-payments`. Confirmation depth, reorg detection, nonces and transaction construction go to
`fin-onchain`. Order matching, allocation and settlement batching go to `fin-matching-and-settlement`.
Rounding direction, decimal contexts, and deriving an idempotency key for an **outbound** call go to
`fin-money-core`.

## Core rules

### A posting is a set of legs that nets to zero in every unit of account

One economic event produces one group of entries. Value leaves an account only by arriving in another,
including the accounts nobody thinks of as accounts: the counterparty you hold funds with, the revenue
account that receives a fee you charged, the expense account for a cost you actually paid.

**Shape**

```
event -> enumerate every account the event moves value across
      -> partition the legs by unit of account
      -> commit only if every partition sums to zero
```

Legs get enumerated from the paying user's point of view, so the visible half is written and the invisible
half is not. A withdrawal with a fee charged to the user and a network cost paid out of your own float is at
minimum four legs. A sum taken across mixed units hides the missing leg entirely.

**How it appears**: the shipped assertion is
`assert all(sum(l.amount for l in legs if l.currency == c) == 0 for c in currencies(legs))`. The copyable
`assert sum(legs) == 0` passes on `+100 JPY, -100 USD` and is therefore not the assertion. Counterparty legs
carry names like `world`, `nostro`, hot wallet, bank, processor. *Measured: three of three reps wrote a
journal and three of three journals failed to balance (a single leg, a missing hot-wallet counterparty, and
one settlement group summing to `-2 × amount` in a table its own comment called "append-only
double-entry").* Per *a comment is a claim*, that comment was the thing that let it survive review.

### Conservation belongs to the write path, not to a checker that runs later

If an unbalanced state can exist long enough for a checker to find it, the write API permitted it. The
posting entrypoint accepts the whole set and either commits all of it or rejects it, in the same
transaction, with a typed error naming the imbalance.

**Shape**

```
post(set of legs) -> validate the net per unit inside the transaction
                  -> commit everything, or return a typed rejection having written nothing
```

A runtime "do the books balance" check that can fire proves a bypass exists; close the bypass instead of
alerting on it. And halting is the wrong response even when it fires, because a discrepancy against an
external record is a reconciliation break, which takes a suspense posting and the reconciliation rule below.

**How it appears**: a typed rejection such as `UnbalancedGroup { currency, delta }`, returned to the caller,
so no caller can create an unbalanced state. TigerBeetle documents `exceeds_credits` as *"The transfer was
not created."*: the rejection leaves nothing behind, not a partial group.

### An entry is immutable once posted, and immutability is a permission the database enforces

Immutability that lives in a review convention is not immutability. State in the schema which lifecycle
states are mutable, then remove the ability to mutate the rest.

**Shape**

```
migration: revoke UPDATE and DELETE on the entry table from the application role
           (or install a trigger that raises)
test:      assert the grant is absent
correction: a new record, never an edit
```

"Everything is immutable" reads as immutable from creation, which contradicts every two-phase design and so
gets quietly relaxed by the first person who needs a pending row to change. Immutable **once posted** is the
property that actually holds and can be enforced.

**How it appears**: Modern Treasury states it as *"a ledger transaction is mutable while pending and
immutable once posted"*. The evidence is a migration plus a test that reads the grant, never a docstring.

### A control that stops ordinary debits must not stop the remedy for them

Every guard on a balance is written with the honest user in mind, and every guard is exercised during an
incident. Condition each one on the posting type, not on the account, so the compensating entry can always
land.

**Shape**

```
ordinary debit      -> guard applies
compensating entry  -> guard conditioned on posting type, not removed
reversal link       -> unique: one original resolves to exactly one reversal
```

The standard fraud flow is freeze the recipient, then claw back. A guard keyed on account state forces an
unfreeze, reopening exactly the drain window the freeze existed to close. A non-negativity constraint keyed
on the account makes any overdraft-permitting flag dead code at the one moment the funds are already spent.

**How it appears**: `CHECK (balance_cents >= 0)` governs ordinary debits and not compensating entries, so
permit overdraft on the **reversal posting type specifically** with a partial index or a `CHECK` conditioned
on `posting_type`. **Do not delete the constraint.** Make sure a raw `CheckViolation` cannot escape the
typed error hierarchy mid-clawback. Raising `AccountNotActive` on a frozen account blocks reversals and
clawbacks, which must post while the account is frozen; only customer-initiated debits are blocked. Put a
uniqueness constraint on `reverses_transaction_id`, with `reversed_by_transaction_id` on the original, so
one transaction cannot be reversed twice by two operators or by an operator plus a retry. *Measured:
`CHECK (balance_cents >= 0)` made `allow_overdraft=True` dead code in a shipped reversal path.*

### A repeated posting request returns the original outcome only if it is the same request

The identity of a posting is supplied by the caller and required by the signature. A hit on that identity
must prove the stored economic content matches the request, field by field, before it reports success. This
specialises *durable intent before the external effect* onto the ledger's own write.

**Shape**

```
identity required at the signature -> look it up
  miss -> write the balanced set under that identity
  hit  -> compare stored (source, destination, amount, unit) against the request
          equal  -> return the original result
          differ -> distinct typed error naming the field that differs
```

A unique index plus an insert that ignores conflicts accepts a *different* request under a used identity and
reports success, which is how a replay marks work done that was never done. An optional identity, with
enforcement deferred to prose about "the API layer", is no identity at all.

**How it appears**: `idempotency_key: str`, positional and required, never
`idempotency_key: Optional[str] = None`. TigerBeetle ships one code per mismatched field:
`exists_with_different_debit_account_id = 37`, `_credit_account_id = 38`, `_amount = 39`, `_flags = 36`,
explicitly *"to prevent silent data inconsistencies"*. `INSERT ... ON CONFLICT DO NOTHING` keyed on the
idempotency key alone is the failing shape. *Measured: both partial reps built `idempotency_key text
UNIQUE`, made it optional, and compared nothing; the unvalidated replay let `reverse_transfer` mark a
transfer reversed **while writing zero compensating entries**.*

### Provisional, available and posted are three numbers, and only one of them authorises

A hold is a state of the funds, not a subtraction from them. Reserving is a committed fact with its own
expiry, and the number that authorises a spend is derived from posted balance minus live holds.

**Shape**

```
available = posted - active holds, holds filtered by expiry at read time
reserve -> check the invariant at reserve time, commit the hold with an intrinsic expiry
resolve -> post at most the reserved amount; the reservation is released in full
        -> an over-post is rejected, never clamped; resolution happens exactly once
```

Release that depends on a callback, a cron or the happy path completing strands funds precisely when the
callback path is the one that failed, so expiry has to be enforced by the reader. Counting inbound
provisional credit as available lets a customer spend money that has not arrived. Clamping an over-post
silently changes an amount someone is owed.

**How it appears**: the reader filters (`WHERE expires_at > now()`). TigerBeetle's
`debits_must_not_exceed_credits` rejects when `debits_pending + debits_posted + amount > credits_posted`;
`credits_pending` is absent from the right-hand side, deliberately. Two-phase resolution: posting less than
reserved restores the remainder, posting more is `exceeds_pending_transfer_amount`, voiding must be exact,
and a pending transfer resolves once (`pending_transfer_already_posted`, `pending_transfer_already_voided`).

### A stored balance is a cache, and a cache with no drift detector is a rumour

Any number kept alongside the entries can disagree with them. It is written in the same transaction as the
entries that justify it, and it is recomputed from those entries, independently, on a schedule.

**Shape**

```
transaction { insert the entries ; update the balance, carrying a monotonic version }
scheduled:  recompute from the entries, order independent -> compare -> raise a break
```

Writing entries, committing, then updating the balance leaves a window in which a crash makes the two
permanently disagree, with no error and no log line. A recompute that repairs the number in place hides how
often that happens and destroys the evidence of why.

**How it appears**: `SELECT SUM(amount) FROM entries WHERE account_id = ?` is the independent recompute.
Never `INSERT INTO entries ...; COMMIT;` followed by a separate `UPDATE balances`. The recompute raises a
break row under the rule below; it does not fix the balance.

### Every balance you report has a named authority, a join key, and a comparison that actually runs

This is *reconciliation runs in production* in ledger mechanism. Name the external record for each economic
quantity, name the key you join on, and ship the comparison as a scheduled entrypoint that reads the ledger
through a path the writer does not share.

**Shape**

```
scheduled entrypoint -> read through a path independent of the writer
                     -> compare against the external authority on a stated join key
                     -> post the difference to a suspense account in the chart of accounts
                     -> raise a break row, with aging, an escalation threshold and a sweep
```

A comparison that reads through the writer's own code finds arithmetic bugs and never a missing write.
Opening balances that were never backfilled break a per-account comparison on its first run and mute the
alert permanently. Recording a difference in a nullable column or a log line instead of a real account
leaves the trial balance unbalanced, which is the one signal you were trying to protect.

**How it appears**: the break row carries `detected_at`, `source_a`, `source_b`, `amount`, `currency`,
`status`. **Opening balances are backfilled by a migration before the first run.** The alert destination is
a config key with no default. Reconcile on three axes: completeness (are all records present?), clearing
(did every clearing account return to zero?), and balance. Precedent for continuing to operate with a
difference open: Fed FAM §4.50's **Difference account** absorbs *"an out-of-balance condition resulting from
the normal operation of a department"* and is swept monthly; SEC 17a-11(c) says notify same day, remediate
in 48 hours, **do not cease operating**. *Measured: the near-miss wrote flawless SQL, ran it nowhere, and
its per-account comparison was broken on day one by un-backfilled openings. Transfer and reversal arithmetic
is written correctly unaided; journals that do not balance and reconciliations that never run ship at close
to 100%.*

## Currency is a dimension of the entry, not a shape of the schema

No expression multiplies or adds two amounts in different units, and every group balances per unit. An FX
movement is a balanced two-account transaction, not a rate applied to a stored number.

```
fx: debit source-unit account, credit target-unit account
    record rate, provenance, side and pivot on the transaction
    book the spread to its own revenue account
    post rounding residue to a named residue account
```

The ledger dimension carries currency **and scale**, and it is immutable: never change the asset scale or
currency of an existing ledger, migrate to a new one. This is one journal with a currency dimension and a
per-unit balance constraint on the group, not one table or one database per currency.

## Solvency and its chokepoint

State the system-level solvency invariant explicitly (`Σ customer balances <= custodied assets`, **per
asset**) and assert it continuously against the custodian's own figure. Enumerate every function that can
change a balance and show that each terminates in the one chokepoint that checks it.

```
every balance-changing path -> the single chokepoint that evaluates solvency -> the write
make the bypass unrepresentable, not merely unlikely
```

**Make the bypass unrepresentable rather than provable:** `REVOKE UPDATE, DELETE ON balances` from the
application role so the only writer is the chokepoint's own role or a `SECURITY DEFINER` function, with a
test asserting the grant is absent. "Prove each path terminates in the chokepoint" is an architecture-review
question answered with a paragraph; a revoked grant is a fact a test can read. **Euler's `donateToReserves`
was the single path without the health check: ~$197M.** A per-account override on a solvency, credit-limit
or liquidation check is an unbounded liability generator: it raises the tier and requires field-level audit
logging of every change.

## Accrual is a posting

**Interest, fees and funding create money in an income or expense account by posting; they never mutate a
balance field.** IFRS 9 defines the effective interest method as the method used in the *"allocation and
recognition of the interest revenue or interest expense in profit or loss"*. Recognition is an event. For
every accrual run, `Σ credited to customer accounts == Σ debited to interest expense`, exactly, to the minor
unit.

```
accrual run -> read the stored period marker
            -> already accrued for this period ? post nothing : post the balanced set
            -> advance the marker in the same transaction as the postings
```

**Key every accrual on a stored period marker and make a re-run inside the same period post nothing.**
Compound v2: `if (accrualBlockNumberPrior == currentBlockNumber) return;`; Aave:
`if (reserveCache.reserveLastUpdateTimestamp == uint40(block.timestamp)) return;`. The test runs the job
twice for one period and asserts the second run wrote zero rows. This is *proven coverage before the cursor
advances* applied to time.

**Where per-account posting every period is infeasible, use a scaled-balance index and keep exactly one
aggregate posting per period for the pool's own income** (Aave's `_accrueToTreasury`). The index makes the
per-account work O(1); the aggregate posting is what keeps the journal balanced.

**A back-dated accrual posts a catch-up adjustment in the current open period; it never re-runs over a
closed period** (IFRS 9 B5.4.6 recomputes at the *original* effective interest rate and recognises the
adjustment now). Period attribution is a correctness property distinct from amount correctness: June's
accrual posted in July balances perfectly and is wrong in every statement, interest certificate, covenant
test and tax filing. For each closed period, assert that the sum of postings *effective* in that period is
unchanged after any later correction run.

## Seams

S1, S2 and S3 are each stated from both sides: the same boundary appears in `fin-payments`, `fin-onchain`
and `fin-exchange-integration` in those skills' own vocabulary, and a contradiction between the two
statements is a suite defect to be reported, not a judgement call.

**S1 · payments ↔ ledger.** Every payment state transition emits **exactly one balanced ledger transaction**
whose id derives from the payment's idempotency key. Every clearing account between payment states returns
to zero, monitored as a continuous assertion. **Never derive a balance by scanning payment objects.**
Authorizations are reserved amounts in the payments layer, not ledger entries; only captures, refunds,
disputes, fees and settlement adjustments post.

**S2 · onchain ↔ ledger.** **(i) Identity.** A deposit credit is exactly one balanced ledger transaction
whose idempotency key is `(chainId, blockHash, txHash, logIndex)`, never the tx hash and never
`balance += amount`; the same log re-observed after a reconnect, a backfill overlap, or a provider failover
is a no-op. **(ii) Staging.** The credit posts on observation to a per-user **PENDING (unavailable)**
account and moves to **AVAILABLE** only at the credit policy's finality (L1 finality for rollups, not L2
block count; below the policy depth, credit only inside a stated exposure cap you are willing to lose).
Withdrawal and onward transfer authorise from AVAILABLE alone. **(iii) Unwind.** A reorg detected by
parent-hash mismatch produces a reversing balancing entry keyed on the orphaned log identity, never an
in-place edit or a delete; a reorg deeper than the indexer's rollback floor is an unrecoverable-state halt.
**(iv) Assertion.** A continuous reconciliation asserts `Σ credited at-or-below finalized height == Σ
observed on-chain value deltas to deposit addresses`.

**S3 · exchange ↔ ledger.** Fills are the economically-final fact: **realized PnL, fees and funding post as
journal entries; positions do not.** The ledger transaction id derives from the venue's `trade_id`, and the
same fill arriving on both the stream and the poll must post **once**. A fill reported as final can be
busted inside the clearly-erroneous window, so booked economic history must accept **retroactive reversal as
a new balancing entry**. The position is revisable, the entry is not editable.

## Verb index

Not separate rules: this is the table of which rules above bind on the operation you are writing, and what
that leaves in the diff.

| verb | the diff must contain |
|---|---|
| `post` | one call to the balanced-set entrypoint; every leg enumerated including counterparty, fee and cost; a caller-supplied identity required by the signature; the application role unable to mutate or remove a posted entry (no `UPDATE`/`DELETE` grant on the entry table) |
| `hold` | the invariant checked **at reserve time**; an expiry the reader enforces without waiting on a callback (an intrinsic `expires_at`); the spendable number derived as posted minus live holds (`available = posted - active_holds`), and nothing else authorising |
| `release` | release of the **pending transfer's own amount, in full**, whether you post less than reserved or void; an over-post is rejected, never clamped |
| `reverse` | a uniqueness constraint on the link from reversal to original (`reverses_transaction_id`), so no transaction is reversed twice; overdraft permitted on the reversal posting type; the reversal posts while the account is frozen |
| `close` | the residual swept to a control account and the account closed in one transaction; holds already pending are not resolved by the close, and they still expire on their own clock |
| `reconcile` | a scheduled entrypoint; a read path the writer does not share; the difference landing in a real account with an aged break record (a suspense account plus a `break` row); opening balances backfilled before the first run |

## Output

Every response that touches a posting path ends with this block, filled in:

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which counterparty responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the effect and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

A control named with no `file:line` and no `UNRESOLVED:` line is a defect: *implemented, not described*.

**A ledger of record is T3 by definition, so a change that writes a balance this system owns adds the LEDGER
CONTRACT block, whatever tier the rest of the change reads as.** Three kinds of change are not the record and
stay on the FINANCIAL CHECK alone: a ledger that mirrors an external processor and is reconciled against it, a
read-only derivation that writes no balance, and a change with no value-moving path reachable. Emit only the
rows whose predicate this change matches. A row you emit and cannot fill is work not yet done, not a row to
delete: carry it on the `controls:` line as `UNRESOLVED`. A row no predicate matches is not a finding, and
every control this change needs that no row names is covered by the `controls:` line. An emitted row whose
evidence cell is empty, or that contains "should", "would", "recommend" or "next step", fails the run.

```
LEDGER CONTRACT
| item (emit when the predicate holds) | evidence |
| balanced-set entrypoint (the only writer), when the change adds or routes a write to the entries | file:line |
| legs written, one line each: account · dr/cr · currency, when the change writes or reshapes a posting group | ... · Σ/ccy = 0 |
| UPDATE/DELETE revoked on entries, when the change creates or migrates an entry table | migration + test |
| idempotency_key required; stored fields compared on hit, when the change adds or alters a posting entrypoint | file:line ×2 |
| reconciliation entrypoint · schedule · alert config key, when the change writes, reports or closes a balance another system reads | file:line |
| opening balances backfilled before first reconcile run, when the change adds or alters a per-account comparison | migration file:line |
| reversal path: overdraws, and posts to a frozen account, when the change reverses, corrects or claws back a posting | file:line + test |
```

At T3, add the per-technique evidence table that `fin-verification` owns, and load that skill alongside this
one. Emit one row for every technique
[tier-matrix.md](../fin-verification/references/tier-matrix.md) marks required at T3, each row marked
PRESENT or ABSENT with its `file:line`.

## References

**When a row's trigger appears in the diff or the task text, read that file immediately and apply it in
order. Do not summarise it, and do not proceed on your memory of it.**

| file | read it when the diff contains |
|---|---|
| [double-entry.md](references/double-entry.md) | a `CREATE TABLE` for `entries`/`postings`/`journal`/`ledger_transactions`, a `direction` or `debit`/`credit` column, `normality`, `world`, `nostro`, `linked`, or a call that writes more than one leg |
| [balances-and-holds.md](references/balances-and-holds.md) | `available`, `pending`, `posted`, `hold`, `reserve`, `expires_at`, `void_pending`, `authorization`, `debits_must_not_exceed_credits` |
| [corrections-and-bitemporality.md](references/corrections-and-bitemporality.md) | `reverse`, `reversal`, `correct`, `adjust`, `void`, `discard`, `clawback`, `effective_at`, `as_of`, `period_close` against a ledger table |
| [accrual-and-time.md](references/accrual-and-time.md) | `accrue`, `accrual`, `interest`, `apr`, `apy`, `day_count`, `30/360`, `ACT/365`, `business_day`, `coupon`, `index`, `SECONDS_PER_YEAR` |
