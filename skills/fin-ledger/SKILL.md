---
name: fin-ledger
description: >-
  Use when code writes or reads an authoritative balance: tables entries, postings, journal,
  ledger_transactions, accounts, balances, holds; debit/credit legs; TigerBeetle, Formance; posted,
  pending, available; reversal, clawback, suspense, trial balance, period close, as-of balance, accrual.
  Fires on `balance += amount`, `CHECK (balance_cents >= 0)`. Read it before the first INSERT. Skip
  processor lifecycles: use fin-payments.
license: MIT
---

# The books that balance

You are editing the system of record for balances. The question this skill makes you ask on every diff is
**"does the group of entries this code writes sum to zero per currency, and is there a scheduled job that
would notice if it stopped?"** Measured: transfer and reversal arithmetic is written correctly unaided;
journals that do not balance and reconciliations that never run are shipped at close to 100%. Processor lifecycles belong to `fin-payments`, chain mechanics to `fin-onchain`,
matching and allocation to `fin-matching-and-settlement`.

> **`G1`–`G7`** are the always-on financial guardrails: **G1** economic-diff gate · **G2** a named risk is implemented or the process refuses to start · **G3** every comment claim checked against the code · **G4** an ambiguous external call has three phases and the first one COMMITs · **G5** enumerate legal `(state, event)` pairs, guard the version on the entity id, re-read from the authority · **G6** a watermark advances only past a verifiably covered range · **G7** the reconciliation runs in production or it does not exist. Install them with `scripts/install-guardrails.sh`; every rule below stands on its own without them.

## When this applies

Any of these in the diff or the file being edited: a table named `entries`, `postings`, `journal`,
`ledger_entries`, `ledger_transactions`, `accounts`, `balances`, `holds`; an import of `tigerbeetle` or a
Formance/Numscript client; a `debit`/`credit` column or a `direction` enum; an `amount` alongside an
`account_id`; **any function that increments, decrements or overwrites a stored balance**; the identifiers
`available`, `pending`, `posted`, `hold`, `reserve`, `reversal`, `clawback`, `suspense`, `trial_balance`,
`period_close`, `effective_at`; or a code path that credits an account from a deposit, fill, capture or
payout.

Not this skill: the processor's own state machine and its capture/refund/dispute windows →
`fin-payments`. Confirmation depth, reorg detection, nonces, transaction construction → `fin-onchain`. Order
matching, allocation, settlement batching → `fin-matching-and-settlement`. Rounding direction, decimal
contexts, and deriving an idempotency key for an **outbound** call → `fin-money-core`.

## Non-negotiables

**1 · Enumerate the legs, then assert the sum. "Per currency" goes inside the assert.**
Before writing the `INSERT`, list the legs: the user leg, the counterparty leg (hot wallet, bank, processor,
`world`/nostro), the revenue leg for a fee you charged, the expense leg for a cost you actually paid. A
withdrawal of `amount` with a `fee` charged to the user and `gas_spent` leaving the hot wallet is **at minimum
four legs**. The shipped artifact is
`assert all(sum(l.amount for l in legs if l.currency == c) == 0 for c in currencies(legs))`. A copyable
`assert sum(legs) == 0` passes on `+100 JPY, −100 USD`. *Measured: three of three reps wrote a journal and
three of three journals failed to balance (a single leg, a missing hot-wallet counterparty, and one
settlement group summing to `-2 × amount` in a table its own comment called "append-only double-entry").*
**The property is a journal that balances.**

**2 · Conservation is a property of the write API, not a check you run later.**
The posting entrypoint accepts a **set** of entries and commits them only when they net to zero per currency,
returning a typed rejection (`UnbalancedGroup { currency, delta }`) in the same transaction as the write, so
no caller can create an unbalanced state. TigerBeetle's `exceeds_credits` is documented as *"The transfer was
not created."* **Do not write a runtime "do the books balance" check that halts.** If such a check can fire,
either a bypass path exists (close the bypass), or it is a **reconciliation** breach against an external
record rather than a conservation breach, which takes a suspense posting and rule 4, not a halt.

**3 · Append-only is a grant, not a comment.**
The migration **revokes `UPDATE` and `DELETE` on the entries table** from the application role, or installs a
trigger that raises, and a test asserts the grant is absent. A posted entry is never `UPDATE`d or `DELETE`d;
corrections are new records. State in the schema which lifecycle states are mutable: "immutable" in practice
means **immutable once posted**, not immutable from creation (Modern Treasury: *"a ledger transaction is
mutable while pending and immutable once posted"*), and a naive "everything is immutable" reading gets that
wrong.

**4 · The reconciliation is a scheduled entrypoint that was backfilled before its first run.**
G7 in ledger mechanism. It reads through a path **independent of the writer**; otherwise it finds arithmetic
bugs and never a missing write. Discrepancies post to a real `suspense`/`clearing` account **in the chart of
accounts**, never a nullable column and never a log line, so the trial balance still balances; each raises a
`break` row carrying `detected_at`, `source_a`, `source_b`, `amount`, `currency`, `status`, with an aging
policy, a hard escalation threshold, and a periodic sweep. **Opening balances are backfilled by a migration
before the first run**. The measured near-miss wrote flawless SQL, ran it nowhere, and its per-account
comparison was broken on day one by un-backfilled openings, guaranteeing the alert would be muted. Reconcile
on three axes: completeness (are all records present?), clearing (did every clearing account return to zero?),
balance. Precedent for continuing: Fed FAM §4.50's **Difference account** absorbs *"an out-of-balance
condition resulting from the normal operation of a department"* and is swept monthly; SEC 17a-11(c) says
notify same day, remediate in 48 hours, **do not cease operating**.

**5 · Every control must be operable against a hostile counterparty.**
`CHECK (balance_cents >= 0)` governs ordinary debits and **not** compensating entries: permit overdraft on the
**reversal posting type specifically** (a partial index, or a `CHECK` conditioned on `posting_type`), so a
clawback of already-spent funds can post while an ordinary debit still cannot. **Do not delete the
constraint.** Make sure a raw `CheckViolation` cannot escape the typed error hierarchy mid-clawback.
Reversals and clawbacks post **against frozen accounts**; only customer-initiated debits are blocked. Raising
`AccountNotActive` on a frozen account blocks the standard fraud flow (freeze the recipient, then claw
back), forcing an unfreeze that reopens exactly the drain window the freeze existed to close. Put a
**uniqueness constraint on `reverses_transaction_id`**, with `reversed_by_transaction_id` on the original, so
one transaction cannot be reversed twice by two operators or by an operator plus a retry.
*Measured: `CHECK (balance_cents >= 0)` made `allow_overdraft=True` dead code in a shipped reversal path.*

**6 · The posting API's idempotency key is a required parameter, and a key hit is compared field by field.**
`idempotency_key: str` is positional and required, never `idempotency_key: Optional[str] = None` with
enforcement deferred to prose about the API layer. On a key hit the ledger compares the stored row's
`debit_account_id`, `credit_account_id`, `amount` and `currency` against the request and returns a **distinct
typed code per mismatched field**: TigerBeetle ships `exists_with_different_debit_account_id = 37`,
`_credit_account_id = 38`, `_amount = 39`, `_flags = 36`, explicitly *"to prevent silent data
inconsistencies"*. `INSERT … ON CONFLICT DO NOTHING` keyed on the idempotency key alone silently accepts a
**different** request under a used key. *Measured: both partial reps built `idempotency_key text UNIQUE`, made
it optional, and compared nothing; the unvalidated replay let `reverse_transfer` mark a transfer reversed
**while writing zero compensating entries**.*

## Verb index

The verb in the function you are editing selects the artifacts that must be in the diff.

| verb | the diff must contain |
|---|---|
| `post` | one call to the balanced-set entrypoint; every leg enumerated including counterparty, fee and cost; required `idempotency_key`; no `UPDATE`/`DELETE` grant on `entries` |
| `hold` | the invariant checked **at reserve time**; an intrinsic `expires_at`; `available = posted − active_holds` as the only number that authorises |
| `release` | release of the **pending transfer's own amount, in full**, whether you post less than reserved or void; an over-post is rejected, never clamped |
| `reverse` | uniqueness on `reverses_transaction_id`; overdraft permitted on the reversal posting type; the reversal posts while the account is frozen |
| `close` | sweep the residual to a control account and close in one transaction; closing does not resolve holds already pending, and they still expire on their own clock |
| `reconcile` | a scheduled entrypoint, an independent read path, a suspense account plus a `break` row, opening balances backfilled |

## Balances and holds

**A hold expires by itself, and only `available` authorises.** The hold carries an intrinsic `expires_at`
that the **reader** enforces (`WHERE expires_at > now()`), not a release that depends on a callback, a cron,
or the happy path completing, because callback-driven release strands funds precisely when the callback path
is the one that failed. Check the invariant **at reserve time**, so no committed reservation can later be
un-postable.

**Name each balance you expose and never let one number mean all three.** `posted`, `pending`, `available`,
with `available = posted − active_holds`. Inbound pending is never available: TigerBeetle's
`debits_must_not_exceed_credits` rejects when `debits_pending + debits_posted + amount > credits_posted`.
`credits_pending` is absent from the right-hand side, deliberately.

**Two-phase resolution releases the reservation in full.** Posting less than reserved restores the remainder;
posting more is `exceeds_pending_transfer_amount`; voiding must be exact; and a pending transfer resolves
exactly once (`pending_transfer_already_posted`, `pending_transfer_already_voided`).

## Materialised balances

A materialised balance drifts silently and nothing tells you. The balance `UPDATE` goes in the **same
transaction** as the entry `INSERT`, carries a monotonic version, and is verified by a separate
**order-independent checksum recompute** (`SELECT SUM(amount) FROM entries WHERE account_id = ?`) that
alerts on drift. Never `INSERT INTO entries …; COMMIT;` followed by a separate `UPDATE balances`. The
recompute runs on a schedule and **does not fix the balance in place**: it raises a `break` per
non-negotiable 4.

## Currency is a dimension

**Every journal group balances per currency, and no expression multiplies or adds two amounts of different
currencies.** An FX movement is a **balanced two-account transaction** with the rate, its provenance, its side
and its pivot recorded on the transaction, and the spread booked to its own revenue account, never a rate
multiplied in place on a balance. Rounding residue from the conversion posts to a named residue account. The
ledger dimension carries currency **and scale** and is immutable: never change the asset scale or currency of
an existing ledger, migrate to a new one. This is one journal with a currency dimension and a per-currency
balance constraint on the group, not one table or one database per currency.

## Solvency and its chokepoint

State the system-level solvency invariant explicitly (`Σ customer balances <= custodied assets`, **per
asset**) and assert it continuously against the custodian's own figure. Enumerate every function that can
change a balance and show each terminates in the one chokepoint that checks it. **Make the bypass
unrepresentable rather than provable:** `REVOKE UPDATE, DELETE ON balances` from the application role so the
only writer is the chokepoint's own role or a `SECURITY DEFINER` function, with a test asserting the grant is
absent. "Prove each path terminates in the chokepoint" is an architecture-review question answered with a
paragraph; a revoked grant is a fact a test can read. **Euler's `donateToReserves` was the single path
without the health check: ~$197M.** A per-account override on a solvency, credit-limit or liquidation check is
an unbounded liability generator: it raises the tier and requires field-level audit logging of every change.

## Accrual is a posting

**Interest, fees and funding create money in an income or expense account by posting; they never mutate a
balance field.** IFRS 9 defines the effective interest method as the method used in the *"allocation and
recognition of the interest revenue or interest expense in profit or loss"*. Recognition is an event. For
every accrual run, `Σ credited to customer accounts == Σ debited to interest expense`, exactly, to the minor
unit.

**Key every accrual on a stored period marker and make a re-run inside the same period post nothing.**
Compound v2: `if (accrualBlockNumberPrior == currentBlockNumber) return;`; Aave:
`if (reserveCache.reserveLastUpdateTimestamp == uint40(block.timestamp)) return;`. The test runs the job twice
for one period and asserts the second run wrote zero rows.

**Where per-account posting every period is infeasible, use a scaled-balance index and keep exactly one
aggregate posting per period for the pool's own income** (Aave's `_accrueToTreasury`). The index makes the
per-account work O(1); the aggregate posting is what keeps the journal balanced.

**A back-dated accrual posts a catch-up adjustment in the current open period; it never re-runs over a closed
period** (IFRS 9 B5.4.6 recomputes at the *original* effective interest rate and recognises the adjustment
now). Period attribution is a correctness property distinct from amount correctness: June's accrual posted in
July balances perfectly and is wrong in every statement, interest certificate, covenant test and tax filing.
For each closed period, assert that the sum of postings *effective* in that period is unchanged after any
later correction run.

## Seams

Each boundary is stated once here and once from the other side, in that skill's own vocabulary. A
contradiction between the two is a suite defect to be reported, not a judgement call.

**S1 · payments ↔ ledger.** Every payment state transition emits **exactly one balanced ledger transaction**
whose id derives from the payment's idempotency key. Every clearing account between payment states returns to
zero, monitored as a continuous assertion. **Never derive a balance by scanning payment objects.**
Authorizations are reserved amounts in the payments layer, not ledger entries; only captures, refunds,
disputes, fees and settlement adjustments post.

**S2 · onchain ↔ ledger.** **(i) Identity.** A deposit credit is exactly one balanced ledger transaction
whose idempotency key is `(chainId, blockHash, txHash, logIndex)`, never the tx hash and never
`balance += amount`; the same log re-observed after a reconnect, a backfill overlap, or a provider failover is
a no-op. **(ii) Staging.** The credit posts on observation to a per-user **PENDING (unavailable)** account
and moves to **AVAILABLE** only at the credit policy's finality (L1 finality for rollups, not L2 block count;
below the policy depth, credit only inside a stated exposure cap you are willing to lose). Withdrawal and
onward transfer authorise from AVAILABLE alone. **(iii) Unwind.** A reorg detected by parent-hash mismatch
produces a reversing balancing entry keyed on the orphaned log identity, never an in-place edit or a delete; a
reorg deeper than the indexer's rollback floor is an unrecoverable-state halt. **(iv) Assertion.** A
continuous reconciliation asserts `Σ credited at-or-below finalized height == Σ observed on-chain value deltas
to deposit addresses`.

**S3 · exchange ↔ ledger.** Fills are the economically-final fact: **realized PnL, fees and funding post as
journal entries; positions do not.** The ledger transaction id derives from the venue's `trade_id`, and the
same fill arriving on both the stream and the poll must post **once**. A fill reported as final can be busted
inside the clearly-erroneous window, so booked economic history must accept **retroactive reversal as a new
balancing entry**. The position is revisable, the entry is not editable.

## REQUIRED OUTPUT: the LEDGER CONTRACT block

Every response that adds or changes a posting path ends with this block, filled in. An evidence cell that is
empty, or that contains "should", "would", "recommend" or "next step", fails the run.

```
LEDGER CONTRACT
| item                                                        | evidence          |
| balanced-set entrypoint (the only writer)                   | file:line         |
| legs written, one line each: account · dr/cr · currency     | ... · Σ/ccy = 0   |
| UPDATE/DELETE revoked on entries                            | migration + test  |
| idempotency_key required; stored fields compared on hit     | file:line ×2      |
| reconciliation entrypoint · schedule · alert config key     | file:line         |
| opening balances backfilled before first reconcile run      | migration file:line |
| reversal path: overdraws, and posts to a frozen account     | file:line + test  |
```

## References

**When a row's trigger literal appears in the diff or the task text, read that file immediately and apply it
in order. Do not summarise it, and do not proceed on your memory of it.**

| file | read it when the diff contains |
|---|---|
| [double-entry.md](references/double-entry.md) | a `CREATE TABLE` for `entries`/`postings`/`journal`/`ledger_transactions`, a `direction` or `debit`/`credit` column, `normality`, `world`, `nostro`, `linked`, or a call that writes more than one leg |
| [balances-and-holds.md](references/balances-and-holds.md) | `available`, `pending`, `posted`, `hold`, `reserve`, `expires_at`, `void_pending`, `authorization`, `debits_must_not_exceed_credits` |
| [corrections-and-bitemporality.md](references/corrections-and-bitemporality.md) | `reverse`, `reversal`, `correct`, `adjust`, `void`, `discard`, `clawback`, `effective_at`, `as_of`, `period_close` against a ledger table |
| [accrual-and-time.md](references/accrual-and-time.md) | `accrue`, `accrual`, `interest`, `apr`, `apy`, `day_count`, `30/360`, `ACT/365`, `business_day`, `coupon`, `index`, `SECONDS_PER_YEAR` |
