# Financial Engineering Skills

[![validate](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Correctness infrastructure for software that moves, records, reconciles, or trades money.

Payment flows, ledgers, reconciliation jobs, trading clients, on-chain workers and prediction-market
bots fail the same way: the code compiles, raises nothing, and produces the wrong balance, payout,
position or settlement.

Six focused skills for Claude Code, Codex, Cursor, and other agents that read the Agent Skills
format. Your agent loads the one matching the code in front of it and reviews the money path, not
the syntax.

## Install

```bash
npx skills add nbailo/financial-engineering-skills
```

```bash
npx skills list
```

They land in your agent's own directory: `.claude/skills/` for Claude Code, `.agents/skills/` for
Codex, Cursor and several others.

> Pre-1.0: this installs the current `main` branch. Review updates before applying them to sensitive
> financial code.

<details>
<summary>Claude Code plugin (optional)</summary>

```text
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

</details>

More paths, including the two advanced skills, are in [INSTALL.md](INSTALL.md).

## What it catches

| Failure | What goes wrong |
| --- | --- |
| Ambiguous submission | A timeout is read as a failure, the retry lands, and one intent becomes two charges or two live orders |
| Refunds and disputes | A refund is issued against headroom a dispute or in-flight refund already took, and the customer is paid twice |
| Concurrent balance updates | Two transfers read the same balance, both write, and the difference is gone with no error |
| Reconciliation | A break is detected, then posted away to suspense, so the books balance and the signal is gone |
| Venue constraints | Price and size are rounded separately, or quantized to zero, and the order fills at a size nobody chose |
| On-chain credits | A deposit is credited from a notification's amount field, before finality, or without the token's real transfer semantics |
| Prediction-market settlement | A redelivered settlement double-credits, a split payout reads as a winner, or a provisional determination is spent |

The one most agents write by default:

```python
try:
    payout = processor.create_payout(vendor=vendor_id, amount_minor=41250, currency="EUR")
except requests.Timeout:
    payout = processor.create_payout(...)   # retry
```

The timeout says nothing about whether the first request reached the processor. The retry can pay
the vendor twice, and the ledger will record one payout against two debits nobody can attribute.
`fin-payments` treats a lost response as UNKNOWN: commit the operation identity before the send, then
query that identity rather than guessing. What happens next is the provider's contract, not a
universal rule: where a replay is safe it goes out under the same key with identical economic fields,
and where it is not, the answer is resolved by asking. The same discipline is what stops a retried
order becoming two live orders on a venue.

## How to use it

Copy one of these after installing:

```text
Review this Stripe refund flow for combined refund and dispute exposure.

Check this ledger transfer for lost updates, incorrect holds, and unsafe corrections.

Review this payout worker for duplicate payment after an ambiguous timeout.

Review this trading bot for duplicate orders, unknown outcomes, venue filters, and position drift.

Review this withdrawal worker for nonce ownership, ambiguous broadcast, and replay recovery.

Review this prediction-market client for outcome identity, fee semantics, and terminal settlement.
```

Routing is automatic: the agent picks the skill from the code and the request. Name one directly to
force a specific review, for example "use `fin-verification`: is this safe to ship?".

## The six skills

| Skill | What it owns | Typical use |
| --- | --- | --- |
| `fin-money-core` | Amount arithmetic, rounding, operation identity, retries, dedupe, concurrency, limits | Anything that moves or records value |
| `fin-exchange-integration` | Orders, fills, positions, PnL, fees, reconnects, and **prediction markets** | Trading bots, venue clients, market makers |
| `fin-payments` | Capture, refunds, disputes, payouts, webhooks, settlement reconciliation | Processor and rail integrations |
| `fin-ledger` | Postings, balances, holds, reversals, period close | Balance systems other services trust |
| `fin-onchain` | Deposits, finality and reorgs, nonces, token semantics, custody state | Wallets, indexers, withdrawal workers |
| `fin-verification` | Reconciliation, planted-break tests, crash recovery, replay | Tests, kill switches, ship decisions |

Prediction markets are part of `fin-exchange-integration`, not a seventh skill: a Polymarket or
Kalshi client is a venue client with binary-contract arithmetic on top.

## Where it applies

**Payments.** Stripe, Adyen, PayPal and bank rails, where a redelivered webhook, a refund ignoring
an open claim, or a reversal assuming the fee came back moves real money.

**Ledgers.** Double-entry books, holds and available-versus-posted balances, where a correction
written as an edit destroys the audit trail.

**Reconciliation and release.** The nightly job that is supposed to make an overstatement impossible,
the crash-boundary test nobody wrote, and the question of whether a money path is safe to ship.

**Trading and exchange clients.** A few hundred lines against Binance, Bybit, Hyperliquid or ccxt
already carry duplicate-order risk, filters that quantize a size to zero, fills that arrive out of
order, and a position that drifts from the venue's own.

**On-chain.** Nonce ownership, reorg handling and credited value, where a notification's amount is
not what the protocol actually moved.

**Prediction markets.** Polymarket, Kalshi and Limitless, with the arithmetic binary contracts need:
a fee on notional and the same rate on payout are different trades, and complete-set collateral is
the maximum liability across resolution states, not the largest entry in a payout vector.

Exchange-venue coverage is deliberately uneven. [docs/providers.md](docs/providers.md) is that one
table: which trading venues have dedicated references and how fresh each one's sourcing is. It is
not an index of the repository, and says nothing about payments, ledger or reconciliation coverage.

## Run the examples

Cloning here is for running the examples, not for installing the skills.

```bash
git clone https://github.com/nbailo/financial-engineering-skills
cd financial-engineering-skills
python3 examples/payment-ledger-reconciliation/demo.py
python3 examples/payment-ledger-reconciliation/run_tests.py
```

One invoice is paid through a fake processor and recorded in a double-entry ledger. The request
times out ambiguously, the settlement webhook is redelivered, an injected failure lands after the
money moved but before the local write finished, and the morning settlement report disagrees with
the books. The unsafe version charges the customer three times, credits the settlement twice, then
forces cash and fees to match the report and drops the difference into suspense. It raises no
exception. It does not end clean either: it ends with one `duplicate_entry` break it could not plug,
125.00 at stake, and a local intent and ledger that attribute the two extra charges to nothing. The
corrected version prevents or reports each one.

```bash
python3 examples/prediction-market-bot/demo.py
python3 examples/prediction-market-bot/run_tests.py
```

Two bots read the same frozen event log. The unsafe one books a redelivered settlement twice, reads
a split payout as a winner and credits nothing, charges fees in the wrong asset, and never reserves
for concurrent resting orders. The corrected bot prevents or detects each one.

Both suites run offline, with no credentials and no live venue or processor. Each ships a counterparty that runs in the same process, which is the point: an ambiguous timeout is only
interesting if the other side already recorded the effect.

## Advanced skills

Two more skills live in `advanced/`, **not** installed by default. `fin-matching-engine` is for
teams operating the venue that creates executions; `fin-market-data-publication` is for teams
originating a canonical feed. Both are opt-in BETA. Clients of someone else's venue, and consumers
of someone else's feed, want `fin-exchange-integration` instead.
See [advanced/README.md](advanced/README.md).

## Evidence and limitations

What exists, and runs on every push: 235 offline worked-example tests, 153 evaluator unit tests, 12
behavioral repair fixtures whose oracles are proved to fail on the planted defect and pass on the
reference fix, 82 lexical routing cases, 198 adversarial installer tests, 20 cited incidents mapped
to the rules they motivate, provider and protocol provenance in the references, and strict
structural and size validation.

The repository does not yet publish a model-based skills-on/off benchmark. CI proves the
repository's deterministic contracts and examples, not a measured uplift in agent performance.

[docs/methodology.md](docs/methodology.md) explains what the rules were sourced from and what is
not measured. [SECURITY.md](SECURITY.md) covers the security policy and how to report an issue.

## Contributing

The useful contributions are corrections:

- an invariant that is wrong, with the case it gets wrong;
- a provider correction, with the primary source;
- a real financial incident, or a minimal reproduction.

[CONTRIBUTING.md](CONTRIBUTING.md) has the checks and the bar a claim has to meet. Security policy
and reporting: [SECURITY.md](SECURITY.md). MIT licensed, see [LICENSE](LICENSE).
