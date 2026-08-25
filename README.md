# Financial Engineering Skills

[![validate](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml/badge.svg)](https://github.com/nbailo/financial-engineering-skills/actions/workflows/validate.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

Six agent skills for code that trades, pays, keeps a ledger, or moves value on-chain. They target one class
of defect: the program runs, every component behaves exactly as specified, and the economic outcome is still
wrong.

Every rule traces to something you can check: vendor API documentation, a protocol specification, source
code read at a pinned commit, or a cited incident. Where a venue's behaviour could not be confirmed, the
reference says so in place instead of stating it flatly. There is no published head-to-head score against a
baseline, and none is claimed here.

## The bug they are built for

Ask an agent for a trading bot and it writes this. It compiles, it reads well, and it passes review:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", type="LIMIT",
                             quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

A timeout says nothing about whether the first request reached the matching engine. The retry can buy twice.

`fin-exchange-integration` treats that response as UNKNOWN rather than as a failure: mint the order identity
from the intent, commit it durably before the send, and resolve the unknown by asking the venue about the
identity you sent, never by sending again. It also checks price and quantity against `tickSize`, `stepSize`,
`minNotional`, `minQty` and `maxQty` together in exact arithmetic, and requires an explicit skip when
quantization would produce `qty == 0`. `examples/trading-bot/` is the full before-and-after review of a
200-line bot carrying this defect and five others.

## Install

These use the `skills` CLI, version 1.5.23 at the time of writing, in that order.

```bash
npx skills add nbailo/financial-engineering-skills#v0.5.0   # install the six into this project
npx skills list                                             # verify what landed
npx skills update                                           # update, all or the ones you name
npx skills remove                                           # remove, chosen from the installed list
```

The `#v0.5.0` fragment is the release tag, and it is the part that matters. Without it the CLI clones this
repository's default branch, so what you install is whatever was pushed most recently, which is not a thing
you can review before it lands or reproduce afterwards. In the CLI's own source, a `#` fragment on a git
source becomes the `ref` passed to `git clone --depth 1 --branch <ref>` (`src/source-parser.ts`,
`src/git.ts`, read at commit `435076e78988e1e6ec40d00b0b1d76bdbbc5419a`, which is what the upstream release
tag for version 1.5.23 resolves to). A tag is still a pointer that whoever controls a repository can move,
so it is a weaker pin than a commit. For the stronger one, use the clone below, which prints the commit it resolved
before anything runs.

The files land in your agent's own skills directory: `.claude/skills/` for Claude Code, `.agents/skills/`
for Codex, Cursor and several others. Add `-g` to install at user level instead of into the project.
`npx skills add` discovers the six skills under `skills/` and nothing under `advanced/`.

`npx skills remove` also takes names, as in `npx skills remove fin-ledger fin-payments`. Its `--all` flag is
documented as every installed skill in every agent directory, not only these six, so name what you mean.

Claude Code users can install it as a plugin instead, which namespaces the skills so they cannot collide
with anything else installed:

```
/plugin marketplace add nbailo/financial-engineering-skills@v0.5.0
/plugin install financial-engineering-skills@financial-engineering-skills
```

The `@v0.5.0` suffix pins the marketplace source to the release tag. Claude Code's plugin marketplace
documentation states that a git-based marketplace source supports `ref`, a branch or tag, and not `sha`, and
that a commit SHA can be pinned only on a plugin source inside `marketplace.json`
(<https://code.claude.com/docs/en/plugin-marketplaces>). Dropping the suffix tracks the default branch. `/plugin list` shows what is installed, and
`/plugin uninstall financial-engineering-skills@financial-engineering-skills` removes it.

**Optional routing reinforcement.** The skills are self-sufficient. What a skill cannot do is guarantee it
gets consulted. If you want routing to be more reliable, there is a small block you can install into the
files every agent reads on every turn: the routing table and one instruction, do not call a financial risk
resolved just because you described it.

This one is a shell script that edits files in your repository, so install it from a commit you have looked
at, not from whatever is on the default branch at the moment you run it.

```bash
# 1. Fetch exactly the released tree. --branch takes the tag, so this is a pin, not a branch.
git clone --depth 1 --branch v0.5.0 https://github.com/nbailo/financial-engineering-skills fes
cd fes

# 2. Verify what you got before running any of it. The first command prints the commit the tag
#    resolves to: compare it with the commit shown on the v0.5.0 release page. The second prints
#    the digest of the one file that is about to execute.
git rev-parse v0.5.0^{commit}
shasum -a 256 scripts/install-guardrails.sh   # or sha256sum, whichever your system has

# 3. Run its own test suite, which is what CI runs, then install into the repository you want.
./scripts/test-install-guardrails.sh
./scripts/install-guardrails.sh /path/to/your/repo
```

Nothing in this repository pipes a download into a shell, and nothing asks you to clone a moving branch
into a shared temporary directory and execute what lands there.

What it writes is a marked block in your `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`:
this repository's `AGENTS.md`, which CI holds under 2,048 bytes, plus a BEGIN marker, an END marker and one
metadata line. `scripts/install-guardrails.sh --uninstall .` removes it again. The skills behave identically
either way.

The specific claims, each covered by a case in `scripts/test-install-guardrails.sh` on both Linux and macOS:
re-running replaces the block rather than appending a second copy; bytes outside the markers and the file's
existing permissions are preserved; a target that is a symlink, a non-regular file, a file with more than one
hard link, or a file whose markers are unbalanced or ambiguous is refused before anything is written;
temporary files are created by `mktemp` in the destination directory and each file is replaced by rename;
a failure or an interrupt part way through restores every file the run had already replaced and deletes what
it had created; uninstall never deletes a file that existed before install; and an install followed by an
uninstall leaves the host file byte-identical, including a file with no final newline and a zero-byte file.

## The six installed skills

| Skill | For | Loads when |
|---|---|---|
| **fin-exchange-integration** | Clients of a venue they do not operate: bots, market makers, execution engines, prediction-market traders, FIX clients | `create_order`, `tickSize`, `clientOrderId`, ccxt, a depth stream, an outcome token |
| **fin-payments** | Processor and rail integrations: Stripe, Adyen, PayPal, Square, ACH/SEPA/RTP | Intents, capture, refunds, disputes, webhooks, payouts |
| **fin-ledger** | Balances, postings, holds, double-entry books | A journal writer, a balance column, a transfer |
| **fin-onchain** | The chain boundary: deposits, withdrawals, indexers, custody, DeFi integration | Nonces, reorgs, `eth_getLogs`, `decimals()`, a signing session |
| **fin-money-core** | Amount arithmetic, value-moving calls, dedupe keys, money-path rollout | Nothing more specific matches, or a cross-domain mechanism a domain skill does not cover |
| **fin-verification** | Tests, reconciliation, and ship decisions | "is this ready", "write tests", "reconcile", "prove it" |

You do not invoke these by name. Each skill carries its own routing description, and the agent loads the
ones that match the code in front of it. To force one, name it: *use fin-onchain to review this withdrawal
flow*.

**How many load.** A domain skill normally wins alone, because it already specialises the generic invariants
onto its own mechanisms. `fin-money-core` loads alongside only for a cross-domain mechanism the domain skill
does not cover, and never merely because a domain-specific retry appears in the diff. `fin-verification`
loads when tests, proof or reconciliation are actually being changed, when the ask is review or readiness,
or when a domain skill demands stronger proof for the mechanism in scope; customer money alone does not load
it. A 200-line Binance bot therefore loads exactly one skill, and never sees double-entry accounting,
chargebacks, or blockchain finality. A crypto exchange backend that credits deposits loads `fin-onchain`
**and** `fin-ledger`, because both mechanisms are present.

## Two advanced skills, opt-in and BETA

Matching against resting orders, pro-rata allocation, auctions, self-trade prevention, price bands and
market-data publication are a different job from calling a venue, and a much rarer one. `fin-matching-engine`
and `fin-market-data-publication` live in `advanced/`. They are **BETA**: newer than the six, outside the
default routing table, and the least exercised material here.

Neither is installed by the commands above, because a description that sits in every agent's skill listing
is paid for by everyone, and their audience is a small fraction of the users of the other six. Install one
deliberately:

```bash
npx skills add nbailo/financial-engineering-skills#v0.5.0 --full-depth --skill fin-matching-engine
```

`advanced/README.md` explains which of the two you want, and the distinction that decides it: these are for
code whose authority is SELF, where nothing outside can tell you that you are wrong.

## Where it applies

**A trading bot.** The most common case, and the one with the shortest path from a small script to real
money. A few hundred lines that send orders to Binance, Bybit, Hyperliquid or ccxt already carry
duplicate-order risk, venue filters that silently quantize a quantity to zero, fill streams that arrive out
of order, and a position number that drifts from the venue's own.

**A prediction-market bot.** Polymarket, Kalshi or Limitless, handled inside `fin-exchange-integration` as
references beside the spot and derivatives venues rather than as a separate product. The general order-book
priors are not merely incomplete here, several are inverted: there is often no short, only the purchase of a
complement; two bids can cross; the fee is a function of expected profit rather than of notional; and the
payout is a number the venue publishes rather than a constant. A settlement credit then arrives through a
path no order of yours created.

**A payment integration.** Stripe, Adyen, PayPal, Square, or an ACH, SEPA or RTP rail. Refund ceilings
computed from your own order table instead of the processor's, webhooks replayed out of order, disputes and
refunds racing on the same charge.

**A ledger or balances service.** Wallet credits, marketplace payouts, internal transfers. A `SELECT` then
`UPDATE` on a balance, a hold that is never released, a correction written as an edit instead of a reversal.

**An on-chain deposit or withdrawal path.** An indexer that credits deposits, a withdrawal signer, a custody
integration. Reorgs after crediting, nonce gaps, token decimals, a broadcast whose outcome is unknown.

**Plain money arithmetic.** Fee splits, invoice totals, currency conversion, proration. Rounding that leaks
value in one direction, a residue with no owner, a float that becomes an obligation.

## Evidence

Four places to check the work. They live in this repository rather than in the installed skill directory,
because an agent should not pay context for them on every turn.

- `docs/providers.md`: which venues have dedicated references, which are covered only generically, and how
  fresh the sourcing behind each one is. Coverage is deliberately uneven, and the table says where.
- `docs/methodology.md`: what the rules were sourced from, what they were selected against, what is not
  measured, the review output contract, and what the suite deliberately does not cover.
- `incidents/`: twenty real, cited incidents mapped to the rules they motivate, each with its own sourcing
  warning where the public record is press reporting rather than a regulator's finding.
- `examples/`: worked money paths, each starting from code a competent engineer would plausibly ship under
  time pressure and ending in the corrected version. Not all of them end `SHIP`, deliberately.

## Repository map

```
skills/                     the six installed skills, each SKILL.md plus references/
advanced/                   the two opt-in BETA skills, not installed by default
AGENTS.md                   the optional routing block (CLAUDE.md is a symlink)
incidents/                  real, cited incidents mapped to the rules they motivate
examples/                   before and after on real money paths
docs/architecture.md        the hierarchy, the budgets, authority and exposure, the output contract
docs/failure-taxonomy.md    the ways a correct-looking system produces a wrong number
docs/providers.md           provider coverage levels and the evidence state of each reference
docs/methodology.md         sourcing, selection, and what is not claimed
CONTRIBUTING.md             how to add a rule, a reference or an incident
SECURITY.md                 how to report an incorrect invariant, provider drift, or a validator bug
scripts/validate.py         spec conformance, budgets, provenance and the reference web
scripts/lint_routing_lexical.py  lints each description against the tasks it must still match
scripts/install-guardrails.sh   idempotent install of the optional routing block
scripts/test-install-guardrails.sh  the hostile suite that installer has to pass, Linux and macOS
evals/routing-cases.yaml    tasks with the skill set each should load, positive and negative
```

## Contributing

The most valuable contribution is an incident: a wrong economic outcome you watched happen, with a citable
source, that motivates a rule an agent could apply. The second is evidence that a rule is unnecessary or
wrong. Rules have been cut both ways. `CONTRIBUTING.md` has the format, the sourcing bar, and what gets
rejected.

Run `python3 scripts/validate.py` and `python3 scripts/lint_routing_lexical.py` before opening a PR. The
validator
enforces the Agent Skills spec and this repository's own budgets, including the per-skill line ceiling and
the shared description budget, because the agent's skill listing is shared with every other suite a user has
installed. The numbers live in the validator rather than in this file, so they cannot drift apart.

MIT licensed.
