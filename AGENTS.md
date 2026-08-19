# Financial correctness — always on

Applies to any code that trades, moves, stores, prices, settles, or accounts for money or economically
valuable assets. Security asks whether an attacker can force an unauthorised action. This asks a different
question: **can the system produce an incorrect economic outcome while every component behaves as specified?**

## G1 — Gate first, and default to ON

This diff is economic if a changed path matches the repo's money paths, imports a payment / exchange / chain /
ledger client, or touches a symbol matching
`balance|amount|price|qty|order|refund|payout|transfer|ledger|posting|settle|withdraw`.
Otherwise answer **AMOUNT · EFFECT · AUTHORITY · REPLAY · ROLLOUT** from the diff alone:

- **AMOUNT** — touches a value that is or becomes an amount owed, held, ordered, posted, priced or settled.
- **EFFECT** — calls, retries, or handles the failure of something that moves value or instructs someone else to.
- **AUTHORITY** — changes who or what decides a balance, price, limit or eligibility, *including reuse of an
  existing flag, enum or config value*.
- **REPLAY** — changes identity, keys, ordering or dedupe.
- **ROLLOUT** — changes deploy or config for a money path that is sharded, regionalised, or must be fleet-uniform.

All five NO and no match → emit `ECONOMIC-DIFF: none` and review normally.
Otherwise emit `ECONOMIC-DIFF: <which>` and `Financial tier: T<n> (inferred from: <signal>)`, then load the
matching `fin-` skill. **Skip** when the numbers are analytics that never become an obligation — backtest
statistics, greeks, implied vol, Monte Carlo — and no balance, order, payment or transfer is written.

*The gate's job is to exempt, not to admit.*

## G2 — A named risk is implemented, or the process refuses to start

In a money path, a comment, a TODO, a design note, a "worth adding", a defined-but-uncalled function, or a
`...` stub describing a missing control **is the same defect as the missing control**. Any response touching a
money path ends with a **NAMED RISKS** table — one row per risk identified, three columns:
`risk | implemented at file:line | test name`. A row with no `file:line` fails the run. If you will not
implement it, make the path uncallable: `raise NotImplementedError` **on a path that is actually reached**.

*Measured: the single highest-frequency failure — the model names the correct control accurately, then writes a
comment instead of implementing it.*

## G3 — Every claim in a comment is checked against the code in the same pass

Read the design notes and docstrings as a list of claims. For each asserted property — "the flush guarantees
the row exists", "the monotonic guard makes gaps impossible" — either point at the test that proves it or
delete the sentence.

*Measured: the asserted invariant is repeatedly exactly where the bug lives, and the assertion is what let it
survive self-review.*

## G4 — An ambiguous external call has three phases, and the first one COMMITs

Mint the idempotency key from the **intent instance**, from a value that survives `ROLLBACK`. **Commit** the
intent row carrying that key — `flush()` inside an open transaction is not persistence — then make the call,
then record the outcome. A timeout, socket close, 5XX, 429, `-1006` or `-1007` is **UNKNOWN, never "did not
happen"**: leave the intent committed, query the counterparty for the identity you sent, and never resubmit.
No `session.begin()` / `engine.begin()` / `@transaction.atomic` may lexically enclose the external call.

*A `rollback()` on the exact timeout the row exists for, while the sequence keeps advancing, mints a new key on
retry and pays twice.*

## G5 — A pushed event is a notification whose arrival order you do not control

Two guards, both required.
**Legality:** enumerate the legal `(state, event)` pairs and reject everything else with an explicit error —
never silently ignore. A terminal state accepts exactly the events by which the counterparty corrects a fact
you already booked (a late fill, a fill void) and nothing else; it is never re-opened by a *status* message.
**Version:** the watermark is keyed on the entity id, stored independently of the live object, and the guard
**is** the write — `UPDATE watermarks SET v=:v WHERE id=:id AND v<:v`, proceed only on rowcount 1, in the same
transaction as the effect.
Then **re-read the object from its authority** — amount, status and attribution — before any value-moving
decision. Never act on the pushed payload's state.

## G6 — A watermark advances only past a range you verifiably covered

Advance a cursor, watermark or high-water mark only inside the same conditional and the same transaction that
covered that range. An error, a provider range rejection, a result count at the documented cap, or a truncated
page is a **hole, not an empty result**. A branch that skips the work skips the advance.

*Failure mode: permanent silent under-crediting — value vanishes with no error and no log line.*

## G7 — The reconciliation runs in production, or it does not exist

Name the external authority and the join key for every economic quantity you report, and ship the comparison as
a scheduled entrypoint reading through a path independent of the writer. The alert destination is a config key
with **no default** that raises at import if unset. An invariant that exists as SQL in a comment, a docstring,
or a "worth running as a cron" note counts as absent.

*The only control that catches the failures of every other rule.*

## Which skill

| The code… | Load |
|---|---|
| sends/cancels/tracks orders on a venue it does not operate, or derives fills, positions, PnL or a book from one | `fin-exchange-integration` |
| **is** the venue — matching, allocation, market-data publication, clearing, settlement, liquidation | `fin-matching-and-settlement` |
| integrates a payment processor or rail — intents, capture, refunds, disputes, webhooks, payouts | `fin-payments` |
| maintains balances, postings, holds, or double-entry books | `fin-ledger` |
| crosses the chain boundary — transactions, nonces, finality, reorgs, indexing, token semantics | `fin-onchain` |
| none of the above, or amount arithmetic / retry logic / rollout of a money path | `fin-money-core` |
| is about to ship, or needs tests, reconciliation, or proof it is correct | `fin-verification` |

Load every row that matches. An exchange backend is `fin-matching-and-settlement` **and** `fin-ledger`.

## Tier

Report it; it gates the required evidence, never which rules apply.

- **T0** — no value-moving call reachable, or all behind a `dry_run`/paper guard, and every host is a sandbox.
- **T1** — a value-moving call is reachable and a live credential path exists. *Own capital, bounded loss.*
- **T2** — a `user_id`/`customer_id`/`tenant_id` on a balance or position row, a payout or withdrawal path, a
  crediting webhook, or two or more venue/processor adapters. *Someone else eats the error.*
- **T3** — you are the system of record: matching or allocating across resting orders, a ledger writer that is
  not a mirror of an external processor, a custody signer, an ID assigner other systems consume, a sequencer or
  settlement batch, a mint/burn authority. **No external oracle exists to reconcile against.**

**Escalate one tier** for: a `SELECT` then `UPDATE` on a balance in separate statements; a money transaction
whose isolation level is never set; a per-entity override on a solvency or liquidation check; an immutable
deploy target with multi-day fix latency; one codebase deployed to N chains or regions.

A `FINANCIAL_TIER:` line in this file overrides inference. It may raise the tier freely; lowering it requires an
explicit statement, because under-tiering is the dangerous direction.
