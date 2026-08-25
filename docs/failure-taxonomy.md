# The Financial Failure Taxonomy

**What it is for.** One question: *given a diff, what kind of wrong can this be?* A class earns its place
only if it has a defining question you can ask of a diff, a check that would have caught three or more cited
failures in two or more domains, and no collapse into a neighbouring class when you try to use it.

**What is being classified.** Not "bugs in financial software" but *financial correctness* failures: the
system produced an incorrect economic outcome while every component behaved exactly as specified. No attacker
is required for any mechanism here.

**Its relationship to the skills.** This file classifies. The skills carry the rules. Each class below names
the money-core invariant that owns it, and the domain skill that specialises it; the rule itself is stated
once, in the skill, and is not restated here.

## Derivation, in short

Nine candidate classes came out of a trading-only incident review: representation, identity, state,
concurrency, ordering, partial-failure, reconciliation, pricing-execution, authority. Tested against the
incident corpus in `incidents/`, that list became thirteen.

- **representation split three ways**: F1 representation, F2 rounding and residue, F3 sentinel and absence.
  Bitcoin 2010 is representation with no rounding, the arithmetic was exact and `uint64` could not hold the
  sum. Balancer is rounding with an adequate type. Nomad is a sentinel where the type is fine. The fixes live
  in different places.
- **partial-failure split two ways**: F5 indeterminacy (one call's return value) and F13 change and config
  (the estate the code runs on). F5 is fully diff-visible; F13 mostly is not.
- **ordering separated from concurrency**: F7 is two writers racing on one authoritative quantity, F6 is one
  consumer receiving events out of occurrence order. The checks share nothing.
- **state dissolved.** A container, not a mechanism. Its four sub-modes each belong elsewhere.
- **conservation (F9) and settlement state (F8) added.** The candidate list's largest holes: F9 is the only
  class that catches *value appeared* as distinct from *value was mis-stated*, and F8 is the case where the
  amount is right, the identity is right, the arithmetic conserves, and the money is not there yet.

**The classes are not disjoint, and that is the point.** A class is a question you ask, not a bucket an
incident falls into. The count of classes a diff fails is the best severity proxy this corpus offers.

| Incident | Classes | Loss as its source states it |
|---|---|---|
| Knight Capital SMARS, 2012-08-01 | F13 + F9 + F10 | $460,000,000 (SEC ¶17); $12,000,000 penalty |
| Citigroup / CGML, 2022-05-02 | F3 + F1 + F11 | US$48m (FCA ¶2.8); £27,766,200 penalty |
| FTX `fiat@ftx` / `allow_negative`, 2019 to 2022 | F8 + F9 + F11 | ~$8bn hole in customer fiat |
| NASDAQ Facebook IPO cross, 2012-05-18 | F7 + F6 + F10 | $10M penalty; $62M accommodation fund |
| Balancer V2, 2025-11-03 | F2 + F13 | ~$128M across 8+ chains |

Nine of the thirteen are owned by `fin-money-core`. That is why the core skill exists, and why a domain skill
must name a real API, error code or schema object rather than restate the general theory.

## The thirteen classes

### F1 · Representation: *can the type hold the fact?*

**Ask of the diff:** does every amount carry its own unit, scale and full domain, and does every check on it
run where it cannot already have wrapped or truncated?

**Mechanism.** A number crosses a module, storage or wire boundary carrying less information than the fact it
represents. The domain is too narrow (a price assumed non-negative, a sum that wraps before its own bounds
check); or the unit is carried by convention rather than by the type (quantity vs notional, shares vs
currency, gross vs net, minor vs major); or the scale is hardcoded where it is runtime metadata; or the
field's *name* asserts one financial quantity while the value is another.

- **Bitcoin value overflow, CVE-2010-5139** (2010-08-15): `sum(outputs)` wrapped `uint64` *before*
  `outputs <= inputs` ran, so the check compared the wrapped value and passed. Exactly `2^64` satoshi created.
- **Interactive Brokers, negative WTI** (2020-04-20): `price > 0` assumed across parse, store, display, order
  entry and margin. $82,570,000 restitution + $1,750,000 penalty (CFTC 8432-21).
- **Citigroup Global Markets** (2022-05-02): a basket intended as $58m of *notional* entered against a
  *quantity* field; nothing downstream re-derived the other and cross-checked. US$48m loss.
- **Samsung Securities "ghost shares"** (2018-04-06): a dividend denominated in won issued as shares.
- **freqtrade stores every money field as SQL `Float`** (`freqtrade/persistence/trade_model.py:95-117`) while
  aggregating in arbitrary precision. The exposure is not the arithmetic, it is the storage and wire boundary.

**Owner.** *exact representation*, in `fin-money-core`. Every domain skill instantiates it.

### F2 · Rounding and residue: *when the exact value is not representable, who gets the difference?*

**Ask of the diff:** does every rounding call site name its mode, and is the direction a function of who is
credited rather than a global default?

**Mechanism.** Exact arithmetic is impossible somewhere, and the code makes an unnamed choice. Three shapes:
one direction applied to both legs of an exchange, so one side is systematically favoured; a remainder nobody
accounts for; a comparison at the exact boundary where the direction changes. All three are invisible per
operation and material in aggregate.

- **Balancer V2 Composable Stable Pools** (2025-11-03): `_upscale` applied `mulDown` "independently from the
  direction of the swap", safe only while the scaling factor was `1e18`. ~$128M across 8+ chains. The attacker
  packed 65 micro-swaps, some as small as 17 units, into one `batchSwap`.
- **Onyx Protocol** (2023-11-01): `shares = assets * totalSupply / totalAssets` with `totalSupply == 0`
  unguarded and `totalAssets` inflatable by direct transfer. $2.1M; >$10M across the class in 2023.
- **Compound COMP over-distribution, Proposal 62** (2021-09-29): `>` where `>=` was required, in two places.
  ~168,000 COMP claimed.
- **CME pro-rata allocation:** pro-rata rounds down, cannot allocate everything, and must never be the last
  step in the pipeline.
- **Per-line VAT and cent-splitting:** `sum(rounded lines) != round(total)`, and the residue has to be posted
  to a named account.

**Owner.** *rounding and conservation*, in `fin-money-core`. Instantiated in `fin-onchain` (direction per
leg), `fin-ledger` (largest-remainder, FX residue).

### F3 · Sentinel and absence: *does a legal value also mean "there is no value"?*

**Ask of the diff:** is there a value in the domain meaning "unset", "unknown", "not yet priced" or "not
proven", and what happens to it at the next boundary?

**Mechanism.** A placeholder is only a placeholder inside the subsystem that agreed on it. At every boundary
it is an ordinary value of its type and will be treated as one. The mirror image is the same defect: a
genuinely absent input coerced to a legal value.

- **Nomad bridge** (2022-08-01): `trustedRoot` initialised to `0x00`, the same value meaning "not proven";
  `process()` then accepted every message. ~$190M across 1,175 withdrawals.
- **Goldman Sachs options router** (2013-08-20): an internal `$1.00` placeholder axe reaching a venue, on an
  off-hours path whose validation was weaker than the continuous one. ~$38M realised; $7,000,000 penalty.
- **Citigroup $81 trillion credit** (2024-04): an amount field pre-populated with fifteen zeros that was valid
  on submit. $0 realised; reported as a near miss.
- **Robinhood zero-value mark-to-market** (2018-08-10): an unavailable price returned `0` rather than raising.
- **May 6 2010 stub quotes:** a taker cannot distinguish a sentinel quote from real liquidity at top of book.

**Owner.** `fin-money-core`, adjacent to *exact representation*.

### F4 · Identity and replay: *is this the same fact, or a second one?*

**Ask of the diff:** what key decides two observations are the same economic event, who controls that key,
and does it exist before the effect it names?

**Mechanism.** Dedupe, reconciliation and idempotency all rest on one identity. It fails when the key is
counterparty-mutable, positional rather than content-derived, a tuple of business attributes two distinct
events can share, minted *after* the effect, or minted from a value that does not survive `ROLLBACK`. It also
fails when one external event can be applied by two code paths that do not share an executed-set.

- **Mt. Gox transaction malleability** (2014-02): reconciling against your own log of what you believe you
  issued, keyed on a counterparty-mutable txid. The mechanism is real, the attribution is not: Decker and
  Wattenhofer measured 421 conflict sets totalling 1,811.58 BTC network-wide for the year to 2014-02-07.
- **Solana blocks tracked by slot number** (2020-12): identity keyed on position, so two different blocks at
  one slot were one block and forks could not be reconciled.
- **Solana durable-nonce double execution** (2022-06-01): one transaction applied twice through two code
  paths. A double-spend primitive, ~4.5h halt.
- **Wintermute / Optimism** (2022-06): a `CREATE`-derived address is a function of `(factory, nonce)` only and
  is not a portable identity across chains. 20,000,000 OP.
- **Client order ID scope, per venue:** Binance Spot and Futures, OKX and Kraken enforce uniqueness only among
  *open* orders; Bybit rejects duplicates, which is not returning the original; Coinbase Advanced Trade is the
  only venue in the set with true create-order idempotency; Deribit has none. No vendor document states the
  divergence.

**Owner.** *operation identity* and *durable dedupe*, in `fin-money-core`. Scope tables live in
`fin-exchange-integration`, `fin-payments` and `fin-onchain`.

### F5 · Indeterminacy: *does the code know whether the effect happened?*

**Ask of the diff:** for every `catch`, `except` and `Err` around a value-moving call, what does the code now
believe about the world, and on what evidence?

**Mechanism.** A timeout, severed socket, 5XX, 429 or rate-limit rejection tells you nothing about whether the
counterparty acted. Treating it as "did not happen" and resubmitting pays twice; treating it as "did happen"
strands money. The third state, UNKNOWN, resolved by querying authoritative state for an identity you
generated, must be represented in the persisted record, not only in the retry logic.

- **Robinhood order-entry outage** (2020-03-09): ~166,000 orders stuck "pending", not cancellable, execution
  status unknown. >$5M outage restitution within a $70M total.
- **Binance's own documentation:** 5XX, `-1006` and `-1007` are documented as "execution status UNKNOWN";
  `-2013 NO_SUCH_ORDER` right after placement is not proof of non-creation; `-2021` on a cancel/replace means
  one leg succeeded.
- **Stripe:** 500s **are** cached, and Stripe states there is no client-side algorithm that resolves the
  outcome alone.
- **geth and Solana:** `already known` means success; a mined-but-reverted transaction consumes the nonce,
  burns gas and emits no logs; `getSignatureStatuses` must be queried with `searchTransactionHistory: true`.
- **ccxt's retry funnel:** the retry predicate in the single funnel every signed REST call passes through is
  `e instanceof OperationFailed` with **no HTTP-method or path discrimination**
  (`ts/src/base/Exchange.ts:6435`), and the re-sent request carries the identical `newClientOrderId`, which
  Binance scopes to open orders only.

**Owner.** *ambiguous outcomes*, in `fin-money-core`. Instantiated by `fin-exchange-integration`,
`fin-payments` and `fin-onchain`.

### F6 · Ordering and arrival: *is arrival order being read as occurrence order?*

**Ask of the diff:** does this consumer assume the sequence it receives is the sequence that happened?

**Mechanism.** One consumer, many producers, no total order: redelivery, a REST backfill interleaved with a
socket stream, a webhook queued before another and delivered after, a replayed pre-snapshot event. Damage
depends on path-dependence: position averaging is path-dependent, so an out-of-order fill corrupts entry price
permanently; a status field is not, but a late `pending` regresses a settled object and re-arms a money branch
under a fresh event id the dedupe table cannot catch. Two guards are required, legality and version.

- **NASDAQ Facebook IPO cross** (2012-05-18): the printed cross was computed against a book from 11:11 and
  printed at 11:30. Per SEC 34-69655 ¶26 the 19-minute staleness was an input backlog inside the livelocked
  primary, not a promoted lagging replica. >38,000 orders excluded.
- **Stripe webhooks:** no ordering guarantee; the signature timestamp is not an ordering key; a late
  `refund.created` regresses a settled refund to `pending`.
- **Venue fill delivery:** the same fill arrives on the stream *and* the poll; `CumQty` is cumulative across
  the whole replace chain; `PossDupFlag` and `PossResend` mean different things.
- **`nautilus_trader`:** `crates/model/src/orders/mod.rs:250` ships `(Canceled, Filled) => Filled`, annotated
  `// Real world possibility`. **"Terminal states must be absorbing" is false as written**: a cancel is a
  request, a fill is money.

**Owner.** `fin-money-core`, specialised by `fin-exchange-integration` and `fin-payments`.

### F7 · Concurrency: *can two writers touch this authoritative quantity at once?*

**Ask of the diff:** is there a read, a computation, and a write-back of a quantity that authorises something,
and what makes that atomic?

**Mechanism.** Lost update (read-modify-write on a balance under Read Committed); write skew (two rows guarded
by one invariant, neither transaction seeing the other's write); and the venue-side shape, an optimistic
revalidation loop whose per-pass progress is smaller than the arrival rate, which is a livelock rather than a
slow success. A race detector finds none of these: a lost update across transactions is not a data race.

- **NASDAQ Facebook IPO cross** (2012-05-18): each revalidation pass incorporated only the first cancellation
  received during the previous calculation (¶9, ¶20), so the loop could not converge; NASDAQ then deleted the
  validation check to force completion. $10,000,000 penalty; $62M accommodation fund. The popular fix is
  wrong: a retry ceiling would have *aborted* the cross. NASDAQ's own remediation (¶65) is to freeze the input
  set or drain the whole queue in one recomputation.
- **Materialised-balance drift:** `INSERT INTO entries...; COMMIT;` followed by a separate `UPDATE balances`
  is two transactions and drifts silently.
- **Nonce and key allocation:** a withdrawal signer with two writers mints two transactions at one nonce.
  Single-writer discipline plus a fencing token is the whole control.

**Owner.** *concurrency on authoritative state*, in `fin-money-core`. Specialised by `fin-ledger` and
`fin-onchain`.

### F8 · Settlement state: *is this value spendable before it is real?*

**Ask of the diff:** which balance authorises the spend, and what event moved value into it?

**Mechanism.** "Credited" and "settled" share a field. The provisional amount is a flag on one balance column
rather than a distinct state, so the same number credited on *observation* is debited on *spend*. Every
variant follows: crediting from an operator action instead of a settlement event; treating an inbound pending
as available; releasing a hold on a callback that never arrives instead of on intrinsic expiry; crediting an
L2 deposit on L2 block count rather than L1 batch finality.

- **FTX `fiat@ftx`** (2019-07 to 2022-11): deposits "posted to their FTX accounts, even though the fiat
  deposits actually remained in Alameda-controlled bank accounts" (¶¶47-49), credited manually by staff.
  ~$8bn hole in customer fiat.
- **Kraken, spendable credit before deposit finality** (2024-06): users could "receive funds in their account
  without fully completing the deposit". ~$3M taken, fully recovered.
- **Revolut US, refunds on declined transactions** (2021-12 to spring 2022): two networks disagreed about what
  "declined" meant and the delta was funded from Revolut's own balance sheet for months. ~$20M net.
- **Stripe refunds:** `refund.created` is **pending**, and `refund.failed` can arrive up to 30 days from the
  post date; a pending refund reserves nothing unless it counts against the ceiling.
- **L2 finality:** "12 confirmations" is folklore. Polygon has produced a 157-block reorg, and Ethereum went
  non-finalizing for over an hour on 2023-05-12.

**Owner.** `fin-ledger` (posted, pending, available), with `fin-payments` and `fin-onchain` for their own
finality events.

### F9 · Conservation: *do the two sides of this transformation agree?*

**Ask of the diff:** does this turn one thing into many, or credit something that was not debited, and what
compares the two sides before the transaction commits?

**Mechanism.** A transformation, fan-out, split, netting, conversion, issuance or minting with no structural
relationship between input and output. What distinguishes it from F10 is scope: conservation is an
*intra-transaction* predicate assertable at a chokepoint, and its absence lets value **appear** rather than
merely be mis-stated. The recurring code shape is one balance-mutating path that skips the assertion every
other path performs.

- **Knight Capital SMARS** (2012-08-01): 212 parent orders in, millions of child orders out, and nothing at
  the router compared the two. $460,000,000 in 45 minutes.
- **Euler `donateToReserves`** (2023-03-13): the single balance-mutating path that did not terminate in
  `checkLiquidity`. ~$197M, largely returned after negotiation.
- **Bitcoin duplicate-input inflation, CVE-2018-17144** (2018-09-17): 0.14 dropped a duplicate-input check as
  redundant; 0.15 separately rewrote away the `assert` that would have caught its absence. Two changes, years
  apart, different people.
- **Samsung Securities** (2018-04-06): `issued_shares_after <= authorised_shares` was not enforced inside the
  issuance transaction.

**Owner.** the conservation half of *rounding and conservation*, in `fin-money-core`. Specialised by
`fin-ledger`.

### F10 · Divergence and reconciliation: *does anything independent agree with this number?*

**Ask of the diff:** for every economic quantity this code reports or stores, who is the external authority,
what is the join key, and where does the comparison run?

**Mechanism.** Two representations of one economic fact, computed by different paths, with nothing
structurally requiring them to agree. The divergence is inevitable; the failure is that nothing notices.
Sub-shapes that matter: detection whose only action is to withhold output; a reconciliation broken on day one
so it gets muted; a booking backlog that removes the reconciliation which would have caught a day-one error;
and a mixed-purpose suspense account, where breaks go to become unattributable.

- **Revolut US** (2021-12 to spring 2022): no invariant tied the two payment systems together; the only
  control that fired was a partner bank's cash-position report, months later. ~$20M net.
- **Robinhood displayed balances** (2016-09 to 2021-05): `displayed(x) == ledger(x)` was never asserted.
  >135,000 customers with doubled negative cash; ~4.2 million with doubled negative buying power.
  $57,000,000 FINRA fine + $12.6M restitution.
- **NASDAQ's Execution App** (2012-05-18): reconciliation *detected* the divergence and its only action was to
  withhold output. No escalation, no notification, no halt.
- **BNP Paribas / "Armin S."** (2015): ~8,000 unbooked trades over a week removed the reconciliation that
  would have caught a pricing error on day one. ~€160M in dispute. Alert on unbooked-trade **age**, not count.
- **Knight's "33 Account"** (2012): unmatched fills, error positions and real positions in one bucket, so
  neither an automated limit nor a human could attribute exposure.

**Owner.** *reconciliation* and *authority*, in `fin-money-core`; cadence, break aging and halt semantics in
`fin-verification`. Where no external authority exists, authority is SELF and the proof burden moves before
deployment into replay and simulation.

### F11 · Bounds and authority: *what stops this, and can it be clicked through?*

**Ask of the diff:** is there at least one non-overridable automatic bound on this path, and how fast can it
be exercised relative to the rate the loss accrues?

**Mechanism.** A limit that warns is not a control; a per-item limit where an aggregate is required is not a
control; a limit present in one region and absent in another is not a control; a kill switch behind a
seven-day governance process is not a kill switch. The most expensive variant is the per-entity **exemption**,
a flag removing one account from the solvency, credit-limit or liquidation check. The UX variant is
default-send: the money-moving action is what happens when you do nothing.

- **Citigroup $81 trillion** (2024-04): no ceiling of any kind on an operator-entered amount where $280 was
  intended. Citi self-reported 10 near misses at or above $1bn in 2024 and 13 in 2023.
- **Citigroup / CGML** (2022-05-02): every meaningful limit soft; per-item limits where an aggregate was
  needed (349 line items); the hard block had existed in New York since 2013 and not in EMEA; a warning UI of
  18 visible lines with no forced scroll. Per ¶4.39, 291 orders / US$196bn proceeded.
- **FTX risk parameters:** `allow_negative = true` and `borrow = $65,000,000,000` on exactly one account, and
  because database logs were not kept the debtors could not determine when or by whom it was set.
- **Citibank / Revlon** (2020-08-11): suppression required setting three fields; all three humans believed one
  sufficed; the confirmation named neither the amount nor the destination. ~$894M wired.
- **Compound Proposal 62** (2021-09-29): "there are no admin controls or community tools to disable the COMP
  distribution", and a patch required a seven-day governance process.
- **May 6 2010 Sell Algorithm:** a participation target with no price bound and no time bound; 75,000 E-Mini
  contracts in 20 minutes, at a rate driven by a volume metric its own fills inflated.

**Owner.** *hard limits*, in `fin-money-core`. Knight emitted 97 "Power Peg disabled" emails to a group Knight
"did not design ... to be system alerts", and per SEC 34-70694 ¶19 n.6 those emails came from orders distinct
from the 212 that caused the loss, which is the general case for pre-open canaries.

### F12 · Valuation and input integrity: *who produced the number you are deciding against?*

**Ask of the diff:** for every price, mark, rate or reference this code reads, what is its provenance, how old
is it, and can the party who benefits from the decision move it?

**Mechanism.** The number you value, margin, liquidate, route or settle against is an input like any other,
and it is wrong exactly when a single venue, a thin market, a stale cache, a test fixture, or the system's own
trading is what produces it. Four shapes: single-source with no deviation guard; self-referential, where the
position holder can move the mark for less than they can extract; stale, present but old with no age check;
and out-of-domain, a model whose support excludes a reachable state.

- **Compound DAI oracle liquidations** (2020-11-26): a single-venue feed printed DAI at $1.30 while it was
  ~$1.00 elsewhere. ~$89M liquidated. The manipulation-vs-market debate is unresolved and irrelevant: the
  design is wrong either way.
- **Mango Markets** (2022-10-11): a mark the position holder moved 13x in 30 minutes by self-trading, for far
  less than the value extractable against it. >$110M drained.
- **Knight Capital LMM desk** (2011-10): DR/test market data reaching a live quoting path. Nearly $7.5M.
- **CME Whaley to Bachelier** (2020-04-22): a model whose domain excluded a reachable state (`log(F/K)` with
  F <= 0). *Sourcing caveat: the advisory text was never read directly.*
- **Robinhood PFOF routing** (2015 to 2018): a router objective function that ranked venues by payment
  received. $34,100,000 of customer harm net of commission savings.

**Owner.** `fin-onchain` (oracles), `fin-exchange-integration` (mark vs index vs last, freshness gates),
`fin-verification` (provenance).

### F13 · Change, deploy and configuration: *does correct source run correctly everywhere it runs?*

**Ask of the diff:** how many hosts, regions, shards, chains or environments must agree for this to be
correct, and what proves they do?

**Mechanism.** Correctness is a property of the deployed fleet, not of the diff. A flag repurposed while a
deployed consumer still reads the old meaning; dead money-path code left callable; a build that reached N-1 of
N hosts; a rollback that was never a tested path; two environments "specified to be identical" and configured
differently; a toolchain that miscompiles a safety annotation. One codebase deployed to N chains multiplies
one bug by N.

- **Knight Capital** (2012-08-01): all at once. Dead code left callable, a flag repurposed rather than
  retired, a deploy that reached seven of eight servers with no second-technician review, and a cumulative
  quantity counter moved out of the generator that used it as a termination condition. $460,000,000.
- **TSB core-banking migration** (2018-04-20): two data centres "were, in certain areas, configured
  inconsistently despite having been specified to be identical", undetected across four years of testing.
  £32.7M customer redress; £48.65M in fines.
- **RBS / NatWest / Ulster Bank, CA-7** (2012-06-17): an upgrade uninstalled in production on a path never
  exercised. £56M in fines; 6.5M+ customers over weeks.
- **Curve / Vyper** (2023-07-30): Vyper 0.2.15/0.2.16/0.3.0 mis-allocated named re-entrancy locks, so two
  functions declared with the same lock name did not share one. ~$70M gross. One of the catalogue's three
  "an agent could not have caught it" verdicts: there was nothing to find above the compiler.
- **NASDAQ Trade Through / SHO Through** (2011-10 and 2012-08): a configuration edit turned a gate into a
  no-op, and an acknowledged alert about a missing component never re-fired on subsequent starts.

**Owner.** *rollout*, in `fin-money-core`; `fin-verification` owns what counts as proof that it holds.

## The cross-domain table

Rows are the classes; columns are the five code positions the suite recognises. A class fillable in fewer than
three columns would not be in this taxonomy. The venue column is served by the material in `advanced/`.

| Class | Trading (client of a venue) | Payments | Ledger | On-chain | Venue (you are it) |
|---|---|---|---|---|---|
| **F1** Representation | tick/step/minNotional quantization; `abs(price)` in a margin formula; inverse and quanto contract units | currency exponent (JPY 0, KWD 3); charge / payout / display / calculation are four scales | an amount column with no currency dimension; `int32` cents; an aggregate wider than its column | `decimals()` cached as a constant; `1e18` against USDC's 6; `uint256` wrap before the check | quantity conventions inverted between messages: OUCH replace is chain-cumulative, ITCH `Modify` is a decrement |
| **F2** Rounding and residue | gross up for round-trip fees *before* quantizing, then quantize toward validity | per-line VAT vs the total; the remainder a partial capture destroys; FX spread booked separately | largest-remainder allocation into a named residue account; FX rate side and pivot | `mulDiv` with an explicit direction per leg; first-depositor share rounding; `>` where `>=` | pro-rata rounds down and cannot allocate everything; the FIFO exception; iceberg refresh |
| **F3** Sentinel and absence | a placeholder axe price reaching an order; a stub quote read as top-of-book liquidity | an amount field pre-populated with a value that is valid on submit | a missing price marked to `0` in a valuation; `account_id = ""` | `0x00` meaning "unproven"; `address(0)`; a call to a codeless address returning success | a price band computed from a default because the reference feed was unavailable |
| **F4** Identity and replay | clOrdID scope per venue: open-orders-only on Binance/OKX/Kraken, none on Deribit | `pspReference` vs `merchantReference`; what an `Idempotency-Key` does not survive | the transaction id derived from the payment's idempotency key; `reverses_transaction_id` unique | `(chainId, from, nonce)`, never the tx hash; log key `(chainId, blockHash, txHash, logIndex)` | UserRefNum and ClOrdID day-uniqueness; PossDupFlag vs PossResend; iLink3 `(sequence, UUID)` |
| **F5** Indeterminacy | 5XX / `-1006` / `-1007` / socket timeout / 429 are UNKNOWN; `-2013` right after placement proves nothing | a cached 500 the client cannot resolve alone; a fresh key after any non-409 4xx | a transfer timeout is not a failure; resolve unresolved intents on startup | `already known` is success; a broadcast that never mines; `searchTransactionHistory: true` | an execution generated but never delivered because the transport was severed |
| **F6** Ordering and arrival | the same fill on stream *and* poll; `CumQty` across a replace chain; `(Canceled, Filled)` is legal | no webhook ordering guarantee; a late `refund.created` regressing a settled refund | `effective_at` vs `created_at`; back-dated entries into a closed period | a re-included tx at a new `logIndex`; a reorg unwind as a reversing entry | a cancel and an aggressor totally ordered by one sequencer; per-instrument sequence resets |
| **F7** Concurrency | two workers minting one client order ID; a position read, computed, written back | a refund issued twice because the check and the act sat in different transactions | lost update on a balance at Read Committed; write skew across two rows under one invariant | a nonce allocator with two writers; a lock keyed on a per-process hash | revalidation consuming one cancel per pass is livelock; single-writer core with fencing tokens |
| **F8** Settlement state | a fill reported as final and busted inside the clearly-erroneous window | `refund.created` is pending; pending refunds count against the ceiling; ACH returns at 60 days | posted / pending / available, and only `available` authorises; holds with intrinsic expiry | PENDING on observation, AVAILABLE at the finality budget; L1 batch finality, not L2 block count | error and suspense positions; confirmations that escalate rather than merely withhold |
| **F9** Conservation | `Σ child_qty(parent) <= parent.qty`; a counter and a hard bound on the emit path | every clearing account returns to zero; the refund is funded by the capture it reverses | every transaction sums to zero per currency; `Σ customer balances <= custodied assets` | every balance-mutating path ends in the health check; credit the measured delta, not the event value | issued <= authorised, checked inside the issuance transaction; netting that reproduces the gross |
| **F10** Divergence and recon. | position and realized PnL against the venue's own report, joined on `trade_id` | the settlement report is the money; the API is state; the webhook is only a trigger | trial balance; materialised-balance checksum recompute; three-way recon with aged breaks | `Σ credited at-or-below finalized height == Σ observed on-chain deltas to deposit addresses` | no external authority exists, so authority is SELF and the proof moves before deploy |
| **F11** Bounds and authority | client-side pre-trade limits; a collar on every marketable order; halt on ambiguous reconciliation | a finite business-derived amount ceiling that rejects; an affirmative act to send | which accounts may go negative, enforced at reservation time; no per-account exemption | a governance kill switch measured against the drain rate; per-asset borrow caps | 15c3-5 order-by-order rejection on the last hop; price bands; a kill switch requiring diagnosis to clear |
| **F12** Valuation and inputs | mark vs index vs last for stops, sizing and liquidation distance; freshness gates market data | the FX rate's provenance, side and timestamp recorded with the conversion | a displayed figure reads the authoritative ledger; revaluation rate provenance | `updatedAt` vs heartbeat; `minAnswer`/`maxAnswer`; sequencer uptime; AMM spot is not a valuation | settlement-price discretion; a self-referential mark the backstop's own position moves |
| **F13** Change and config | one adapter change reaching one of N venue processes; a repurposed enum a live consumer reads | a processor migration where old and new keys have different retention | migration cutover with per-account balance preservation, shadow comparison, a verified reverse path | an immutable target with multi-day fix latency; one codebase x N chains = N x blast radius | a config edit turning a gate into a no-op; published rulebook vs implemented behaviour |

## The classes that appear in generated and hastily-written code

**Different evidence, and it has to be read differently.** Nothing below is cited to an incident, because no
postmortem describes any of it: a postmortem records the loss and attributes it to whichever class the missing
control belonged to, never that the control was *named in a comment* and not built. These are defects of the
writing rather than of the economics, and they are read out of code as written. What they share is that each
one **looks, on the page, exactly like the thing it is not**, which is why they survive the author's own
review, and why they cluster in code produced fast: by a model, or by a person against a deadline.

Two of the five are new mechanisms. Three are new signatures of classes above.

**A1 · The prose TODO: the named risk documented instead of implemented.** The author identifies the correct
control, articulates the risk accurately, and then writes a note about it. The absence is **documented**, and
that is precisely what lets it pass self-review: the diff contains a paragraph proving the risk was
understood, and no code enforcing anything. The phrasings recur and are greppable: a reconciliation job marked
"not built, suggest we schedule it" · "that's a risk-policy call, not a technical one" · "persisting
`last_trade_id` to disk/redis closes that hole; I left it in memory" · "mitigations I'd add before this handles
real money" · "should probably be required at the API layer". It implies a wording doctrine for every rule in
this suite: a rule must be an artifact requirement ("there MUST be a call to X from Y"), never a consideration
("consider a dead-man switch"). Considering it is the failure mode.
**Check.** Every control the response names carries a real `file:line`, or an explicit
`UNRESOLVED: <control> (<why>)`. **Owner:** *implemented, not described*, stated in `fin-money-core` and
carried by the output contract of every skill.

**A2 · The documented invariant that is false.** A comment or docstring asserts a property the code does not
have, and the assertion is what makes the defect survive review: the reader checks the claim against their
model of what the code ought to do, never against the code. The recognisable shapes: "it flushes so the
`OrderRefund` row exists before the Stripe call returns", where the flush sits forty lines *below* the call ·
"reserve a local row first so we always have a record even if the call times out", contradicted four lines
later by `db.rollback()` · "the monotonic guard drops anything already reflected in the snapshot, so gaps are
not possible", false as implemented · "move money into `refunded_cents` exactly once", written over a branch
whose body is `pass`. Design notes are where the author's **belief** lives, and the belief is frequently
unverified.
**Check.** Read the design notes and docstrings as a list of claims; for each asserted property, either point
at the test that proves it or delete the sentence. **Owner:** `fin-money-core`, mirrored in
`fin-verification`.

**A3 · The decorative transaction: the lock released before the section it protects (a signature of F7).**
Reviewing for the *presence* of a lock passes this every time; what fails is the *extent* of the critical
section. The shapes: `with db.engine.begin() as conn: SELECT ... FOR UPDATE`, where the lock dies at the
dedent, before the sign and broadcast it was meant to protect · `poll_batch()` doing
`FOR UPDATE SKIP LOCKED LIMIT 50` and closing the transaction after `fetchall()`, so N workers process the
same 50 rows · an admin `reject()` that drops the row lock between the status check and `reverse()` · a worker
using `async with session_factory()` with no `session.begin()`, so `FOR UPDATE` holds nothing · an
`asyncio.Lock()` declared and never acquired.
**Check.** Name the lock, its key, its subject and its duration; the object holding the lock is the object
that performs the act; verify the transaction boundary, not the presence of a lock call. **Owner:**
*concurrency on authoritative state*.

**A4 · Dedupe state that evaporates on restart (a signature of F4).** The code dedupes on exactly the right
key and keeps the key set in memory, `_seen_trade_ids` as a `set`, `last_trade_id` as an attribute. The
double-count then happens on precisely the path that recovers from a restart. The inverse shape is the same
defect from the other side: an event that could not be resolved is still committed to `processed_events`, so
the provider never redelivers it and the miss is permanent. Neither shape is visible to a unit test, or to a
reviewer checking that dedupe exists.
**Check.** Dedupe state is as durable as the state it protects and is written in the same transaction. Mark an
event processed only when it was actually applied. **Owner:** *durable dedupe*.

**A5 · The safety constraint that defeats the safety operation (a signature of F11).**
`CHECK (balance_cents >= 0)` reads as an unimpeachable control, and it makes the adjacent safety operation
structurally impossible: `allow_overdraft=True` on `reverse_transfer` is dead code because the constraint
rejects the clawback debit. Account state shows the same shape: `_apply_movement` raising `AccountNotActive`
for frozen accounts blocks the standard fraud flow (freeze the recipient, then claw back), so you must
unfreeze first, creating exactly the drain window the freeze existed to close.
**Check.** Every safety constraint is tested against the operations performed under duress: clawback,
reversal, freeze-then-recover, forced liquidation, kill switch. **Owner:** `fin-ledger`.

**Related shapes, not promoted to a class.**

- **The "no work to do" branch that still commits progress**: `if (addresses.size > 0) { ...getLogs... }`
  guards the query while `saveCursor(tx, { lastBlock: toBlock })` runs unconditionally, so on a fresh deploy
  with an empty address table the cursor sprints to the safe head and every address registered afterwards can
  never see a deposit in a passed block. That is F10's watermark shape, not a new class.
- **Ghost-order resurrection**: a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts an order a terminal
  event had already popped, because the monotonic guard is skipped once the order is gone; `reconcile` then
  sees the client id in `live`, skips re-placing, and the bot believes it is quoting with nothing on the book.
  The defect is a missing deny-by-default arm in the transition table. F6's best worked example.
- **Hedge-mode collapse**: positions keyed on symbol alone with `positionSide` dropped, so a +5/-3 pair becomes
  a fabricated flat -3. F4.
- **Internal-transfer minting**: nothing checks that `from` is not itself one of our own deposit addresses, so
  moving USDC between two owned addresses creates a credit with no debit. F9.
- **Unchecked unsigned subtraction on a published aggregate**: `level.total_qty -= qty` on `u64` guarded only
  by `debug_assert`; in release, drift wraps and is published as depth to consumers who trade against it. F1.

## How to use this on a diff

The economic-diff gate produces the routing signal; this walk consumes it. Each gate answer points at a
family, and one class is asked unconditionally.

| Gate answer | Walk |
|---|---|
| **AMOUNT** | F1, F2, F3, then F12 if the number came from outside |
| **EFFECT** | F5, F8, F9 |
| **AUTHORITY** | F11, F12 |
| **REPLAY** | F4, F6, F7 |
| **ROLLOUT** | F13 |
| *(always)* | **F10** |

The walk, in order, with the question that opens each step:

1. **Is anything here an amount?** F1, F2, F3. Cheapest and most local, answerable from the diff alone.
2. **Does the diff mint, consume or compare an identity?** F4.
3. **Does it call something that moves value?** F5.
4. **Does it consume an event someone else emits?** F6.
5. **Does it read then write a quantity that authorises something?** F7.
6. **Does it make value spendable?** F8.
7. **Does it transform value (split, fan out, convert, net, issue)?** F9.
8. **Does it report a number to anyone?** F10.
9. **What bound stops it, and can that bound be clicked through?** F11.
10. **Does it decide against a number it did not produce?** F12.
11. **Must more than one host, region, shard or chain agree for this to be correct?** F13.

**Stopping rules.**

- **Stop before step 1** if the gate exempted the diff: numbers that are analytics and never become an
  obligation (backtest statistics, greeks, implied vol, Monte Carlo), with no balance, order, payment or
  transfer written. The gate's job is to exempt, not to admit.
- **Stop at the first class the diff fails, fix it, and re-walk from the top.** A fix in one class routinely
  opens another: `CHECK (balance_cents >= 0)` is an F11 fix that creates an A5 defect.
- **Steps 1 to 7 are answerable from the diff alone; steps 8 to 11 need the repo.** With only the diff, say so
  rather than passing them silently.
- **Do not walk steps 8 to 11 for a type-only change** unless the type crosses a module, storage or wire
  boundary, which is exactly where F1 fails.
- **F10 is not optional and not a tiebreaker.** If the diff adds a reported economic quantity and nothing
  compares it to an independent authority, that is the finding regardless of what else the walk turns up.
- **Count the classes.** Two or more failing classes on one diff is this corpus's signature of a large loss.

Before any of this, **A1 and A2 apply to your own output**: a control you named and did not implement is the
defect you named, and a property your comment asserts is a claim you must point at a test for.

## What this taxonomy does not do

- **It does not partition.** The classes overlap by design; the largest incidents fail three at once. Use them
  as questions, not buckets.
- **It does not claim diff-review coverage.** Across the 51 catalogued incidents the verdicts are
  **30 Yes · 18 Partly · 3 No**: roughly two-thirds of the money-loss surface is reachable by reviewing a
  diff. F1 to F7 and F9 are largely diff-visible; F8 and F11 partly; **F10 and F13 mostly are not.** You can
  see that a diff adds a reported number with no reconciliation; you cannot see the eighth server, the
  vendor's stale manual, the data-centre estate, or the compiler. That is why the suite is not a linter and
  why `fin-verification` is a separate skill.
- **It does not cover security.** Unauthorised action is a different question with a different suite.
  Wormhole (2022-02, ~$326M) is a signature-verification failure and is deliberately excluded.
- **It does not cover risk-model error.** A leveraged position against a central-bank peg is not a defect. The
  in-scope thread is narrow and already covered: a risk engine that cannot *represent* a market state is F1.
- **It does not weight the classes for you.** How much a class *cost* and how often it is *written* are
  different quantities, and they disagree: F8 is enormous in the incident corpus, while A1 is invisible to
  every postmortem ever written and is a defect you will meet in most of the code this suite reviews.
