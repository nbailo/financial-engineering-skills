# Financial Engineering Skills

Ask a coding agent for a trading bot, a refund handler, or a balance update and you get working software.
It compiles, it reads well, it passes review. It can still lose money, because the defect is economic
rather than technical: nothing throws, no test goes red, and the number at the end is wrong.

Four questions, in the form you actually ask them:

- *Your trading bot times out after sending a marketable order. Is retrying safe?*
- *A Stripe refund webhook is delivered twice and out of order. Can the customer be credited twice?*
- *An on-chain withdrawal broadcast times out. How do you avoid signing or sending a second transfer?*
- *Two concurrent balance updates both pass the same limit check. Can the account overspend?*

Six skills answer them at the level of the diff: the mechanism that produces the loss, the code that closes
it, and the test that proves it stays closed. They install once and load themselves when the code in front
of the agent is a money path.

---

## Install

```bash
npx skills add nbailo/financial-engineering-skills
```

That is the whole installation. The six skills land in `skills/`, and your agent picks them up from there.

Claude Code users can instead install as a plugin, which namespaces the skills so they cannot collide with
anything else installed:

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

---

## What people use it for

**A trading bot.** The most common case, and the one with the shortest path from a small script to real
money. A few hundred lines that send orders to Binance, Bybit, Hyperliquid, Alpaca or ccxt already carry
duplicate-order risk, venue filters that silently quantize a quantity to zero, fill streams that arrive out
of order, and a position number that drifts from the venue's own.

**A payment integration.** Stripe, Adyen, PayPal, Square, or an ACH, SEPA or RTP rail. Refund ceilings
computed from your own order table instead of the processor's, webhooks replayed out of order, disputes and
refunds racing on the same charge.

**A ledger or balances service.** Wallet credits, marketplace payouts, internal transfers. A `SELECT` then
`UPDATE` on a balance, a hold that is never released, a correction written as an edit instead of a reversal.

**An on-chain deposit or withdrawal path.** An indexer that credits deposits, a withdrawal signer, a
custody integration. Reorgs after crediting, nonce gaps, token decimals, a broadcast whose outcome is
unknown.

**Plain money arithmetic.** Fee splits, invoice totals, currency conversion, proration. Rounding that leaks
value in one direction, a residue with no owner, a float that becomes an obligation.

---

## The six skills

| Skill | For | Loads when |
|---|---|---|
| **fin-exchange-integration** | Clients of a venue they do not operate: bots, market makers, execution engines, broker OMS, FIX clients | `create_order`, `tickSize`, `clientOrderId`, ccxt, a depth stream |
| **fin-payments** | Processor and rail integrations: Stripe, Adyen, PayPal, Square, ACH/SEPA/RTP | Intents, capture, refunds, disputes, webhooks, payouts |
| **fin-ledger** | Balances, postings, holds, double-entry books | A journal writer, a balance column, a transfer |
| **fin-onchain** | The chain boundary: deposits, withdrawals, indexers, custody, DeFi integration | Nonces, reorgs, `eth_getLogs`, `decimals()`, a signing session |
| **fin-money-core** | Amount arithmetic, value-moving calls, dedupe keys, money-path rollout | Nothing more specific matches, or a cross-domain mechanism a domain skill does not cover |
| **fin-verification** | Tests, reconciliation, and ship decisions | "is this ready", "write tests", "reconcile", "prove it" |

---

## How routing works

You do not invoke these by name. Each skill carries its own routing description, and the agent loads the
ones that match the code in front of it.

| You ask for | What loads |
|---|---|
| a Binance bot that buys when the 5-minute RSI drops below 30 | `fin-exchange-integration` |
| a Stripe refund handler | `fin-payments` |
| a double-entry posting path for marketplace payouts | `fin-ledger` |
| an indexer that credits deposits on Base | `fin-onchain` |

Three rules keep the load small. The domain skill normally wins on its own, because it already specialises
the generic invariants that apply to its domain. `fin-money-core` loads alongside only for a cross-domain
mechanism the domain skill does not cover. `fin-verification` loads when tests, proof or reconciliation are
actually being changed, when the ask is review or readiness, or when the domain skill demands stronger proof
for the mechanism in scope. Customer money alone does not load it.

A 200-line Binance bot therefore loads exactly one skill, and never sees double-entry accounting,
chargebacks, or blockchain finality. A crypto exchange backend that credits deposits loads
`fin-onchain` **and** `fin-ledger`, because both mechanisms are present.

To force a particular skill, name it:

> use fin-onchain to review this withdrawal flow

---

## What changes in the generated code

Ask an agent for a trading bot and it writes this:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", type="LIMIT",
                             quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

That is a duplicate order. The first request may well have reached the matching engine.

With `fin-exchange-integration` loaded, the same request produces code that commits a client order ID to a
durable intent row **before** the socket write, treats the timeout as `UNKNOWN` rather than as a failure,
and resolves it by querying that ID instead of resubmitting. It also validates price and quantity against
`tickSize`, `stepSize`, `minNotional`, `minQty` and `maxQty` together in exact arithmetic, returns an
explicit skip when quantization would produce `qty == 0`, and writes the timeout-that-already-filled test in
the same response.

It knows one more thing: on Binance, OKX and Kraken a client order ID is unique only among *open* orders, so
resending after a fill creates a second order, while Coinbase Advanced Trade is the one venue in the set
where re-POSTing returns the original. That distinction is in no single vendor's documentation.

**What a review looks like.** Where the change is economic, the response opens with one line naming whose
copy of each quantity is the record and whose money is at stake, then one entry per real finding and nothing
for a concept the change does not touch:

```
authority: EXTERNAL (Binance) · exposure: own

FINDING   the wrong economic outcome, concretely
WHY       the mechanism that produces it
EVIDENCE  file:line
FIX       the change that closes it
TEST      the property to assert
```

A control the agent names but does not build is reported as `UNRESOLVED: <control> (<why>)`, never as a
completed checklist row. That single rule addresses the most common failure in generated money code: naming
the correct control accurately, then writing a comment instead of implementing it.

Four worked before/after reviews live in `examples/`, one per money path.

---

## What it deliberately does not cover

- **Security.** It will not find injection, broken auth, or leaked secrets. Security asks whether an
  attacker can force an unauthorised action. This asks whether the system can produce an incorrect economic
  outcome while every component behaves exactly as specified. Use both; neither substitutes for the other.
- **Smart-contract auditing.** `fin-onchain` covers integration correctness: nonces, finality, reorgs,
  indexing, token semantics. Reentrancy and access control belong to a contract-audit tool.
- **A ban on floating point.** Float is the correct type for greeks, implied vol, Monte Carlo and backtest
  statistics. The rule is about obligations, not about finance.
- **Institutional ceremony for small projects.** Exposure gates how much evidence is owed, never which rules
  apply. A 300-line bot trading its author's own capital is asked for a handful of tests and a daily
  comparison against the venue. It is not asked for deterministic simulation.

---

## Why `advanced/` exists

Matching against resting orders, pro-rata allocation, auctions, self-trade prevention, price bands,
market-data publication, netting, settlement and liquidation are a different job from calling a venue, and a
much rarer one. That material lives in `advanced/`, is not installed by `npx skills add`, and is not part of
the routing table.

Two reasons. Its audience is a small fraction of the users of the other six, and a description that sits in
every agent's skill listing is paid for by everyone. And shipping it by default would make this read as a
toolkit for building trading venues, which it is not. The common case is being a venue's *client*, which is
`fin-exchange-integration`. The `advanced/` README says how to install it deliberately.

---

## How this was built

Every rule traces to something you can check yourself.

**Primary sources, not blog posts.** Exchange and processor API documentation read directly from the
vendors. Protocol specifications: FIX, Nasdaq OUCH, CME MDP, ISO 20022. TigerBeetle, PostgreSQL isolation
semantics, Jepsen. SEC, FCA and CFTC enforcement documents read in full, which is how we know the CFTC-SEC
flash-crash report names no firm ("Waddell" appears nowhere in it) and contains no "$1 trillion" figure.

**Real source code, not documentation.** ccxt, freqtrade, hummingbot, nautilus_trader, TigerBeetle and the
Stripe SDKs were cloned and read. That is how we know ccxt's retry funnel has no HTTP-method discrimination,
so a `POST` create-order is retried on the same terms as a `GET` ticker, under the identical client order ID.

**Cited incidents, corrected against the primary text.** Nobody attacked Knight Capital: a repurposed flag,
dead code left in the binary, and a deploy that reached seven of eight servers cost $460M in 45 minutes.
Nobody attacked Citigroup when a customer account was credited with $81 trillion; the path had no ceiling.
Nobody attacked Revolut when two payment systems disagreed for months about what "declined" meant, at a cost
of roughly $20M, until a partner bank's cash-position report noticed. Twenty of these are in `incidents/`,
each mapped to the rule it motivates.

**A deliberately small rule set.** Guidance a competent engineer already applies was left out on purpose,
and rules were attacked rather than admired. "Terminal states must be absorbing" sounds obvious and is
**wrong**: nautilus_trader deliberately ships `(Canceled, Filled) => Filled`, annotated
`// Real world possibility`, because a fill and a cancel acknowledgement cross on the wire and the fill is
real money.

---

## Optional routing guardrails

The skills are self-sufficient. What a skill cannot do is guarantee it gets consulted. If you want routing
to be more reliable, there is a small block you can install into the files every agent reads on every turn.
It holds the routing table and one instruction: do not call a financial risk resolved just because you
described it.

```bash
git clone https://github.com/nbailo/financial-engineering-skills /tmp/fes \
  && /tmp/fes/scripts/install-guardrails.sh .
```

It writes a marked block into your `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`. It is
under 2 KB, it is idempotent, and it preserves your existing content.
`scripts/install-guardrails.sh --uninstall .` restores your content. The skills behave identically either
way.

---

## Repository

```
skills/                     the six installed skills, each SKILL.md plus references/
advanced/                   the venue-side skill, opt-in
AGENTS.md                   the optional routing block (CLAUDE.md is a symlink)
incidents/                  real, cited incidents mapped to the rules they motivate
examples/                   before and after on four money paths
CONTRIBUTING.md             how to add a rule, a reference or an incident
docs/architecture.md        the hierarchy, the invariants, authority and exposure, the output contract
docs/failure-taxonomy.md    the ways a correct-looking system produces a wrong number
scripts/validate.py         spec conformance and budget enforcement
scripts/install-guardrails.sh   idempotent install of the routing block
```

Run `python3 scripts/validate.py` before opening a PR. It enforces the Agent Skills spec and this repo's own
budgets: 210 lines per `SKILL.md`, 2 KB for `AGENTS.md`, 430 characters per description and 2,600 characters
across the suite, because the agent's skill listing is shared with every other suite a user has installed.

---

## Contributing

The most valuable contribution is an incident: a wrong economic outcome you watched happen, with a citable
source, that motivates a rule an agent could apply. The second is evidence that a rule is unnecessary or
wrong. Rules have been cut both ways. `CONTRIBUTING.md` has the format, the sourcing bar, and what gets
rejected.

MIT licensed.
