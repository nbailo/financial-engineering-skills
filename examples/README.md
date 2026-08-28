# Worked examples

Four before/after code reviews and two runnable examples. Each review starts from code a competent engineer
would plausibly ship under time pressure, names the specific defects the suite catches with the rule cited
by the name the owning skill gives it, and shows the corrected version.

Two are not prose but code that runs, offline, with a test suite that denies itself a socket.
[payment-ledger-reconciliation](payment-ledger-reconciliation/) is the shortest path through a money flow:
one invoice charged through a fake in-process processor, an ambiguous timeout, a redelivered and
concurrently delivered settlement webhook, balanced immutable postings, an injected failure after the money
moved, and
a reconciliation against a frozen settlement report. Run
`python3 examples/payment-ledger-reconciliation/demo.py` and watch the unsafe version charge 375.00 for a
125.00 invoice, force its books to agree with the report, and still end with one `duplicate_entry` break it
could not plug, and a local intent and ledger that attribute the two extra charges to nothing.
[prediction-market-bot](prediction-market-bot/) is the larger one: a fake venue in this process, a safe bot,
a counter-example and a frozen event log. Run `python3 examples/prediction-market-bot/demo.py` and watch the
unsafe version credit a payout twice while the safe one credits it once.

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

Citing a money-core name is not the same as loading `fin-money-core`. On each of these pages the domain
skill wins on its own, because it already specialises the invariants that apply. `fin-money-core` is loaded
alongside only for a cross-domain mechanism the domain skill does not cover, and `fin-verification` only
where tests, proof or reconciliation are actually being changed, where the ask is review or readiness, or
where the domain skill requires stronger proof for the mechanism in scope. Customer exposure alone never
loads it.

Each of the four reviews ends with the review output: the `authority` and `exposure` line, one entry per
real finding, and a `VERDICT`, because those four pages are framed as reviews. There is no seven-label
block and no tier number. Each also ends with a section on what was **not** changed. That second section is
the point. A correctness suite that rewrites everything it touches is a suite nobody runs twice.

| Example | The code | authority · exposure | What it demonstrates |
|---|---|---|---|
| [trading-bot](trading-bot/) | A live Binance spot bot that buys and rests a take-profit | MIXED · own | The retry that duplicates an order, a client order ID minted too late to be useful, a design note that reads `-2010 "Duplicate order sent."` as a safe rejection when it is evidence the first order is open, `0.29 % 0.01 == 0.009999999999999974`, and a take-profit computed from the wrong price and never grossed up for fees |
| [payment-flow](payment-flow/) | A Stripe refund endpoint and its webhook | MIXED · customer | `db.rollback()` on the exact timeout the row exists for while BIGSERIAL keeps advancing, acting on the webhook payload instead of re-fetching, no dispute check, and a `>=` guard against a second-granularity clock |
| [ledger](ledger/) | A transfer with a fee, and its reversal | MIXED · customer | A journal that does not balance because the fee has no counterparty leg, an idempotency key made `Optional = None`, conservation as SQL in a comment, and `CHECK (balance >= 0)` blocking the clawback |
| [onchain-indexer](onchain-indexer/) | An ERC-20 deposit crediting service | MIXED · customer | A cursor that advances past ranges it never covered, a dedupe key without block identity, `1e18` against USDC's six decimals, and the empty-address branch that strands every customer registered later |
| [payment-ledger-reconciliation](payment-ledger-reconciliation/) | Runnable. A fake in-process processor, a safe flow and a counter-example, with tests | MIXED · customer | An operation identity bound to invoice, amount, currency and provider and committed before the send, so an ambiguous timeout is resolved by querying and then following the provider's contract; postings built from the processor's own figures and never from the notification body; two separate workers over one shared store crediting a settlement exactly once; balances that name a currency; and a planted settlement difference reported as one compound break instead of posted away to suspense |
| [prediction-market-bot](prediction-market-bot/) | Runnable. A fake in-process venue, a safe bot and a counter-example, with tests | MIXED · own | Collateral held while an order rests, a lost response left UNKNOWN instead of retried under a fresh key, fee amount, fee rate and fee asset kept apart across maker and taker fills, deterministic rebuild from the log, and a payout credited once rather than twice on reconnect or zero on a split |

Authority is a property of a quantity, not of a codebase. Where one authority covers every quantity in
scope the page emits the single line, `authority: EXTERNAL (<who>) · exposure: <e>`. None of these six is
that case, because each one keeps local state that decides something economic, so each review emits
`authority: MIXED` and qualifies only the quantities that differ, on one line each and never more than
three. What each page holds alone is different every time: the unresolved intent rows on `trading-bot`,
the in-flight refund reserve on `payment-flow`, the postings and the transaction ids on `ledger`, the
customer liabilities and the address mapping on `onchain-indexer`, the committed intents and the collateral
held against them on `prediction-market-bot`, the operation identities and the postings on
`payment-ledger-reconciliation`. The last two emit no review line: the same two facts are visible in their
code, where a fake counterparty answers for what it holds and this process alone holds the intents.

The two fields are the reason the pages end differently. Exposure decides how much evidence a page has
to show; authority decides which kind: where an external party holds the number the proof is a comparison,
and where nothing outside holds it the proof is replay, conservation and a planted break. `trading-bot`
trades its own capital against a venue that answers any question about its orders. `ledger` assigns the ids
and decides the balances, and only its aggregate has a custodian on the other side. `payment-flow` and
`onchain-indexer` sit between: an external authority holds the money, and somebody else's money is what is
lost. `prediction-market-bot` and `payment-ledger-reconciliation` end with neither a verdict nor a finding list,
because their evidence is executable: an offline double of the counterparty, and cases that run.

Two of the four reviews end `NO-SHIP`, deliberately. `payment-flow` has no settlement-report reconciliation and
`onchain-indexer` has one nobody has ever seen fire, and no amount of correct code upstream closes either
gap.

Run `payment-ledger-reconciliation` first if you would rather watch the difference than read about it; read
`trading-bot` if you want the shortest written path to deciding whether this suite is real; read `ledger`
for the one where the fix is a schema change rather than a code change.
