# Worked examples

Four before/after code reviews. Each one starts from code a competent engineer would plausibly ship under
time pressure, names the specific defects the suite catches with the rule cited by the name the owning
skill gives it, and shows the corrected version.

Rules are cited by name, never by an id. `fin-money-core` states ten invariants in full and each domain
skill specialises them under its own heading, so every name in a "What the suite catches" table is a string
you can find with `grep -F` in the skill it is attributed to. The ten are:

| Invariant | Stated in full at |
|---|---|
| *exact representation* | `fin-money-core` |
| *rounding and conservation* | `fin-money-core` |
| *operation identity* | `fin-money-core` |
| *ambiguous outcomes* | `fin-money-core` |
| *durable dedupe* | `fin-money-core` |
| *concurrency on authoritative state* | `fin-money-core` |
| *authority* | `fin-money-core` |
| *reconciliation* | `fin-money-core` |
| *hard limits* | `fin-money-core` |
| *rollout* | `fin-money-core` |

Two more money-core names recur on these pages: *Implemented, not described* and *A comment is a claim*,
both stated in
[authority-limits-and-rollout.md](../skills/fin-money-core/references/authority-limits-and-rollout.md).

Each example ends with the v0.3 output: the `authority` and `exposure` line, one entry per real finding,
and a `VERDICT`, because every page here is framed as a review. There is no seven-label block and no tier
number. Each also ends with a section on what was **not** changed. That second section is the point. A
correctness suite that rewrites everything it touches is a suite nobody runs twice.

| Example | The code | authority · exposure | What it demonstrates |
|---|---|---|---|
| [trading-bot](trading-bot/) | A live Binance spot bot that buys and rests a take-profit | EXTERNAL (Binance) · own | The retry that duplicates an order, a client order ID minted too late to be useful, `0.29 % 0.01 == 0.009999999999999974`, and a take-profit computed from the wrong price and never grossed up for fees |
| [payment-flow](payment-flow/) | A Stripe refund endpoint and its webhook | EXTERNAL (Stripe) · customer | `db.rollback()` on the exact timeout the row exists for while BIGSERIAL keeps advancing, acting on the webhook payload instead of re-fetching, no dispute check, and a `>=` guard against a second-granularity clock |
| [ledger](ledger/) | A transfer with a fee, and its reversal | SELF · customer | A journal that does not balance because the fee has no counterparty leg, an idempotency key made `Optional = None`, conservation as SQL in a comment, and `CHECK (balance >= 0)` blocking the clawback |
| [onchain-indexer](onchain-indexer/) | An ERC-20 deposit crediting service | EXTERNAL (Ethereum mainnet) · customer | A cursor that advances past ranges it never covered, a dedupe key without block identity, `1e18` against USDC's six decimals, and the empty-address branch that strands every customer registered later |

The two fields are the reason the four pages end differently. Exposure decides how much evidence a page has
to show; authority decides which kind. `trading-bot` trades its own capital against a venue that answers any
question about it, so reconciliation is available and the bar is the five properties plus one scheduled
comparison. `ledger` is the system of record, so nothing outside it can say it is wrong, and its proof is
conservation, replay and a planted break instead. `payment-flow` and `onchain-indexer` sit between them: an
external authority exists, and somebody else's money is what is lost.

Two of the four end `NO-SHIP`, deliberately. `payment-flow` has no settlement-report reconciliation and
`onchain-indexer` has one nobody has ever seen fire, and no amount of correct code upstream closes either
gap.

Read `trading-bot` first if you want the shortest path to deciding whether this suite is real; read `ledger`
first if you want the one where the fix is a schema change rather than a code change.
