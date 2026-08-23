# Worked examples

Four before/after code reviews. Each one starts from code a competent engineer would plausibly ship under
time pressure, then names the specific defects the suite catches, with the rule id from `docs/rules.md`, and
shows the corrected version.

Each example ends with a section on what was **not** changed. That section is the point. A correctness suite
that rewrites everything it touches is a suite nobody runs twice.

| Example | The code | What it demonstrates |
|---|---|---|
| [trading-bot](trading-bot/) | A live Binance spot bot that buys and rests a take-profit | The retry that duplicates an order, a client order ID minted too late to be useful, `0.29 % 0.01 == 0.009999999999999974`, and a take-profit computed from the wrong price and never grossed up for fees |
| [payment-flow](payment-flow/) | A Stripe refund endpoint and its webhook | `db.rollback()` on the exact timeout the row exists for while BIGSERIAL keeps advancing, acting on the webhook payload instead of re-fetching, no dispute check, and a `>=` guard against a second-granularity clock |
| [ledger](ledger/) | A transfer with a fee, and its reversal | A journal that does not balance because the fee has no counterparty leg, an idempotency key made `Optional = None`, conservation as SQL in a comment, and `CHECK (balance >= 0)` blocking the clawback |
| [onchain-indexer](onchain-indexer/) | An ERC-20 deposit crediting service | A cursor that advances past ranges it never covered, a dedupe key without block identity, `1e18` against USDC's six decimals, and the empty-address branch that strands every customer registered later |

Read `trading-bot` first if you want the shortest path to deciding whether this suite is real; read `ledger`
first if you want the one where the fix is a schema change rather than a code change.
