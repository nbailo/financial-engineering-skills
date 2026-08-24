# Financial Engineering Skills — v1 Architecture

**Status:** decided. This document is the specification the skills are drafted against.
**Date:** 2026-08-24.
**Evidence base:** vendor and protocol documentation, regulator findings, published source code, and named, dated incidents. Every rule cited below traces to a primary source or to one of those incidents.

---

## 1. Research synthesis: what "financial engineering" is, and the gap it occupies

### 1.1 The discipline

**Financial correctness is the property that a system cannot produce an incorrect economic outcome, including when every component behaves exactly as specified.**

Security asks *can an attacker make the system do something unauthorised?* Financial correctness asks *can the system, running normally, on well-formed input, from an authorised caller, move the wrong amount, move it twice, move it to the wrong place, credit value it does not hold, or report a number that is not true?*

Every incident in the two incident briefs has the same shape: no attacker was required for the mechanism to exist. Knight Capital's $460M loss needed no adversary — a repurposed flag, dead code, and a deployment that reached seven of eight servers. Citi's $81 trillion credit needed no adversary — the absence of any plausibility ceiling on an operator-entered amount. Revolut's ~$20M loss needed no adversary — two payment systems whose message semantics disagreed about what "declined" meant, with no invariant tying them together, detected months later by a partner bank's cash-position report. Bitcoin's 2010 value overflow needed no adversary until someone noticed that `sum(outputs)` wrapped `uint64` *before* the `outputs <= inputs` check ran.

The unifying mechanism across all of them is that **the system's model of the money diverged from the money**, and nothing in the system was structurally required to notice.

### 1.2 The eight failure mechanisms that recur across every domain

Across the incident record, a small set of mechanisms recurs identically in trading, payments, ledger, and on-chain code. These are the core:

| # | Mechanism | Cross-domain instances |
|---|---|---|
| 1 | **Indeterminate outcome read as non-occurrence** | Binance documents 5XX/-1006/-1007 as "execution status UNKNOWN"; Stripe caches 500s and says the client cannot resolve it alone; geth returns `already known`; TigerBeetle's timeout is not a failure; Robinhood's 166,000 pending orders |
| 2 | **Provisional value made spendable** | FTX `fiat@ftx` (~$8bn manually credited while cash sat in Alameda's bank); Kraken's spendable credit before deposit finality (2024-06); Gate.io/ETC; `credits_pending` |
| 3 | **Representation: float, scale, or unit** | Citi/CGML quantity-vs-notional field; Samsung "ghost shares" (shares issued where won were intended); `1e18` vs USDC's 6 decimals; JPY exponent 0, KWD exponent 3 |
| 4 | **Rounding direction and remainder** | Balancer `mulDown` on both legs; ERC-4626 first-depositor; per-line VAT; CME pro-rata that cannot allocate everything |
| 5 | **Sentinel escaping its domain** | Nomad `trustedRoot = 0x00 == "unproven"` → 1,175 withdrawals; Goldman's $1.00 placeholder axe; Robinhood's zero mark; Citi's fifteen pre-populated zeros |
| 6 | **Missing conservation across a transformation** | Knight: 212 parent orders in, millions of child orders out; Bitcoin `uint64` wrap; Samsung ghost shares; Euler `donateToReserves` — the one balance-mutating path that skipped the health check |
| 7 | **Concurrency on an authoritative quantity** | Read-modify-write on a balance under Read Committed; write skew across two rows guarded by one invariant; NASDAQ's Facebook IPO cross recomputing over a book that concurrent cancels kept mutating |
| 8 | **Change, deploy, and config discipline** | Knight 7-of-8 hosts + repurposed flag + dead code; RBS uninstalling CA-7 without testing the uninstall; TSB's two data centres "configured inconsistently despite having been specified to be identical"; Goldman testing A–H and L–Z while the misconfigured stripe was I–K |

Everything whose vocabulary does not survive translation into all four domains is **domain-bound**: double-entry and bitemporality are ledger-only; chargeback windows and capture deadlines are payments-only; tick filters and order state machines are exchange-client-only; matching allocation and market-data publication are venue-only; reorgs, decimals and allowances are on-chain-only.

### 1.3 The gap

This discipline has no home in any adjacent body of guidance:

| Adjacent field | What it covers | What it does not |
|---|---|---|
| **Secure coding / OWASP / SAST** | Authorisation, injection, secrets, supply chain. Asks "can an attacker force an unauthorised action?" | Cannot see a correctly-authorised, correctly-authenticated transfer of the wrong amount. Knight, Citi/$81T, Revolut, TSB are all clean by every security checklist ever written. |
| **Distributed-systems literature** | Consistency models, consensus, exactly-once semantics, isolation levels. Correct and load-bearing. | Stops at "the message was delivered once". It does not say that a Binance `-2013 NO_SUCH_ORDER` immediately after placement is not proof the order does not exist, or that a Stripe idempotency key does not survive a 429, a 401, most 400s, retention expiry, or cross-region failover. The general theorem does not produce the diff. |
| **Quantitative finance** | Pricing, risk, optimisation, backtesting. Correctly uses binary floating point. | Concerns model error, not execution error. Its numbers are estimates; the moment an estimate becomes an obligation the discipline changes and quant literature is silent on the transition. |
| **Exchange / processor API docs** | Authoritative, mechanism-level, and per-venue. | Nine documents, mutually contradictory, none of which tells you that the other eight disagree. Binance Spot and Binance USDⓈ-M Futures have *different* order-book sync algorithms. Coinbase Advanced Trade has real create-order idempotency; Binance, OKX and Kraken only guard against collisions among *open* orders; Deribit has no client order ID at all. There is no document that states the divergence, and the divergence is where multi-venue code breaks. |
| **Smart-contract security** | Reentrancy, access control, oracle manipulation, Solidity-specific vulnerability classes. Excellent within scope. | Scoped to the contract. It does not cover the off-chain integrator: nonce management, fee bumping, reorg-safe indexing, when to credit a deposit, `eth_getLogs` never setting `removed: true`, or the fact that a transaction's identity is `(chainId, from, nonce)` and never the hash. |
| **Accounting theory** | Double-entry, chart of accounts, period close. Unchanged since Pacioli and still correct. | Says nothing about isolation levels, materialised-balance drift, hot-account contention, idempotent posting, or what happens when a fill reported as final is busted three hours later. |

**Financial engineering is the discipline that sits in the middle of all six.** It is mechanism-level, it is falsifiable, it names real API error codes and real protocol fields, and its unit of work is a diff.

### 1.4 The two constraints that dominate the architecture

Two facts about how guidance actually reaches the agent shape everything below more than any taxonomy argument.

1. **The default failure of a skill is that it is never loaded.** A skill sits behind a discretionary load. Before one rule is in front of the agent, the agent must decide it needs help, decide which skill supplies it, and decide to read it. Any of those can go the other way, and when one does the skill delivers nothing — not a degraded answer, no answer, with no signal that it happened. Content carried with no decision point in front of it has none of those failure modes.
2. **A correctness suite faces the adversarial version of that failure.** An agent reaches for a skill when it judges the task to exceed what it can do alone. Every agent believes it can already write `exchange.create_order(...)`, `stripe.Refund.create(...)` and `balance -= amount`. What this suite carries is not knowledge the model knows it is missing; it is behaviour the model is confident about and gets wrong. The discretionary-load mechanism is weakest exactly where this suite needs it to be strongest.

Therefore the architecture is **two layers**: an always-on guardrails block that carries the gate, the tier signals and the non-negotiable invariants, with no decision point in front of it; and seven skills that carry the mechanism-level, API-named procedures, discovered by mutually exclusive frontmatter descriptions.

---

## 2. Final skill taxonomy

Seven skills. Frontmatter is `name`, `description`, `license` only — nothing else, ever (any other key hard-fails `package_skill.py`, claude.ai upload, and the Skills API).

Total description budget: **≤3,000 characters across all seven**, each ≤430 characters, enforced in CI. The listing budget is 1% of the context window (Claude Code) or min(2%, 8,000 chars) (Codex), it is shared with every other suite the user has installed, and overflow drops descriptions **starting with the least-invoked skills** — which is always a newly installed suite.

---

### 2.1 `fin-money-core` — 240 lines

**Target user.** Anyone whose diff touches an amount, a value-moving call, a dedupe key, an isolation boundary, or a rollout of any of those — with no domain more specific in the diff. Also the fallback when the gate fires and no domain row matches.

**Description (≤430 chars).**
> Use when a change touches a monetary or asset amount, a call that moves value or creates an obligation, the retry/timeout/error handling around one, a deduplication key, a rounding or unit conversion, or the isolation level guarding a balance. Covers exact representation, rounding, idempotency, indeterminacy, concurrency, conservation, and money-path deploy safety. Defer to fin-exchange-integration, fin-matching-and-settlement, fin-payments, fin-ledger or fin-onchain when the diff names their APIs.

**Trigger conditions.** The economic-diff gate fired and no dispatch row matched; or the diff is pure amount arithmetic, pure partial-failure handling, or a refactor/rollout of a money path with no venue, processor, ledger schema or chain in it.

**Responsibilities (owns).**
- The economic-diff gate procedure (mirrored verbatim from the always-on block into the first 40 lines).
- The risk-tier signal table and the required one-line tier declaration.
- Amount representation: binary-float ban for obligations and the explicit legitimacy of float for risk analytics; minor units; explicit scale resolved from runtime metadata; currency/asset carried inside the value; the four different scales (charge / payout / display / calculation); integer-width proof; checked arithmetic *before* the comparison, never after.
- Rounding: mode named at every call site; no truncation as a substitute for rounding; direction per operation as a function of who is credited; largest-remainder allocation with the residue posted to a **named** account; boundary tests at `threshold-1 / threshold / threshold+1`.
- Sentinels and absent values: no legal value doubles as "unset"; a missing price propagates as absent, never 0 or last-known; signed domains (a price is not non-negative — see IBKR/CME, 2020-04-20).
- Indeterminacy: timeout / 5XX / dropped connection ⇒ UNKNOWN, never "did not happen"; the reconcile-before-retry shape.
- Idempotency mechanics: key minted and durably written **pre-effect**, bound to the request body, replaying the stored response, with a TTL longer than the replay horizon; concurrent-duplicate handling; content-derived or caller-supplied identity, never a tuple of business attributes and never a positional index.
- Concurrency: lost update, write skew, explicit isolation level selection, retry-on-40001 that **re-reads**, no blind transaction retry replaying stale reads.
- Persistence ordering: persist intent → effect → outcome; resolve unresolved intents on startup; no dual writes; outbox; at-least-once consumers dedupe on business identity; no wall-clock ordering and no last-write-wins for money.
- Conservation: sum of deltas is zero across every value transformation; one chokepoint assertion that every value-mutating path terminates in; at least one non-overridable aggregate ceiling; no per-entity exemption on a solvency check, and audited limit changes.
- Controls: a limit that warns is not a control; a kill switch must be exercisable faster than the loss accrues; anomaly signals go to a monitored channel with an owner, never to a log line or a mailing list.
- Money-path change safety: never repurpose a flag/enum/field a deployed consumer still reads; delete dead money paths; verify build identity on **every** host before enabling; rollback is a change and needs its own test; assert config parity across regions and shards; per-shard tests, not a representative shard.

**Exclusions.** Any rule that names a concrete API, error code, protocol field, account concept or chain primitive. Double-entry mechanics. Processor lifecycles. Venue filters and order state machines. Matching and allocation. Token semantics and finality. The tier→technique test matrix (that is `fin-verification`).

**Dependencies.** None. Loads alone or alongside at most two domain skills.

**Reference files** (one level deep; each ≥100 lines opens with a table of contents):
- `references/representation.md` — exact-decimal and minor-unit types per language/DB/wire format, scale tables, serialization boundaries. *Read when the diff introduces or changes a numeric type on an amount, or converts scale or decimals.*
- `references/rounding-and-allocation.md` — modes, directed rounding, largest-remainder algorithm, residue accounts, worked cent-splitting. *Read when the diff divides, multiplies by a rate, converts currency, or does percentage math.*
- `references/indeterminacy-and-idempotency.md` — timeout taxonomy, key lifecycle, storage-before-effect ordering, TTL, concurrent duplicates, the resolve-unresolved-intents loop. *Read when the diff calls, retries, or handles the failure of an external value-moving system.*
- `references/concurrency.md` — per-engine isolation semantics, the double-spend SQL shapes, fencing tokens, SERIALIZABLE retry. *Read when the diff reads a value, computes, and writes it back, or changes a transaction or lock.*
- `references/change-and-rollout.md` — flag retirement, dead code, partial deploy, rollback, config parity, shard coverage, migration cutover. *Read when the diff changes a flag, enum, or config, or must reach more than one host, region or shard.*

---

### 2.2 `fin-exchange-integration` — 300 lines

**Target user.** Anyone whose code is a **client of a venue it does not operate**: a 200-line Binance bot, a ccxt script, a multi-venue execution engine, a broker OMS/EMS, a FIX client, an execution algorithm.

**Description (≤430 chars).**
> Use when code sends, cancels, amends, or tracks orders on an exchange or broker it does not operate — ccxt, Binance, Bybit, OKX, Kraken, Coinbase, Deribit, Hyperliquid, Alpaca, IBKR, FIX client — or derives fills, average price, fees, positions, PnL, or an order book from one. Triggers on create_order, tickSize, clientOrderId, a depth stream, or "my trading bot", even for a throwaway script. Skip when the code IS the venue: use fin-matching-and-settlement.

**Trigger conditions.** Any import or call in the dispatch row (§5.3); any order/fill/position/book vocabulary; any retry or timeout around an order endpoint.

**Responsibilities (owns).**
- Instrument filter validation and rounding **toward validity**: tick/step/minNotional/MARKET_LOT_SIZE/PERCENT_PRICE_BY_SIDE/maxQty, and the fact that LIMIT and MARKET orders validate against *different filter sets*.
- Client order ID semantics **per venue**, and the fact that on most venues it is not idempotency: Binance Spot and Futures, OKX and Kraken enforce uniqueness only among *open* orders; Bybit rejects duplicates (rejection ≠ returning the original); Coinbase Advanced Trade is the only venue in the set with true create-order idempotency; Deribit has no client order ID and recovery must go through `get_open_orders_by_label`; Hyperliquid `cloid` is a correlation key with no documented dedup guarantee.
- The ambiguity set: HTTP 5XX, `-1006`, `-1007`, a socket timeout, and a 429 are all UNKNOWN, not failure. `-2013 NO_SUCH_ORDER` immediately after placement is not proof of non-creation (Binance documents three data sources with different staleness). `-2011 CANCEL_REJECTED` is expected in normal operation. Cancel/replace is not atomic: `-2021` means one leg succeeded.
- CCXT's own documented timeout-recovery procedure does **not** use the client order ID and is race-prone; override it with client-order-ID reconciliation where the venue supports it.
- The order state machine as an explicit transition table; terminal states are not absorbing (busts, fill-voids); `leaves_qty` is venue-authoritative and is **not** `order_qty - cum_qty`; `CumQty` is cumulative across the whole replace chain.
- Fills: dedupe on `trade_id` plus field comparison (the same fill arrives on the stream *and* the poll); recompute average price from the fill set, never accumulate; overfills are real and must be recorded, not clamped; commission taken in the base asset changes the position size.
- Order-book sync **per venue** — Binance Spot and Binance Futures use different algorithms and this is the single most-copied incorrect snippet in the ecosystem; a sequence gap means discard and re-snapshot, never patch; OKX's checksum is now always 0; Bybit `u == 1` means service restart; Kraken CRC32 covers exactly the top 10.
- Keepalive and blind position: `listenKey` lapse, reconnect-then-resync, subscribe before the first order.
- Rate-limiter shapes (weight / count / credits / order-age) and reconnect storms.
- Position, PnL, margin, funding, fees derived from venue-authoritative state; `avg_px_open` vs `avg_px_close`; realized quantity capped on a flip; inverse/quanto contracts and contract multipliers; mark vs index vs last for stops, sizing and liquidation distance.
- TIF, post-only reprice, reduce-only, and STP outcomes counted as **non-fills**.
- Execution algorithms: mandatory price bound and time bound; never drive participation rate from a metric your own fills inflate; volume is not liquidity; collar every marketable order; re-check parent liveness each tick.
- Client-side pre-trade limits, and halt-on-ambiguous-reconciliation.
- Startup and periodic reconciliation by client order ID, including adoption of externally-created orders.
- **Required output slot:** before any bot runs against live keys, emit the two tests in §9 step 7.

**Exclusions.** Matching, allocation, priority, auction computation, self-trade prevention *as an implementer* (→ `fin-matching-and-settlement`). Double-entry booking of fills (→ `fin-ledger`, via the exchange↔ledger seam rule). On-chain settlement (→ `fin-onchain`). Fiat rails (→ `fin-payments`). Generic idempotency and decimal theory stated without a venue API (→ `fin-money-core`).

**Dependencies.** None. Pairs with `fin-ledger` at the exchange↔ledger seam; pairs with `fin-verification` at T2+.

**Reference files.**
- `references/venues/binance.md`, `okx-bybit-kraken.md`, `coinbase-deribit-hyperliquid.md` — per-venue filters, error codes, clOrdID scope and charset, book algorithms, contract units. *Read immediately when the code imports or hits that venue.*
- `references/venues/divergence-matrix.md` — **one table**, rows = behaviours that differ across venues, columns = venues. *Read immediately when the repo constructs more than one venue adapter, or defines any venue-agnostic Order / OrderBook / RateLimiter / clOrdID abstraction.*
- `references/ccxt.md` — `precisionMode`, the timeout-recovery override, unified-vs-raw field traps.
- `references/order-state-machine.md` — full (status, event) transition table, in-flight resolution categories, fill dedupe, overfills.
- `references/position-and-pnl.md` — avg price, flips, inverse/quanto, commissions, funding, netting snapshots.
- `references/orderbook-sync.md` — per-venue snapshot/incremental join algorithms and gap recovery.
- `references/execution-algorithms.md` — parent/child accounting, participation feedback, collars, benchmarking.
- `references/fix-client.md` — PossDupFlag vs PossResend, ResendRequest handling, CumQty on replace, iLink3 UUID rules.

---

### 2.3 `fin-matching-and-settlement` — 300 lines

**Target user.** Anyone whose code **is the venue**: a matching engine, order book, allocation algorithm, auction or cross, market-data publisher, sequenced feed, pre-trade risk gateway, clearing or settlement batch, liquidation engine, prediction-market resolution engine.

**Description (≤430 chars).**
> Use when the code IS the venue rather than its client: a matching engine, limit order book, allocation or priority algorithm, auction or cross, self-trade prevention, price bands and halts, a market-data feed you publish, a sequencing or retransmission protocol (ITCH/OUCH/MoldUDP64/FIX server/SBE), a pre-trade risk gate, clearing, settlement, or a liquidation engine. Skip when calling someone else's venue: use fin-exchange-integration.

**Trigger conditions.** A loop matching an incoming order against resting orders; order-book/price-level/allocation structures the repo owns; encoding or publishing a feed; a gap/retransmission protocol; auction or cross computation; halt/resume/LULD; settlement-price computation; a gateway rejecting on a broker-dealer's behalf.

**Responsibilities (owns).**
- **Cancel and amend at the venue** (in the first 60 lines — see §5.5):
  1. A cancel and a concurrent aggressor must be totally ordered by the same sequencer before either mutates the book. The loser emits a **defined** outcome — silent ignore or explicit reject, chosen per protocol and documented — never a pending state the client can block on. (OUCH: "There is no 'too late to cancel' message since by the time you received it, you would already have gotten the execution. Superfluous Cancel Order Messages are silently ignored.")
  2. Pending-cancel is a protocol-model choice, not a free one: FIX makes `PendingCancel` the highest-precedence `OrdStatus`; OUCH has no general pending-cancel and argues it cannot meaningfully exist. Pick one, state which, and adapt explicitly at a cross-protocol gateway.
  3. **Never compute an auction or cross price over a book that concurrent cancels can mutate between compute and print.** Freeze a consistent snapshot; if revalidation fails, re-run over **all** cancellations received during the pass, never one per pass; bound the retry count and fail to a defined state. Disabling the consistency check is not a mitigation. (NASDAQ/Facebook IPO: the recomputation "did not account for those two additional cancellations… only the first"; the loop could not converge; NASDAQ removed the check and printed a 19-minute-stale cross.)
  4. Execution identity — ExecID / Match Number / the resting order's reference — is assigned **inside** the deterministic core and journaled as an input-derived output, so replay reproduces it byte-identically. A cancel that races an execution must not renumber or un-emit a published execution; corrections travel as Trade Cancel / broken-trade referencing the original match number.
  5. Cancel-driven ID consumption is protocol-specific and has silent branches: on OUCH replace, an *invalid replacement kills the existing order* while not consuming the replacement ID; a non-cancellable original consumes the ID unrecoverably. State the venue's consumption rule as a table and assert it in replay. Recover next-ID state from the journal (OUCH exposes `Account Query` for `NextUserRefNum`), never from an in-memory counter.
- Order identity at the protocol layer: monotonic client reference numbers with retransmission-drop semantics; `ClOrdID` is *not* day-unique in OUCH; PossDupFlag (session layer) vs PossResend (application layer); iLink3 identity is (sequence, UUID) and re-negotiating with a new UUID permanently strands unrecovered messages; SequenceReset-Reset is explicitly lossy.
- Replace vs modify: replace always loses time priority; a reducing cancel preserves it; CME loses priority on quantity *increase*, price change, **or account-number change** — an economically invisible edit.
- Quantity conventions and their inversion: OUCH replace quantity is chain-cumulative *including prior executions and STP decrements*; OUCH cancel quantity is an absolute intended total; ITCH `Modify` is a decrement. Same vendor, same concept, inverted.
- An accept is not an echo: accepted price, accepted TIF and order state may differ from what was sent.
- Allocation: pro-rata rounds down, cannot allocate everything, and must never be the last step; the FIFO exception when aggressing quantity exceeds resting; execution at the **resting** order's price; iceberg refresh and requeue.
- Self-trade prevention as an implementer: four incompatible semantics, no standard, no neutral default; counterfactual reporting (`Quantity prevented from trading`); failure to prevent is an enforcement matter that propagates into third-party settlement indices.
- Deterministic core: single writer; journal **inputs**, not outputs; no clock reads, RNG or I/O inside the core; replay plus snapshots; validate before mutate; fencing tokens, epochs and single-writer authority across failover.
- Market-data publication: per-message not per-packet sequencing (MoldUDP64); A/B line arbitration **before** declaring a gap; snapshot/incremental join keys and direction; per-instrument sequence resets; deliberately-constant fields; printable flags and volume double-count; 603(a) timing fairness.
- Pre-trade controls: reject order-by-order on the last hop, under direct and exclusive control — not post-trade alerting; aggregate credit and capital thresholds; kill switches that cannot be cleared without diagnosis.
- Halt and resume: quiesce the engine rather than severing the wire; define the fate of in-flight and undelivered executions **before** resuming.
- Post-match obligations: confirmations that escalate rather than merely withhold; error and suspense positions wired to a control; trade breaks (PnL and positions are revisable, the book is not); settlement-price discretion, with the implemented behaviour treated as the published rulebook.
- Liquidation waterfalls, backstop vaults, ADL, self-referential mark prices, and the rule that a backstop which can be forced to inherit a position is the attack target.

**Exclusions.** Client-side order construction, venue SDKs, ccxt, reconciliation against a venue (→ `fin-exchange-integration`). The customer-balance ledger behind the venue (→ `fin-ledger`). Fiat rails (→ `fin-payments`). On-chain settlement or sequencer finality (→ `fin-onchain`). Generic idempotency and decimal theory (→ `fin-money-core`).

**Dependencies.** None. Pairs with `fin-ledger` and with `fin-verification` (this skill is T3 by definition of Axis B).

**Reference files.**
- `references/cancel-and-amend.md` — the full cancel/replace/modify matrix, ID-consumption branches per protocol, race interleavings.
- `references/matching-and-allocation.md` — CME step pipeline, pro-rata rounding and leftovers, priority preservation, iceberg, price improvement.
- `references/protocol-sessions.md` — OUCH/ITCH/FIX/MoldUDP64/iLink3 identity, sequencing, gap recovery, reset hazards.
- `references/deterministic-core.md` — input journaling, replay, the banned-construct list, snapshot/recovery, failover testing.
- `references/market-data-publication.md` — sequencing, A/B arbitration, fairness obligations, crossed/locked assertions, backpressure.
- `references/market-access-controls.md` — 15c3-5 clause by clause with Knight and Goldman as failure exemplars; bands, halts, error accounts.
- `references/settlement-and-liquidation.md` — confirmations, error positions, trade breaks, waterfalls, ADL, settlement override as an integrity event.

---

### 2.4 `fin-payments` — 290 lines

**Target user.** Anyone integrating a payment processor or bank rail: a single checkout route, a webhook handler, a refund endpoint, a marketplace payout pipeline.

**Description (≤430 chars).**
> Use when code calls a payment processor or bank rail — Stripe, Adyen, PayPal, Braintree, Square, Checkout.com, card, ACH, SEPA, wire, RTP — or handles their webhooks: charges, authorizations, captures, refunds, disputes and chargebacks, payouts, marketplace transfers, idempotency keys sent to a third party, settlement-report reconciliation. Triggers on "checkout", "webhook handler", "add a refund endpoint". Skip internal balance schemas: use fin-ledger.

**Trigger conditions.** Any processor import or SDK call; any lifecycle verb (`charge`, `authorize`, `capture`, `refund`, `dispute`, `payout`, `transfer`); an `Idempotency-Key` header; a provider webhook handler; a settlement or balance-transaction report.

**Responsibilities (owns).** The body is a **verb-indexed MUST table** (§5.4) — one row per lifecycle verb, each carrying its 3–5 MUSTs inline.
- The five distinct features all called "idempotency", and exactly what a key does **not** survive: retention expiry, cross-region failover (Adyen), the rate limiter (429), the auth layer (401), most validation errors (400), and a different processor. 500s **are** cached and mean indeterminate; Stripe states there is no client-side algorithm that resolves it.
- Authority order: the API is current state; the webhook is a **trigger**; the settlement report is the money. The books close on settlement data with a reversal tail.
- Webhooks: verify signature → ack → work; dedupe on event id *and* object id; no ordering guarantee; never trust the payload as current state; never order by signature timestamp; never fulfil on a client redirect.
- Authorize/capture: deadlines read from metadata, never hardcoded (they are scheme- and method-specific); **partial capture destroys the remainder**; incremental authorization takes an absolute total, not a delta.
- **Refunds** (the verb row that carries the most incident weight): `refundable = captured − already_refunded − pending_refunds − disputed_amount`, computed per charge and per currency, enforced in your own code, never from `paymentIntent.amount`; *cancel*, do not refund, a `requires_capture` intent; refuse a refund while a dispute is open or another refund is pending; `refund.created` is **pending** and `refund.failed` can arrive up to 30 days later; send an idempotency key on the refund call itself and generate a **fresh** key after any non-409 4xx; reverse the transfer in the same unit of work on a marketplace refund.
- Disputes, chargebacks and early fraud warnings: double-refund via refund + chargeback; refund exceeding a partial capture; dispute amount may differ from the charge; dispute outcomes are not immutable; EFW refund thresholds are a business-policy input, never a code default.
- Rail reversibility taxonomy and windows: card chargebacks; ACH return codes; SEPA Reject/Return/Recall/RFRO; wires and instant rails as irreversible on send; destination verification before a send.
- Multi-party and marketplace fund flow: reverse the transfer with the refund; never transfer against an unsettled async payment; `transfer_group` is not a functional link; `merchantReference` is not unique — reconcile on the processor's own identifier (`pspReference`) with the merchant reference as a grouping attribute.
- Reconciliation against external statements, joined on the processor's identifier, with an aged break bucket.
- Holds/authorizations live in the payments layer as reserved amounts; only captures, refunds, disputes, fees and settlement adjustments become ledger entries. This is a design decision the skill **surfaces** rather than silently imposes.
- **Seam rule (verbatim in `fin-ledger`):** every payment state transition emits exactly one balanced ledger transaction whose id derives from the payment's idempotency key; every clearing account returns to zero; never derive a balance by scanning payment objects.

**Exclusions.** Double-entry mechanics, chart of accounts, normality, bitemporality, trial balance (→ `fin-ledger`). Order lifecycles (→ `fin-exchange-integration`). Crypto deposits and withdrawals (→ `fin-onchain`). Generic timeout/idempotency theory (→ `fin-money-core`). Test strategy (→ `fin-verification`).

**Dependencies.** None. Pairs with `fin-ledger`.

**Reference files.**
- `references/processors/stripe.md`, `adyen.md`, `others.md` — per-processor idempotency scope/retention/mismatch behaviour, in-flight error codes, state machines, authoritative response fields.
- `references/webhooks.md` — verification, replay window, ack-then-work ordering, dedupe key selection, framework redirect traps.
- `references/rails.md` — ACH R-codes and windows, SEPA taxonomy, card clearing and MIT windows, wire and instant-rail finality.
- `references/disputes-and-refunds.md` — refund/dispute/EFW/reversal state machines, partial-capture refund arithmetic.
- `references/ledger-seam.md` — the exact mapping from each lifecycle transition to balanced entries, including why holds stay out of the ledger.

---

### 2.5 `fin-ledger` — 270 lines

**Target user.** Anyone whose code is the **system of record for balances**: a double-entry ledger, a core-banking or wallet-balance service, an exchange's internal accounting, or "just a balances table".

**Description (≤430 chars).**
> Use when code records, posts, or reads an authoritative balance: double-entry journals, postings, debits and credits, a chart of accounts, an entries or balances table you own, TigerBeetle, Formance; available vs pending vs posted, holds, corrections and reversals, as-of balances, period close, trial balance, clearing accounts, multi-currency, ledger migration. Triggers on "credit their account" or "deduct from their balance". Skip processor lifecycles: use fin-payments.

**Trigger conditions.** Schema or code touching `entries`, `postings`, `journal`, `accounts`, `balances`, `holds`; TigerBeetle/Formance imports; debit/credit identifiers; any function that increments, decrements or overwrites a stored balance; `available`/`pending`/`posted`; `reversal`/`correction`/`period-close`/`trial-balance`; as-of balance queries; **and** `deposit`/`credit`/`confirmations`/`blockHash` (so the on-chain deposit pairing fires from the crypto vocabulary, not only from a table named `balances`).

**Responsibilities (owns).**
- Double-entry invariants: every transaction sums to zero **per currency**; a transfer *is* a debit and a credit; explicit chart of accounts; account normality (customer funds are liabilities, not assets). Signed amounts are safe if and only if the sign convention is global and type-enforced with no redundant direction field — the footgun is redundancy and locality, not signs.
- Balance semantics: posted / pending / available, and which one authorises a spend. Inbound pending is never available.
- Holds and two-phase transfers with **intrinsic expiry** (not callback-driven release), and the invariant checked at reserve time so no committed reservation can be un-postable.
- Immutability and corrections: posted records are append-only; corrections are new balancing entries, never in-place edits. **Reversal links are bidirectional (`reverses_transaction_id`, `reversed_by_transaction_id`) with a uniqueness constraint on `reverses_transaction_id`, so one transaction cannot be reversed twice by two operators or by an operator plus a retry.**
- Bitemporality: `effective_at` vs `created_at`; back-dated entries; the discard window; a historical balance query must be stable — `effective_at <= T AND (discarded_at IS NULL OR discarded_at >= T)`.
- **Materialised-balance drift, in mechanism form in the body:** the balance `UPDATE` must be in the **same transaction** as the entry `INSERT`, carry a monotonic version, and be verified by a separate order-independent checksum recompute that alerts on drift. Never `INSERT INTO entries…; COMMIT;` followed by a separate `UPDATE balances`.
- Hot-account concurrency: explicit isolation, retry on serialization failure that re-reads, and why read-compute-write on a balance column is the canonical double-spend.
- Clearing and suspense accounts must drain to zero, monitored as a continuous assertion.
- Trial balance and three-way reconciliation expressed as production assertions with aged breaks, not as reports.
- Multi-currency: one ledger per currency; no cross-currency arithmetic; FX as a balanced two-account transaction with rate provenance, side and pivot recorded and spread booked separately; never a rate applied in place.
- Negative-balance policy: which accounts may go negative, enforced at reservation time, never at posting time. A per-account override on a solvency invariant is an unbounded liability generator and requires field-level audit logging of every change (who, when, old, new).
- Customer-fund segregation and the system-level solvency invariant `sum(customer balances) <= custodied assets`.
- Displayed figures must read the authoritative ledger, with a test asserting display equals ledger for every monetary quantity.
- Ledger migration: per-account balance preservation, dual-run and shadow comparison, checksum backfill, cohorted cutover with a verified reverse path.
- Control plane vs data plane: reporting queries never on the write path.
- **Seam rules (verbatim in the paired skill):** payments↔ledger; onchain↔ledger; exchange↔ledger (see §5.6).

**Exclusions.** Processor lifecycle, rails, chargeback windows (→ `fin-payments`). Order state and fills (→ `fin-exchange-integration`). Matching, clearing houses and error positions as an operator (→ `fin-matching-and-settlement`). Chain finality and deposit crediting mechanics (→ `fin-onchain`). Generic conservation and idempotency theory (→ `fin-money-core`).

**Dependencies.** None. Pairs with `fin-payments`, `fin-onchain`, or `fin-exchange-integration`.

**Reference files.**
- `references/data-model.md` — entry/transaction/account schemas from TigerBeetle, Modern Treasury, Square Books and Uber, and the tradeoff each makes; normality; tenancy.
- `references/balances-and-holds.md` — posted/pending/available formulas, holds with timeouts, two-phase transfers, reservation-time invariant checks.
- `references/corrections-and-time.md` — reversal vs adjustment vs void, bitemporal queries, as-of reconstruction, the discard window.
- `references/reconciliation-monitors.md` — trial balance, clearing-account-zero monitors, materialisation drift, versioned change-log gap detection, break aging.
- `references/multicurrency-and-fx.md` — per-currency ledgers, FX accounts, rate provenance and side, revaluation, rounding residue accounts.

---

### 2.6 `fin-onchain` — 300 lines

**Target user.** Anyone integrating with a blockchain: submitting or tracking transactions, crediting deposits, indexing events, moving tokens, reading oracles, operating a withdrawal pipeline, integrating a DEX/lending market/bridge/vault.

**Description (≤430 chars).**
> Use when code submits, tracks, indexes, or credits value on a blockchain: transaction identity and nonces, fee bumps, confirmation and finality policy, reorg handling, event indexing and backfills, ERC-20 and token semantics, decimals, approvals and permits, price oracles, bridges and cross-chain messages, slippage and deadlines, deposit crediting and withdrawal queues. Triggers on ethers, viem, web3.py, @solana/web3.js, wagmi. Skip centralized venue APIs: use fin-exchange-integration.

**Trigger conditions.** Any chain library import; `eth_sendRawTransaction`, `eth_getLogs`, `eth_getTransactionReceipt`, `getSignatureStatuses`; nonce management or fee bumps; confirmation counting; a deposit-crediting or withdrawal path; token, allowance or oracle reads.

**Responsibilities (owns).**
- Transaction identity is `(chainId, from, nonce)` on EVM and the signature-over-blockhash on Solana — **never the tx hash**; store every broadcast hash for a nonce as a set.
- Replacement requires a ≥10% bump on **both** 1559 fee fields at the same nonce; `already known` means success; nonce gaps stall everything behind them (`AccountQueue = 64`, `Lifetime = 3h` in geth defaults); a mined-but-reverted transaction consumes the nonce, burns gas and emits no logs — `receipt.status == 0x1` is required before treating any effect as having occurred.
- Solana: rebroadcast the identical signed bytes; only re-sign after `getBlockHeight("confirmed") > lastValidBlockHeight`; query `getSignatureStatuses` with `searchTransactionHistory: true` before concluding non-execution; a non-null `err` is landed-and-failed, not retryable; durable nonces have an asymmetric failure mode and `AdvanceNonceAccount` must sit at instruction index 0.
- Confirmation policy derived from a **stated reorg-loss budget**, per chain and per amount, recorded alongside the credit. "12 confirmations" is folklore — Polygon has produced a 157-block reorg. Do not credit an L2 deposit on L2 block count above the budget; wait for the L1 batch to finalize. If low latency is required, credit immediately up to a **bounded global exposure** rather than lowering the depth globally. Alarm and degrade explicitly if the `finalized` head stops advancing (Ethereum went non-finalizing for >1h on 2023-05-12).
- Reorg detection and indexing: parent-hash chaining is the only transport-independent detector; **`eth_getLogs` never sets `removed: true`** (only the subscription/filter path does); key logs on `(chainId, blockHash, txHash, logIndex)`; use EIP-1898 block-hash-pinned reads; never advance a cursor past a range you did not fully read (a provider range error is not "no events"); contract-initiated native transfers emit no logs; indexer rollback floors are finite (graph-node prunes below 250 blocks by default) and a deeper reorg is an unrecoverable-state halt, not a rollback.
- Token semantics: `decimals()` is runtime metadata, not a constant; fee-on-transfer and rebasing mean amount-in ≠ amount-received — measure by balance delta or refuse the token, explicitly; non-reverting `false` returns; calls to codeless addresses succeed; phantom functions; the approval race; blocklists, pausability and upgradeability; native currency with an ERC-20 address.
- Signature and order replay: EIP-712 domain must bind `chainId`, `verifyingContract` and version; per-protocol cancellation semantics (Seaport counter, 0x salt floor, Permit2 vs EIP-2612 nonce spaces); validated orders outliving signatures; expiry and deadline as **correctness parameters, not defaults**.
- Bridges: replay protection scoped to `(sourceChain, destChain, nonce)` and consumed atomically with the effect; credit from an observed value delta, never from an emitted event; duplicate relayer delivery is the default; source/destination reorg coupling.
- Oracles: `updatedAt` against the feed's published heartbeat; `minAnswer`/`maxAnswer` clamping; L2 sequencer-uptime feed with a grace period; feed decimals ≠ token decimals; `answeredInRound` is deprecated in the current API reference even though audit convention still requires it; AMM spot is a quantity you can buy, not a valuation; a single-venue feed is a design defect regardless of whether anyone attacks it.
- On-chain arithmetic where an integrator must review it: share/asset conversion and the first-depositor/donation attack; accounted totals vs `balanceOf`; rounding per leg in the pool's favour; `mulDiv` with an explicit direction rather than `a*b/c`.
- Address derivation is chain-specific; a Safe or `CREATE`-derived proxy address is not a portable identity across chains.
- Custody and withdrawal pipelines: key and nonce single-writer discipline; in-flight ledgering of an unmined send; a governance kill switch measured against the drain rate.
- One codebase deployed to N chains multiplies one bug by N — stage value-bearing changes accordingly.

**Exclusions.** Auditing or authoring smart-contract source, reentrancy, access control and key management (that is a contract-security suite — Trail of Bits' `building-secure-contracts` is the right neighbour). CEX order placement and filters (→ `fin-exchange-integration`). Fiat rails (→ `fin-payments`). The internal ledger that records the credit (→ `fin-ledger`, via the seam rule). Generic idempotency theory (→ `fin-money-core`).

**Dependencies.** None. Pairs with `fin-ledger`.

**Reference files.**
- `references/evm-tx-lifecycle.md` — nonce management, replacement, mempool eviction, dropped-vs-pending detection.
- `references/solana-tx-lifecycle.md` — blockhash expiry, rebroadcast rules, durable nonces, status-history flags.
- `references/finality-and-confirmations.md` — per-chain observed reorg depths, L1-vs-L2 units, exposure-capped fast credit, escape-hatch deadlines.
- `references/indexing-and-reorgs.md` — parent-hash chaining, dedupe keys, provider range limits, internal-transfer detection, rollback floors.
- `references/token-semantics.md` — the weird-ERC20 taxonomy with the exact guard shapes.
- `references/oracles.md` — freshness, bounds, sequencer uptime, feed decimals, TWAP vs spot, venue depth.
- `references/bridges-and-signed-orders.md` — replay scoping, Nomad/Wormhole/Qubit mechanisms, EIP-712 domains, per-protocol cancellation matrices.

---

### 2.7 `fin-verification` — 200 lines

**Target user.** Anyone deciding *how much proof* a money-path change needs: writing or reviewing tests, adding a reconciliation job or drift alarm, or answering "is this safe to ship?".

**Description (≤430 chars).**
> Use when writing, reviewing, or choosing tests for code that moves, prices, stores, or accounts for money, when adding a reconciliation job, drift alarm, or kill switch, or when deciding whether a money-path change is safe to ship. Covers the risk tier and what each tier requires, invariant and property tests, timeout/crash/replay/duplicate tests, recorded fixtures, sandbox limits, model-based testing, deterministic simulation, and production reconciliation.

**Trigger conditions.** The task verb is write-tests / add-coverage / review / approve / ship / roll back / "is this safe"; **or** the declared or inferred risk tier is **T2 or above** (tier-gated, not verb-gated — a live multi-venue team never says the word "test").

**Responsibilities (owns).**
- The two tier axes and the tier→technique matrix, including the explicit *actively wasteful* column: **Axis A** blast radius per unit time; **Axis B** does an external oracle exist. Axis B is the mechanical one and it is the T2→T3 line.
- Conservation, idempotence, permutation-invariance and reservation-implies-postable as property tests asserted after **every** step, not only at the end.
- The timeout/5XX ⇒ reconcile-by-client-ID test, and the crash-between-effect-and-commit test at every phase boundary.
- Recorded-fixture replay with `record_mode=none`, captured from **production**, not testnet — testnet order books are independent, wiped periodically, missing `/sapi`, carry different filter thresholds, and can receive breaking API changes *before* production. Testnet proves protocol conformance and nothing else; dry-run is optimistic by construction.
- Model-based / reference-model state machine testing, with the model derived from the **specification or venue documentation**, not from the implementation, and kept architecturally separate.
- Fault-injection ordering: delay/reorder/duplicate first, then asymmetric partition heal, then process kill, then clock skew, then storage corruption.
- Production reconciliation and drift alarms as the test that runs forever, and differential testing against the venue's or processor's own reported fee, price and balance.
- Deterministic simulation testing gated to T3, why it is wasteful below that, and why DST does **not** subsume external adversarial testing (TigerBeetle ran 1024 cores of VOPR and Jepsen still found two safety bugs, both from blind spots in the fault model and the generator).
- Mutation testing on money math; why a race detector cannot find a double-spend (a lost update across transactions is not a data race).
- CI derandomization of property tests (Hypothesis auto-sets `derandomize=True` in CI and CI discards `.hypothesis/`) and committing counterexamples.
- Generator-coverage assertions, and the acknowledgement that no standard tooling answers "does my generator ever produce a partial fill exceeding the remaining quantity?".
- Assertion placement: impossible ⇒ halt; corrupt-but-recoverable ⇒ repair. **Fail-closed (stop taking risk, keep cancelling) is usually the right target for trading systems, not fail-fast** — halting mid-flight with open orders is often worse than continuing degraded. An assertion may only guard state for which no recovery path exists.
- Change-safety **evidence**: proving a flag was retired, that every shard has a test, that the rollback path was exercised, that build identity was verified fleet-wide. (The *rules* live in `fin-money-core`; this skill owns what counts as proof.)

**Exclusions.** The domain rules themselves — what correct rounding, capture, matching, posting or nonce handling *is*. The risk-tier signal table (that is `fin-money-core`, so the tier is available even when this skill never loads). The economic-diff gate. General software testing practice with no economic quantity. CI/CD pipeline configuration. Security review.

**Dependencies.** None. Loads *in addition to* a domain skill, never instead of one.

**Reference files.**
- `references/tier-matrix.md` — the full technique × tier table with boundary justifications and the wasteful column.
- `references/partial-failure-tests.md` — copyable skeletons in Python and TypeScript for the timeout, crash, replay-and-permute, and limits tests.
- `references/property-and-model-based.md` — Hypothesis / fast-check / proptest state-machine recipes for order and ledger workloads.
- `references/fault-injection.md` — Toxiproxy toxics, kill-at-phase-boundary harnesses, partition and clock-skew nemeses.
- `references/production-invariants.md` — reconciliation job shapes, drift alarms, break aging, halt semantics.

---

## 3. Core vs domain matrix

One owner per concept. A concept appearing in a domain skill that is owned by `fin-money-core` may appear **only** as a domain-native instantiation naming a real API, error code, protocol field or schema object — never as a restatement of the general theory. CI enforces this (§10.3).

Legend: **O** = owner (states the general rule and the rationale) · **I** = domain instantiation only (must name a concrete API/field/error/table) · **S** = duplicated seam rule, byte-identical in both skills · blank = out of scope.

| Concept | core | exchange | matching | payments | ledger | onchain | verification |
|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| Economic-diff gate | **O** | | | | | | |
| Risk-tier signal table | **O** | | | | | | |
| Tier → required technique matrix | | | | | | | **O** |
| Binary-float ban / obligation boundary | **O** | I | I | I | I | I | |
| Minor units, explicit scale, currency in the type | **O** | I | | I | I | I | |
| Scale from runtime metadata (`decimals()`, exponent tables) | **O** | I | | I | I | I | |
| Checked arithmetic before comparison | **O** | | I | | I | I | |
| Rounding mode named at call site | **O** | I | I | I | I | I | |
| Directed rounding / who benefits | **O** | | I | | I | I | |
| Largest-remainder allocation, named residue account | **O** | | I | | I | I | |
| Tick / lot / notional quantization | | **O** | | | | | |
| Pro-rata & FIFO allocation at the venue | | | **O** | | | | |
| Sentinel escaping its domain | **O** | I | I | | | I | |
| Timeout / 5XX ⇒ indeterminate | **O** | I | I | I | | I | |
| Idempotency key lifecycle & body binding | **O** | | | I | I | | |
| Client order ID scope per venue | | **O** | | | | | |
| Protocol order identity (UserRefNum, ClOrdID, PossDup) | | | **O** | | | | |
| Processor idempotency envelope (what a key doesn't survive) | | | | **O** | | | |
| Transaction identity `(chainId, from, nonce)` | | | | | | **O** | |
| Event/log dedupe key `(chainId, blockHash, txHash, logIndex)` | | | | | | **O** | |
| At-least-once consumers dedupe on business identity | **O** | I | | I | I | I | |
| Webhook ordering, ack-before-work, signature verification | | | | **O** | | | |
| Provisional vs final as distinct states | **O** | | | I | **O**¹ | I | |
| Available vs pending vs posted; holds with expiry | | | | | **O** | | |
| Double-entry, normality, chart of accounts | | | | | **O** | | |
| Immutability, corrections, reversal uniqueness | | | | | **O** | | |
| Bitemporality, as-of balances | | | | | **O** | | |
| Materialised-balance drift | | | | | **O** | | |
| Lost update / write skew / isolation level | **O** | | I | | I | | |
| Fencing tokens, epochs, single-writer failover | | | **O** | | | | |
| Deterministic core, journal inputs not outputs | | | **O** | | | | |
| Conservation across a transformation | **O** | I | I | | I | I | |
| Chokepoint solvency assertion | **O** | | | | I | I | |
| Non-overridable aggregate ceiling; no per-entity exemption | **O** | | | | I | | |
| Pre-trade rejection controls (15c3-5) | | I | **O** | | | | |
| Kill switch latency vs drain rate | **O** | I | I | | | I | |
| Alerts vs log lines vs mailing lists | **O** | | | | | | I |
| Flag repurposing, dead code, partial deploy, config parity | **O** | | | | | | I² |
| Order state machine, fills, overfills, avg price | | **O** | | | | | |
| Cancel/replace race & auction revalidation | | I | **O** | | | | |
| Book sync, sequence gaps, A/B arbitration (consume) | | **O** | | | | | |
| Market-data publication & fairness (publish) | | | **O** | | | | |
| Position, PnL, funding, margin, mark selection | | **O** | | | | | |
| Capture/refund/dispute/return state machines | | | | **O** | | | |
| Rail reversibility windows & return codes | | | | **O** | | | |
| Marketplace transfers & reversal | | | | **O** | | | |
| Confirmation policy & reorg-loss budget | | | | | | **O** | |
| Reorg-safe indexing & rollback floors | | | | | | **O** | |
| Token semantics (FoT, rebasing, returns, approvals) | | | | | | **O** | |
| Oracle staleness, bounds, sequencer uptime | | I | I | | | **O** | |
| EIP-712 / signed-order replay & cancellation | | | | | | **O** | |
| Bridges & cross-chain message replay | | | | | | **O** | |
| FX rate provenance, side, pivot, spread | **O**³ | | | | **O** | | |
| Reconciliation as a continuous assertion | **O**⁴ | I | | I | I | I | **O** |
| Property / model-based / fault-injection recipes | | | | | | | **O** |
| DST boundary and what is wasteful below it | | | | | | | **O** |
| Recorded fixtures, sandbox and dry-run limits | | | | | | | **O** |

¹ `fin-money-core` owns *provisional value is a state, not a flag*; `fin-ledger` owns the concrete posted/pending/available model. These are different rules, not two statements of one rule.
² `fin-money-core` owns the change-safety **rules**; `fin-verification` owns what counts as **proof** that they hold.
³ `fin-money-core` owns rate arithmetic and provenance recording; `fin-ledger` owns FX as a balanced two-account transaction with separately booked spread.
⁴ `fin-money-core` owns *name the external authority and the join key*; `fin-verification` owns cadence, break aging and halt semantics; each domain skill names its own authoritative source.

---

## 4. Decisions D1–D9

### D1 — Payments is a separate skill, not a lifecycle layer inside the ledger.

**Two skills, `fin-payments` and `fin-ledger`, with one seam rule duplicated verbatim in both.**

The users are disjoint and their 300-line files are different files. A Stripe integrator's world is a checkout route and a webhook handler; making them load double-entry, chart of accounts, bitemporality and trial-balance monitors to learn "ack the webhook before you do work" fails the 300-line-bot test as badly as making a Binance bot load clearing architecture. The ledger author's world is conservation, isolation and immutability and has no rails in it. The failure vocabularies barely overlap: ledger failures are conservation, isolation, bitemporality, materialisation drift; payment failures are settlement windows, chargebacks, ACH R-codes, retries against a third party you do not control.

The dependency edge is clean and directional — payments emit economically-final facts, the ledger adjudicates whether they add up — and that is exactly what a skill boundary should follow. Every operator that runs both at scale ships them separately: TigerBeetle mandates a control-plane/data-plane split where the transfer path must not touch the business database; Square built Books as a service consumed by the Payments, Refunds and Payouts teams; Formance ships Ledger and Payments as separate modules; Uber separates the money-order/entity-balance stores from the payment microservices.

The split is conditional on one thing: the shared primitives — money representation, idempotency, the timeout/reconcile rule — must be hoisted into a core rather than duplicated into both halves. D2 keeps that core, so the precondition holds. The inverse split — double-entry in payments, idempotency in the ledger — is what naive decompositions produce, and it puts the universal rule in the domain-specific place; the matrix in §3 forbids it.

A merged skill is two disjoint checklists in one file, and the agent loads the wrong half. Whichever half the task needs, the other half is inapplicable and stays in context for the rest of the session — the rendered body enters the conversation once and does not leave.

The strongest argument *for* merging — that the hardest bugs live on the seam (Revolut's ~$20M refund path that did not reference a settled capture is simultaneously a payments bug and a ledger bug) — is answered by shipping the seam rule verbatim in both skills and loading both when the diff crosses it (§5.6), not by merging.

### D2 — No hub skill, and the core skill is not the router. Routing is emergent from descriptions, backstopped by an always-on block.

A hub skill inserts a second discretionary hop into a chain whose first hop is already the design's weakest link. The second hop is worse than the first: an agent following a bare skill name written inside another skill's body has **no mechanism behind it at all** — nothing loads the named skill, nothing errors when the reference is ignored, and there is no signal when it is. It also spends ~400 characters of a shared listing budget on something that emits no rules.

Frontmatter descriptions cost nothing extra to consult — they are already in the system prompt every turn — so making them the routing surface *removes* a decision point rather than adding one. The always-on `AGENTS.md` block does everything a hub would do (name the suite, carry the gate, state priority, dispatch by observable signal) with strictly better delivery: no decision point, present every turn, no ordering issues.

`fin-money-core` exists as a **content** skill, not a router, for three reasons: the always-on block must compress to ≤8KB and the full universal material is larger; it is the correct destination for a change that is pure money math or pure partial-failure handling with no domain; and it carries the tier signal table so tiering survives even when the block was never installed.

Its description ends with an explicit deferral to all five domain siblings, so it **loses every contested match**. This is not decoration. Without the deferral clause, a core whose description names amounts, retries, rounding and dedupe keys matches nearly every money diff and loads *alongside* the domain skill that already answers the task — ~290–300 lines of mostly-inapplicable theory, paid for on every remaining turn.

I reject the alternative of deleting the core entirely and moving everything to `AGENTS.md`. The delivery gap is fatal: `npx skills add` installs skills and does **not** write the consumer's `AGENTS.md`. Without a core, the realistic shipped configuration has no home for money arithmetic, idempotency mechanics, the gate procedure, or change-safety.

### D3 — Yes: every domain skill must be fully useful with `fin-money-core` NOT loaded. Domain-native restatement is legitimate specialisation.

Requiring the core to be loaded would make correctness depend on a discretionary load and on cross-skill chaining — the two weakest links in the whole delivery path. Three independent silent-failure paths exist: description truncation drops the least-invoked skill first; compaction re-attaches only the first 5,000 tokens of each skill within a 25,000-token budget filled newest-first; and a chained load may simply not happen. Self-sufficiency is not a preference — it is forced.

Duplication is avoided by a **mechanical ownership test**, not editorial judgement: *if a rule can be stated without naming a concrete API, error code, protocol field, schema object or chain primitive, it belongs in core; if it cannot, it belongs in the domain skill.* §3 is that test applied.

Domain-native restatement is specialisation because the wording that binds is the wording phrased in the code the agent is writing. "Never infer non-occurrence from a timeout" is inert prose. "Binance documents HTTP 5XX, `-1006` and `-1007` as execution status UNKNOWN; hold the order INFLIGHT and resolve it by `newClientOrderId` — and note that Binance's uniqueness guarantee is only among *open* orders, so a reused ID after a fill creates a second order; do not resubmit" changes the diff. The second is not a copy of the first: it is unusable without the venue facts, and the first is unusable as code.

**The anti-drift mechanism is a coverage checker, not a prose generator.** `shared/invariants.yaml` holds, per invariant id, per skill: a hand-written domain-native rule text, and a machine-checkable `assert` field stating the invariant's normative content in a canonical form. CI fails if a skill in the invariant's `applies_to` list has no entry, if two entries' `assert` fields conflict, or if a "Non-negotiables" block has drifted from its YAML source. The generator **places and verifies**; humans **write**. This is the correct synthesis of two conflicting pressures: wording that binds has to be tuned to the code the agent is writing, so it cannot be template-generated; and N hand-maintained copies of one rule rot into N contradictory rules, so presence and consistency must be mechanical.

### D4 — Two trading skills: `fin-exchange-integration` (client of a venue) and `fin-matching-and-settlement` (you *are* the venue). Not one skill with a references split.

**The split predicate is Axis B: can an external party confirm your state?** A bot, an execution algo and a broker OMS all reconcile against the exchange. A matching engine, market-data publisher or clearing system **is** the record and has nothing to reconcile against. That single fact changes the entire correctness strategy — reconciliation is the primary safety net on one side and structurally unavailable on the other — and it is the same line that separates risk tier T2 from T3 and the principled boundary for demanding deterministic simulation.

The vocabularies are near-disjoint. One population says `create_order`, `tickSize`, `clientOrderId`, `listenKey`, funding. The other says `RptSeq`, pro-rata leveling, MoldUDP64 message counts, AIQ, LULD limit state, 15c3-5. Almost nobody writes both.

A references split is not equivalent, for a mechanical reason: **descriptions are always loaded; reference files are not.** A references split makes the venue engineer's path two discretionary hops where a skill makes it one, triggered directly by nouns already in their task text. And a merged description makes the failure concrete: one ending `SKIP when no order is ever sent to a venue` is *satisfied* by a matching engine (it **receives** orders), directly contradicting the same description's `...or implements a matching engine` clause. SKIP clauses read as authoritative negations. That is a coin-flip on whether the venue engineer reaches anything at all.

Volume justifies it independently: the client-side material is already ~300 lines on its own and the venue material another ~300. One 600-line file gets skimmed.

Within `fin-exchange-integration`, the retail bot author and the quant execution developer are the *same* population under different task names — both are venue clients calling the same APIs with the same order state machine — so they share one skill with `references/execution-algorithms.md` and `references/position-and-pnl.md` for depth.

### D5 — Both. Inference is primary and continuous; declaration overrides; lowering the tier requires an explicit statement.

A declaration-only tier fails silently when the user does not answer, and users do not answer. A pure-inference tier fails when a paper repo and a live repo differ only by a base URL. So: infer every time the gate fires, **print the tier and the signal that set it in one line**, and let a `FINANCIAL_TIER:` line in `AGENTS.md`/`CLAUDE.md` or a repo marker override it. Printing makes a wrong inference correctable in one message; under-tiering is the dangerous direction, so a declaration may raise the tier freely but may only lower it by explicit statement.

**The tier gates the required evidence, never which rules apply.** A rule carrying an exemption clause is a weaker rule everywhere, not a scoped one: the exemption reads as licence to look for exemptions, and it does not stay attached to the case it was written for. So the rule set is unconditional and the *proof burden* is tiered. Observable signals are in §6.

### D6 — No sixth domain skill. Seven skills total: five domain, one core, one verification.

Every rejected candidate splits cleanly along boundaries already drawn:

- **Market data** has no independent population. Consuming a feed (sequence gaps, book sync, checksums, stale marks) is `fin-exchange-integration`; publishing one (603(a) fairness, A/B arbitration, deliberately-constant fields) is `fin-matching-and-settlement`. There is no shared middle worth a description.
- **Custody / wallets** decomposes with nothing left over: keys, nonces, confirmation policy and withdrawal submission are `fin-onchain`; the balance that backs it, segregation and the solvency invariant are `fin-ledger`. As its own skill it would collide with both.
- **Prediction markets** are a CLOB plus a resolution event. The CLOB is `fin-exchange-integration`; resolution and settlement is `fin-matching-and-settlement`. The one incident on record here — the NautilusTrader/Polymarket overfill — is an order-lifecycle finding.
- **Clearing / settlement** is the back half of a venue operator's own pipeline.
- **Risk** is not one thing. Runtime *design* of client-side caps and halt-on-ambiguity is `fin-exchange-integration`; regulatory pre-trade rejection on the last hop is `fin-matching-and-settlement`; solvency invariants are `fin-ledger`; *verifying* a control exists, is wired to something a human reads, and holds under generated sequences is `fin-verification`. That last split is the non-obvious one and it is justified: "design a kill switch" and "prove the kill switch is wired to a monitored channel" are different failures. Knight's system emitted 97 "Power Peg disabled" emails 89 minutes before the open and nobody read them — a verification failure, not a design failure.

`fin-verification` earns the seventh slot because it is the only cross-cutting concern that changes what the agent *does* rather than what it knows, it is invoked at a different moment with different words, and the tier matrix (~25 techniques × 4 tiers) is too large to duplicate five times.

Seven is the top of the derived safe range (5–9) and the description budget holds at ≤3,000 characters.

### D7 — Ship seven artefacts. No Cursor rules, no `.github/instructions/`, no `gemini-extension.json` at v1, no non-spec frontmatter.

1. `skills/<name>/SKILL.md` at repo root — the first directory the `skills` CLI searches after a root `SKILL.md`, and what Claude Code's plugin loader scans by default.
2. `.agents/skills` → symlink to `skills/` — covers Codex, Cursor, Gemini CLI, Copilot/VS Code, Amp, OpenCode, Cline, Zed, Warp, Firebender, Antigravity. **Claude Code does not read it.**
3. `.claude/skills` → symlink to `skills/` — covers Claude Code, and is separately read by Cursor, VS Code and Amp for back-compat. Neither directory alone covers both camps.
4. `AGENTS.md` (canonical, ≤8KB) with `CLAUDE.md` as a symlink — read by 25+ tools and 60k+ projects with closest-file-wins precedence. This is the highest-expected-value artefact in the package.
5. `.github/copilot-instructions.md` — three lines pointing at `AGENTS.md`. GitHub Copilot ranks this file **above** `AGENTS.md` and warns that agent instruction files are "currently not supported by all Copilot features", so it is the only way the always-on layer reaches Copilot reliably.
6. `.claude-plugin/plugin.json` + `marketplace.json` — near-free, and plugin skills are namespaced `plugin:skill` so they **cannot collide** with the user's personal or project skills. The safest install path for a suite meant to coexist with superpowers (14 skills) and Trail of Bits (11).
7. `scripts/install-guardrails.sh` — idempotent, marker-delimited append of the always-on block into the *consumer's* `AGENTS.md`, `CLAUDE.md` and `.github/copilot-instructions.md`.

**Artefact 7 is not optional decoration; it closes the single biggest adoption gap in the design.** `npx skills add` installs skills and does not write `AGENTS.md`. A user who runs only the headline install gets the skills-only configuration, in which every rule in the suite sits behind a discretionary load. The README must lead with both commands, and state why.

A `SessionStart` hook ships **inside the Claude Code plugin only**, as a second delivery path for users who install via `/plugin install` (where the hook ships and fires automatically). It emits exactly one context key per detected harness — Claude Code reads both `additional_context` and `hookSpecificOutput.additionalContext` **without deduplication**, so union-emitting double-injects. The hook is never load-bearing: everything it injects is already in `AGENTS.md`.

**Rejected:** `.cursor/rules/*.mdc` — Cursor already reads `.agents/skills`, `.claude/skills` and `AGENTS.md`, and ships `/migrate-to-skills`; Cursor itself treats Skills as the successor to Rules. `.github/instructions/*.instructions.md` — `.github/copilot-instructions.md` already outranks `AGENTS.md` in Copilot's own precedence order, and shipping both means two divergent copies for one runtime with no path scoping to justify the second. `gemini-extension.json` — deferred to v2. Every non-spec frontmatter field (`paths`, `when_to_use`, `context: fork`, `argument-hint`, `allowed-tools`) — these hard-fail `package_skill.py`, claude.ai upload and the Skills API with `Unexpected key(s) in SKILL.md frontmatter`, discovered at distribution time after the suite is written.

### D8 — The economic-diff gate lives in the always-on block and is mirrored verbatim in the first 40 lines of `fin-money-core`. Five yes/no questions, biased toward NO, with a required verdict line.

The gate's job is to decide *whether* to engage the suite. If it lived behind a skill load it could only run once the agent had already decided to engage — the exact circularity that makes it useless, and the never-loaded failure one layer down. It must be present with no decision point, and short enough to pay for on every turn.

It is deliberately biased toward NO so the suite does not tax every diff, and it has an explicit SKIP clause. Without one it fires on every backtest and the suite gets ignored as noise.

**The five questions — AMOUNT · EFFECT · AUTHORITY · REPLAY · ROLLOUT** — each answerable by pattern-matching the diff:

1. **AMOUNT.** Does the change touch a value that is or becomes an amount owed, held, ordered, posted, priced or settled? Signals: identifiers matching `amount|price|qty|quantity|balance|fee|total|notional|rate|units|decimals|cost|principal|interest|shares`; a type change on such a field; `* 100`, `/ 100`, `1e18`, `round(`, `floor(`, `trunc(`, `int(`, `Decimal(`, `float(`, `parseFloat` adjacent to one.
2. **EFFECT.** Does it call, retry, or handle the failure of something that moves value or instructs someone else to? Signals: `create_order|cancel|replace|capture|refund|transfer|payout|withdraw|charge|settle|mint|burn|sendRawTransaction|post|journal`, **or** `retry|backoff|timeout|except|catch|Result::Err` in the same function as one of those.
3. **AUTHORITY.** Does it change who or what decides a balance, price, limit or eligibility? Signals: a write to a balance/position/limit field; a comparison operator on a threshold (`>` vs `>=`); an oracle or price read; a flag, config value or enum guarding any of those — **including reuse of an existing one**.
4. **REPLAY.** Does it change identity, keys, ordering or dedupe? Signals: idempotency-key construction, unique/primary key changes, `ON CONFLICT`, dedupe sets, nonce/sequence/cursor handling, webhook or event handlers.
5. **ROLLOUT.** Does it change deploy or config for a money path that is sharded, regionalised, or must be fleet-uniform? Signals: a per-shard/per-region config value; a feature-flag rollout percentage; a migration cutover; a rollback path.

**All five NO** ⇒ emit `ECONOMIC-DIFF: none` and review normally; load no `fin-` skill. **Any YES** ⇒ emit the full verdict line and continue.

**SKIP override:** the numbers are analytics that never become an obligation — backtest statistics, Greeks, implied vol, Monte Carlo, model calibration — **and** no balance, order, payment or on-chain transfer is written.

Three of the five questions exist because of specific incidents that a naive gate reads as touching nothing economic. Question 2 explicitly includes error handling because "refactor the retry logic" does not look like a money change and is where phantom charges and duplicate orders are born. Question 3 says "including reuse" because Knight repurposed a flag. Question 5 exists because Goldman tested A–H and L–Z while the misconfigured stripe was I–K, and because TSB's two data centres were "configured inconsistently despite having been specified to be identical".

The verdict is a **required slot**, not a prose reminder. The failure mode here is omission, and what survives omission is a REQUIRED field in a template the agent is already filling in — a blank it can see it has not filled — rather than a sentence it can decline to have read.

### D9 — `fin-` prefix, audience-named second token, no `-engineering` suffix.

**Ship:** `fin-money-core`, `fin-exchange-integration`, `fin-matching-and-settlement`, `fin-payments`, `fin-ledger`, `fin-onchain`, `fin-verification`.

Three constraints decide this.

**Collision safety.** A local skill named `code-review`, `verify`, `plan`, `commit`, `deploy`, `debug`, `init` or `security-review` **overrides Claude Code's bundled command of that name** with no error surface; Codex does not merge duplicate names and shows both; Amp lets local and built-in skills override repository skills. A suite meant to coexist must carry a suite token. `fin-` is short and unambiguous.

**Names spend characters in a truncation-prone listing, so every token must discriminate.** `-engineering` appears identically in all five straw-man names, so a truncated listing renders five near-identical entries; it adds a word to every name and no trigger value. The second token does real work instead: `exchange-integration` vs `matching-and-settlement` states the client/venue seam **in the name**, which is the most collision-prone boundary in the suite and therefore the one worth spending name characters on.

**Scope accuracy.** `money-engineering` reads as the suite's brand rather than a scope, which invites loading it for anything money-shaped — exactly wrong for a skill designed to lose contested matches; `money-core` reads as "the fundamentals, when nothing more specific applies". `onchain` rather than `defi` because the skill is integration mechanics — nonces, finality, indexing, token semantics — and a custody withdrawal pipeline is squarely in scope while not being DeFi; `defi` would simultaneously under-trigger for the custody backend and over-trigger into contract auditing this suite does not own. `verification` rather than `testing` because the skill also owns reconciliation design, drift alarms and ship/no-ship evidence.

Names are secondary to descriptions for discovery, so they are optimised for collision safety and scope clarity, which the description does not do well, rather than for keyword matching, which the description does far better.

---

## 5. Routing mechanism

Three layers, evaluated in order. **No layer depends on the layer below it succeeding.**

### 5.1 Layer 0 — always-on guardrails (`AGENTS.md` + `CLAUDE.md` + `.github/copilot-instructions.md`, ≤8KB, present every turn)

Contents, in this order:

1. The five-question economic-diff gate **verbatim**, with its SKIP clause and its required verdict line.
2. The risk-tier signal table (§6).
3. Twelve one-line non-negotiable invariants (the principles in §7, one line each).
4. The signal → skill dispatch table (§5.3).
5. The four seam rules by name, one line each (§5.6).
6. The line: **"Prefer retrieval-led reasoning over pre-training-led reasoning for money-handling code."** It is the direct counter to the agent proceeding from its prior, which is what constraint 2 of §1.4 says this suite is up against.

Compression target ≤8KB because this content costs tokens on **every** turn. It carries the gate, the tiers, the invariants, the dispatch table and the seam names, and nothing else: anything that can be stated once inside a skill body does not belong here.

### 5.2 Layer 1 — the gate and the tier

Run the five questions. Emit exactly one of:

```
ECONOMIC-DIFF: none
```
```
ECONOMIC-DIFF: amount,effect | tier T2 (payout path + customer_id on balances) | skills: fin-payments, fin-ledger
```

Zero hits ⇒ stop, load nothing, review normally. Any hit ⇒ declare the tier from §6 and continue to Layer 2.

### 5.3 Layer 2 — dispatch by observable signal, not by topic word

**Match every row that fires. Do not stop at the first.** This is the single most important correction in the routing design: the deposit-indexer, marketplace-payout, brokerage and crypto-exchange cases are all genuinely two-sided, and a one-skill rule silently drops half the answer.

| The diff imports / calls / defines | → skill |
|---|---|
| `ccxt`, `python-binance`, `alpaca`, `ib_insync`/`ibapi`, `hyperliquid`, `deribit`, a FIX/OUCH/SBE **client**, `create_order`/`createOrder`/`placeOrder`, `cancelOrder`, `newClientOrderId`/`orderLinkId`/`clOrdId`/`cloid`, `exchangeInfo`, `tickSize`/`stepSize`/`minNotional`, a depth/trade/user-data websocket | `fin-exchange-integration` |
| a loop matching an incoming order against resting orders; `order_book`/`price_level`/`allocation`/`priority` structures the repo owns; encoding or publishing a feed; a gap/retransmission protocol; auction or cross computation; `halt`/`resume`/`LULD`/circuit breaker; settlement-price computation; a pre-trade gate rejecting on a broker-dealer's behalf | `fin-matching-and-settlement` |
| `stripe`, `adyen`, `braintree`, `paypal`, `squareup`, `checkout`, `plaid`, `moderntreasury`, `PaymentIntent`, `charges.create`, `Idempotency-Key`, `merchantReference`/`pspReference`, a provider webhook handler, a settlement or balance-transaction report, `nacha`/`pain.001` | `fin-payments` |
| tables or identifiers named `entries`, `postings`, `journal`, `accounts`, `balances`, `holds`; `tigerbeetle`/`formance`; `debit`/`credit`/`double_entry`; a function that increments, decrements or overwrites a stored balance; `available`/`pending`/`posted`; `reversal`/`correction`/`period_close`/`trial_balance`; as-of balance queries; **`deposit`/`credit`/`confirmations`/`blockHash` reaching a balance** | `fin-ledger` |
| `ethers`, `viem`, `web3`, `wagmi`, `@solana/web3`, `eth_sendRawTransaction`, `eth_getLogs`, `eth_getTransactionReceipt`, `getSignatureStatuses`, nonce management or fee bumps, confirmation counting, `IERC20`/`ERC20`/`decimals()`, `AggregatorV3`/`latestRoundData`, `permit2`/`EIP712` | `fin-onchain` |
| the gate fired and **no** row matched | `fin-money-core` alone |

**Load bound: at most two domain skills, plus `fin-verification`.** A third domain body is a permanent per-turn tax — the rendered SKILL.md enters the conversation once and stays for the whole session, and after compaction only the first 5,000 tokens of each skill are re-attached within a 25,000-token budget filled newest-first, so skills six-deep are silently dropped mid-task.

**When more than two rows fire**, the diff is on a seam. Load the two whose call sites the diff actually touches — the call that reaches the external system decides one side, the call that writes the authoritative record decides the other — and apply the named seam rule (§5.6).

**Add `fin-verification`** when the task verb is write-tests / add-coverage / review / approve / ship / roll back / "is this safe", **or** when the tier from Layer 1 is **T2 or above**. It never replaces a domain skill. The tier gate matters: a live multi-venue team asked to "add a third venue" never says the word "test", and that team is exactly who needs production reconciliation, drift alarms and the timeout-that-already-filled test.

### 5.4 Layer 3a — inside a skill: the verb-indexed MUST table

Every domain skill's body opens (within the first 60 lines) with:

1. A **Non-negotiables** block of ≤8 lines, generated-and-verified from `shared/invariants.yaml`, written in that domain's vocabulary with a domain-native code example.
2. A **verb-indexed MUST table** — one row per lifecycle verb the domain has, each carrying its 3–5 MUSTs **inline**, not by pointer.

The verb is the cheapest observable predicate available and it is the one string the agent has definitely already seen in the code it is editing. `fin-payments` rows: charge · authorize · capture · refund · dispute · payout · transfer. `fin-exchange-integration`: place · amend · cancel · query · consume-fill · sync-book. `fin-onchain`: build · sign · broadcast · replace · index · credit · withdraw. `fin-ledger`: post · hold · release · reverse · close · reconcile. `fin-matching-and-settlement`: accept · match · allocate · cancel · replace · publish · halt · settle.

### 5.5 Layer 3b — reference dispatch, three reliability tiers

Only Tier B may hold a MUST.

- **Tier A** — anything the agent must not miss lives in `SKILL.md` itself, inside the first 60 lines. Never in a reference. Non-negotiables, the verb table, and the domain's highest-incident-weight rules.
- **Tier B** — a reference may hold MUST-rules **only** when selected by a dispatch row keyed on a **literal string already present in the diff, the repo, or the task text**: an import name, an endpoint path, a table or column identifier, a wire-protocol message name, or a lifecycle verb. The row is written imperatively and forbids paraphrase:

  > the code imports `binance` or hits `api.binance.com` → Read `references/venues/binance.md` **immediately** and follow it in order. **Do not summarize it — apply it.**

  > the diff modifies a function that removes or reduces a resting order, or walks a price level → Read `references/cancel-and-amend.md` **and** `references/deterministic-core.md` immediately and follow them in order. Do not summarize.

  > the diff contains `reverse|reversal|correct|adjust|void|discard` against a table named `entries|postings|journal|ledger_transactions` → Read `references/corrections-and-time.md` immediately and apply it in order.

  **The key is any literal string, not only a library import.** This is a deliberate correction: keying only on third-party imports structurally excludes matching engines, hand-rolled ledgers, and every system that is the record — precisely the population the venue test exists to serve. What remains forbidden is a *judgement* predicate: never "if this is complex", never "for more detail", never "see `references/` for details". Those produce load-nothing or load-everything.
- **Tier C** — background, rationale, dated tables, worked examples. Marked at the top: *"Explanatory; contains no rule."* If a rule needs to live here, the layout is wrong.

All references are **exactly one hop** from `SKILL.md`, never reference→reference: on a second hop the agent previews with `head -100` instead of reading the file and silently acquires an incomplete rule set. Every reference over 100 lines opens with a table of contents so a `head -100` preview still reveals its full scope.

### 5.6 The four seam rules

Each boundary is stated once in each of the two skills, in that skill's own vocabulary, because D3 requires every domain skill to stand alone. The hazard is not the restatement but *unmanaged divergence*: two skills giving contradictory instructions for the same boundary is a suite defect, not a judgement call. A generation step with CI divergence checking is proposed in `docs/adoption.md` as a v0.2 gate; it does not exist today and divergence is currently caught by review.

**S1 — payments ↔ ledger.** Every payment state transition emits exactly one balanced ledger transaction whose id derives from the payment's idempotency key. Every clearing account between payment states returns to zero, monitored as a continuous assertion. Never derive a balance by scanning payment objects. Authorizations are reserved amounts in the payments layer, not ledger entries; only captures, refunds, disputes, fees and settlement adjustments post.

**S2 — onchain ↔ ledger.** (i) *Identity* — a deposit credit is exactly one balanced ledger transaction whose idempotency key is `(chainId, blockHash, txHash, logIndex)`, never the tx hash and never `balance += amount`; the same log re-observed after a reconnect, a backfill overlap, or a provider failover is a no-op. (ii) *Staging* — the credit posts on observation to a per-user PENDING (unavailable) account and moves to AVAILABLE only at the credit policy's finality (L1 finality for rollups, not L2 block count; below the policy depth, credit only inside a stated exposure cap you are willing to lose). Withdrawal and onward transfer authorise from AVAILABLE alone. (iii) *Unwind* — a reorg detected by parent-hash mismatch produces a **reversing balancing entry** keyed on the orphaned log identity, never an in-place edit or a delete; a reorg deeper than the indexer's rollback floor is an unrecoverable-state halt, not a rollback. (iv) *Assertion* — a continuous reconciliation asserts `Σ credited at-or-below finalized height == Σ observed on-chain value deltas to deposit addresses`, and credit derives from an **observed balance delta**, never from an emitted event or a mined receipt.

**S3 — exchange ↔ ledger.** Fills are the economically-final fact: realized PnL, fees and funding post as journal entries; positions do not. The ledger transaction id derives from the venue's `trade_id`, and the same fill arriving on both the stream and the poll must post once. A fill reported as final can be busted inside the clearly-erroneous window, so booked economic history must accept retroactive reversal as a new balancing entry — the position is revisable, the entry is not editable.

**S4 — exchange ↔ matching.** A client reconciles against the venue; a venue has nothing to reconcile against. Where one process is both (a broker OMS: system of record for its clients' orders and simultaneously a client of the exchange), split the diff: the client half is T2 and requires reconciliation by client order ID; the venue half is T3 and requires order-by-order rejection on the last hop, a deterministic core, and DST. Do not let one tier declaration cover both halves. **Knight Capital sat exactly on this boundary.**

### 5.7 Priority and cross-references

- **Authority:** user instructions and `CLAUDE.md`/`AGENTS.md` outrank skills, which outrank default behaviour.
- **Conflict:** where a domain rule and a core rule address the same mechanism, **the domain rule wins** — it is the same rule expressed in the code being written, and the mechanism is the executable form. Seam rules are identical by construction, so a conflict between two skills' seam text is a **suite defect to be reported**, not a judgement call.
- **No `@file` references anywhere.** `@` force-loads and burns context. Cross-references are by bare name with an explicit marker (`**COMPANION SKILL:** fin-ledger`) and **nothing a skill needs to be correct lives in a sibling**.

---

## 6. Risk tiering

Two axes decide the tier. The second is the one people miss and the one that is actually mechanical.

- **Axis A — blast radius per unit time:** `max loss per erroneous action × actions per second`, i.e. how much can be lost before a human notices.
- **Axis B — does an external oracle exist?** Can you reconcile your state against somebody else's authoritative record? A bot can (the exchange). A payments integrator can (the processor). **A matching engine, custodian, or system-of-record ledger cannot — it *is* the oracle.** When Axis B is "no", reconciliation is unavailable as a safety net and the proof burden must move *before* deployment, into simulation. This is the principled boundary for demanding deterministic simulation testing.

| Tier | Definition | Observable code signals that place a codebase here |
|---|---|---|
| **T0** | Paper, read-only, research | No value-moving call reachable from an entrypoint, **or** every such call sits behind a `dry_run`/`simulate`/`paper` guard, **and** every base URL is a testnet/sandbox/paper host. Only analytics quantities exist. |
| **T1** | Live, own capital, bounded loss | A value-moving call — `create_order`, `charges.create`, `transfer`, `eth_sendRawTransaction`, `withdraw`, a journal write — is reachable from an entrypoint **and** a live credential path exists: `os.environ['*_API_KEY']`, a secrets-manager read, or a client constructed against a non-sandbox host. |
| **T2** | Someone else eats the error | Any of: an `account_id`/`user_id`/`customer_id`/`tenant_id` column on a balance/position/holdings row, or as a parameter of the money-moving function; a payout or withdrawal path; a webhook or callback that credits; **two or more** venue or processor adapters; a transfer whose two sides belong to different principals. |
| **T3** | System of record — no external oracle | Any of: a loop matching or allocating across resting orders; a `journal_entry`/`ledger_entry` writer that is **not** a mirror of an external processor; a custody signer holding user keys; assignment of trade/transfer IDs that other systems consume; a sequencer, consensus, or settlement-batch path; a mint/burn authority. |

**Escalators — raise the tier by one regardless of the above, and always report them:**
- a `SELECT` … then `UPDATE` on a balance column in separate statements;
- a money transaction whose isolation level is never explicitly set;
- a per-account or per-entity override flag on a solvency, credit-limit or liquidation check;
- an immutable deploy target with a multi-day fix latency (an on-chain contract behind a 7-day governance process raises the required pre-deploy tier to T3 regardless of TVL);
- one codebase deployed to N chains or N regions (model blast radius as N×).

**Declaration.** A `FINANCIAL_TIER: T2` line in `AGENTS.md`/`CLAUDE.md` or a repo marker overrides inference. It may raise the tier freely; lowering it requires an explicit user statement, because under-tiering is the dangerous direction.

**Required output.** One line, always: `Financial tier: T2 (inferred from: payout path + customer_id on balances)`. A wrong inference is then correctable in one message.

**What the tier gates.** The required *evidence*, never which *rules* apply. Boundary justifications: T0→T1 is crossed by "an order can actually be sent" — property tests become required not because the code is complex but because filters and rounding are adversarial input spaces the author cannot enumerate by hand. T1→T2 is crossed by "someone else eats the error" — loss is no longer bounded by capital deliberately exposed, which justifies model-based testing, an independent reconciliation path, network-level fault injection, and shadow diffing for any rewrite. T2→T3 is crossed by "no external oracle exists" — a bug is not detectable by reconciliation after the fact, so the only place to find it is a simulator, and "correct" becomes a *claimed consistency model*, which is what an external adversarial audit tests and an internal simulator systematically under-tests.

---

## 7. Core principles

Twelve. Each is falsifiable and each implies a concrete code check. These are the always-on invariant list, one line each in `AGENTS.md`, expanded here.

**P1 — Float is a modelling type, not an obligation type.**
*Check:* no binary floating-point type (`float`, `double`, `number`, `f64`, `REAL`, `DOUBLE PRECISION`, protobuf `double`) reaches any value that is owed, held, ordered, posted, priced or settled. Float **is** correct for Greeks, implied vol, Monte Carlo and backtest statistics — and a float-valued analytic must pass through an explicit, named quantization step before it becomes an amount. The boundary is "is this number an obligation?", not "is this finance?".

**P2 — An amount without a scale and a unit is not an amount.**
*Check:* every amount carries its currency or asset identifier inside the same value; cross-currency arithmetic fails to compile or raises; scale is resolved from runtime metadata, never hardcoded. Grep for `* 100`, `/ 1e18`, a hardcoded 2 decimals, and any `decimals()` result cached as a constant. JPY is exponent 0, KWD is 3, CLF is 4, and Stripe's charge scale, payout scale, display scale and calculation scale are four possibly-different numbers for one currency.

**P3 — A timeout is not a "no".**
*Check:* every `catch`/`except`/`Err` around a value-moving call resolves the outcome by querying with the client-supplied identity **before** any retry. A path that resubmits on timeout, 5XX, or 429 without a prior query is the bug. Binance says explicitly: "It is important to **NOT** treat this as a failure operation; the execution status is **UNKNOWN**". A "not found" immediately after submission is not proof of non-creation.

**P4 — Identity is minted and durably written before the effect.**
*Check:* the dedupe identity — idempotency key, client order ID, `(chainId, from, nonce)`, `(chainId, blockHash, txHash, logIndex)`, event id — is persisted before the external call, is bound to the request body, and replays the stored response on a duplicate. It is content-derived or caller-supplied, never a tuple of business attributes `(account, amount, date)` and never a positional index. Solana tracked blocks by slot number instead of hash and could not tell two different blocks apart.

**P5 — Rounding has a direction and the direction has a beneficiary.**
*Check:* every rounding call site names its mode; no `int()`, `floor`, `trunc` or integer `/` substitutes for rounding on a money value; the two legs of an exchange never share one global direction; every allocation preserves its remainder into a **named** account. Test at `threshold-1`, `threshold`, `threshold+1` — Cetus's overflow guard used the wrong mask constant *and* `>` instead of `>=`, and both errors are invisible except at the boundary.

**P6 — Provisional value is a state, not a flag.**
*Check:* authorized, provisional and final are distinct states in the type or the schema; nothing authorises a withdrawal or onward transfer against anything but the final/available balance. Grep for a single `balance` column that is both credited on observation and debited on spend. FTX credited ~$8bn of fiat deposits that never left a third party's bank account.

**P7 — Every value-mutating path ends at the same assertion.**
*Check:* enumerate every function that can change a balance or position; each must terminate in the one solvency/conservation assertion. A path that moves value and skips it **is** the bug — Euler's `donateToReserves` was the single path without the health check, for ~$197M. State the conservation invariant globally (`Σ balances + fees == Σ deposits − Σ withdrawals`, or `Σ debits == Σ credits`), not only per entity.

**P8 — Read-modify-write on a balance is a double-spend unless the isolation level says otherwise.**
*Check:* every money transaction sets its isolation level explicitly; every `SELECT`-then-`UPDATE` on a balance is either SERIALIZABLE with a retry that **re-reads** on 40001, or a single atomic conditional `UPDATE`. A transaction at Read Committed does not prevent the lost update, and a race detector will never find it because a lost update across transactions is not a data race.

**P9 — A limit that warns is not a control, and an alert nobody is paged for is not an alert.**
*Check:* the amount ceiling **rejects**, it does not warn — Citi credited $81 trillion where $280 was intended and no ceiling of any kind existed on the path. The kill switch must be exercisable faster than the loss accrues — Compound could not stop an ~$80M mis-distribution because the change process took seven days. The anomaly signal goes to a monitored channel with an owner and an SLA, never to a log line or a mailing list — Knight emitted 97 "Power Peg disabled" emails 89 minutes before the open.

**P10 — You do not own a fact until an independent path agrees.**
*Check:* for every economic quantity the system reports, name the external authority and the join key, and assert equality continuously with an aged break bucket. Join on the counterparty's own identifier (`pspReference`), not on yours (`merchantReference`, which is not unique). Where no external authority exists you **are** the oracle — that is T3, and the proof moves before deployment into simulation. Revolut's only effective control was a partner bank's cash-position report.

**P11 — Never repurpose a flag, enum, or field that a deployed consumer still reads; delete dead money paths.**
*Check:* grep every deployed artefact for readers of the value before reusing it; verify build identity on **every** host before enabling a feature — a deployment that succeeded on N-1 of N targets is a failed deployment; treat rollback as a change with its own test; assert config parity across regions and shards continuously, and test **every** shard, not a representative one. Knight stacked all four failures at once.

**P12 — A sentinel that is also a legal value voids the check that uses it.**
*Check:* no `0`, `""`, `0x00`, `-1` or `null` doubles as "unset" in a money path; use a presence flag or a type that cannot represent the sentinel. A missing price propagates as **absent**, never as 0 or last-known, and a price is not non-negative. Nomad initialised its trusted root to `0x00`, the same value meaning "not proven", and `process()` then accepted every message — 1,175 withdrawals.

---

## 8. The core agent workflow

The numbered procedure `fin-money-core` runs on activation. Each step names its **output artefact**; the artefacts are what make the pass falsifiable rather than performative.

**Step 0 — the economic-diff gate (cheap exit, always first).**
Answer AMOUNT · EFFECT · AUTHORITY · REPLAY · ROLLOUT from the diff alone. All five NO ⇒ emit `ECONOMIC-DIFF: none` and stop; review normally. Any YES ⇒ name which and continue.
→ *Artefact:* the verdict line.

**Step 1 — declare the tier.**
Read `FINANCIAL_TIER:` if present; otherwise infer from §6 and report the signal.
→ *Artefact:* `Financial tier: T<n> (inferred from: <signal>)`.

**Step 2 — enumerate the economic effects.**
List every operation in the changed code that moves value, creates an obligation, or changes an amount someone is entitled to. For each: what moves, from whom to whom, in what units, and whether it is reversible and for how long.
→ *Artefact:* a table of effects. If the list is empty, Step 0 was wrong — re-run it.

**Step 3 — fix representation before anything else.**
For every quantity in Step 2: its type, its scale, where the scale comes from, its unit/currency, and its rounding mode with a named beneficiary. You cannot state an invariant about a quantity whose type is wrong. Reject binary float on obligations; reject implied scale; reject an unnamed rounding mode; reject truncation.
→ *Artefact:* per-quantity `(type, scale, source-of-scale, unit, rounding mode, beneficiary)`.

**Step 4 — identify authoritative state.**
For each quantity: who is the system of record? Us, the venue, the processor, the chain, the counterparty? Where two systems hold a version, which one is authoritative **and in what time window** — the API is current state, the webhook is a trigger, the settlement report is the money. If we are the record for anything in the list, Axis B is "no" and the tier is at least T3 for that quantity.
→ *Artefact:* per-quantity authority, with divergence windows.

**Step 5 — extract the invariants.**
State them as executable predicates, not prose: conservation (`Σ deltas == 0`), solvency (`Σ customer balances <= custodied assets`), non-negativity where configured, monotonicity, reservation-implies-postable, `Σ lines == total`. Name the single chokepoint function every value-mutating path must terminate in, and enumerate the paths to prove none bypasses it.
→ *Artefact:* the predicate list plus the chokepoint name plus the path enumeration.

**Step 6 — mark the provisional/final boundary.**
For each inbound value: at what event does it become spendable? Prove that no path authorises a withdrawal or onward transfer against a provisional balance. Confirm the boundary is a **state**, not a boolean on one row.
→ *Artefact:* the state machine, with the spendable transition marked.

**Step 7 — determine operation identity.**
For every effect in Step 2: what identity deduplicates it, who mints it, when is it durably written relative to the effect, what is its scope and retention, and what does a duplicate return? Reject any identity minted after the effect, any identity that is a tuple of business attributes, and any assumption of idempotency the counterparty does not actually document.
→ *Artefact:* per-effect `(identity, minted-by, written-when, scope, retention, duplicate behaviour)`.

**Step 8 — inspect retries and ambiguity.**
For every effect: enumerate the failure signals the counterparty can emit and classify each as DEFINITE-NO, DEFINITE-YES or UNKNOWN. Prove that every UNKNOWN path queries by the Step 7 identity before retrying, and that the classification survives to the decision point rather than being flattened into a generic exception.
→ *Artefact:* the per-effect error classification table.

**Step 9 — inspect concurrency.**
For every read-then-write on an authoritative quantity: the isolation level, whether it is explicitly set, whether the retry re-reads, and whether a second concurrent actor can violate the Step 5 invariant. Check for write skew across two rows guarded by one invariant, and for a single-writer assumption that failover can break without a fencing token.
→ *Artefact:* per-site `(isolation, lock, retry semantics, the concurrent interleaving that would break it)`.

**Step 10 — inspect failure boundaries.**
Enumerate every point where the process can die between an external effect and the local commit. For each: what does startup recovery do? Prove there is a resolve-unresolved-intents pass and that it converges to exactly one effect. Check the dual-write sites and confirm an outbox or a saga with compensations that are legal for irreversible effects.
→ *Artefact:* the crash-point list with the recovery action for each.

**Step 11 — check controls and the rollout surface.**
Is there a hard ceiling that **rejects**? A kill switch faster than the drain rate? An alert on a monitored channel with an owner? Does the diff repurpose a flag, enum or field a deployed consumer still reads? Does it leave a dead money path callable? Must it reach every host, region or shard uniformly, and is every shard tested?
→ *Artefact:* the control inventory plus the rollout-surface answer.

**Step 12 — determine the reconciliation path, then require proportional tests.**
Name the external authority and the join key for every quantity from Step 4, and the cadence and break-aging policy. Where no external authority exists, say so explicitly — that is the T3 declaration. Then require the tests the tier demands (defer to `fin-verification` for the matrix; the tier-appropriate floor is non-negotiable regardless of whether that skill loads).
→ *Artefact:* the reconciliation spec, and the required-test list with a pass/fail for each.

**Two changes from the draft order, both deliberate.** Representation (Step 3) was inserted before invariants because you cannot state an invariant about a quantity whose type is wrong, and representation is the single largest bug class among the incidents in §1.1–1.2. Controls and rollout (Step 11) were inserted because the draft order had no step at which Knight's repurposed flag, Goldman's untested shard, Citi's missing ceiling or Knight's unread alert emails would ever be caught — and those are four of the most expensive incidents on that list.

---

## 9. The trading fast-path workflow

`fin-exchange-integration` runs this instead of §8 when the tier is T0 or T1 and the code is a single-venue client. It is seven steps, and it must never mention matching, allocation, clearing, or blockchain finality.

**Step 1 — pin the instrument metadata.** Fetch filters from the live venue (`exchangeInfo` or equivalent) at startup, cache with a refresh, and commit a production fixture for tests. Validate against the filter set for the **order type you are sending** — LIMIT and MARKET validate against different sets.

**Step 2 — quantize toward validity.** Round price to `tickSize` and quantity to `stepSize` in the direction that keeps the order legal, then re-check **every** filter simultaneously — `price % tick == 0`, `qty % step == 0`, `price*qty >= minNotional`, `qty <= maxQty`. Serialize as a decimal string; never let scientific notation reach the wire. If quantization would produce `qty == 0`, emit an explicit skip signal, never a silent no-op.

**Step 3 — mint the client order ID before you send it.** Generate it client-side, persist it with status `INFLIGHT` **before** the HTTP call, and know your venue's actual guarantee: on Binance, OKX and Kraken uniqueness holds only among *open* orders, so reusing an ID after a fill creates a **second order**; Coinbase Advanced Trade is the only venue in the set that returns the original order on a duplicate; Deribit has no client order ID and you must recover by label; Hyperliquid's `cloid` is a correlation key with no dedup guarantee.

**Step 4 — classify the response, then resolve.** A 200 means accepted, never executed. HTTP 5XX, `-1006`, `-1007`, a socket timeout and a 429 are all UNKNOWN. **Do not resubmit.** Query by client order ID with backoff across the venue's propagation window; a "not found" immediately after placement is not proof of non-creation. If the venue cannot answer, hold the order INFLIGHT and resolve it from the private fill stream. Do not use ccxt's documented balance-check recovery — it is race-prone against fees, funding and other strategies.

**Step 5 — treat fills as cumulative and dedupe them.** Read cumulative filled quantity from the venue, not deltas you accumulate; dedupe on `trade_id` plus a field comparison, because the same fill arrives on both the stream and the poll; recompute average price from the fill set every time; record an overfill rather than clamping it; subtract base-asset commission from the received quantity.

**Step 6 — keep the book and the connection honest.** Follow your venue's exact snapshot/incremental join algorithm — Binance Spot and Binance Futures differ, and the wrong one is the most-copied incorrect snippet in the ecosystem. On any sequence gap, discard and re-snapshot; never patch. Renew the user-data stream keepalive; a lapsed `listenKey` means a blind position. Judge staleness from the data, not from socket state, and stop sending orders while the book is unsynced.

**Step 7 — REQUIRED before this bot runs against live keys: emit these two tests.**
> **(a) The timeout that already filled.** Stub `POST /order` (or put a `timeout` toxic in front of it) so the request is delivered upstream and then returns 503. Assert: the bot does **not** resubmit; it calls query-by-clientOrderId; and after the venue reports the order exists, internal state shows **exactly one** order. Run the same test for the case where the order genuinely does not exist.
> **(b) The filter property test.** Generate `(price, qty)` across the realistic range, including values near `minQty`, near `minNotional`, and with more decimal places than `tickSize`/`stepSize` allow. Run them through `normalize()`/order construction and assert every filter holds **simultaneously**, that normalization never returns `qty == 0` without an explicit skip signal, and that rounding is always toward validity and never increases size beyond available balance. Parameterise over LIMIT and MARKET so `MARKET_LOT_SIZE` is exercised. Drive filter values from a **production** `exchangeInfo` fixture.

This is a required output slot, not a pointer to another skill. These two tests catch the two failures that dominate this population — the timeout that already filled, and the order that violates a filter — and the tidy place for them is `fin-verification`. That would strand them behind a cross-skill name reference plus a verb ("test") this user never says. A failure of omission is closed by a required slot in a template the agent is already filling in, not by a reminder in a file that was never opened.

At T2+ (multi-venue, customer funds, or a payout path) this fast-path is insufficient: load `fin-verification` and run §8 Steps 4, 9, 10 and 12 in full.

---

## 10. Repository layout

```
financial-engineering-skills/
├── AGENTS.md                          # CANONICAL always-on guardrails block, <=8KB
├── CLAUDE.md                          # symlink -> AGENTS.md
├── .github/
│   └── copilot-instructions.md        # 3 lines: read AGENTS.md before changing money code
├── README.md                          # install matrix; BOTH commands, with the reason
├── LICENSE
│
├── skills/                            # canonical; skills CLI + CC plugin loader read this
│   ├── fin-money-core/
│   │   ├── SKILL.md                   # ~240 lines
│   │   └── references/                # representation, rounding-and-allocation,
│   │                                  # indeterminacy-and-idempotency, concurrency,
│   │                                  # change-and-rollout
│   ├── fin-exchange-integration/
│   │   ├── SKILL.md                   # ~300 lines
│   │   └── references/
│   │       ├── venues/                # binance, okx-bybit-kraken,
│   │       │                          # coinbase-deribit-hyperliquid, divergence-matrix
│   │       ├── ccxt.md
│   │       ├── order-state-machine.md
│   │       ├── position-and-pnl.md
│   │       ├── orderbook-sync.md
│   │       ├── execution-algorithms.md
│   │       └── fix-client.md
│   ├── fin-matching-and-settlement/   # ~300 lines + 7 references
│   ├── fin-payments/                  # ~290 lines + 7 references
│   ├── fin-ledger/                    # ~270 lines + 5 references
│   ├── fin-onchain/                   # ~300 lines + 7 references
│   └── fin-verification/              # ~200 lines + 5 references
│
├── shared/                            # single sources of truth; NOT shipped as skills
│   ├── invariants.yaml                # invariant id -> {applies_to, assert, per-skill text}
│   ├── guardrails.md                  # the <=8KB block; AGENTS.md is generated from it
│   └── seams/
│       ├── payments-ledger.md
│       ├── onchain-ledger.md
│       ├── exchange-ledger.md
│       └── exchange-matching.md
│
├── .agents/skills -> ../skills        # Codex, Cursor, Gemini CLI, Copilot/VS Code, Amp, Zed…
├── .claude/skills -> ../skills        # Claude Code (+ Cursor/VS Code/Amp back-compat)
├── .claude-plugin/
│   ├── plugin.json                    # {"name":"financial-engineering","skills":"./skills/"}
│   └── marketplace.json
├── hooks/                             # SHIPPED IN THE PLUGIN PATH ONLY; never load-bearing
│   ├── hooks.json                     # SessionStart matcher "startup|clear|compact"
│   └── session-start                  # emits EXACTLY ONE context key per detected harness
│
├── scripts/
│   ├── install-guardrails.sh          # idempotent marker-delimited append into the
│   │                                  # CONSUMER's AGENTS.md / CLAUDE.md / copilot-instructions
│   ├── check_frontmatter.py           # real YAML parse; name==dirname; only name/description/license
│   ├── check_description_budget.py    # <=430 chars each, <=3000 total, <=1024 hard cap
│   ├── check_invariant_coverage.py    # every invariant present in every applies_to skill
│   ├── check_seam_parity.py           # seam text byte-identical across both skills
│   └── check_reference_depth.py       # one hop only; ToC on every reference >100 lines
│
├── evals/
│   ├── trigger-queries.json           # ~20 per skill: 8-10 positive, 8-10 near-miss negative
│   ├── gate-corpus.json               # real diffs from the incident set, labelled economic/not
│   └── evals.json                     # paired with_skill / without_skill cases + assertions
│
├── incidents/                         # one file per cited incident, machine-readable
├── examples/
└── docs/
    └── architecture.md                # this file
```

### 10.1 Deviations from the conventional hub-and-spoke skill layout, with reasons

| Change | Reason |
|---|---|
| **No hub skill directory** (`financial-correctness/`) | D2. A hub is a decision in front of a decision, costs ~400 chars of a shared listing budget to emit no rules, and depends on cross-skill chaining that no harness implements. Its job is done by `AGENTS.md`, which is present every turn. |
| **Seven skills, renamed** | D6/D9. The conventional five collapse the client/venue seam — the most collision-prone boundary in the suite — and use a null `-engineering` suffix that renders five near-identical entries under truncation. |
| **`shared/` added** | D3. `invariants.yaml` makes self-sufficiency mechanically checkable rather than editorial; `seams/` makes the four duplicated rules byte-identical by construction with CI failing on divergence. |
| **`scripts/install-guardrails.sh` added and promoted to the README's second line** | The single biggest adoption risk in the design. `npx skills add` does **not** write the consumer's `AGENTS.md`, so the default install leaves every rule in the suite behind a discretionary load. |
| **`hooks/` demoted to the plugin path only** | Claude Code reads `CLAUDE.md` and every other harness reads `AGENTS.md`, so a universally-shipped hook would double-inject; Claude Code reads both `additional_context` and `hookSpecificOutput` **without deduplication**. It survives as a second delivery path for plugin installs, emitting exactly one key per detected harness, and is never load-bearing. |
| **`gemini-extension.json` deferred** | Gemini CLI reads `.agents/skills/`, which is already shipped. A ninth manifest to maintain buys no new reach at v1. |
| **`evals/gate-corpus.json` added** | The whole architecture rests on the gate firing correctly, and its five questions were derived from failure modes rather than checked against real diffs. A gate that under-fires reproduces the never-loaded failure with a respectable one-line justification attached, which is worse than no gate because it looks like diligence. |
| **`.cursor/rules/`, `.github/instructions/`, `.cursorrules` not shipped** | D7. All three are divergent copies that rot, for runtimes already covered. Cursor ships `/migrate-to-skills`; Copilot's `copilot-instructions.md` already outranks `AGENTS.md`. |

### 10.2 README install block (the exact text to ship)

```markdown
## Install

**1. The skills — any agent (77 supported)**

    npx skills add <owner>/financial-engineering-skills

**2. The guardrails — REQUIRED, and the installer above does not do it**

    ./node_modules/.bin/fin-skills-guardrails   # or: bash scripts/install-guardrails.sh

This appends an 8KB always-on block to your AGENTS.md and CLAUDE.md. Skills alone are
not enough: a skill only helps once the agent decides it needs help, and an agent
writing money code rarely decides that. The guardrails block is present on every turn
with no decision in front of it. Step 2 is where most of the value is.

**Claude Code (plugin — namespaced, cannot collide, installs the guardrails hook too)**

    /plugin marketplace add <owner>/financial-engineering-skills
    /plugin install financial-engineering-skills@financial-engineering-skills
```

### 10.3 CI gates (all blocking)

1. **Frontmatter** — real YAML parse; `name` matches the directory; only `name`/`description`/`license`; `[a-z0-9-]`, no consecutive hyphens, no "claude"/"anthropic".
2. **Description budget** — ≤430 chars each, ≤3,000 total, hard ceiling 1,024 (Anthropic's own `claude-api` ships 1,068 and would fail `package_skill.py`).
3. **Size** — every `SKILL.md` ≤350 lines; every non-negotiable rule inside the first 5,000 tokens (compaction re-attaches only that much).
4. **Reference depth** — one hop only, no reference→reference; table of contents on every reference over 100 lines.
5. **Invariant coverage** — every invariant present in every skill in its `applies_to` list, with non-empty distinct domain-native text and non-conflicting `assert` fields.
6. **Seam parity** — the four seam rules byte-identical in both their skills.
7. **No `@file` references anywhere in any `SKILL.md`.**
8. **Guardrails size** — `AGENTS.md` ≤8KB.
9. **Trigger evals** — ≥0.5 trigger rate on positives, <0.5 on near-miss negatives, 3 runs each, 60/40 train/validation split.

---

## 11. Open risks

**R1 — The always-on layer carries the most important content and its delivery is not guaranteed.** The whole two-layer design rests on `AGENTS.md`, and `npx skills add` does not write it. `scripts/install-guardrails.sh` and the plugin hook are two independent mitigations, but both require the user to do or choose something. If most users run only the headline install, the realistic shipped configuration is skills-only — every rule behind a discretionary load, with no signal when it is not taken. **Mitigation to build:** make the guardrails install the *first* README command, ship it as a `postinstall` where the packaging allows, and instrument nothing — we cannot measure adoption, so the design must be robust to its absence. That is why every non-negotiable rule is *also* in the first 60 lines of its skill.

**R2 — Always-on context may bind less well against a confident prior than it does against a gap, and the whole layering rests on it.** Passive context is good at supplying something the model does not have. This suite asks it to do something harder: override behaviour the model is sure about, on code it believes it can already write. Filling a void and contradicting a prior are not the same instrument, and the second is the one the design leans on. If passive context does not bind for discipline failures, the most important content is in the weakest position. **This must be the suite's first eval:** the same invariant block, skill-body arm against `AGENTS.md` arm, on tasks where the unaided model gets it wrong.

**R3 — The gate is unvalidated and everything downstream depends on it.** Five questions derived from failure modes, not from a body of real diffs. A gate that under-fires produces the never-loaded failure with a respectable justification attached. A gate that over-fires makes the suite noise and gets ignored. **Mitigation:** `evals/gate-corpus.json` must be built from the incident set and run before any skill ships, counting false negatives first.

**R4 — Seven descriptions is the top of the safe range and the budget is an externality we do not control.** A user running superpowers (14) plus a contract-security suite (11) plus personal skills can push Codex past `min(2%, 8000 chars)`; overflow drops descriptions starting with the least-invoked, which is always a newly installed suite. `fin-matching-and-settlement` is almost certainly our least-invoked entry and `fin-verification` plausibly second. Truncation strips exactly the trigger keywords, and the symptom is indistinguishable from never having written the skill. **We have no mitigation beyond the ≤3,000-char budget and putting the key use case first.** If evals show truncation biting, the fallback is to merge `fin-verification` into `fin-money-core` and accept a 240→320-line core.

**R5 — Domain-native restatement has a coverage checker but not a semantics checker.** `check_invariant_coverage.py` proves every skill *has* a statement of each invariant it needs; it cannot prove five hand-written statements of "timeout ⇒ indeterminate" agree at the boundary. Drift here is invisible: each rule reads correctly in isolation while quietly disagreeing about what counts as UNKNOWN. **Mitigation to build:** make the `assert` field in `invariants.yaml` a genuinely canonical predicate that a reviewer diffs, and add the five restatements to the eval set as a consistency scenario.

**R6 — `fin-verification` is the weakest skill and may be a fifth wheel.** Its trigger overlaps with hosts' bundled `/code-review` and with generic review behaviour the agent already performs; once the gate has fired and a domain skill is loaded, the agent has a plausible reason to proceed without it — the same non-invocation failure one layer down. Its content is also the least mechanism-level in the suite: "choose the right tier of testing" is a judgement call, and judgement calls bind worst. Tier-gating it at T2+ and pulling the two bot tests into `fin-exchange-integration` are direct responses, but they are mitigations, not proof it earns a description slot.

**R7 — Whole-repo tiering is wrong for monorepos and we cannot fix it portably.** Claude Code's `paths` and Cursor's `globs` would scope a skill to a directory, but they are non-spec frontmatter and D7 forbids shipping them. A monorepo containing a bot, a ledger and a matching engine gets one whole-repo tier and one whole-repo route. S4 tells the agent to split the diff at the exchange/matching seam, but it is prose, not a mechanism. **Mitigation:** the tier declaration is per-diff, not per-repo, and the agent must re-declare on each economic diff.

**R8 — The dispatch table's keys are heuristic strings.** A repo whose internal table is named `entries` but which only calls Stripe mis-routes to `fin-ledger`. A repo with a `positions` table and no venue adapter mis-routes to `fin-exchange-integration`. False-positive routing is cheaper than false-negative routing (an extra 270 lines vs a missed rule), which is why the table is deliberately loose, but it costs context on every mis-fire and the load bound of two domain skills is the only thing keeping that bounded.

**R9 — None of this is eval-verified, and the two decisions committed to hardest are the two most in need of one.** D2 (no hub, routing emergent from descriptions) and the seven-way partition are exactly what a 20-query trigger eval with 8–10 near-miss negatives would settle. The near-misses that would break it are obvious and must be in the set: *"add a refund button"* (payments or ledger?), *"track my position across two exchanges"* (exchange or ledger?), *"credit the user when the deposit confirms"* (onchain or ledger — the answer is **both**, and a design that loads one is wrong), *"write a script that reads a CSV of transactions and uploads to Postgres"* (should trigger nothing).

**R10 — The rule surface is proposed whole, and no rule in it has been shown to be load-bearing.** Some fraction of the twelve principles and the domain MUSTs guard against failures a current model does not actually make, and every such rule is pure cost — it competes for attention and pushes real rules toward the compaction cliff. Rules stop earning their place long before the suite visibly degrades, so the point at which the surface got too large does not show up in the output. **Before v1.1, every rule must be justified by a case where its absence actually produces the failure, and the ones that cannot be must be deleted.**

---

## 12. The load-bearing corrections, and what was rejected

**The base structure** is the one §1.4 forces: two layers, no hub, mutually-exclusive descriptions with SKIP clauses, a core that defers to all five domain siblings, the client/venue trading split, and the three-tier reference reliability model.

**Corrections made to it, each closing a case where the base structure silently drops half the answer:**
- *Match every row that fires; do not stop at the first*, plus the four explicit seam rules. This is the single most important correction: a one-skill rule is wrong on every genuinely two-sided diff, and the deposit indexer — an on-chain observation that terminates in a balance — is the canonical one.
- Tier-gating `fin-verification` at T2+ rather than verb-gating it, so a live multi-venue team gets reconciliation and drift alarms without saying the word "test".
- The `onchain ↔ ledger` seam rule stated at body level in both skills, and the `deposit|credit|confirmations|blockHash` dispatch key on the ledger row, so the pairing fires from the crypto vocabulary rather than only from a table named `balances`.
- The two bot tests as a REQUIRED output slot inside `fin-exchange-integration`, which is where the user who needs them actually is.
- `references/venues/divergence-matrix.md` gated on a structural predicate — more than one adapter, or any venue-agnostic abstraction — rather than on a topic word.
- The verb-indexed MUST table as the body structure of **every** domain skill, not only `fin-payments`.
- The five-rule cancel/amend block in `fin-matching-and-settlement`'s first 60 lines, and the redefinition of the Tier-B dispatch key from *library import* to *any literal string in the diff, repo or task text*. Keying on imports structurally excluded venue code, ledger code and every hand-rolled system from the suite's own MUST-bearing reference gate.
- Reversal-link uniqueness and the mechanism-form materialised-balance rule in `fin-ledger`'s body.

**Rejected, with reasons:**

1. **Build-time duplication of `references/money-arithmetic.md` into three reference trees with CI byte-identity.** It exists only to compensate for deleting the core skill. With `fin-money-core` present the file lives once, and each domain skill states its own instantiation (tick/lot quantization, processor scale quirks, token decimals). The failure it creates is worse than the one it avoids: a fork or a partial install diverges silently with no runtime signal, trading a documented runtime risk for an undocumented supply-chain one.
2. **Deleting the core skill entirely and moving everything to `AGENTS.md`.** The case for the always-on layer is real and is adopted, but the delivery gap is fatal (R1): `npx skills add` writes skills and does not write `AGENTS.md`, so this configuration ships nothing at all to a large fraction of users. Both layers ship.
3. **Mandatory core-first loading on every economic diff.** It pays ~300 lines of largely inapplicable core on every diff whose domain skill already answers the task, and it spends that budget *first*, ahead of the rules that will actually apply. Core loads when no domain row fires, or when the diff is pure arithmetic or pure partial-failure handling.
4. **"Load exactly ONE skill."** Wrong on every seam diff — deposit indexer, marketplace payout, brokerage, crypto exchange — where it silently drops half the answer. Replaced with match-every-row, a two-domain cap, and the seam rules.
5. **Fifteen core rule families.** Past some rule count, added rules stop being followed and start crowding out the ones that were. Core ships eight; money-moving UX defaults, the aged unmatched-item bucket and rarely-exercised-path parity move to references or to `fin-verification`.
6. **A core skill with no deferral clause in its description.** Such a description matches nearly every money diff and loads alongside the domain skill that already answers it. `fin-money-core`'s description ends by deferring to all five domain siblings, so it loses every contested match.
7. **`.github/instructions/financial-correctness.instructions.md`.** `.github/copilot-instructions.md` already outranks `AGENTS.md` in Copilot's precedence order; shipping both means two divergent copies for one runtime, with no path scoping to justify the second.
8. **A universally-shipped `SessionStart` hook as the primary delivery mechanism.** Documented double-read hazard, and the `AGENTS.md`/`CLAUDE.md` path already covers the harnesses that matter. It survives in the plugin path only, emitting one key per detected harness, and is never load-bearing.
9. **Every non-spec frontmatter field** — `paths`, `when_to_use`, `context: fork`, `argument-hint`, `allowed-tools`. Hard distribution failure with `Unexpected key(s) in SKILL.md frontmatter`, discovered after the suite is written. This is the one place where a genuinely useful capability (`paths` would fix R7) is given up for portability, and the trade is accepted deliberately.
