# Incident catalogue

This directory is the evidence base for *Financial Engineering Skills*. It is not a history of
financial disasters. **Every entry exists to justify a rule an AI coding agent can act on.** An
incident that does not change what an agent should write, flag, or refuse does not belong here, no
matter how large the loss or how good the story.

That filter is strict and it excludes things people expect to find. Pure access-control breaches
(a stolen key, a forged signature, a missing `onlyOwner`) are security failures, and a security
suite owns them. What this catalogue collects is the narrower and less-discussed class: **a system
that produces the wrong economic outcome while every component behaves exactly as specified.** No
attacker was required for the mechanism to exist in Knight Capital, Citibank/Revlon, Robinhood's
doubled balances, Bitcoin's 2010 overflow, or Revolut's declined-transaction refunds.

## How to read an entry

Twenty incidents have a file of their own, at `<slug>.md`. They were selected for the combination
of **primary sourcing** and **code-level clarity**, not for size of loss. Each file states what
happened, what the software actually did, the violated invariant as a checkable predicate, an
honest verdict on whether an agent reviewing the diff would have caught it, the rule the incident
motivates, and its sources with primary documents first.

The remaining entries appear in the mapping table only. They are real and they motivate rules, but
either the mechanism is thinner in public, or the rule they motivate is already carried by an
incident with a better source.

## Sourcing rules applied throughout

1. **Every correction below comes from the regulator's own document, read verbatim.** Where a
   widely circulated account conflicts with the primary text (an SEC order, an FCA Final Notice, a
   CFTC complaint, a court opinion), the primary text wins. Those corrections are applied here and
   called out where a reader is likely to have absorbed the wrong version.
2. **Loss figures are the figure the primary source gives, attributed to it.** Where a widely
   repeated figure is not in the primary source, this catalogue says so rather than repeating it.
3. **No entry asserts a mechanism its sources do not establish.** Where the mechanism is not
   public, the entry says "mechanism not publicly established" and no rule is derived from the
   part that is unknown.

### Corrections you should know about before reading anything else

| Widely repeated | What the primary source actually says |
|---|---|
| The CFTC/SEC flash-crash report blames Waddell & Reed | The string "Waddell" appears **zero** times. The report identifies the seller only as "a large fundamental trader (a mutual fund complex)". The identification is press reporting. |
| The flash crash destroyed "$1 trillion" of market value | The string "trillion" appears **zero** times in the report. Its own quantifications are ~2bn shares traded 2:40–3:00pm with volume "exceeding $56 billion", and "over 98% of all shares were executed at prices within 10% of their 2:40 p.m. value". |
| NASDAQ's Facebook cross failed over to a **lagging replica** | No replica was behind and none was promoted for its data. The 19-minute staleness was an **input backlog inside the livelocked primary**; the failover was of the matching engine, to a duplicate with the validation-check lines deleted (34-69655 ¶23, ¶26). |
| NASDAQ's retry loop "snapshotted the cancels known at its start", so the fix is a retry ceiling | Each retry incorporated **only the first cancellation received during the previous calculation**, one event per pass (¶9, ¶20). A retry ceiling would have *aborted* the cross. NASDAQ's own agreed remediation (¶65) is to freeze the input set or drain the whole queue in one recomputation. |
| Malleability caused the loss of Mt. Gox's 850,000 BTC | Decker & Wattenhofer measured the whole network for the year to 2014-02-07 and found **421 conflict sets totalling 1,811.58 BTC**. The 286,076 BTC of malleability attacks came *after* Mt. Gox's press release. |
| The FCA's CGML Final Notice is dated 22 May 2024 | The Notice's own cover states **17 May 2024**; 22 May is the press-release date. |
| Citi's erroneous basket drove OMX Stockholm 30 down ~8% | The Notice quantifies one index move: "the **MSCI Europe ex UK Index fell just over 4%** … within five minutes" (¶2.8). The 8% figure is press-sourced. |
| Citi's controls stopped US$255bn, and that figure is press-sourced | The figure is the FCA's own, in the Notice's narrative ¶4.6 ("Various controls prevented US$255bn of the US$444bn basket … progressing") and in the press release, but it is a rounded aggregate (444 − 189) that collapses two control stages. The Notice's own funnel table (¶4.39) is the number to quote: PTE hard blocks suspended 58 orders / **US$248bn**; 291 orders / US$196bn proceeded to CitiSmart; 7 / US$7bn were rejected there; 284 / **US$189bn** were received. |
| Robinhood showed doubled balances to 4.2M customers for four years | Two different populations. Inaccurate **cash** balances: "either doubled … or inflated … by displaying buying power as 'cash'": >135,000 customers, Dec 2019 – Jun 2020. Doubled negative **buying power**: ~4.2 million customers, Sept 2016 – Sept 2020. |
| Robinhood's 2 March 2020 outage was a leap-year bug | FINRA's AWC says "a key firm system was overloaded, which caused a cascading failure of other systems". No primary source supports a date-handling cause. |
| The Revlon lenders got to keep the money | The Second Circuit **vacated** that judgment on 8 Sep 2022 (49 F.4th 42) on inquiry-notice and not-presently-payable grounds. |

**Retrieval note.** `sec.gov` returned HTTP 403 to automated requests while this catalogue was
compiled, on both the `/files/litigation/admin/YYYY/` and `/litigation/admin/YYYY/` URL forms. The
SEC orders cited here were read in full from retrieved copies verified against their own cover
pages (release number and date). URLs are given in the canonical `/files/…` form; they may need a
browser to open.

---

## The mapping table

One row per incident, sorted by failure class. `Rule motivated` states the single highest-value
rule; the per-incident files carry the rest.

`Rule motivated` is stated as a proposition, in the incident's own terms, and `Owning skill` says
where that proposition is a rule with a heading of its own. There are no rule ids anywhere in this
catalogue and there are none in the skills: a rule is cited by its name, and the ten cross-cutting
ones (*exact representation*, *rounding and conservation*, *operation identity*, *ambiguous
outcomes*, *durable dedupe*, *concurrency on authoritative state*, *authority*, *reconciliation*,
*hard limits*, *rollout*) are stated in full in `fin-money-core`. A domain skill cites them by those
exact names and adds only what its domain changes.

A few venue-side incidents are owned by `advanced/fin-matching-and-settlement`, which is not one of
the six installed skills; it ships in [`advanced/`](../advanced/README.md) for engineers whose code
**is** the venue and is installed deliberately.

**Failure classes.** **A** Representation · **B** Sentinel escape · **C** Rounding & precision ·
**D** Missing conservation · **E** Identity & idempotency · **F** Indeterminate outcome ·
**G** Provisional value made spendable · **H** Concurrency & ordering · **I** State divergence ·
**J** Change, deploy & configuration · **K** Missing or overridable control · **L** Pricing &
oracle integrity.

Rows in **bold** have a file in this directory.

| Incident | Date | Loss (as the source states it) | Class | Invariant violated | Agent-detectable? | Rule motivated | Owning skill |
|---|---|---|---|---|---|---|---|
| **[Interactive Brokers: negative WTI](interactive-brokers-negative-oil-2020.md)** | 2020-04-20 | $82,570,000 restitution + $1,750,000 penalty (CFTC 8432-21) | A | `price ∈ ℝ` (signed) across parse, store, display, order entry and margin, not `price > 0` | Yes | Never assume a price, rate or spread is non-negative; use a signed type end to end and assert every pricing model's domain | fin-money-core |
| **[Bitcoin value overflow (CVE-2010-5139)](bitcoin-value-overflow-2010.md)** | 2010-08-15 | 184,467,440,737.09551616 BTC created; 53 blocks orphaned; no lasting loss | A | `sum(outputs) <= sum(inputs) + subsidy` evaluated in a domain that cannot wrap | Yes | Bounds-check operands *before* the arithmetic, or use checked arithmetic; never validate a value that may already have wrapped | fin-money-core |
| **[Cetus `checked_shlw`](cetus-overflow-guard-2025.md)** | 2025-05-22 | ~$223M drained; ~$162M frozen; ~$61M likely unrecoverable | A | `checked_shlw(n)` reports overflow for **every** `n >= 2^192`; boundary `n == 2^192` must overflow | Yes | Prove an overflow guard at its exact boundary by construction: test `threshold-1`, `threshold`, `threshold+1` | fin-money-core |
| Samsung Securities "ghost shares" | 2018-04-06 | ~2.81bn shares issued (notionally ~₩112tn); ~₩1.86–1.9bn damages to the National Pension Service | A + D | `issued_shares_after <= authorised_shares`, enforced inside the issuance transaction | Yes | A numeric field whose unit depends on a sibling enum needs cross-validation; every issuance checks the resulting total against an authoritative ceiling in the same transaction | fin-ledger |
| Mizuho Securities / TSE: J-Com | 2005-12-08 | ~¥40.7bn (≈$345M at the December 2005 rate of ~118 JPY/USD); TSE held 70% at fault, ~¥10.7bn damages | A + K | `order_qty <= f(shares_outstanding, ADV)`; `cancel(order_id)` is total over the order state machine | Yes | Bound order quantity by instrument supply, not by a fixed ceiling; cancel must be a total function with a test in every reachable state | fin-exchange-integration |
| Deutsche Bank gross-vs-net payment | 2015 (and €28bn, 2018) | US$6bn paid, recovered the next day | A | `payment_amount` is derived from the trade record, never re-entered | Yes | Derive settlement amounts from the trade record; never accept human re-entry of a number the system already holds | fin-payments |
| BNP Paribas / "Armin S." | 2015 | ~€160M in dispute | A + J | `unbooked_trade_age < bound`, alerted; `instrument.price` validated against a reference at setup | Partly | Alert on unbooked-trade **age**, not count, because a booking backlog removes the reconciliation that would catch a pricing error on day one | fin-ledger |
| CME Whaley → Bachelier model switch | 2020-04-22 | no direct loss attributable | A | `reachable_state_space ⊆ model_domain` | Partly | When a model's support excludes a reachable state, change the model; do not clamp the input to keep `log(F/K)` defined | fin-exchange-integration |
| **[Goldman Sachs options router](goldman-options-router-2013.md)** | 2013-08-20 | ~$38M realised after busts; "up to a potential $500 million" exposure (¶5); $7,000,000 penalty | B | `∀ outbound order: price is a real quoted price, not a placeholder`; `validation(pre-market) ⊒ validation(continuous)` | Yes | An internal un-priced object must be structurally unable to reach a venue; the off-hours branch must be at least as strict as the continuous one, and bands must be computed per instrument | fin-exchange-integration |
| **[Citigroup Global Markets (CGML)](citigroup-cgml-2022.md)** | 2022-05-02 | US$48m (FCA ¶2.8); £27,766,200 penalty (£39,666,000 before discount) | B + A + K | `price_feed_unavailable ⇒ every computation that reads it fails`; the confirmation renders a **derived** notional or refuses to render | Yes | A missing external price must fail the computation that depends on it; never substitute `-1`, `0`, or a null coerced to zero into a price variable | fin-exchange-integration |
| Nomad bridge trusted root | 2022-08-01 | ~$190M in 960 transactions / 1,175 withdrawals | B | a trusted-set membership test must not be satisfiable by the type's zero value | Yes | Never use a value that is also a legal domain value as an "unset/untrusted" sentinel; use a presence flag or a type that cannot represent it | fin-onchain |
| Robinhood zero-value mark-to-market | 2018-08-10 | part of the $57M fine + $12.6M restitution | B | `price_unavailable ⇒ the valuation raises`, never returns `0` | Yes | A missing, stale or unavailable price must propagate as an explicit absent value; zero is a legal price, "unknown" is not zero | fin-ledger |
| **[Citigroup $81 trillion credit](citigroup-81-trillion-2024.md)** | 2024-04 (reported 2025-02) | $0 realised, reported to the Fed and OCC as a "near miss" | B + K | `0 < amount <= ceiling(product, account, operator)` at the point of entry; no amount field is pre-populated with a value that is valid on submit | Yes | Enforce a finite, business-derived maximum on every operator- or API-supplied amount and **reject**, not warn; never pre-populate an amount | fin-payments |
| May 6 2010: stub quotes and trade breaks | 2010-05-06 | >20,000 trades / 5.5 million shares broken; almost two-thirds executed below $1.00 | B + K | `∀ marketable order: fill within collar(independent reference)`; downstream systems accept `execution.status = BUSTED` retroactively | Yes | Collar every marketable order against an independent reference, because a taker cannot distinguish a sentinel quote from real liquidity at top of book | fin-exchange-integration |
| **[Balancer V2 Composable Stable Pools](balancer-v2-rounding-2025.md)** | 2025-11-03 | ~$128M across 8+ chains (reported range $116M–$128.64M) | C | rounding favours the pool on **both** legs; `invariant_after >= invariant_before` measured on *settled*, not transient, balances | Partly | Choose rounding direction per operation as a function of who is credited; never apply one global direction to both legs of an exchange | fin-onchain |
| Onyx Protocol (and Hundred Finance, Midas Capital) | 2023-11-01 | $2.1M (1,164 ETH) at Onyx; >$10M across the class in 2023 | C | `deposit(x > 0)` mints `> 0` shares; `totalAssets()` is an accounted quantity, not `token.balanceOf(this)` | Yes | Never compute `shares = assets * totalSupply / totalAssets` without guarding `totalSupply == 0` and a `totalAssets` inflatable by direct transfer | fin-onchain |
| Compound COMP over-distribution (Proposal 62) | 2021-09-29 | ~168,000 COMP (~$50M) claimed; bounded worst case 280,000 COMP (~$80–83M) | C + K | `claimed(user) <= accrued(user)` and `sum(claimed) <= sum(accrued)`; a value-distributing path has a pause exercisable faster than the loss accrues | Yes | Unit-test every `>` / `>=` in a payout or accrual guard **at equality**; every path that distributes value needs a kill switch | fin-onchain |
| **[Bitcoin duplicate-input inflation (CVE-2018-17144)](bitcoin-duplicate-input-2018.md)** | disclosed 2018-09-17 | no known mainnet exploitation; supply cap was breakable on 0.15.0–0.16.2 | D + J | all inputs of a transaction are pairwise distinct, checked on **every** path that can accept a block | Yes | When removing a check as "redundant", record the invariant it enforced and add a test that fails without it; never remove both the check and the assertion that detects its absence | fin-onchain |
| **[Euler `donateToReserves`](euler-donate-to-reserves-2023.md)** | 2023-03-13 | ~$197M (substantially returned over the following weeks; the return is not in the cited rekt.news source) | D | `after every state change: healthScore(account) >= 1` | Yes | Route every balance-mutating path through a single function that ends in the solvency assertion; a new function that moves value without it *is* the bug | fin-onchain |
| **[Mt. Gox: transaction malleability](mt-gox-malleability-2014.md)** | 2014-02 | ~650,000–850,000 BTC claimed lost; malleability measured at 1,811.58 BTC network-wide in the preceding year | E | the reconciliation key is immutable and not counterparty-controllable; balances derive from authoritative external state | Yes | Never key reconciliation on an identifier a counterparty can change; reconcile against authoritative state, never against your own log of what you believe you issued | fin-onchain |
| Wintermute / Optimism 20M OP | 2022-05-27 (sent) / 2022-06-05 (taken) | 20,000,000 OP (~$27.6M at the time) | E | before transferring value to an address on chain C, assert code exists there and is controlled by the intended party | Yes | An address is scoped to a network and an environment; a `CREATE`-derived address is a function of `(factory, nonce)` only and is re-derivable by anyone | fin-onchain |
| Solana durable-nonce double execution | 2022-06-01 | ~4.5h halt; the underlying capability is a double-spend primitive | E | `apply(tx)` is idempotent across **all** code paths; one shared executed-set | Yes | Ensure a given external event can be applied by exactly one code path; a real-time handler and a replay job must write through a common idempotent apply function | fin-money-core |
| Solana blocks tracked by slot number | 2020-12 | outage; forks unreconcilable | E | object identity is keyed on content, not position | Partly | Key deduplication on a caller-supplied idempotency key or a content hash, never on a positional index or a tuple of business attributes | fin-money-core |
| Solana JIT infinite recompile loop | 2024-02-06 | ~5h halt | E | `cache_key(entry)` is unique per entry and advances on rewrite | Partly | A cache or dedup key that can be constant across distinct entries is a defect, even when the entries differ | fin-money-core |
| **[Robinhood order-entry outage](robinhood-order-entry-outage-2020.md)** | 2020-03-09 | ~166,000 orders stuck "pending"; $5,213,557.98 outage restitution within the FINRA action's $57M fine + $12,598,445.16 restitution | F + J | `timeout ⇒ state = INDETERMINATE`, resolved by querying authoritative state keyed by a client-generated order ID | Yes | Never infer from a timeout, disconnect or error that an order was not created, modified or cancelled; and test a counterparty's protocol change against a conformance suite before go-live | fin-exchange-integration |
| **[Tokyo Stock Exchange: arrowhead](tokyo-stock-exchange-arrowhead-2020.md)** | 2020-10-01 | no published direct trading loss; the first full-day cash-equity outage since arrowhead launched | F + J | `halt ⇒ matching engine quiesced ∧ every execution delivered or explicitly voided` | Partly | Halting by severing the network is not a halt; the halt must quiesce the engine and the design must define the fate of in-flight and unreported executions | advanced/fin-matching-and-settlement |
| Nasdaq UTP SIP "flash freeze" | 2013-08-22 | trading halted in all Nasdaq-listed securities for 3h11m; no published aggregate loss | F + K | `∀ client connection: message_rate <= per_port_quota`, server-enforced; the reject path is budgeted separately from the success path | Partly | Rate-limit and apply backpressure per client connection; bound the cost of the error path; exercise failover under saturation, not only under clean failure | advanced/fin-matching-and-settlement |
| Solana forwarder queue OOM | 2021-09-14 | ~17h with no settlement finality | F | every queue in the value path has a bounded capacity and a defined shed/reject policy | Yes | No unbounded in-memory queue on any ingest path that carries value | fin-money-core |
| UK banking sector outage dataset | 2023 – early 2025 | 803+ hours of unplanned outage across 158+ incidents at 9 firms; Barclays confirmed 56% of online payments failed in one incident | F | n/a, base-rate evidence, not a single defect | No | Design the money path assuming the rail is unavailable: client-generated idempotency key, durable queue, reconcile on recovery | fin-payments |
| Kraken: spendable credit before finality | 2024-06 | ~$3M taken across three accounts; fully recovered | G | `spendable_balance <= Σ settled_credits − Σ settled_debits` | Yes | Never credit a spendable balance before the funding leg is final; provisional credit is a distinct, non-withdrawable ledger state, not a flag | fin-ledger |
| **[Revolut US: refunds on declined transactions](revolut-us-refunds-2022.md)** | 2021-12 → spring 2022 | ~$23M taken, ~$20M net (FT reporting), roughly two-thirds of Revolut's 2021 net profit | G + I | `∀ refund r: ∃ capture c with r.amount <= c.amount ∧ c.state == CAPTURED` | Partly | A refund must be funded from the capture it reverses, never from house funds when two networks' messages disagree; run three-way reconciliation on a cadence | fin-payments |
| **[FTX: `fiat@ftx`, `borrow`, `allow_negative`](ftx-ledger-and-risk-parameters-2019.md)** | 2019-07 → 2022-11 | ~$8bn hole in customer fiat | G + D | `sum(customer_balances) <= assets_actually_custodied`; every balance credit traces to a settlement event; every risk-parameter change has an immutable audit record | Yes | Never introduce a per-account flag that exempts an account from a solvency, credit-limit or liquidation check; derive the ledger credit from a settlement event, never an operator action | fin-ledger |
| **[NASDAQ: Facebook IPO cross](nasdaq-facebook-ipo-2012.md)** | 2012-05-18 | $10,000,000 penalty; $62M accommodation fund; $10.8M error-account profit; $35.3M haircut → $26.5M NES net-capital deficiency | H | a revalidation pass consumes the **entire** pending queue, or the input set is frozen before computing; `ack(cancel) ⇒ honoured or retracted to the member`; `∀ published quote: bid <= ask` | Yes | Freeze the input set before computing, or drain the whole pending queue into one recomputation, because a pass that consumes one event is a livelock; and never delete a correctness check to force completion | advanced/fin-matching-and-settlement |
| May 6 2010: internaliser repricing chase | 2010-05-06 | part of the broken-trade volume; internalisers were the sellers for almost half of it | H | a repricing retry loop has an absolute floor/ceiling independent of the reference it chases | Yes | A retry loop that chases a moving reference price must carry an absolute bound that does not move with the reference | fin-exchange-integration |
| **[Robinhood: displayed balances and labels](robinhood-displayed-balances-2016.md)** | 2016-09 → 2021-05 | $57,000,000 FINRA fine + $12.6M restitution, the largest FINRA penalty at the time | I | `displayed(x) == ledger(x)` for every monetary quantity, asserted in test; `label(field)` names the exact financial quantity | Yes | A number shown to a user must be read from the authoritative ledger, not recomputed on a second path; and a field's label is an assertion about *which* quantity it holds | fin-ledger |
| TSB core-banking migration | 2018-04-20 | £32.7M customer redress; FCA £29.75M + PRA £18.9M = £48.65M (£69.5M before a 30% discount) | I + J | `config(dc_A) ≡ config(dc_B)` for every setting on the value path | Partly | Assert environment and configuration equivalence as an automated continuous check, never as a design intention | fin-money-core |
| Bitcoin March 2013 chain fork (BIP-50) | 2013-03-11 | ~6h of ambiguous settlement finality; one double-spend | I | `validate(block, node_A) == validate(block, node_B)` for all nodes | Partly | No configurable resource limit, timeout or local state may influence whether a value-bearing record is accepted as valid | fin-onchain |
| **[Knight Capital: SMARS](knight-capital-2012.md)** | 2012-08-01 | $460,000,000 loss (SEC ¶17); $12,000,000 penalty | J + D + I | `Σ child_qty(parent) <= parent.qty`; `count(orders_out) / count(orders_in) <= K` at the router; every deployed node agrees on a flag's semantics | Partly | Never repurpose a flag while a deployed consumer of the old meaning exists; and when moving a counter another component uses as a termination condition, identify every reader and prove each still terminates | fin-money-core |
| RBS / NatWest / Ulster Bank: CA-7 batch scheduler | 2012-06-17 | FCA £42M + PRA £14M = £56M; 6.5M+ customers over weeks | J | `rollback(upgrade(S)) == S`, demonstrated by a test in a production-matching environment | Partly | Treat rollback/uninstall as a code change requiring its own tested path; never uninstall a component in production on a path that has never been exercised | fin-money-core |
| **[NASDAQ: Trade Through / SHO Through controls](nasdaq-price-test-controls-2011.md)** | 2011-10 and 2012-08 | 2,004 trade-throughs; >4,400 non-compliant short sales; part of the $10,000,000 penalty | J + K | a control deprived of its input fails closed and alarms; an acknowledged alert about a **missing component** re-fires on every subsequent start | Yes | Fail closed when a control's input disappears; never let a configuration edit turn a gate into a no-op; and an acknowledgement must never permanently suppress a missing-component alert | advanced/fin-matching-and-settlement |
| EDGA / EDGX: order-type disclosure | 2015-01-12 | $14,000,000, joint and several | J | published behaviour ≡ implemented behaviour, reconciled by a test that fails on divergence | Partly | A spec change with no corresponding code change is a defect; reconcile documented and implemented behaviour by test (NASDAQ ran a randomization step it had removed from its rules for 5½ years) | advanced/fin-matching-and-settlement |
| Knight Capital LMM desk: DR test data | 2011-10 | "nearly $7.5 million" (SEC ¶33) | J | `market_data.source == PRODUCTION ∧ market_data.age < maxStaleness` at every quote generation | Partly | Assert provenance and freshness of market-data inputs in production paths; test and DR fixtures must be structurally unreachable from a quoting path | fin-verification |
| Curve / Vyper reentrancy-lock miscompilation | 2023-07-30 | ~$70M gross (~$52M net after whitehat returns) | J | `@nonreentrant("lock")` mutually excludes all functions bearing that lock name, a property of the *toolchain*, not the source | **No** | Pin and record the exact compiler/toolchain version for value-bearing artifacts, and run invariant tests against compiled output, not source | fin-verification |
| BATS Global Markets: its own IPO | 2012-03-23 | IPO withdrawn; no published dollar figure | J | rarely-exercised auction paths are exercised at production scale before the event they exist for | **No** | Rehearse opening/auction/listing paths at production scale (*pattern only: the mechanism is not publicly established and no code-level rule is derived from it*) | fin-verification |
| **[Citibank / Revlon erroneous wire](citibank-revlon-2020.md)** | 2020-08-11 | ~$894M wired; ~$385M returned voluntarily; ~$500M litigated (2d Cir. vacated the lenders' judgment) | K | the money-moving action requires an affirmative act; a confirmation states the amount and the destination | Partly | Make the safe outcome the default: require an explicit act to *send*, never an explicit act to *suppress sending*; and never require N independent fields to express one semantic outcome without a consistency check | fin-payments |
| Hyperliquid JELLY | 2025-03-26 | HLP unrealised loss peaked ~$13.5M against a ~$290M vault; closed ~+$703k after an off-market settlement | K + L | `max_backstop_exposure(instrument) <= configured_cap(instrument)`, the cap a function of instrument liquidity | Partly | Cap the notional a liquidation engine can force onto a backstop pool per instrument, and gate low-liquidity listings behind that cap | advanced/fin-matching-and-settlement |
| **[May 6 2010: the Sell Algorithm](flash-crash-2010.md)** | 2010-05-06 | no single-firm figure is published; 75,000 E-Mini contracts (~$4.1bn) sold in 20 minutes | L | `hasPriceBound ∧ hasTimeBound`; `rate_input ⊥ own_executions` | Yes | An execution algorithm must carry a price bound and a time bound in addition to any participation target, and must never drive its rate from a metric its own fills inflate | fin-exchange-integration |
| Compound DAI oracle liquidations | 2020-11-26 | ~$89M liquidated; 124 of 225,793 users affected | L | `abs(oracle_price − median(independent_venue_prices)) <= deviation_bound` and a staleness bound, before any liquidation | Yes | Derive any price used for liquidation, margin or settlement from multiple independent venues with a deviation guard and a staleness bound; never from a single venue | fin-onchain |
| Mango Markets oracle manipulation | 2022-10-11 | >$110M drained (CFTC); ~$67M returned | L | `cost_to_move_oracle(Δ) > value_extractable(Δ)`; `borrowing_power(asset) <= f(market_depth(asset))` | Partly | Never use a mark price a position holder can move for less than the value they can extract; bound the notional a market's own oracle can collateralise | fin-onchain |
| Robinhood: PFOF routing vs best execution | 2015 – 2018 | $34,100,000 of customer harm net of commission savings; $65M SEC penalty | L | `∀ routed order: realised price >= benchmark(NBBO at receipt)`, measured and reported | Partly | A smart order router's objective function must be stated and tested against a best-execution benchmark, and must never rank venues by payment received | fin-exchange-integration |

**51 entries. 20 have files.**

---

## Failure-class summary

| Class | Count | The lesson, once |
|---|---|---|
| **A. Representation** | 9 | A monetary quantity is a number *and* a unit *and* a domain; wherever the type carries only the number, the unit and the domain are enforced by hope. |
| **B. Sentinel escape** | 6 | A placeholder is only a placeholder inside the subsystem that agreed on it; at every boundary it is an ordinary value, and it will be treated as one. |
| **C. Rounding & precision** | 3 | Rounding direction is an economic decision about who is credited, not a formatting default, and losses too small to see per operation compose inside one atomic transaction. |
| **D. Missing conservation** | 2 | Every transformation that can turn one thing into many, or credit what was not debited, needs something that compares the two sides and halts, not logs, on a break. |
| **E. Identity & idempotency** | 5 | If the key you reconcile on can be changed by a counterparty, reused across environments, or derived from a position rather than content, then "I don't see it" and "it didn't happen" are different statements your code is treating as one. |
| **F. Indeterminate outcome** | 5 | A timeout, a severed network, or a full queue tells you nothing about whether the money moved; only authoritative state, queried by a key you generated, does. |
| **G. Provisional value made spendable** | 3 | "Credited" is not "settled", and the moment a system lets the two share a field is the moment it can spend money it does not have. |
| **H. Concurrency & ordering** | 2 | A computation over a set that keeps mutating must freeze the set or drain it whole; anything that makes less progress per pass than the arrival rate is a livelock waiting for your busiest day. |
| **I. State divergence** | 3 | The instant two code paths compute the same quantity, they will disagree, and the one the user reads is usually not the one the ledger holds. |
| **J. Change, deploy & configuration** | 7 | Correctness is a property of the deployed fleet, not of the diff: partial deploys, untested rollbacks, config drift between "identical" environments and specs that no longer describe the code are all ways for correct source to run wrong. |
| **K. Missing or overridable control** | 2 | A limit that can be clicked through, a ceiling that does not exist, and an alert nobody owns are not controls; at least one bound on every money path must be non-overridable and automatic. |
| **L. Pricing & oracle integrity** | 4 | The number a system marks, margins, liquidates or routes against is an input like any other, and it is wrong exactly when a single venue, a thin market, or the system's own trading is what produces it. |

---

## Incidents an AI coding agent could NOT have caught

This section defines the suite's boundary. Claiming universal coverage would be false and would
make every other claim here less believable.

Across the 51 entries: **30 Yes · 18 Partly · 3 No.** The three "No" verdicts and the reason each
is genuinely out of reach:

**Curve / Vyper (2023-07-30, ~$70M gross).** The source was correct. Vyper 0.2.15, 0.2.16 and
0.3.0 mis-allocated named re-entrancy locks, so two functions declared with the same lock name did
not in fact share one. An agent reviewing that contract would have read `@nonreentrant("lock")` on
both functions and found nothing, because there was nothing to find above the compiler. This is the
strongest case in the catalogue for the limits of diff review, and the only defences (pin the
toolchain version, test invariants against compiled bytecode rather than source) are process
rules, not review findings.

**BATS's own IPO (2012-03-23).** No regulator order or vendor notice describes the defect beyond
"a software bug in the auction path for symbols A–BFZZZ". Because the mechanism is not publicly
established, no code-level rule can honestly be derived from it. It is retained for the pattern,
the rarely-exercised opening path failing at scale on the one day it matters, and for nothing
more.

**The UK banking outage dataset (803+ hours / 158+ incidents / 9 firms).** Not a defect at all.
It is base-rate evidence that the rails are unavailable far more often than product designs assume,
which is a reason to build idempotent, queued, reconciling clients, but there is no diff to review.

### The "Partly" cases, and what specifically was invisible

An agent reviewing a diff sees the diff. It does not see the fleet, the estate, the vendor, the
operator, or the counterparty. Concretely:

- **Knight's deployment.** The 2012 RLP diff is reviewable: a repurposed flag with a dead-but-
  callable old consumer is statically detectable, and so is the 2005 diff that moved the
  cumulative-quantity counter out of the generator that used it as a termination condition. What no
  agent could see is that a technician copied the build to seven of eight servers. That is a
  deployment-verification failure, and it is why `fin-verification` exists as a separate skill.
- **TSE's vendor manual.** An agent cannot know that Fujitsu's manual said auto-switchover happens
  "regardless of the NAS setting" while the product specification had silently changed at
  generation 2 in 2015. It *can* flag the two design defects in the repository: a failover test
  that injects a different fault class than the one being defended against, and a halt routine that
  cuts the transport without quiescing the engine.
- **Citibank / Revlon's shared mental model.** Three humans independently held the same wrong belief
  about which fields suppressed a wire. Review multiplies a misconception; it does not test one. An
  agent *can* see the code shapes that made the misconception fatal: a default branch that sends
  externally, one semantic outcome expressed across three independent fields with no consistency
  check, and a confirmation string with no interpolated amount or destination.
- **TSB and RBS.** An agent cannot inspect a data-centre estate or a batch-scheduler upgrade
  performed by an operations team. It can flag environment configuration asserted by intent rather
  than by test, a cutover with no reverse path, and a migration suite that runs against one
  environment.
- **Mango and Hyperliquid.** An agent can flag a collateral configuration with no per-asset borrow
  cap, or a liquidation path with no backstop cap. It cannot price market depth, which is the
  quantity that decides whether the configuration is safe.
- **Nasdaq's SIP.** An agent can see a message loop with no per-client rate limit, no bounded
  reject path and no backpressure. It cannot predict that a specific counterparty's
  connect/disconnect sequence will produce ~26,000 updates per port per second against a design of
  ~10,000.
- **FTX.** The shape (`if (account.allow_negative) skip_liquidation()`, and a `borrow` field with
  no system-wide ceiling and no audit record) is exactly what a reviewer should refuse. Fraud is
  not detectable; the code that made the fraud expressible is.
- **Goldman's operators.** The `$1.00` placeholder, the weaker pre-market validator and the untested
  I–K stripe are all visible in the repository. That control personnel repeatedly lifted a working
  circuit breaker between 8:44 and 9:32 without the authorisation their own written policy required
  is not.
- **Balancer.** An agent cannot re-derive AMM invariant mathematics. It can flag the three things
  that made the loss possible: one rounding helper applied to both directions of an exchange, a
  safety comment predicated on a value a subclass can override, and a batch primitive that settles
  only at the end of a multi-operation sequence.

### What this boundary means for the suite

Roughly two-thirds of the money-loss surface in this catalogue is reachable by diff review. The
remaining third is reachable only by rules about **deployment, configuration, reconciliation and
proof**, which is why the suite is not a linter. A skill set that only taught diff review would
have caught Cetus, Nomad, Euler, Onyx, both Bitcoin bugs, the Citi amount bound, Goldman's
sentinel, Robinhood's zero mark and Knight's flag, and would have missed Knight's eighth server,
TSB's data centres, RBS's rollback, TSE's failover and Curve's compiler entirely.

---

## Deliberately excluded

- **Pure access-control breaches.** Wormhole (2022-02, ~$326M) turned on `verify_signatures` using
  a deprecated API that did not check the passed account was the genuine instructions sysvar. That
  is a real and important bug, and it belongs to a smart-contract security suite. Trail of Bits'
  `building-secure-contracts` is the right neighbour. Including it here would blur the distinction
  the project rests on.
- **Risk-model failures that are not software defects.** Everest Capital's collapse on the SNB
  de-peg (2015-01-15) and Robinhood's January 2021 DTCC collateral call are frequently listed
  alongside these incidents. Neither is a defect: one was a leveraged position against a
  central-bank peg, the other a correctly computed VaR charge. The in-scope thread is narrow and is
  already covered: a risk engine that cannot *represent* a market state (a peg break, a negative
  price) is a representation bug, and that is Interactive Brokers and CME.
- **Incidents the research could not verify.** Binance, BitMEX, Deribit, OKX, Huobi and the 3Commas
  API-key incident were all reachable in outline and none to the standard used here. They are
  absent rather than asserted from recollection. The 3Commas case in particular leaves a real gap:
  this catalogue has **no verified incident covering delegated-trading-authority scope**.
- **Duplicate-charge postmortems.** No first-party "we double-charged customers" postmortem from a
  named processor or bank could be verified. The duplicate-payment failure mode is motivated here
  by Solana's durable-nonce double execution and by idempotency reasoning, and no incident was
  invented to fill the space.

## Known gaps and open questions

- **The PRA's £33,880,000 fine against CGML** does not appear in the FCA Final Notice, but it *is*
  stated in the FCA's own press release of 22 May 2024 ("the PRA has also fined CGML £33,880,000").
  The PRA Final Notice itself was not retrieved; cite the amount to the FCA press release.
- **UBS's ~$356M Facebook IPO loss** is not in SEC 34-69655; the order quantifies only NASDAQ/NES
  figures and the $62M accommodation programme.
- **CME's Whaley → Bachelier advisory text was never read directly** (the CME pages are
  JS-rendered and the PDFs returned 403). Model names, direction, products and dates are
  corroborated across independent reports and are consistent with the mathematics, but any skill
  quoting the advisory should re-fetch Chadv20-152 / 160 / 171 first.
- **Kraken's code-level mechanism is not public.** The CSO described the effect precisely
  ("receive funds in their account without fully completing the deposit"); no postmortem describes
  the code.
- **Compound Proposal 62's exact code site** is attributed to auditor Kurt Barry (`>` where `>=`
  was required, in two places) in every public account, but no official postmortem or diff naming
  the function was located. The class is solid; the exact line is not verified.
- **Mango Markets' legal aftermath is disputed.** Eisenberg was convicted in 2024; the research
  could not verify the 2025 post-trial history. Cite the CFTC complaint for the mechanism only.
- **Bank of England RTGS/CHAPS outage (2014-10-20) and the Deloitte independent review**: the
  best available UK payment-infrastructure postmortem, and it could not be retrieved. Worth a
  second pass.
- **Bank of Ireland (2023-08) and the JPMorgan Chase "infinite money glitch" (2024-08)** are both
  on-topic (provisional credit versus settled funds, ATM dispensing against unsettled balances)
  and neither could be verified from a primary source. Flagged rather than included.
