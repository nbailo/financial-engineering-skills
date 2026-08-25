# Financial Engineering Skills

**Coding-agent skills for software where a bug has a balance sheet.**

Exchange and broker integrations, payment processors and rails, ledgers and double-entry books, onchain
deposits and withdrawals, and the matching, clearing and settlement systems underneath them.

---

## Install

```bash
npx skills add nbailo/financial-engineering-skills
```

That is the whole installation. The seven skills land in `skills/`, and your agent picks them up from there.

Claude Code users can instead install as a plugin, which namespaces the skills so they cannot collide with
anything else you have:

```
/plugin marketplace add nbailo/financial-engineering-skills
/plugin install financial-engineering-skills@financial-engineering-skills
```

---

## How it works

Install once, then ask for what you want in the ordinary way. You do not invoke these skills by name. Each
one carries its own routing description, and the agent loads the ones that match the code in front of it.

| You ask for | What loads |
|---|---|
| a Binance bot that buys when the 5-minute RSI drops below 30 | `fin-exchange-integration` |
| a Stripe refund handler | `fin-payments` |
| an indexer that credits deposits on Base | `fin-onchain` |
| a matching engine for the venue you operate | `fin-matching-and-settlement` |

If you want a particular skill regardless of what the agent infers, name it in the request:

> use fin-onchain to review this withdrawal flow

---

## Why this exists

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

## What changes in the generated code

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

**New code.**

> Write a Python bot that places a limit buy on Binance when the 5-minute RSI drops below 30.

`fin-exchange-integration` loads, and the agent now:

- commits a client order ID to an intent row before the socket write, carrying the full economic intent, the
  venue plus account plus API-key identity, and `state = INTENT_RECORDED`
- treats a timeout, a 5XX, a 429 and Binance `-1006`/`-1007` as `UNKNOWN`, and resolves by querying that client
  order ID instead of resubmitting
- validates price and quantity against `tickSize`, `stepSize`, `minNotional`, `minQty` and `maxQty` together in
  `Decimal`, and returns an explicit skip signal when quantization would produce `qty == 0`
- emits `test_timeout_that_already_filled` and the filter property test as code in the same response

**Existing code.**

> Review this refund handler for financial correctness.

`fin-payments` loads. The agent computes the ceiling as
`captured_amount - already_refunded - pending_refunds - disputed_amount` from the processor's numbers rather
than from your `orders.amount_cents`, refuses to refund while any dispute on that charge is open or another
refund on it is `pending`, and reverses the principal only, because Stripe does not return the processing fee.

**The output slot.** A response that touched a money path ends with a `FINANCIAL CHECK` block:

```
FINANCIAL CHECK
tier:       T<n>, and the signal that placed it there
effect:     what moves value, from whom to whom, in what unit
identity:   the stable identity of the intent, durably recorded at file:line
ambiguity:  which counterparty responses are UNKNOWN, and how they resolve
authority:  whose copy of each quantity is the record
recovery:   what a crash or restart between the effect and the local commit does
controls:   <control> -> <file:line>, one per line; at T2 and above also `· <test name>`
            UNRESOLVED: <control> (<why>), for anything not implemented
```

Seven labels, about ten lines for a routine change. `tier:` comes first because it decides what else is owed. `controls:` is where the evidence lives: every control named
is either a real `file:line` or an explicit `UNRESOLVED:` line. A control with no location is a defect, because
the most common failure in generated money code is naming the correct control accurately and then writing a
comment instead of implementing it.

Higher-risk work adds to this block rather than replacing it. Once someone else's money is at stake, the
response also carries the domain contract block for the skill in play, whose rows carry the same evidence: the
control, the `file:line` that implements it, and the test that proves it.

---

## The skills

| Skill | For | Loads when |
|---|---|---|
| **fin-money-core** | Anything touching an amount, a value-moving call, a dedupe key, or a money-path rollout | Nothing more specific matches, or alongside one for arithmetic, retries and rollout |
| **fin-exchange-integration** | Clients of a venue they don't operate: bots, execution engines, broker OMS, FIX clients | `create_order`, `tickSize`, `clientOrderId`, ccxt, a depth stream |
| **fin-matching-and-settlement** | Engineers who **are** the venue: matching, allocation, market data, clearing, liquidation | Matching across resting orders, publishing a feed, settling |
| **fin-payments** | Processor and rail integrations: Stripe, Adyen, PayPal, Square, ACH/SEPA/RTP | Intents, capture, refunds, disputes, webhooks, payouts |
| **fin-ledger** | Balances, postings, holds, double-entry books | A journal writer, a balance column, a transfer |
| **fin-onchain** | The chain boundary: deposits, withdrawals, indexers, custody, DeFi integration | Nonces, reorgs, `eth_getLogs`, `decimals()`, a signing session |
| **fin-verification** | Anyone about to ship, or needing tests, reconciliation, or proof | "is this ready", "write tests", "reconcile", "prove it" |

Skills compose. An exchange backend loads **fin-matching-and-settlement** *and* **fin-ledger**. A 200-line
Binance bot loads exactly one, and never sees double-entry accounting, chargebacks, or blockchain finality.

---

## Optional routing guardrails

The skills are self-sufficient. Each one carries its own rules, its own evidence and its own output contract,
and needs nothing else installed to do its job.

What a skill cannot do is guarantee it gets consulted. If you want that routing to be more reliable, there is a
small block you can install into the files every agent reads on every turn. It holds the routing table above
and one instruction: do not call a financial risk resolved just because you described it.

```bash
git clone https://github.com/nbailo/financial-engineering-skills /tmp/fes \
  && /tmp/fes/scripts/install-guardrails.sh .
```

It writes a marked block into your `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`. It is under
2KB, it is idempotent, and it preserves your existing content.

```bash
scripts/install-guardrails.sh --uninstall .
```

Uninstalling restores your content. For a file ending in a single newline, which is what an editor writes, the
round trip is byte-identical and CI asserts it; a file with trailing blank lines or no final newline comes back
with that whitespace normalised. It costs you routing reliability and nothing else. The skills themselves behave
identically either way.

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
  never which rules apply. A 300-line bot is asked for five tests and a daily comparison, two of which the
  exchange skill hands you as code. It is not asked for deterministic simulation.

---

## Repository

```
skills/                the seven skills, each SKILL.md + references/
AGENTS.md              the optional routing block (CLAUDE.md is a symlink)
incidents/             catalogue of real, cited incidents mapped to the rules they motivate
examples/              before and after on four money paths, with the rules that caught each defect
docs/
  architecture.md      the taxonomy, routing, risk tiers and principles, with reasoning
  rules.md             the canonical rule spine: every rule, its owner, its evidence
  failure-taxonomy.md  the thirteen ways a correct-looking system produces a wrong number
  adoption.md          rolling the suite into an existing codebase
  scope-adjudication.md  four domains that asked for their own skill, and the rulings
scripts/
  validate.py          spec conformance and budget enforcement
  install-guardrails.sh  idempotent install of the routing block
```

Run `python3 scripts/validate.py` before opening a PR. It enforces the Agent Skills spec (frontmatter keys,
name rules, description limits) and this repo's own budgets: 500 lines per SKILL.md, 2KB for `AGENTS.md`,
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
