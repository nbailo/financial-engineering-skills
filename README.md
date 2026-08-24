# Financial Engineering Skills

**Coding-agent skills for software where a bug has a balance sheet.**

Security asks: *can an attacker make this system do something unauthorised?*

This asks a different question:

> **Can the system produce an incorrect economic outcome while every component behaves exactly as specified?**

Nobody attacked Knight Capital. A repurposed flag, dead code left in the binary, and a deploy that reached
seven of eight servers cost $460M in 45 minutes. Nobody attacked Citigroup when it credited a customer account
with $81 trillion. The path had no ceiling. Nobody attacked Revolut when two payment systems
disagreed about what "declined" meant for months, at a cost of roughly $20M, until a partner bank's
cash-position report noticed.

Every one of those passes a security review. That gap is what this suite is for.

---

## Install

Two commands, and **you need both**.

```bash
# 1. The skills: deep, domain-specific guidance, loaded on demand
npx skills add nbailo/financial-engineering-skills

# 2. The guardrails: always-on rules, in context every turn
git clone https://github.com/nbailo/financial-engineering-skills /tmp/fes \
  && /tmp/fes/scripts/install-guardrails.sh .
```

**Why both.** A skill is consulted only when the agent decides it needs help; always-on guardrails do not
depend on that decision. A correctness suite faces the adversarial version of that problem: every agent
believes it can already write `exchange.create_order(...)`, so it never reaches for the skill.

Step 2 writes a marked block into your `AGENTS.md`, `CLAUDE.md`, and `.github/copilot-instructions.md`. It is
idempotent, it preserves your existing content, and `--uninstall` removes it byte-for-byte.

Claude Code users can instead install as a plugin, which namespaces the skills so they cannot collide with
anything else you have:

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

---


---

## What changes

Ask any agent to write a trading bot and it will produce something like this:

```python
try:
    order = client.new_order(symbol="BTCUSDT", side="BUY", type="LIMIT",
                             quantity=qty, price=price)
except requests.Timeout:
    order = client.new_order(...)   # retry
```

That is a duplicate order. The first request may well have reached the matching engine.

With the suite installed, the same request produces code that mints and **commits** a client order ID before
the socket write, treats the timeout as `UNKNOWN` rather than failure, and resolves it by querying that ID.
It also knows that on Binance, OKX and Kraken the ID is unique only among *open* orders, so resending after a
fill creates a second order, while Coinbase Advanced Trade is the one venue in the set where re-POSTing returns
the original.

That last distinction is not in any single vendor's documentation.

---

## The skills

| Skill | For | Loads when |
|---|---|---|
| **fin-money-core** | Anything touching an amount, a value-moving call, a dedupe key, or a money-path rollout | Nothing more specific matches |
| **fin-exchange-integration** | Clients of a venue they don't operate: bots, execution engines, broker OMS, FIX clients | `create_order`, `tickSize`, `clientOrderId`, ccxt, a depth stream |
| **fin-matching-and-settlement** | Engineers who **are** the venue: matching, allocation, market data, clearing, liquidation | Matching across resting orders, publishing a feed, settling |
| **fin-payments** | Processor and rail integrations: Stripe, Adyen, PayPal, Square, ACH/SEPA/RTP | Intents, capture, refunds, disputes, webhooks, payouts |
| **fin-ledger** | Balances, postings, holds, double-entry books | A journal writer, a balance column, a transfer |
| **fin-onchain** | The chain boundary: deposits, withdrawals, indexers, custody, DeFi integration | Nonces, reorgs, `eth_getLogs`, `decimals()`, a signing session |
| **fin-verification** | Anyone about to ship, or needing tests, reconciliation, or proof | "is this ready", "write tests", "reconcile", "prove it" |

Skills compose. An exchange backend loads **fin-matching-and-settlement** *and* **fin-ledger**. A 200-line
Binance bot loads exactly one, and never sees double-entry accounting, chargebacks, or blockchain finality.

---

## How this was built

Every rule traces to something you can check yourself.

**Primary sources, not blog posts.** Exchange and processor API documentation read directly from the vendors.
Protocol specifications: FIX, Nasdaq OUCH, CME MDP, ISO 20022. TigerBeetle, PostgreSQL isolation semantics,
Jepsen. SEC, FCA and CFTC enforcement documents read in full, which is how we know the CFTC-SEC flash-crash
report names no firm ("Waddell" appears nowhere in it) and contains no "$1 trillion" figure.

**Real source code, not documentation.** ccxt, freqtrade, hummingbot, nautilus_trader, TigerBeetle and the
Stripe SDKs were cloned and read. That is how we know ccxt's retry funnel has no HTTP-method discrimination, so
a `POST` create-order is retried on the same terms as a `GET` ticker, under the identical client order ID.

**A deliberately small rule set.** Guidance a competent engineer already applies was left out on purpose.

**Adversarial review.** Rules were attacked, not admired, and an obvious-sounding one did not survive.
"Terminal states must be absorbing" is **wrong**: nautilus_trader deliberately ships
`(Canceled, Filled) => Filled`, annotated `// Real world possibility`, because a fill and a cancel
acknowledgement cross on the wire and the fill is real money.

---

## What it deliberately does not do

- **It is not a security tool.** It will not find injection, broken auth, or leaked secrets. Use a security
  reviewer for that; the two disciplines are complementary and neither substitutes.
- **It is not a smart-contract auditor.** `fin-onchain` covers integration correctness: nonces, finality,
  reorgs, indexing, token semantics. Reentrancy and access control belong to a contract-audit tool.
- **It does not ban floating point.** Float is the correct type for greeks, implied vol, Monte Carlo and
  backtest statistics. The rule is about *obligations*, not about finance, and a blanket ban would be wrong.
- **It does not demand institutional ceremony from small projects.** Risk tiers gate the required *evidence*,
  never which rules apply. A 300-line bot is asked for two specific tests, not deterministic simulation.

---

## Repository

```
AGENTS.md              always-on guardrails (CLAUDE.md is a symlink)
skills/                the seven skills, each SKILL.md + references/
incidents/             catalogue of real, cited incidents mapped to the rules they motivate
docs/
  architecture.md      the taxonomy, routing, risk tiers and principles, with reasoning
  rules.md             the canonical rule spine: every rule, its owner, its evidence
  scope-adjudication.md  four domains that asked for their own skill, and the rulings
scripts/
  validate.py          spec conformance and budget enforcement
  install-guardrails.sh  idempotent always-on install
```

Run `python3 scripts/validate.py` before opening a PR. It enforces the Agent Skills spec (frontmatter keys,
name rules, description limits) and this repo's own budgets: 500 lines per SKILL.md, 8KB for `AGENTS.md`,
3,000 characters of description across the whole suite, because the skill listing budget is shared with every
other suite a user has installed.

---

## Contributing

The most valuable contribution is **an incident**. If you have operated financial infrastructure and watched
something produce a wrong economic outcome, the catalogue entry format is in `incidents/README.md`. Entries
need a citable source and must motivate a rule an agent could apply. A story that changes no code does not
belong.

The second most valuable is **evidence that a rule is unnecessary or wrong**. If you can show that an agent
already gets something right without being told, or that a rule is false as written, that rule should be cut
or rewritten. Candidate rules have died both ways.

MIT licensed.
