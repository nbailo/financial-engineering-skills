---
name: fin-ledger
description: >-
  Financial correctness for balances and accounting systems: double-entry postings, balanced
  sets, immutable entries, holds and available versus posted balance, reversals, currency as a
  dimension, solvency limits and period close. Use when your code writes or reads a balance
  other systems trust, including TigerBeetle and Formance. For processor lifecycles, fin-payments.
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
- Rounding direction, decimal contexts, and the identity of an **outbound** call: `fin-money-core`, which the
  invariants below already specialise onto postings; load it alongside only for a cross-domain mechanism they
  do not cover.
- `fin-verification`, when tests, proof or reconciliation are actually being changed, when the ask is review,
  readiness or a ship decision, or where a rule below demands stronger proof for the mechanism in scope. Never
  automatically because customers are exposed.

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
8. Report authority per quantity and exposure, load only the references this change triggers, and implement
   each control you name together with its test.

## Invariants

### The balanced set commits whole, and a posted entry is immutable

One economic event produces one group of legs netting to zero in **every unit of account separately**; a sum
taken across mixed units hides the missing leg entirely. The entrypoint validates the net inside the same
transaction as the write and returns a typed rejection naming the imbalance, so no caller can leave an
unbalanced state for a later checker to find. Legs enumerated from the paying user's point of view write the
visible half only: the counterparty you hold funds with, the revenue account for a fee you charged and the
expense account for a cost you paid are legs too. Immutability is enforced by the store rather than by review
convention, through whichever mechanism it offers: a revoked `UPDATE` and `DELETE` grant, an append-only log,
a constraint a test can read. The property that holds is immutable **once posted**, not immutable from
creation.

### Provisional, available and posted are three numbers, and only one authorises

A hold is a state of the funds with an intrinsic expiry the reader enforces, never a subtraction from the
balance and never a release that waits on a callback, a cron or the happy path, because the callback path is
the one that failed. Available is posted minus live holds, and inbound provisional credit is not available.
Resolution happens exactly once, posts at most the reserved amount, releases the reservation in full, and
rejects an over-post rather than clamping it, since clamping silently changes an amount someone is owed.

### Corrections are reversals, never mutations, and the remedy has to be able to land

A correction is a new balanced group linked to the original, under an exclusivity guard on that link that
exactly one writer wins atomically, so no transaction is reversed twice by two operators or by an operator
plus a retry: a uniqueness constraint on the link is one mechanism, a read-then-insert is not. Every guard on
a balance is written with the honest user in mind and every guard is exercised during an incident, so
condition each one on the posting type rather than on the account: a non-negativity constraint governs
ordinary debits while a clawback still overdraws, and a frozen account still accepts a reversal. Blocking only
customer-initiated debits keeps the freeze closed; unfreezing to let the clawback post reopens the drain
window the freeze existed to close.

### One entrypoint, and the solvency check lives inside it

State the system-level invariant explicitly and per asset, then evaluate it in the one function every
balance-changing path terminates in. This specialises *hard limits*: the chokepoint rejects the posting rather
than observing afterwards that it should not have happened. Make the bypass unrepresentable rather than
provable: the chokepoint is the only thing that can write a balance, and that is enforced by something a test
can read. Revoking the application role's direct write so the chokepoint's own role is the sole grantee is one
such mechanism; a store that accepts only the entrypoint's command type is another. "Every path terminates in
the chokepoint" is a paragraph in an architecture review; an enforced restriction a test asserts on is a fact.

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
comparison that reads through the writer's own code finds arithmetic bugs and never a missing write. On a
difference the recompute raises an aged break and quarantines the affected item so nothing spends, nets or
sweeps the disputed amount, and every path depending on it fails closed. It does not repair the number in
place, and it does not post the difference away: a corrective posting waits for an authoritative cause and its
approval, because an unexplained difference booked to suspense balances the books while making the break
unattributable and the next comparison silent.

### Currency is a dimension of the entry, not a shape of the schema

This specialises *exact representation*: the dimension carries currency **and scale**, and it is immutable
once entries exist under it, so a change of scale is a new asset identity rather than an edit to the existing
one. No expression adds or multiplies two amounts in different units. An FX movement is a balanced two-account
transaction recording rate, provenance, side and pivot, booking the spread to its own revenue account and
posting the residue to a named residue account under *rounding and conservation*. The schema has to be able to
express that single balanced group spanning two units, which a journal carrying a currency dimension does and
a table or a database per currency does not.

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
| [double-entry.md](references/double-entry.md) | a `CREATE TABLE` for `entries`/`postings`/`journal`/`ledger_transactions`, a `direction` or `debit`/`credit` column, `normality`, a contra account, an account path or chart of accounts, or a `world`/nostro/hot-wallet leg |
| [posting-api.md](references/posting-api.md) | the signature or result handling of the posting entrypoint: `linked`, `create_transfers`, a per-event result array, `idempotency_key` on a group, `exists_with_different_*`, `pending_id`, `post_pending`/`void_pending` |
| [multi-currency-fx.md](references/multi-currency-fx.md) | two currencies or assets in one group, an FX rate, spread or revaluation, an asset scale or `USD/2`, or a `SUM(amount)` with no `GROUP BY currency` |
| [holds-and-two-phase.md](references/holds-and-two-phase.md) | `available`, `pending`, `reserved`, `hold`, `reserve`, `expires_at`, `debits_must_not_exceed_credits`, `capture_before`, a partial capture, or an authorization window |
| [balance-storage.md](references/balance-storage.md) | a materialised or cached balance column, a checkpoint or running sum, `lock_version`, `FOR UPDATE` on a hot account, a migration's opening balances, or a balance-read timeout |
| [solvency-and-limits.md](references/solvency-and-limits.md) | `CHECK (balance >= 0)`, a floor or overdraft, `allow_negative`, a frozen or closed-account gate, a per-account limit override, or `Σ customer balances` against custodied assets |
| [reversals-and-corrections.md](references/reversals-and-corrections.md) | `reverse`, `reversal`, `correct`, `adjust`, `void`, `discard`, `clawback`, `chargeback`, `reverses_transaction_id`, or a payment recall or return |
| [bitemporality-and-close.md](references/bitemporality-and-close.md) | `effective_at`, `as_of`, `posted_at` beside `created_at`, a historical or statement balance query, `period_close`, `accounting_periods`, or `approved_by`/`reason_code` on a posting |
| [suspense-and-repair.md](references/suspense-and-repair.md) | a `suspense`, `clearing` or `break` record, a reconciliation sweep, a claim that recomputing from the ledger makes a handler idempotent, or a repair script against posted rows |
| [day-count-conventions.md](references/day-count-conventions.md) | `day_count`, `30/360`, `ACT/365`, `year_fraction`, `business_day`, `MODIFIED_FOLLOWING`, a holiday calendar, `value_date`, or a date difference taken in seconds |
| [accrual-posting.md](references/accrual-posting.md) | `accrue`, `accrual`, `interest`, `apr`, `apy`, `coupon`, an interest `index` or scaled balance, `SECONDS_PER_YEAR`, a per-period residue, or a rewards claim counter |
| [corporate-actions.md](references/corporate-actions.md) | a split, dividend, `ex_date`/`record_date`/`pay_date`, cash in lieu, a ticker rename, an airdrop or migration, a rebasing token, or perpetual funding |
| [seams.md](references/seams.md) | a ledger write driven by a payment state transition, a chain log or a venue fill |
| [ledger-contract.md](references/ledger-contract.md) | a `post`, `hold`, `release`, `reverse`, `close` or `reconcile` verb in the diff, or a review or ship decision on a posting path |

## Output

One line when the change is economic and one authority covers every quantity in scope:

```
authority: SELF · exposure: record
```

Most ledgers are mixed, because a ledger that mirrors an upstream system still originates quantities of its
own. The upstream system is the authority for the fact it reports; this ledger is the authority for the
posting that records it, the balance derived from that posting, and any fee, hold, accrual or correction it
originates itself. Say so, on one line per quantity that differs:

```
authority: MIXED · exposure: customer
  mirrored payment state        EXTERNAL (Stripe)
  postings, balances, accruals  SELF
```

Proof follows the quantity: a mirrored one is proven by reconciliation against its authority, a
self-authoritative one by replay, determinism and conservation, because nothing above it can say it is wrong.

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
implemented is `UNRESOLVED: <control> (<why>)`, never a completed row: a named control with no `file:line` is
the defect, not a report of one.

**Threshold for a richer block:** emit the per-verb contract table from
[ledger-contract.md](references/ledger-contract.md) only when a quantity in scope is SELF *and* the change
adds, routes or reshapes a write to the entries; every other change is served by the findings alone.
