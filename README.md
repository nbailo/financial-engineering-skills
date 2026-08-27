# Financial Engineering Skills

[![validate](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Financial correctness skills for coding agents.

Catch bugs in trading, payments, ledgers, on-chain systems, and prediction markets that compile
cleanly but produce the wrong position, balance, payout, or settlement.

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
| Ambiguous order submission | A timeout is read as a failure, the retry lands, and one intent becomes two live orders |
| Venue constraints | Price and size are rounded separately, or quantized to zero, and the order fills at a size nobody chose |
| Refunds and disputes | A refund is issued against headroom a dispute or in-flight refund already took, and the customer is paid twice |
| Concurrent balance updates | Two transfers read the same balance, both write, and the difference is gone with no error |
| On-chain credits | A deposit is credited from a notification's amount field, before finality, or without the token's real transfer semantics |
| Prediction-market settlement | A redelivered settlement double-credits, a split payout reads as a winner, or a provisional determination is spent |
| Reconciliation | A break is detected, then posted away to suspense, so the books balance and the signal is gone |

The one most agents write by default:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

The timeout says nothing about whether the first request reached the matching engine. The retry can
buy twice. `fin-exchange-integration` treats a lost response as UNKNOWN: commit the order identity
before the send, then resolve it by asking the venue about that identity, never by sending again.

## How to use it

Copy one of these after installing:

```text
Review this trading bot for duplicate orders, unknown outcomes, venue filters, and position drift.

Review this Stripe refund flow for combined refund and dispute exposure.

Check this ledger transfer for lost updates, incorrect holds, and unsafe corrections.

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

**Trading and exchange clients.** A few hundred lines against Binance, Bybit, Hyperliquid or ccxt
already carry duplicate-order risk, filters that quantize a size to zero, fills that arrive out of
order, and a position that drifts from the venue's own.

**Prediction markets.** Polymarket, Kalshi and Limitless, with the arithmetic binary contracts need:
a fee on notional and the same rate on payout are different trades, and complete-set collateral is
the maximum liability across resolution states, not the largest entry in a payout vector.

**Payments.** Stripe, Adyen, PayPal and bank rails, where a redelivered webhook, a refund ignoring
an open claim, or a reversal assuming the fee came back moves real money.

**Ledgers.** Double-entry books, holds and available-versus-posted balances, where a correction
written as an edit destroys the audit trail.

**On-chain.** Nonce ownership, reorg handling and credited value, where a notification's amount is
not what the protocol actually moved.

Coverage is deliberately uneven. [docs/providers.md](docs/providers.md) lists which venues have
dedicated references, and how fresh each one's sourcing is.

## Run the example

Cloning here is for running the example, not for installing the skills.

```bash
git clone https://github.com/nbailo/financial-engineering-skills
cd financial-engineering-skills
python3 examples/prediction-market-bot/demo.py
python3 examples/prediction-market-bot/run_tests.py
```

Two bots read the same frozen event log. The unsafe one books a redelivered settlement twice, reads
a split payout as a winner and credits nothing, charges fees in the wrong asset, and never reserves
for concurrent resting orders. It raises no exception in any of them; it just ends up with the wrong
balance. The corrected bot prevents or detects each one.

All 93 tests run offline, with no credentials and no live venue.

## Advanced skills

Two more skills live in `advanced/`, **not** installed by default. `fin-matching-engine` is for
teams operating the venue that creates executions; `fin-market-data-publication` is for teams
originating a canonical feed. Both are opt-in BETA. Clients of someone else's venue, and consumers
of someone else's feed, want `fin-exchange-integration` instead.
See [advanced/README.md](advanced/README.md).

## Evidence and limitations

What exists, and runs on every push: 93 offline worked-example tests, 82 lexical routing cases, 198
adversarial installer tests, 20 cited incidents mapped to the rules they motivate, provider and
protocol provenance in the references, and strict structural and size validation.

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
