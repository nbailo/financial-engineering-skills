# Worked examples

Four before/after code reviews. Each one starts from code a competent engineer would plausibly ship under
time pressure, names the specific defects the suite catches with the rule cited by the name the owning
skill gives it, and shows the corrected version.

Rules are cited by name, never by an id. `fin-money-core` states the seven cross-cutting rules in full and
the domain skills specialise them under their own headings, so every name in a "What the suite catches"
table is a heading you can grep for in the skill it is attributed to. The seven are:

| Rule | Stated in full at |
|---|---|
| *The economic-diff gate* | `fin-money-core` |
| *Implemented, not described* | `fin-money-core` |
| *A comment is a claim* | `fin-money-core` |
| *Durable intent before the external effect* | `fin-money-core` |
| *Arrival order is not occurrence order* | `fin-money-core` |
| *Proven coverage before the cursor advances* | `fin-money-core` |
| *Reconciliation runs in production* | `fin-money-core` |

Each example ends with the output block its tier actually requires, and with a section on what was **not**
changed. That second section is the point. A correctness suite that rewrites everything it touches is a
suite nobody runs twice.

| Example | The code | Tier | What it demonstrates |
|---|---|---|---|
| [trading-bot](trading-bot/) | A live Binance spot bot that buys and rests a take-profit | T1 | The retry that duplicates an order, a client order ID minted too late to be useful, `0.29 % 0.01 == 0.009999999999999974`, and a take-profit computed from the wrong price and never grossed up for fees |
| [payment-flow](payment-flow/) | A Stripe refund endpoint and its webhook | T2 | `db.rollback()` on the exact timeout the row exists for while BIGSERIAL keeps advancing, acting on the webhook payload instead of re-fetching, no dispute check, and a `>=` guard against a second-granularity clock |
| [ledger](ledger/) | A transfer with a fee, and its reversal | T3 | A journal that does not balance because the fee has no counterparty leg, an idempotency key made `Optional = None`, conservation as SQL in a comment, and `CHECK (balance >= 0)` blocking the clawback |
| [onchain-indexer](onchain-indexer/) | An ERC-20 deposit crediting service | T2 | A cursor that advances past ranges it never covered, a dedupe key without block identity, `1e18` against USDC's six decimals, and the empty-address branch that strands every customer registered later |

The tier column is the reason the four pages end differently. `trading-bot` trades its own capital, so it
ends with the `FINANCIAL CHECK` and nothing else. The other three have someone else on the far side of the
money, so each adds its skill's domain contract block: `PAYMENTS CONTRACT`, `LEDGER CONTRACT`,
`CHAIN CROSSING`.

Read `trading-bot` first if you want the shortest path to deciding whether this suite is real; read `ledger`
first if you want the one where the fix is a schema change rather than a code change.
