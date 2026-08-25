# The posting entrypoint: atomicity, batch results, and identity

The primitives that let a group of postings commit or fail as one, and the contract the caller sees: linked
chains and what their result codes mean, why a batch's transport status is not its business outcome, the id
that *is* the idempotency key and the field-by-field comparison behind it, the two-phase release, and the
metadata the posting path must not fetch.

## Contents

1. **Multi-leg atomicity**: `flags.linked` chain semantics and the fact that the linkage is not persisted.
2. **Batch results are per-event**: HTTP 200 on a 50-transfer batch is not "50 transfers succeeded".
3. **The id is the idempotency key**: eleven `exists_with_different_*` codes, `id_already_failed`, balancing.
4. **Two-phase transfers release the reservation in full.**
5. **Tenancy and identity**, and **control plane versus data plane**.

## 1 · Multi-leg atomicity

A group of postings not committed by one operation is not a group. Two primitives exist.

**Linked chains (TigerBeetle).** `flags.linked` links an event to the next; the chain ends at the first event
*without* the flag. Semantics that bite:

- A request whose **last** event still has `linked` set fails with `linked_event_chain_open`.
- The first failure in a chain returns its real error code; every other event in that chain returns
  `linked_event_failed`. Reading only the first non-`ok` result tells you nothing about the cause.
- **The linkage is not persisted**; it is an execution-time construct. If the business relationship between
  the legs must survive the request (for a journal group it must), re-encode it in `user_data_128` or `code`
  on every member. Code that reconstructs a settlement group by looking for `linked` afterwards finds nothing.

**One script, all postings (Numscript).** Formance's DSL expresses the whole flow (splits, fees, cascading
funding sources with caps and overdraft rules) as one script that commits all postings or none, using integer
math only with built-in rounding rules. No intermediate state for a crash to leave behind, no compensating
action to get wrong. The relational equivalent: all legs in one `INSERT … SELECT` from a VALUES list, inside
one database transaction, behind one balanced-posting entrypoint. Never one `INSERT` per leg with a
commit between them.

## 2 · Batch results are per-event

TigerBeetle's Requests documentation draws the distinction that costs money: "All events within a request
batch are committed, or none are… this does **not** mean that all of the events in a batch will succeed, or
that all will fail. Events succeed or fail independently unless they are explicitly linked." Durability is
atomic; business outcome is not.

```python
results = client.create_transfers(batch)                     # len(batch) == 50
ok = {r.index for r in results
      if r.result in (CreateTransferResult.OK, CreateTransferResult.EXISTS)}
failed = [(batch[r.index].id, r.result) for r in results if r.index not in ok]
if failed: raise PostingRejected(failed)                     # do NOT mark all 50 settled
```

`if resp.status_code == 200: mark_all_settled()` sets a settled flag on transfers rejected for
`exceeds_credits`. `exists` is a *success* here and must be in the accepting set (§3); and events within a
request execute in sequence with each one's effects visible to the next, so a later event may legitimately
depend on an earlier one having landed.

## 3 · The id is the idempotency key

TigerBeetle has no separate idempotency header: the user-supplied `id` on the transfer *is* the cluster-wide
idempotency key. The client generates it, **persists it locally before submission**, and reuses it verbatim on
every retry across timeouts, restarts and redeploys; the recommended scheme is a 48-bit millisecond timestamp
concatenated with 80 random bits, so ids are sortable without a central oracle. Minting a fresh id inside the
retry loop is the whole bug. On a hit the payload is compared **field by field**, with eleven distinct result
codes rather than one opaque conflict (`src/tigerbeetle.zig:236-247`):

| code | value | code | value |
|---|---|---|---|
| `exists_with_different_flags` | 36 | `exists_with_different_user_data_128` | 41 |
| `exists_with_different_debit_account_id` | 37 | `exists_with_different_user_data_64` | 42 |
| `exists_with_different_credit_account_id` | 38 | `exists_with_different_user_data_32` | 43 |
| `exists_with_different_amount` | 39 | `exists_with_different_timeout` | 44 |
| `exists_with_different_pending_id` | 40 | `exists_with_different_code` | 45 |
| `exists_with_different_ledger` | 67 | `exists` | 46 |

TigerBeetle states the reason for comparing at all in as many words: it is done "to prevent silent data
inconsistencies". Comparison order is documented in source (`src/state_machine.zig:3990-4045`): flags are compared **first**,
because "the flags change the behavior of the remaining comparisons". Two refinements you will not derive:

- **`id_already_failed = 68`** (`src/tigerbeetle.zig:252`, returned at `src/state_machine.zig:3736` on a
  `.found_orphaned` lookup). An id whose first attempt failed for a *transient* reason (account not found,
  `exceeds_credits`, account already closed) is permanently poisoned; the negative outcome is durable and you
  must mint a new id. Contrast Stripe, where a pre-execution failure saves nothing and the key stays retryable.
  Genuinely different contracts; state which one your API has.
- **The balancing-transfer exception** (`src/state_machine.zig:4016-4030`). For `balancing_debit` /
  `balancing_credit` the committed amount is usually *less* than requested, so a retry carrying the original
  request fails a naive equality check. The code compares `t.amount < e.amount` for balancing transfers and
  `t.amount != e.amount` otherwise: **the fingerprint is over the request as the client meant it, not over the
  committed result.** Every "transfer up to X" operation needs this.

The homegrown anti-pattern is `INSERT INTO entries(idempotency_key, …) ON CONFLICT DO NOTHING` then "if not
inserted, return the cached success": it silently accepts a *different* request under a used key (a different
amount, a different destination) and reports success for a posting that never happened.

The signature carries the same weight as the storage. Write `idempotency_key: str`, positional and required,
never `idempotency_key: Optional[str] = None` with enforcement deferred to prose about the API layer: an
optional identity is no identity, because the caller that omits it is the retry. *Measured: both partial reps
built `idempotency_key text UNIQUE`, made it optional, and compared nothing; the unvalidated replay let
`reverse_transfer` mark a transfer reversed **while writing zero compensating entries**.*

## 4 · Two-phase transfers release the reservation in full

Stated here only because it is the conservation half of the two-phase mechanism.
`src/state_machine.zig:4240-4252` decrements the reservation by `p.amount` (the **pending transfer's own**
amount), never by the amount named on the resolving request:

```zig
dr_account_new.debits_pending  -= p.amount;      // always the reservation, in full
cr_account_new.credits_pending -= p.amount;
if (t.flags.post_pending_transfer) { assert(amount_actual <= p.amount);  … }
if (t.flags.void_pending_transfer) { assert(amount_actual == p.amount);  … }
```

Post less than reserved and the remainder is restored automatically; post more and the request is rejected
(`exceeds_pending_transfer_amount`) rather than clamped; void must be exact. The resolving transfer is a *new*
record with its own id and a `pending_id` back-reference, resolving exactly once:
`pending_transfer_already_posted = 33`, `pending_transfer_already_voided = 34`.


## 5 · Tenancy and identity, and control plane versus data plane

TigerBeetle's account carries `ledger` (u32, non-zero; the currency/asset partition), `code` (u16, non-zero;
the account type, i.e. your chart-of-accounts class as an integer), and `user_data_128` / `_64` / `_32` for
correlation. String↔code mappings live in the business database; the ledger stores the code. TigerBeetle
itself has **no authentication**; the API layer in front of it is where authorization happens. The rule that
shapes the boundary: **"initiating a transfer should not require fetching metadata from the
general purpose database"** (docs.tigerbeetle.com/coding/system-architecture). The posting path takes account
ids and integers. If it must look up a KYC status, a fee schedule or a currency name mid-posting, you have
coupled the money path's availability to the CRM's and given yourself a read whose staleness changes an
economic outcome. Resolve that *before* constructing the group.

The same split downstream: the ledger of record is OLTP and is not a reporting database. Formance ships ledger
logs to replica stores for OLAP querying; Square built Books explicitly to escape group-by aggregations on the
transactional path. A dashboard running `SELECT SUM(amount) FROM entries GROUP BY account_id, currency` over
full history scans the same rows an authorization is trying to read, and Monzo's operational note is that
delayed balance reads force card **stand-in processing**, risking unauthorised negative balances. Write-path
latency is a correctness property here, not a UX one. Replicate the journal for trial balance, statements and
month-end; keep the write path serving the balanced-set entrypoint and the reads that authorise.
