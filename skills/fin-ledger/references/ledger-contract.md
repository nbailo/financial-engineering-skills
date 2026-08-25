# The ledger contract: what each verb must leave in the diff

Two artefacts. The **verb index** says, for the operation you are actually writing, which artefacts have to be
visible in the diff before the path counts as done. The **ledger contract block** is the richer output form,
emitted only when this system is its own authority for the balance (`authority: SELF`) and the change adds,
routes or reshapes a write to the entries. For anything smaller, the default finding entries in `SKILL.md` are
the whole output.

Neither is a new rule. Both are the same invariants read from the other end: instead of "here is the property",
"here is the line of code a reviewer can point at that proves the property is present".

## Contents

1. **The verb index**: `post`, `hold`, `release`, `reverse`, `close`, `reconcile`, and what each requires.
2. **How to read a verb row**: the row binds on the operation, not on the file.
3. **The ledger contract block**: the predicate-gated evidence table and the rules for emitting it.
4. **When the block is not warranted**: three kinds of change that stay on findings alone.
5. **Evidence when authority is SELF**: what `fin-verification` adds on top.

## 1 · The verb index

Not separate rules: this is the table of which invariants bind on the operation you are writing, and what that
leaves behind in the diff.

| verb | the diff must contain |
|---|---|
| `post` | one call to the balanced-set entrypoint; every leg enumerated including counterparty, fee and cost; a caller-supplied identity required by the signature; the application role unable to mutate or remove a posted entry (no `UPDATE`/`DELETE` grant on the entry table) |
| `hold` | the invariant checked **at reserve time**; an expiry the reader enforces without waiting on a callback (an intrinsic `expires_at`); the spendable number derived as posted minus live holds (`available = posted - active_holds`), and nothing else authorising |
| `release` | release of the **pending transfer's own amount, in full**, whether you post less than reserved or void; an over-post is rejected, never clamped |
| `reverse` | a uniqueness constraint on the link from reversal to original (`reverses_transaction_id`), so no transaction is reversed twice; overdraft permitted on the reversal posting type; the reversal posts while the account is frozen |
| `close` | the residual swept to a control account and the account closed in one transaction; holds already pending are not resolved by the close, and they still expire on their own clock |
| `reconcile` | a scheduled entrypoint; a read path the writer does not share; the difference landing in a real account with an aged break record (a suspense account plus a `break` row); opening balances backfilled before the first run |

## 2 · How to read a verb row

The row binds on the **operation**, not on the file or the function name. A method called `adjust_balance` that
writes two legs is a `post`. A method called `post_transfer` that decrements one column is not a `post`, it is
the defect. Match on what the code does to value.

More than one row can bind at once, and usually does. A capture path that resolves a hold and books a fee is
`release` and `post` together: the reservation releases in full, the fee is a leg of the same balanced group,
and the group carries one caller-supplied identity. A clawback against a frozen, emptied account is `reverse`
plus `post`, and the two rows disagree about nothing, because the overdraft permission in the `reverse` row is
conditioned on the posting type that the `post` row's entrypoint is writing.

A row you cannot satisfy is not a row to delete. It is either work not yet done, reported as `UNRESOLVED`, or a
sign that the operation is not the verb you thought it was.

## 3 · The ledger contract block

Emit only the rows whose predicate this change matches. A row you emit and cannot fill is work not yet done,
not a row to delete: carry it as `UNRESOLVED: <control> (<why>)`. A row no predicate matches is not a finding.
Every control this change needs that no row names still has to appear as a finding with its `file:line`. An
emitted row whose evidence cell is empty, or that contains "should", "would", "recommend" or "next step", fails
the run.

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

The evidence column is deliberately narrow. `file:line` means a line of executable code, not a docstring
asserting the property and not a test name standing in for the implementation. Where the row says
`migration + test`, both are required, because a grant that is revoked in one environment and present in
another is not a control. Where the row says `file:line ×2`, the two are the point: requiring the identity at
the signature and comparing the stored content on a hit are separate defects, and shipping only the first is
the common one.

## 4 · When the block is not warranted

Three kinds of change are not the system of record for the balance and stay on findings alone:

1. **A mirror.** A ledger that reflects an external processor and is reconciled against it has
   `authority: EXTERNAL`, and the proof that matters is the reconciliation, not the internal contract table.
2. **A read-only derivation.** A report, an export or an as-of query that writes no balance changes nothing
   another system will be owed.
3. **No reachable value-moving path.** A fixture, a paper or dry-run mode with every host a sandbox, or a
   migration that touches no economic column.

Anything else that writes a balance this system owns adds the block, whatever else the change reads as. The
threshold is about which system holds the truth, not about how large the diff is: a two-line change to the
posting entrypoint of a system-of-record ledger is above the line, and a five-hundred-line refactor of a
reporting view is below it.

## 5 · Evidence when authority is SELF

A ledger of record has no external oracle to reconcile against, so the evidence has to come from replay,
determinism and conservation instead. Load `fin-verification` alongside this skill and emit one row per proof
technique it marks required for a system that is its own authority, each row marked PRESENT or ABSENT with its
`file:line`. The techniques that carry the weight here are the ones that survive without an external
counterparty:

- **Replay.** Rebuild every balance from the entries and assert equality with the materialised figures, over a
  data set that includes back-dated entries, reversals and discards.
- **Conservation.** Assert that the whole journal nets to zero per currency, and that every clearing and
  suspense account is either zero or has an owner and an age.
- **Permutation.** Apply the same set of postings in a different arrival order and assert the same final
  balances, which is the only cheap test that catches a read-modify-write on a balance.
- **Crash-boundary recovery.** Kill the process between the entry insert and the balance update and assert the
  two still agree, since that window is where a materialised balance drifts permanently and silently.

An ABSENT row is an honest output. A PRESENT row with no `file:line` is not.
