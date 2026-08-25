---
name: fin-ledger
description: >-
  Financial correctness for balances and accounting systems: double-entry postings, balanced
  sets, immutable entries, holds and available versus posted balance, reversals and corrections,
  currency as a dimension, solvency limits and period close. Use when your code writes or reads
  a balance other systems trust, including TigerBeetle and Formance. For processor lifecycles
  use fin-payments.
license: MIT
---

# The balance other systems trust

You are editing the number downstream systems quote instead of recomputing, so what this code writes becomes
what someone is owed. The question here is whether a posting, a hold, a correction or a close can leave the
books saying something untrue while every function returns successfully.

## When to use

Your code holds the number that decides what someone is owed, and other systems read it rather than recompute
it. That covers anything recording an economic fact as accounting entries, anything incrementing or
overwriting a stored balance, anything reserving funds against a future spend, anything correcting a balance
after the fact, and anything closing a period over those entries. If a wrong value here would be believed by a
statement, an authorisation decision or a payout, you are in this skill.

Routing hints, not the definition: tables named `entries`, `postings`, `journal`, `ledger_entries`,
`ledger_transactions`, `accounts`, `balances`, `holds`; an import of `tigerbeetle` or a Formance/Numscript
client; a `debit`/`credit` column or a `direction` enum; an `amount` beside an `account_id`; the identifiers
`available`, `pending`, `posted`, `hold`, `reserve`, `reversal`, `clawback`, `suspense`, `trial_balance`,
`period_close`, `effective_at`; or a path that credits an account from a deposit, fill, capture or payout.

## When not to

- A processor's own state machine, and its capture, refund and dispute windows: `fin-payments`.
- Confirmation depth, reorg detection, nonces and transaction construction: `fin-onchain`.
- Matching and allocation across resting orders: that system is a venue, and its own skill owns it.
- Rounding direction, decimal contexts, and the identity of an **outbound** call: `fin-money-core`.
- Whether the evidence is enough to ship: `fin-verification`, loaded alongside this skill.

## Workflow

1. Name the economic fact this posting records, and state who is owed what once it lands.
2. Enumerate every account the event moves value across before writing a leg, counterparty, fee and cost
   accounts included, and fix the sign convention.
3. Route the write through the single posting entrypoint: the balanced set commits whole or is rejected
   whole, and a posted entry is immutable.
4. Separate provisional, available and posted. A hold is a state with its own expiry, not a subtraction.
5. Require the posting identity at the signature, and decide what a repeat of that identity returns.
6. Make every correction a reversal, never a mutation, and decide whether the question is bitemporal.
7. Name the solvency chokepoint, show that no write path reaches a balance without passing through it, and
   ship the independent recompute that detects drift.
8. Report authority and exposure, load only the references this change triggers, and implement each control
   you name together with its test.

## Invariants

### The balanced set commits whole, and a posted entry is immutable

One economic event produces one group of legs netting to zero in **every unit of account separately**; a sum
taken across mixed units hides the missing leg entirely. The entrypoint validates the net inside the same
transaction as the write and returns a typed rejection naming the imbalance, so no caller can leave an
unbalanced state for a later checker to find. Legs enumerated from the paying user's point of view write the
visible half only: the counterparty you hold funds with, the revenue account for a fee you charged and the
expense account for a cost you paid are legs too. Immutability is a grant the database enforces, not a review
convention, and the property that holds is immutable **once posted**, not immutable from creation.

### Provisional, available and posted are three numbers, and only one authorises

A hold is a state of the funds with an intrinsic expiry the reader enforces, never a subtraction from the
balance and never a release that waits on a callback, a cron or the happy path, because the callback path is
the one that failed. Available is posted minus live holds, and inbound provisional credit is not available.
Resolution happens exactly once, posts at most the reserved amount, releases the reservation in full, and
rejects an over-post rather than clamping it, since clamping silently changes an amount someone is owed.

### Corrections are reversals, never mutations, and the remedy has to be able to land

A correction is a new balanced group linked to the original, under a uniqueness constraint on that link so no
transaction is reversed twice by two operators or by an operator plus a retry. Every guard on a balance is
written with the honest user in mind and every guard is exercised during an incident, so condition each one on
the posting type rather than on the account: a non-negativity constraint governs ordinary debits while a
clawback still overdraws, and a frozen account still accepts a reversal. Blocking only customer-initiated
debits keeps the freeze closed; unfreezing to let the clawback post reopens the drain window the freeze existed
to close.

### One entrypoint, and the solvency check lives inside it

State the system-level invariant explicitly and per asset, then evaluate it in the one function every
balance-changing path terminates in. This specialises *hard limits*: the chokepoint rejects the posting rather
than observing afterwards that it should not have happened. Make the bypass unrepresentable rather than
provable, by removing the application role's ability to write a balance directly so the chokepoint's own role
is the only writer and a test can read the grant. "Every path terminates in the chokepoint" is a paragraph in
an architecture review; a revoked grant is a fact.

### A repeat of an identity returns the original outcome only if it is the same request

This specialises *operation identity* and *durable dedupe* onto the ledger's own write, where the effect and
the dedupe record are the same transaction by construction. The identity is supplied by the caller and required
by the signature, never optional with enforcement deferred to prose about the API layer. On a hit, compare the
stored economic content field by field before reporting success, and name the field that differs: an insert
that ignores conflicts accepts a *different* request under a used identity and reports work done that was never
done.

### A stored balance is a cache, and a cache with no drift detector is a rumour

This specialises *reconciliation*. The balance is written in the same transaction as the entries that justify
it, and recomputed from those entries by a scheduled job reading through a path the writer does not share; a
comparison that reads through the writer's own code finds arithmetic bugs and never a missing write. The
recompute raises an aged break and posts the difference to a real suspense account in the chart of accounts. It
does not repair the number in place, which would destroy the evidence of how the drift arose and absorb the
next one silently.

### Currency is a dimension of the entry, not a shape of the schema

This specialises *exact representation*: the dimension carries currency **and scale**, and it is immutable, so
an asset's scale changes by migrating to a new ledger rather than by editing the existing one. No expression
adds or multiplies two amounts in different units. An FX movement is a balanced two-account transaction
recording rate, provenance, side and pivot, booking the spread to its own revenue account and posting the
residue to a named residue account under *rounding and conservation*. One journal with a currency dimension,
not one table or one database per currency.

### Accrual is a posting, and the period is part of the answer

Interest, fees and funding create money in an income or expense account by posting; they never mutate a balance
field. Key each run on a stored period marker so a re-run inside the same period posts nothing, and advance the
marker in the same transaction as the postings. A back-dated accrual posts a catch-up in the current open
period and never re-runs over a closed one: June's accrual posted in July balances perfectly and is wrong in
every statement, interest certificate, covenant test and tax filing.

## References

**When a row's trigger appears in the diff or the task text, read that file and apply it in order. Do not
summarise it, and do not proceed on your memory of it.**

| file | read it when the change contains |
|---|---|
| [double-entry.md](references/double-entry.md) | a `CREATE TABLE` for `entries`/`postings`/`journal`/`ledger_transactions`, a `direction` or `debit`/`credit` column, `normality`, `world`, `nostro`, `linked`, a multi-currency or FX group, or a call that writes more than one leg |
| [balances-and-holds.md](references/balances-and-holds.md) | `available`, `pending`, `posted`, `hold`, `reserve`, `expires_at`, `void_pending`, `authorization`, `debits_must_not_exceed_credits`, a materialised balance, or a solvency, overdraft or account-closure check |
| [corrections-and-bitemporality.md](references/corrections-and-bitemporality.md) | `reverse`, `reversal`, `correct`, `adjust`, `void`, `discard`, `clawback`, `effective_at`, `as_of`, `period_close`, or a suspense or break record |
| [accrual-and-time.md](references/accrual-and-time.md) | `accrue`, `accrual`, `interest`, `apr`, `apy`, `day_count`, `30/360`, `ACT/365`, `business_day`, `coupon`, `index`, `SECONDS_PER_YEAR` |
| [seams.md](references/seams.md) | a ledger write driven by a payment state transition, a chain log or a venue fill |
| [ledger-contract.md](references/ledger-contract.md) | a `post`, `hold`, `release`, `reverse`, `close` or `reconcile` verb in the diff, or a review or ship decision on a posting path |

## Output

One line when the change is economic:

```
authority: SELF · exposure: record
```

A ledger that is the system of record has nothing above it that can tell it that it is wrong, so its proof is
replay, determinism and conservation rather than reconciliation, and `fin-verification` is loaded alongside
this skill. A ledger that mirrors an external processor, venue or chain is `authority: EXTERNAL (<that
system>)`, usually with exposure `customer`, and reconciliation against that system is the primary proof.

Then one entry per real finding, and nothing at all for a concept the change does not touch:

```
FINDING   the wrong economic outcome, concretely
WHY       the mechanism that produces it
EVIDENCE  file:line
FIX       the change that closes it
TEST      the property to assert
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` when the task is a review or a ship decision. No
findings is one or two sentences saying so and why the change is safe. A control you name but have not
implemented is reported as `UNRESOLVED: <control> (<why>)`, never as a completed row: a named control with no
`file:line` is the defect, not a report of one.

**Threshold for a richer block:** emit the per-verb contract table from
[ledger-contract.md](references/ledger-contract.md) only when authority is SELF *and* the change adds, routes
or reshapes a write to the entries; every other change is served by the findings alone.
