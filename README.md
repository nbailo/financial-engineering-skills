# Financial Engineering Skills

[![validate](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Financial correctness skills for coding agents.

Six skills for code that trades, pays, keeps a ledger, or moves value on-chain. They target one
class of defect: the program runs, every component behaves exactly as specified, and the economic
outcome is still wrong.

## The defect they are built for

Ask an agent for a trading bot and it writes this. It compiles, it reads well, and it passes review:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", type="LIMIT",
                             quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

A timeout says nothing about whether the first request reached the matching engine. The retry can
buy twice.

`fin-exchange-integration` treats a lost response as UNKNOWN rather than as a failure: mint the
order identity from the intent, commit it durably before the send, and resolve the unknown by
asking the venue about the identity you sent, never by sending again.

## See it in 30 seconds

No install, no credentials, no network. Two bots read the same frozen event log:

```bash
git clone https://github.com/nbailo/financial-engineering-skills
cd financial-engineering-skills
python3 examples/prediction-market-bot/demo.py
```

```
Scenario A: a reconnect replays the settlement, then the market resolves YES
                                              safe            unsafe
  FUSD available                       1051.000000       1118.879000
  FUSD held for resting orders            0.000000      not modelled
  fees paid                        1.113000 FPOINT     2.121000 FUSD
  payout credited                        70.000000        140.000000
  YES position                                  70                70
  difference in FUSD                                       67.879000
```

The unsafe bot books the payout twice, because a reconnect redelivered the settlement and nothing
made the credit idempotent. It never raises. In a second scenario, where the market resolves half
and half, it credits nothing at all: it reads a winning index that a split resolution does not
have. It charges its fees in the wrong asset too, and never reserves for them.

```bash
python3 examples/prediction-market-bot/run_tests.py    # 93 tests, offline
```

## Install

There is no supported public release yet, so pin a commit you have read:

```bash
npx skills add nbailo/financial-engineering-skills#<commit>
```

The skills land in your agent's own directory: `.claude/skills/` for Claude Code, `.agents/skills/`
for Codex, Cursor and several others. Claude Code users can install the plugin instead with
`/plugin marketplace add nbailo/financial-engineering-skills`.

[INSTALL.md](INSTALL.md) has the other paths, the verification steps, and the optional routing block
that edits files in your repository.

## The six skills

| Skill | Use it when the code |
| --- | --- |
| `fin-money-core` | does money arithmetic, retries an operation that moves value, or rolls one out behind a flag |
| `fin-exchange-integration` | sends orders to a venue it does not operate, or derives fills, positions or PnL from one |
| `fin-payments` | integrates a processor: capture, refunds, disputes, webhooks, payouts |
| `fin-ledger` | keeps balances, postings, holds, or double-entry books |
| `fin-onchain` | signs, broadcasts, indexes, or credits value on a chain |
| `fin-verification` | needs tests, reconciliation, or proof before shipping |

A skill loads when the mechanism it owns appears in the change, and opens a reference only when that
reference's mechanism appears. A change confined to one domain should normally load that one
domain skill; cross-domain work, and anything about tests, reconciliation or a ship decision, can
reasonably load more. Nothing here measures how often that actually happens.

## Where it applies

**Trading bots.** A few hundred lines against Binance, Bybit or Hyperliquid already carry
duplicate-order risk, venue filters that quantize a quantity to zero, fills that arrive out of
order, and a position that drifts from the venue's own.

**Prediction markets.** Part of `fin-exchange-integration`, not a separate skill: Polymarket CLOB
V2, Kalshi, Limitless and Hyperliquid outcome markets, with the arithmetic binary contracts need.
A fee quoted on notional and the same rate quoted on payout are different trades, and the
break-even prices differ by a factor of the price.

**Payments.** Capture, refund and dispute paths, where a redelivered webhook or a reversal that
assumes the fee came back moves real money.

**Ledgers.** Balances, holds and double-entry books, where a correction written as an edit rather
than a reversal destroys the audit trail, and an unexplained break posted to suspense hides itself.

**On-chain.** Nonces, reorgs, token semantics and crediting deposits, where a notification's amount
field is not the credited value.

## Evidence, and what is not proven

Every rule is meant to trace to something you can check: vendor documentation, a protocol
specification, source read at a pinned commit, or a cited incident. That is the standard, not a
guarantee about every sentence. Where a venue's behaviour could not be confirmed, the reference
marks the claim unverified at the point it is used and its provenance block records what was and
was not read. Material marked that way is **non-normative**: it is context for a human to check,
never a rule for an agent to apply.

What actually runs, on every push:

| Check | What it proves |
| --- | --- |
| example tests | the worked prediction-market bot behaves as described, offline |
| installer tests | the routing-block installer does what [SECURITY.md](SECURITY.md) says, on Linux and macOS |
| routing lint cases | a description has not lost the vocabulary of the tasks it owns |

What is **not** proven, stated plainly:

- **No model-based effectiveness benchmark, and no skills-on/off baseline.** Nothing here measures
  whether these skills change what an agent produces. There is no such harness in this repository
  and no published result. Treat any claim that these skills improve an agent as unproven. A proper
  runtime evaluation is deferred to a separate future change.
- **The routing lint is a lint, not a routing measurement.** It scores word overlap between a task
  and eight descriptions. No model runs, nothing observes an agent choosing a skill, and 124
  over-activations are recorded in the fixture and not charged. A green result says a description
  still carries the vocabulary of its cases. It is not an accuracy figure and implies none.
- **Coverage is uneven.** [docs/providers.md](docs/providers.md) says which venues have dedicated
  references and how fresh the sourcing is behind each.
- **Some provider claims are unverified and say so.** A reference that could not be re-read against
  its primary source carries `verified_at: not established` rather than a date that would imply
  someone checked.

## Two advanced skills, opt-in and BETA

`fin-matching-engine` and `fin-market-data-publication` live in `advanced/` and are **not** installed
by default. They are for teams operating the authority itself: the venue that owns the order book
and creates the executions, or the originator of a canonical market-data feed. If you are
integrating with someone else's venue, or consuming someone else's feed, you want
`fin-exchange-integration` instead. See [advanced/README.md](advanced/README.md).

## Contributing, security, license

Corrections are the useful contribution, especially a rule that is wrong or out of date. Bring the
primary source. [CONTRIBUTING.md](CONTRIBUTING.md) has the checks and the bar a claim has to meet.

Security policy and reporting: [SECURITY.md](SECURITY.md).

MIT. See [LICENSE](LICENSE).
