# Financial Engineering Skills

[![validate](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Six agent skills that stop generated money code from producing the wrong number while every component behaves
exactly as specified.

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

**Optional routing reinforcement.** The skills are self-sufficient. What a skill cannot do is guarantee it
gets consulted. If you want routing to be more reliable, there is a small block you can install into the
files every agent reads on every turn: the routing table and one instruction, do not call a financial risk
resolved just because you described it.

```bash
git clone https://github.com/nbailo/financial-engineering-skills /tmp/fes \
  && /tmp/fes/scripts/install-guardrails.sh .
```

It writes a marked block into your `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`. It is
under 2 KB, it is idempotent, and it preserves your existing content.
`scripts/install-guardrails.sh --uninstall .` restores your content. The skills behave identically either
way.

---

## Before and after

Ask an agent for a trading bot and it writes this. It compiles, it reads well, and it passes review:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", type="LIMIT",
                             quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

That is a duplicate order. The first request may well have reached the matching engine.

With `fin-exchange-integration` loaded, the same request produces code that commits a client order ID to a
durable intent row **before** the socket write, treats the timeout as `UNKNOWN` rather than as a failure, and
resolves it by querying that ID instead of resubmitting. It validates price and quantity against `tickSize`,
`stepSize`, `minNotional`, `minQty` and `maxQty` together in exact arithmetic, returns an explicit skip when
quantization would produce `qty == 0`, and writes the timeout-that-already-filled test in the same response.

---

## What people use it for

**A trading bot.** The most common case, and the one with the shortest path from a small script to real
money. A few hundred lines that send orders to Binance, Bybit, Hyperliquid or ccxt already carry
duplicate-order risk, venue filters that silently quantize a quantity to zero, fill streams that arrive out
of order, and a position number that drifts from the venue's own.

**A prediction-market bot.** Polymarket, Kalshi or Limitless. The general order-book priors are not merely
incomplete here, several are inverted: there is often no short, only the purchase of a complement; two bids
can cross; the fee is a function of expected profit rather than of notional; and the payout is a number the
venue publishes rather than a constant. A settlement credit then arrives through a path no order of yours
created.

**A payment integration.** Stripe, Adyen, PayPal, Square, or an ACH, SEPA or RTP rail. Refund ceilings
computed from your own order table instead of the processor's, webhooks replayed out of order, disputes and
refunds racing on the same charge.

**A ledger or balances service.** Wallet credits, marketplace payouts, internal transfers. A `SELECT` then
`UPDATE` on a balance, a hold that is never released, a correction written as an edit instead of a reversal.

**An on-chain deposit or withdrawal path.** An indexer that credits deposits, a withdrawal signer, a custody
integration. Reorgs after crediting, nonce gaps, token decimals, a broadcast whose outcome is unknown.

**Plain money arithmetic.** Fee splits, invoice totals, currency conversion, proration. Rounding that leaks
value in one direction, a residue with no owner, a float that becomes an obligation.

---

## The six installed skills

| Skill | For | Loads when |
|---|---|---|
| **fin-exchange-integration** | Clients of a venue they do not operate: bots, market makers, execution engines, prediction-market traders, FIX clients | `create_order`, `tickSize`, `clientOrderId`, ccxt, a depth stream, an outcome token |
| **fin-payments** | Processor and rail integrations: Stripe, Adyen, PayPal, Square, ACH/SEPA/RTP | Intents, capture, refunds, disputes, webhooks, payouts |
| **fin-ledger** | Balances, postings, holds, double-entry books | A journal writer, a balance column, a transfer |
| **fin-onchain** | The chain boundary: deposits, withdrawals, indexers, custody, DeFi integration | Nonces, reorgs, `eth_getLogs`, `decimals()`, a signing session |
| **fin-money-core** | Amount arithmetic, value-moving calls, dedupe keys, money-path rollout | Nothing more specific matches, or a cross-domain mechanism a domain skill does not cover |
| **fin-verification** | Tests, reconciliation, and ship decisions | "is this ready", "write tests", "reconcile", "prove it" |

You do not invoke these by name. Each skill carries its own routing description, and the agent loads the ones
that match the code in front of it. To force one, name it: *use fin-onchain to review this withdrawal flow*.

**How many load.** A domain skill normally wins alone, because it already specialises the generic invariants
onto its own mechanisms. `fin-money-core` loads alongside only for a cross-domain mechanism the domain skill
does not cover, and never merely because a domain-specific retry appears in the diff. `fin-verification`
loads when tests, proof or reconciliation are actually being changed, when the ask is review or readiness, or
when a domain skill demands stronger proof for the mechanism in scope; customer money alone does not load it.
A 200-line Binance bot therefore loads exactly one skill, and never sees double-entry accounting,
chargebacks, or blockchain finality. A crypto exchange backend that credits deposits loads `fin-onchain`
**and** `fin-ledger`, because both mechanisms are present.

---

## Provider support

Coverage is not uniform across venues, and a table that implies it is gets an integration trusted further
than the evidence goes. Four levels:

- **deep / verified**: a dedicated reference for that venue, written from the venue's own documentation,
  carrying its field names, its error codes and the named regression assertions the reference requires you to
  write.
- **generic**: covered only by the cross-venue invariants and the ccxt or FIX references. Nothing
  venue-specific is asserted.
- **experimental**: the public surface exists and some of it is verified, but the current semantics are
  incomplete and the surface is version-sensitive.
- **unverified**: named as a possible integration and **not** advertised as supported.

| Provider | Level | What backs it |
|---|---|---|
| Binance (spot, USD-M, COIN-M) | deep / verified | Dedicated references for orders and error codes, filters, private streams, and the depth-to-snapshot join |
| OKX | deep / verified | Dedicated order and order-book references, plus the cross-venue divergence matrix |
| Bybit | deep / verified | Dedicated order and order-book references, plus the divergence matrix |
| Kraken | deep / verified | Dedicated order and order-book references, including the CRC32 book checksum |
| Coinbase Advanced Trade | deep / verified | Dedicated reference, including the re-POST semantics that differ from the other venues in the set |
| Deribit | deep / verified | Dedicated reference: order identity, recovery endpoints, price validity as significant figures |
| Hyperliquid spot and perpetuals | deep / verified | Dedicated reference: `cloid`, nonce semantics, positions, funding, venue-originated liquidation and ADL as client-observed facts |
| Polymarket CLOB V2 | deep / verified | Dedicated reference against production V2: the signed-struct field changes, pUSD collateral, protocol-selected fees, tick discovery |
| Kalshi | deep / verified | Dedicated reference: direction vocabularies, fixed-point migration, fee authority chain, market lifecycle, sharding |
| Limitless | deep / verified | Dedicated reference: auth modes and scopes, the delegated and EOA order surfaces, `orderEvent` sources, redemption |
| Hyperliquid outcome markets | experimental | Four `outcomeMeta` field names and the `settledOutcome` request are verified; the naming, asset ids and coin encoding are not, and are labelled unverified in the reference |
| Alpaca | generic | Cross-venue invariants and the ccxt reference only. No primary-source pass on its current API was made in this release, so it was removed from the skill's provider list rather than left implying more |
| Any venue reached through ccxt | generic | The ccxt reference: its precision modes, its retry funnel, and what a unified type flattens |
| Any FIX venue | generic | The FIX reference: `PossDupFlag`, `PossResend`, `OrigClOrdID`, resend semantics |
| Everything else | unverified | Nothing is listed here in this release. A venue arrives at `generic` or better with a reference, or it is not named at all |

Three things this table does not say. No level implies the suite ships recorded API captures: the fixture an
assertion runs against is captured from your own account, and the references say which call to capture and why
a hand-written one proves nothing.

Provenance coverage is now uniform in shape and deliberately uneven in strength, and the difference is the
part to read before you trust a row. Every provider and protocol reference carries a block: provider, surface,
version, `verified_at`, the source URLs, what was verified, an explicit list of what was **not**, and a
`revalidate_when` trigger. What differs between them is the date. The prediction-market references, the
protocol files under the opt-in skills, and three references re-checked for this release carry a real
`verified_at`: the ccxt reference against a pinned commit, the FIX reference against the FIX 4.4 dictionary
and Binance's own FIX document, and the Regulation NMS file against the SEC order and the current CFR text.

The rest, including the Binance, OKX, Bybit, Kraken, Coinbase Advanced Trade, Deribit and Hyperliquid venue
files, the on-chain transaction references and the ISO 20022 file, carry `verified_at: not established`. They
were written from vendor documentation and cite it inline, and nobody has re-read that documentation since, so
the block says exactly that instead of showing a date nobody earned. An honest missing date is the point of
the field: `scripts/validate.py --provenance-report` counts those files on their own line and never lets one
age quietly into looking fresh.

And no row is permanent. A venue changes a field name, a fee rule or an error code whenever it likes, and a
dated block is what makes that visible rather than silent.

---

## Worked examples and evidence

`examples/` holds before and after code reviews. Each starts from code a competent engineer would plausibly
ship under time pressure, names the defects with the rule cited by the name its owning skill gives it, and
shows the corrected version. Not all of them end `SHIP`, deliberately.

Where a change is economic, a review opens with one line naming whose copy of each quantity is the record
and whose money is at stake, then one entry per real finding and nothing for a concept the change does not
touch:

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

**What the rules were selected against.** Candidate rules were graded against a no-guidance control run:
realistic financial coding tasks, several repetitions each, scored per probe, with the model given no
financial guidance at all. Roughly half of what was measured, the model already got right unaided, and a rule
written for those is token waste that pulls attention away from the probes it fails every time. Those rules
were cut. What the suite keeps is the probes that never passed and the ones where repetitions disagreed.

**What is not measured.** The control run is a working artefact and is not published in this repository, so
take it as method rather than as evidence. There is no published head-to-head score for this suite against a
baseline, and none is claimed. What you can check is the primary source under each rule, the provenance block
on the references that carry one, and the worked examples.

---

## Advanced material

Matching against resting orders, pro-rata allocation, auctions, self-trade prevention, price bands and
market-data publication are a different job from calling a venue, and a much rarer one. That material lives
in `advanced/`, is not installed by `npx skills add`, and is not part of the routing table. Its audience is a
small fraction of the users of the other six, and a description that sits in every agent's skill listing is
paid for by everyone. `advanced/README.md` says how to install it deliberately.

Three subjects sit outside the whole repository, `advanced/` included: clearing, netting and settlement
finality; liquidation waterfalls; and venue-operated resolution, meaning the operator's decision about what
an instrument is worth at expiry. They were deleted rather than relocated. No skill covers them and nothing
here routes to them, which is why you will not find a pointer promising otherwise.

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

**Rules attacked rather than admired.** "Terminal states must be absorbing" sounds obvious and is **wrong**:
nautilus_trader deliberately ships `(Canceled, Filled) => Filled`, annotated `// Real world possibility`,
because a fill and a cancel acknowledgement cross on the wire and the fill is real money.

**What it deliberately does not cover.** Security: it will not find injection, broken auth or leaked
secrets, and asks a different question, whether the system can produce an incorrect economic outcome while
every component behaves exactly as specified. Smart-contract auditing: `fin-onchain` covers integration
correctness, while reentrancy and access control belong to a contract-audit tool. A ban on floating point:
float is the correct type for greeks, implied vol, Monte Carlo and backtest statistics, and the rule is about
obligations rather than about finance. Institutional ceremony for small projects: a 300-line bot trading its
author's own capital is asked for a handful of tests and a daily comparison against the venue, not for
deterministic simulation.

---

## Contributing

```
skills/                     the six installed skills, each SKILL.md plus references/
advanced/                   the venue-side skills, opt-in
AGENTS.md                   the optional routing block (CLAUDE.md is a symlink)
incidents/                  real, cited incidents mapped to the rules they motivate
examples/                   before and after on real money paths
CONTRIBUTING.md             how to add a rule, a reference or an incident
docs/architecture.md        the hierarchy, the invariants, authority and exposure, the output contract
docs/failure-taxonomy.md    the ways a correct-looking system produces a wrong number
scripts/validate.py         spec conformance, budgets, provenance and the reference web
scripts/eval_routing.py     scores the routing evals and prints what it cannot decide
evals/routing-cases.yaml    tasks with the skill set each should load, positive and negative
scripts/install-guardrails.sh   idempotent install of the routing block
```

The most valuable contribution is an incident: a wrong economic outcome you watched happen, with a citable
source, that motivates a rule an agent could apply. The second is evidence that a rule is unnecessary or
wrong. Rules have been cut both ways. `CONTRIBUTING.md` has the format, the sourcing bar, and what gets
rejected.

Run `python3 scripts/validate.py` before opening a PR. It enforces the Agent Skills spec and this repo's own
budgets: 210 lines per `SKILL.md`, 2 KB for `AGENTS.md`, 430 characters per description and 2,600 characters
across the suite, because the agent's skill listing is shared with every other suite a user has installed.

MIT licensed.
