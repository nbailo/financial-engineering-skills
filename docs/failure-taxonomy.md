# The Canonical Financial Failure Taxonomy

**Status:** derived, not proposed. Every candidate class was tested against the incident corpus and survived, was split, was widened, or was deleted. §0.2 records each change and its reason.

**What it is for.** One question: *given a diff, what kind of wrong can this be?* A class earns its place only if it has (a) a defining question you can ask of a diff, (b) a checkable predicate that would have caught three or more cited failures in two or more domains, and (c) no collapse into a neighbouring class when you actually try to use it.

**What is being classified.** Not "bugs in financial software" but *financial correctness* failures: the system produced an incorrect economic outcome while every component behaved exactly as specified. No attacker is required for any mechanism here. Pure access-control breaches belong to a security suite and are deliberately absent from the corpus this was derived from.

---

## 0. Derivation

### 0.1 Evidence, and what each source can establish

| Source | Establishes | Cannot establish |
|---|---|---|
| `incidents/README.md`: 51 incidents, 20 with files, classed A–L | The mechanism, the worked example, the loss as its primary source states it | How often the mechanism occurs in code being written now |
| Regulator and enforcement documents: SEC 34-70694 (Knight), 34-69655 (NASDAQ/Facebook), 34-75331 (Goldman), 34-74032 (EDGA/EDGX); FCA Final Notice 124384 (CGML); FINRA AWC 2020066971201 (Robinhood); CFTC 8432-21 (Interactive Brokers); the CFTC/SEC May 6 2010 report | What the system did and which control was absent, in the document's own numbered paragraphs, with the loss and the penalty as stated | The source. Regulators describe behaviour and controls; they almost never describe code |
| Vendor and protocol documentation: Stripe, Binance, Coinbase, OKX, Kraken, Bybit, Deribit, geth, Solana, Chainlink; FIX/iLink3, OUCH, ITCH, CME MDP, ISO 4217, IEEE 754 | What a counterparty guarantees, what it explicitly does not, and which error codes mean *unknown* | How client code in fact treats those guarantees; the divergence between the two is where F5 lives |
| Open-source production code read at a pinned commit: `nautilus_trader`, `ccxt`, `freqtrade` | What mature practice actually does, including where two rigorous projects make opposite choices | Whether a choice is correct; a widely deployed project can be wrong, and two of them can be right for different reasons |
| `docs/architecture.md` §1.2: eight recurring mechanisms | Which mechanisms survive translation into all four domains | Attention weighting |

Incidents and regulator documents set *truth*. Vendor documentation sets the *contract*. Code read in the wild sets *practice*, and it disagrees with both often enough that §1 records the disagreement rather than smoothing it: freqtrade's `Float` money columns and `nautilus_trader`'s non-absorbing terminal states are each a considered choice by a serious project, and each falsifies a rule that sounds obvious. §3 is a separate section because its evidence is different in kind again: it is read out of code as written, and no postmortem records it.

### 0.2 The candidate taxonomy, tested

Candidates: *representation · identity · state · concurrency · ordering/time · partial-failure · reconciliation · pricing-execution · authority.* That is the class list a trading-only incident review produces, a good null hypothesis and a bad final answer, because it was derived from one domain.

| Candidate | Disposition | Reason |
|---|---|---|
| representation | **Split three ways** → F1 representation, F2 rounding & residue, F3 sentinel & absence | The three separate cleanly on the incidents themselves. Bitcoin 2010 is representation with no rounding: the arithmetic was exact and `uint64` could not hold the sum. Balancer is rounding with an entirely adequate type: `1e18` fixed point represented every value and `mulDown` still had to take a side. Nomad is a sentinel where the type is fine: `bytes32` represented `0x00` perfectly, and the defect was what `0x00` was taken to mean. The fixes live in different places (declaration, arithmetic, boundary crossing) and the catalogue carries them as three separate classes for the same reason. |
| identity | **Kept**, as *identity & replay* | Five catalogue entries spanning on-chain, venue and payments. No overlap problem. |
| state | **Dissolved** | A container, not a mechanism. Its four sub-modes each belong elsewhere: C1 termination-condition-moved → F9; C2 authoritative-vs-displayed → F10; C3 stale-input-as-current → F12; C4 mixed-purpose suspense → F10. Every incident classed "state" is already double-classed in the catalogue. Nothing was lost. |
| concurrency | **Kept** as F7 | |
| ordering/time | **Kept** as F6, and this **overrules the catalogue** | `incidents/README.md` merges concurrency and ordering into class H with two entries. Defensible on incident count, wrong on mechanism: F7 is two writers racing on one authoritative quantity; F6 is one consumer receiving events out of occurrence order. The predicates share nothing: isolation level and lock extent versus a version guard keyed on the entity id. The incidents separate too: NASDAQ's cross is a revalidation loop losing to its own arrival rate, with no ordering question in it, while Stripe documents that its webhooks carry no ordering guarantee at all and that a late `refund.created` regresses a settled refund, an F6 with no concurrent writer anywhere in it. |
| partial-failure | **Split two ways** → F5 indeterminacy, F13 change/deploy/config | They share the word "partial" and nothing else: one is a single call's return value, the other is the estate the code runs on. The catalogue already separates them as F (5) and J (7, the largest class after representation), and the split matters for review: F5 is fully diff-visible, F13 mostly is not. |
| reconciliation | **Kept and widened** to F10 *divergence & reconciliation* | Reconciliation is a control, not a mechanism. The failure class is the divergence; the missing reconciliation is what makes it terminal rather than a one-hour incident. Absorbs the dissolved "state" C2 and C4. |
| pricing-execution | **Kept as F12** *valuation & input integrity*; execution half moved to F11 | It bundled two mechanisms. "The number you decide against is produced by someone who benefits, is stale, or has no provenance" is input integrity. "A participation target with no price bound and no time bound" is a missing bound, the same predicate as Citi's absent amount ceiling. The Flash Crash rule is literally a bounds rule. |
| authority | **Kept as F11** *bounds & authority*, absorbing the execution-bound half | |
| n/a | **Added: F9 conservation** | The candidate's largest hole. Mechanism #6 of the architecture's eight, class D of the catalogue, principle P7, and the mechanism of Knight ($460M), Euler (~$197M), both Bitcoin inflation bugs, Samsung, Onyx, Compound. It is the only predicate that catches *value appeared* as distinct from *value was mis-stated*. |
| n/a | **Added: F8 settlement state** | Mechanism #2 of the eight, class G of the catalogue, principle P6. FTX (~$8bn), Kraken, Revolut. Not derivable from any other class: the amount is right, the identity is right, the arithmetic conserves, and the money is not there yet. |

**Net 9 → 13:** two additions the evidence demanded, three splits of two over-broad classes, one dissolution. No class was invented that the corpus does not carry at least three cited instances of.

### 0.3 The classes are not disjoint, and that is the point

A class is a question you ask, not a bucket an incident falls into. The catalogue already multi-classes its largest entries, and the count of classes a diff fails is the best severity proxy this corpus offers.

| Incident | Classes | Loss as its source states it |
|---|---|---|
| Knight Capital SMARS, 2012-08-01 | F13 + F9 + F10 | $460,000,000 (SEC ¶17); $12,000,000 penalty |
| Citigroup / CGML, 2022-05-02 | F3 + F1 + F11 | US$48m (FCA ¶2.8); £27,766,200 penalty |
| FTX `fiat@ftx` / `allow_negative`, 2019–2022 | F8 + F9 + F11 | ~$8bn hole in customer fiat |
| NASDAQ Facebook IPO cross, 2012-05-18 | F7 + F6 + F10 | $10M penalty; $62M accommodation fund |
| Balancer V2, 2025-11-03 | F2 + F13 | ~$128M across 8+ chains |

Nine of the thirteen are owned by `fin-money-core`. That is the reason the core skill exists, and why `architecture.md` §3 marks the domain skills **I** (instantiation only) on those rows: a domain skill must name a real API, error code or schema object, never restate the general theory.

---

## 1. The thirteen classes

### F1 · Representation: *can the type hold the fact?*

**Ask of the diff:** does every amount carry its own unit, scale and full domain, and does every check on it run where it cannot already have wrapped or truncated?

**Mechanism.** A number crosses a module, storage or wire boundary carrying less information than the fact it represents. The domain is too narrow (a price assumed non-negative; a sum that wraps before its own bounds check); or the unit is carried by convention rather than by the type (quantity vs notional, shares vs currency, gross vs net, minor vs major); or the scale is hardcoded where it is runtime metadata; or the field's *name* asserts one financial quantity while the value is another.

- **Bitcoin value overflow, CVE-2010-5139** (2010-08-15, on-chain): `sum(outputs)` wrapped `uint64` *before* `outputs <= inputs` ran, so the check compared the wrapped value and passed. 184,467,440,737.09551616 BTC, exactly `2^64` satoshi, created; 53 blocks orphaned.
- **Interactive Brokers, negative WTI** (2020-04-20, trading): `price > 0` assumed across parse, store, display, order entry and margin. $82,570,000 restitution + $1,750,000 penalty (CFTC 8432-21).
- **Citigroup Global Markets** (2022-05-02, trading): a basket intended as $58m of *notional* entered against a *quantity* field; nothing downstream re-derived the other and cross-checked. US$48m loss (FCA ¶2.8).
- **Samsung Securities "ghost shares"** (2018-04-06, venue/ledger): a dividend denominated in won issued as shares: ~2.81bn shares, notionally ~₩112tn.
- **Robinhood displayed labels** (2016-09 → 2021-05, trading/ledger): "equity" naming market value, "cash" naming buying power. $57,000,000 FINRA fine + $12.6M restitution.
- **freqtrade stores every money field as SQL `Float`** (OSS): `ft_amount`, `ft_price`, `price`, `average`, `amount`, `filled`, `remaining`, `cost`, `funding_fee`, `ft_fee_base`, all `mapped_column(Float())` at `freqtrade/persistence/trade_model.py:95-117`, while the *aggregation* over them runs in arbitrary-precision string math (`freqtrade/util/ft_precise.py:9`) and is cast back to `Float` for storage (`trade_model.py:1329-1335`). The exposure is not the arithmetic; it is the storage and wire boundary, SQL column types, ORM field types, JSON round-trips, protobuf `double`, CSV export.

**Predicate.** Every amount is an exact type carrying its currency or asset identifier inside the same value; cross-currency arithmetic raises rather than compiles; scale resolves from runtime metadata (`decimals()`, the currency exponent table), never hardcoded, never cached as a constant; the declared domain admits every reachable value including signed prices; any bounds check runs on operands that cannot already have wrapped. Grep `float`/`double`/`REAL`/`DOUBLE PRECISION`/protobuf `double` columns, `* 100`, `/ 1e18`, a literal 2 decimal places, `abs(price)`.

**Owner.** `fin-money-core`, MC1 (reference placement), MC2. Principles P1, P2.

### F2 · Rounding & residue: *when the exact value is not representable, who gets the difference?*

**Ask of the diff:** does every rounding call site name its mode, and is the direction a function of who is credited rather than a global default?

**Mechanism.** Exact arithmetic is impossible somewhere (a division, a rate, a share conversion, an allocation across N recipients), and the code makes an unnamed choice. Three shapes: one direction applied to both legs of an exchange, so one side is systematically favoured; a remainder nobody accounts for; a comparison at the exact boundary where the direction changes. All three are invisible per operation and material in aggregate: the Balancer attacker packed 65 micro-swaps, some as small as 17 units, into one `batchSwap` so losses composed against transient internal balances before final settlement.

- **Balancer V2 Composable Stable Pools** (2025-11-03, on-chain): `_upscale` applied `mulDown` "independently from the direction of the swap", safe only while the scaling factor was `1e18`; Composable Stable Pools overrode `_scalingFactors()` with live exchange rates. ~$128M across 8+ chains.
- **Onyx Protocol, after Hundred Finance and Midas Capital** (2023-11-01, on-chain): `shares = assets * totalSupply / totalAssets` with `totalSupply == 0` unguarded and `totalAssets` inflatable by direct transfer, so the victim's shares round to zero. $2.1M at Onyx; >$10M across the class in 2023.
- **Compound COMP over-distribution, Proposal 62** (2021-09-29, on-chain): `>` where `>=` was required, in two places. ~168,000 COMP (~$50M) claimed; the ~$80–83M figure is the bounded worst case, not the event.
- **CME pro-rata allocation** (venue): pro-rata rounds down, cannot allocate everything, and must never be the last step in the pipeline.
- **Per-line VAT and cent-splitting** (payments/ledger): `sum(rounded lines) != round(total)`; the residue has to be posted somewhere, and "somewhere" must be a named account.

**Predicate.** Every rounding call site names its mode; no `int()`, `floor`, `trunc` or integer division substitutes for rounding on a money value; the two legs of an exchange never share one global direction; every allocation preserves its remainder into a **named** account; every `>` or `>=` in a payout, accrual or guard has a test at equality; boundary tests exist at `threshold-1`, `threshold`, `threshold+1`.

**Owner.** `fin-money-core` MC3, principle P5. Instantiated in `fin-onchain` (`mulDiv` direction per leg), `fin-ledger` (largest-remainder, FX residue), `fin-matching-and-settlement` MS8.

### F3 · Sentinel & absence: *does a legal value also mean "there is no value"?*

**Ask of the diff:** is there a value in the domain meaning "unset", "unknown", "not yet priced" or "not proven", and what happens to it at the next boundary?

**Mechanism.** A placeholder is only a placeholder inside the subsystem that agreed on it. At every boundary it is an ordinary value of its type and will be treated as one: `0x00` satisfies a membership test, `$1.00` is a real price to a matching engine, `0` is a legal mark, fifteen pre-populated zeros are a legal amount on submit. The mirror image is the same defect: a genuinely absent input coerced to a legal value, a missing price becoming `0` or last-known, instead of propagating as absent and raising at the point of use.

- **Nomad bridge** (2022-08-01, on-chain): `trustedRoot` initialised to `0x00`, the same value meaning "not proven"; `process()` then accepted every message, and once one exploit transaction existed anyone could replay it with their own address swapped in. ~$190M across 960 transactions / 1,175 withdrawals.
- **Goldman Sachs options router** (2013-08-20, trading): an internal `$1.00` placeholder axe reaching a venue, on an off-hours path whose validation was weaker than the continuous one. ~$38M realised after busts; "up to a potential $500 million" exposure (¶5); $7,000,000 penalty.
- **Citigroup $81 trillion credit** (2024-04, reported 2025-02, payments): an amount field pre-populated with fifteen zeros that was valid on submit. $0 realised; reported to the Fed and OCC as a near miss.
- **Robinhood zero-value mark-to-market** (2018-08-10, trading/ledger): an unavailable price returned `0` rather than raising. Part of the $57M fine + $12.6M restitution.
- **May 6 2010 stub quotes** (venue/trading): a taker cannot distinguish a sentinel quote from real liquidity at top of book. >20,000 trades / 5.5 million shares broken; almost two-thirds executed below $1.00.

**Predicate.** No `0`, `""`, `0x00`, `-1` or `null` doubles as "unset" on a money path. Use a presence flag or a type that cannot represent the sentinel. A missing price propagates as **absent** and raises at the point of use, never `0` and never last-known. An internal placeholder object is structurally unable to reach an external boundary. Off-hours, auction and pre-open validation is at least as strict as the continuous path.

**Owner.** `fin-money-core` MC13, principle P12.

### F4 · Identity & replay: *is this the same fact, or a second one?*

**Ask of the diff:** what key decides two observations are the same economic event, who controls that key, and does it exist before the effect it names?

**Mechanism.** Dedupe, reconciliation and idempotency all rest on one identity. It fails when the key is counterparty-mutable (so "I don't see my id" and "it didn't happen" become one statement), positional rather than content-derived, a tuple of business attributes two distinct events can share, minted *after* the effect, or minted from a value that does not survive `ROLLBACK`. It also fails when one external event can be applied by two code paths that do not share an executed-set.

- **Mt. Gox transaction malleability** (2014-02, on-chain): reconciling against your own log of what you believe you issued, keyed on a counterparty-mutable txid. The mechanism is real, the attribution is not: Decker & Wattenhofer measured **421 conflict sets totalling 1,811.58 BTC** network-wide for the year to 2014-02-07, and the 286,076 BTC of malleability attacks came *after* the press release.
- **Solana blocks tracked by slot number** (2020-12, on-chain): identity keyed on position, so two different blocks at one slot were one block and forks could not be reconciled.
- **Solana durable-nonce double execution** (2022-06-01, on-chain): one transaction applied twice through two code paths; a double-spend primitive, ~4.5h halt.
- **Wintermute / Optimism** (sent 2022-05-27, taken 2022-06-05, on-chain): a `CREATE`-derived address is a function of `(factory, nonce)` only and is not a portable identity across chains. 20,000,000 OP (~$27.6M).
- **Client order ID scope, per venue** (trading): Binance Spot and Futures, OKX and Kraken enforce uniqueness only among *open* orders; Bybit rejects duplicates (rejection is not returning the original); Coinbase Advanced Trade is the only venue in the set with true create-order idempotency; Deribit has none. No single vendor document states the divergence.

**Predicate.** The dedupe identity is caller-supplied or content-derived, never a tuple of business attributes and never a positional index; minted from a value that survives `ROLLBACK`; **committed** before the effect (`flush()` inside an open transaction is not persistence); bound to the request body; a duplicate replays the stored response; exactly one apply function consumes it, shared by the real-time handler and the replay job.

**Owner.** `fin-money-core` MC4, MC5, MC8, under *durable intent before the external effect*; principle P4. Scope tables live in the domain skills: `fin-exchange-integration` EX1, `fin-payments` PAY8, `fin-onchain` ON3/ON5.

### F5 · Indeterminacy: *does the code know whether the effect happened?*

**Ask of the diff:** for every `catch`, `except` and `Err` around a value-moving call, what does the code now believe about the world, and on what evidence?

**Mechanism.** A timeout, severed socket, 5XX, 429 or rate-limit rejection tells you nothing about whether the counterparty acted. Treating it as "did not happen" and resubmitting pays twice; treating it as "did happen" strands money. The correct third state, UNKNOWN, resolved by querying authoritative state for an identity you generated, must be represented in the persisted record, not only in the retry logic. A "not found" immediately after submission is not proof of non-creation.

- **Robinhood order-entry outage** (2020-03-09, trading): ~166,000 orders stuck "pending", not cancellable, execution status unknown. >$5M outage restitution within a $70M total. (FINRA's AWC says a key system was overloaded, causing a cascading failure; no primary source supports the widely repeated leap-year story.)
- **Binance's own documentation** (trading): 5XX, `-1006` and `-1007` are documented as "execution status UNKNOWN"; `-2013 NO_SUCH_ORDER` right after placement is not proof of non-creation because three documented data sources have different staleness; `-2021` on a cancel/replace means one leg succeeded.
- **Stripe** (payments): 500s **are** cached, and Stripe states there is no client-side algorithm that resolves the outcome alone.
- **geth and Solana** (on-chain): `already known` means success; a mined-but-reverted transaction consumes the nonce, burns gas and emits no logs; `getSignatureStatuses` must be queried with `searchTransactionHistory: true` before concluding non-execution.
- **Tokyo Stock Exchange arrowhead** (2020-10-01, venue): the halt severed the transport without quiescing the engine, so executions accumulated undelivered and participants held unknown positions.
- **ccxt's retry funnel** (OSS): `RequestTimeout extends NetworkError extends OperationFailed` (`ts/src/base/errors.ts:219`, `:177`, `:171`); the retry predicate in the single funnel every signed REST call passes through is `e instanceof OperationFailed` with **no HTTP-method or path discrimination** (`ts/src/base/Exchange.ts:6435`), and the re-sent request carries the identical `newClientOrderId` (`ts/src/binance.ts:6969`), which Binance scopes to open orders only. ccxt's own documentation says both things: that on `RequestTimeout` "the user doesn't know the outcome of a request", and forty lines earlier that `OperationFailed` "can be blindly re-tried".

**Predicate.** Every catch around a value-moving call resolves the outcome by querying the counterparty for the identity you sent, **before** any retry, never by resubmitting. The classification (OK / FAILED / UNKNOWN) is carried in the persisted record, not inferred later. No `session.begin()` / `engine.begin()` / `@transaction.atomic` lexically encloses the external call, and the intent row stays committed through the ambiguous failure.

**Owner.** `fin-money-core` MC6, MC15, under *durable intent before the external effect*; principle P3. Instantiated as EX2, PAY8, ON5.

### F6 · Ordering & arrival: *is arrival order being read as occurrence order?*

**Ask of the diff:** does this consumer assume the sequence it receives is the sequence that happened?

**Mechanism.** One consumer, many producers, no total order: redelivery, a REST backfill interleaved with a socket stream, a webhook queued before another and delivered after, a replayed pre-snapshot event. Damage depends on path-dependence: position averaging is path-dependent, so an out-of-order fill corrupts entry price *permanently*; a status field is not, but a late `pending` regresses a settled object and re-arms a money branch under a fresh event id the dedupe table cannot catch. Two distinct guards are required: **legality** (which `(state, event)` pairs are allowed) and **version** (which observation of an entity is newer).

- **NASDAQ Facebook IPO cross** (2012-05-18, venue): the printed cross was computed against a book from 11:11 and printed at 11:30, and cancels acknowledged in the window were discarded by the recovery path. Per SEC 34-69655 ¶26 the 19-minute staleness was an **input backlog inside the livelocked primary**, "the IPO Cross Application's inability to escape the loop … had caused the IPO Cross Application to fall 19 minutes behind the orders received by NASDAQ", not a promoted lagging replica. >38,000 orders excluded; ~8,000 released at 11:30; >30,000 "stuck".
- **Stripe webhooks** (payments): no ordering guarantee; the signature timestamp is not an ordering key; a late `refund.created` regresses a settled refund to `pending`.
- **Venue fill delivery** (trading): the same fill arrives on the stream *and* the poll and must dedupe on `trade_id` plus field comparison; `CumQty` is cumulative across the whole replace chain; `PossDupFlag` (session layer) and `PossResend` (application layer) mean different things.
- **`nautilus_trader`** (OSS): `crates/model/src/orders/mod.rs:250` ships `(Canceled, Filled) => Filled`, annotated `// Real world possibility`, plus a fifteenth status `Voided` (`enums.rs:1452`) so a venue can void a fill after `Filled`. **"Terminal states must be absorbing" is false as written**: a cancel is a request, a fill is money.

**Predicate.** Enumerate the legal `(state, event)` pairs with a deny-by-default arm that raises, never silently ignores. A terminal state accepts exactly the events by which the counterparty corrects a fact you already booked (a late fill, a fill void) and nothing else; it is never re-opened by a *status* message. The watermark is keyed on the entity **id**, stored independently of the live object, and the guard **is** the write: `UPDATE watermarks SET v=:v WHERE id=:id AND v<:v`, proceed only on rowcount 1, in the same transaction as the effect. Then re-read the object from its authority before any value-moving decision.

**Owner.** `fin-money-core` MC10, under *arrival order is not occurrence order*. Instantiated as EX15, PAY2/PAY3.

### F7 · Concurrency: *can two writers touch this authoritative quantity at once?*

**Ask of the diff:** is there a read, a computation, and a write-back of a quantity that authorises something, and what makes that atomic?

**Mechanism.** Lost update (read-modify-write on a balance under Read Committed); write skew (two rows guarded by one invariant, neither transaction seeing the other's write); and the venue-side shape, an optimistic revalidation loop whose per-pass progress is smaller than the arrival rate, which is a livelock rather than a slow success. A race detector finds none of these: a lost update across transactions is not a data race.

- **NASDAQ Facebook IPO cross** (2012-05-18, venue): each revalidation pass incorporated **only the first cancellation received during the previous calculation** (¶9, ¶20), so the loop could not converge; NASDAQ then **deleted the validation check** to force completion and printed a 19-minute-stale cross. $10,000,000 penalty; $62M accommodation fund; $10.8M error-account profit; a $35.3M haircut producing a $26.5M NES net-capital deficiency. The popular fix is wrong: a retry ceiling would have *aborted* the cross; NASDAQ's own remediation (¶65) is to freeze the input set or drain the whole queue in one recomputation.
- **Read-modify-write on a balance** (ledger): the canonical double-spend, and why the balance `UPDATE` must sit in the same transaction as the entry `INSERT`.
- **Materialised-balance drift** (ledger): `INSERT INTO entries…; COMMIT;` followed by a separate `UPDATE balances` is two transactions and drifts silently.
- **Nonce and key allocation** (on-chain): a withdrawal signer with two writers mints two transactions at one nonce; single-writer discipline plus a fencing token is the whole control.

**Predicate.** Every money transaction sets its isolation level explicitly. Every `SELECT`-then-`UPDATE` on a balance is either SERIALIZABLE with a retry that **re-reads** on 40001, or a single atomic conditional `UPDATE`. The lock is named with its key, subject and duration, and the object holding it is the object that performs the act. Lock keys derive from a value stable across processes. A revalidation loop freezes its input set or drains the entire pending queue in one pass, and failure to converge fails to a defined state, never to a disabled check.

**Owner.** `fin-money-core` MC12, principle P8. Venue form in `fin-matching-and-settlement` MS5 (deterministic core, fencing tokens, single-writer failover).

### F8 · Settlement state: *is this value spendable before it is real?*

**Ask of the diff:** which balance authorises the spend, and what event moved value into it?

**Mechanism.** "Credited" and "settled" share a field. The provisional amount is a flag on one balance column rather than a distinct state, so the same number credited on *observation* is debited on *spend*. Every variant follows: crediting from an operator action instead of a settlement event; treating an inbound pending as available; releasing a hold on a callback that never arrives instead of on intrinsic expiry; crediting an L2 deposit on L2 block count rather than L1 batch finality.

- **FTX `fiat@ftx`** (2019-07 → 2022-11, ledger/exchange): deposits "posted to their FTX accounts, even though the fiat deposits actually remained in Alameda-controlled bank accounts" (¶¶47–49), credited **manually** by staff. ~$8bn hole in customer fiat.
- **Kraken, spendable credit before deposit finality** (2024-06, exchange/on-chain): users could "receive funds in their account without fully completing the deposit". ~$3M taken across three accounts, fully recovered; the code-level mechanism is not public.
- **Revolut US, refunds on declined transactions** (2021-12 → spring 2022, payments): two networks disagreed about what "declined" meant and the delta was funded from Revolut's own balance sheet for months. ~$23M taken, ~$20M net (FT reporting).
- **Stripe refunds** (payments): `refund.created` is **pending**, and `refund.failed` can arrive up to 30 days from the post date; a pending refund reserves nothing unless it counts against the ceiling.
- **L2 finality** (on-chain): do not credit an L2 deposit on L2 block count above the stated exposure budget; wait for the L1 batch to finalize. "12 confirmations" is folklore: Polygon has produced a 157-block reorg, and Ethereum went non-finalizing for over an hour on 2023-05-12.

**Predicate.** Authorized, provisional and final are distinct states in the schema or type, not a boolean on one balance column; only `available` authorises a spend or onward transfer; inbound pending is never available; a hold expires by itself rather than by callback, and the invariant is checked at *reserve* time so no committed reservation can be un-postable; every credit traces to a settlement event, never to an operator action; finality depth derives from a stated reorg-loss budget recorded alongside the credit, and fast credit lives inside a bounded global exposure rather than a globally lowered depth.

**Owner.** `fin-ledger` LG7 and seam S2 staging. `fin-money-core` owns *provisional value is a state, not a flag* (P6); `fin-payments` PAY5 owns "pending is not paid".

### F9 · Conservation: *do the two sides of this transformation agree?*

**Ask of the diff:** does this turn one thing into many, or credit something that was not debited, and what compares the two sides before the transaction commits?

**Mechanism.** A transformation, fan-out, split, netting, conversion, issuance, minting, with no structural relationship between input and output. What distinguishes it from F10 is scope: conservation is an *intra-transaction* predicate assertable at a chokepoint, and its absence lets value **appear** rather than merely be mis-stated. The recurring code shape is one balance-mutating path that skips the assertion every other path performs.

- **Knight Capital SMARS** (2012-08-01, trading/venue): 212 parent orders in, millions of child orders out, and nothing at the router compared the two. $460,000,000 (SEC ¶17) in 45 minutes; $12,000,000 penalty.
- **Euler `donateToReserves`** (2023-03-13, on-chain): the single balance-mutating path that did not terminate in `checkLiquidity`, letting a user make themselves insolvent deliberately and self-liquidate at the protocol's own discount. ~$197M, largely returned after negotiation.
- **Bitcoin duplicate-input inflation, CVE-2018-17144** (disclosed 2018-09-17, on-chain): 0.14 dropped a duplicate-input check as redundant; 0.15 separately rewrote away the `assert` that would have caught its absence, converting a crash into silent supply inflation. Two changes, years apart, different people.
- **Samsung Securities** (2018-04-06, venue): `issued_shares_after <= authorised_shares` was not enforced inside the issuance transaction.
- **Double-entry, per currency** (ledger): every transaction sums to zero per currency; clearing and suspense accounts drain to zero as a continuous assertion; `Σ customer balances <= custodied assets`.

**Predicate.** Enumerate the legs and assert the sum per currency **inside the same transaction** as the mutation. Every value-mutating path terminates in one chokepoint assertion; enumerate them and check. A transformation that can emit many outputs from one input carries a counter and a hard bound on the *emit* path. When a check is removed as redundant, record the invariant it enforced and add a test that fails without it; never remove both the check and the assertion that detects its absence.

**Owner.** `fin-money-core` (the general rule, principle P7); `fin-ledger` LG1/LG2/LG10; `fin-matching-and-settlement` MS4.

### F10 · Divergence & reconciliation: *does anything independent agree with this number?*

**Ask of the diff:** for every economic quantity this code reports or stores, who is the external authority, what is the join key, and where does the comparison run?

**Mechanism.** Two representations of one economic fact, computed by different paths, with nothing structurally requiring them to agree: a displayed balance and a ledger balance; a materialised balance and the sum of its entries; your position and the venue's; your credits and the chain's observed deltas. The divergence is inevitable; the failure is that nothing notices. Sub-shapes that matter: detection whose only action is to withhold output; a reconciliation broken on day one so it gets muted; a booking backlog that removes the reconciliation which would have caught a day-one error; and a mixed-purpose suspense account, where breaks go to become unattributable.

- **Revolut US** (2021-12 → spring 2022, payments): no invariant tied the two payment systems together; the only control that fired was a **partner bank's cash-position report**, months later. ~$20M net.
- **Robinhood displayed balances** (2016-09 → 2021-05, trading/ledger): `displayed(x) == ledger(x)` was never asserted. Two distinct populations: >135,000 customers with doubled negative **cash** (Dec 2019 – Jun 2020); ~4.2 million with doubled negative **buying power** (Sept 2016 – Sept 2020). $57,000,000 FINRA fine + $12.6M restitution.
- **NASDAQ's Execution App** (2012-05-18, venue): reconciliation *detected* the divergence and its only action was to withhold output: no escalation, no notification, no halt.
- **BNP Paribas / "Armin S."** (2015, trading/ledger): ~8,000 unbooked trades over a week removed the reconciliation that would have caught a pricing error on day one. ~€160M in dispute. Alert on unbooked-trade **age**, not count.
- **Knight's "33 Account"** (2012, trading): unmatched fills, error positions and real positions in one bucket, so neither an automated limit nor a human could attribute exposure.

**Predicate.** For every economic quantity you report, name the external authority and the join key, the counterparty's identifier (`pspReference`, `trade_id`, `(chainId, blockHash, txHash, logIndex)`), never yours (`merchantReference` is not unique). Ship the comparison as a scheduled entrypoint reading through a path independent of the writer. The alert destination is a config key with **no default** that raises at import if unset. Breaks age into buckets, and an unreconciled break stops the affected path rather than logging. An invariant that exists as SQL in a comment or a "worth running as a cron" note counts as absent. Where no external authority exists (a matching engine, a custodian, a system-of-record ledger), **you are the oracle**; that is T3, and the proof burden moves before deployment into simulation.

**Owner.** `fin-verification` VF1, VF11, under *reconciliation runs in production*. `fin-money-core` owns *name the authority and the join key* (P10); each domain skill names its own authoritative source.

### F11 · Bounds & authority: *what stops this, and can it be clicked through?*

**Ask of the diff:** is there at least one non-overridable automatic bound on this path, and how fast can it be exercised relative to the rate the loss accrues?

**Mechanism.** A limit that warns is not a control; a per-item limit where an aggregate is required is not a control; a limit present in one region and absent in another is not a control; a kill switch behind a seven-day governance process is not a kill switch; an alert to a list nobody designed as an alert channel is not an alert. The most expensive variant is the per-entity **exemption**, a flag removing one account from the solvency, credit-limit or liquidation check, which converts an automatic control into an opt-out feature. The UX variant is default-send: the money-moving action is what happens when you do nothing.

- **Citigroup $81 trillion** (2024-04, payments): no ceiling of any kind on an operator-entered amount where $280 was intended; caught by a human 90 minutes after processing. Citi self-reported **10 near misses ≥$1bn in 2024 and 13 in 2023**, a billion-dollar error roughly monthly, caught by review.
- **Citigroup / CGML** (2022-05-02, trading): every meaningful limit soft; per-item limits where an aggregate was needed (349 line items); the hard block had existed in New York since 2013 and not in EMEA; a warning UI of 18 visible lines with no forced scroll. Per the Notice's own table (¶4.39): hard blocks suspended 58 orders / US$248bn; 291 orders / US$196bn proceeded to CitiSmart; 284 / US$189bn were received there.
- **FTX risk parameters** (ledger): `allow_negative = true` and `borrow = $65,000,000,000` on exactly one account, and **because database logs were not kept the debtors could not determine when or by whom it was set**.
- **Citibank / Revlon** (2020-08-11, payments): suppression required setting three fields (`FRONT`, `FUND`, `PRINCIPAL`); all three humans believed `PRINCIPAL` alone sufficed; the confirmation named neither the amount nor the destination. ~$894M wired; ~$385M returned voluntarily.
- **Compound Proposal 62** (2021-09-29, on-chain): "there are no admin controls or community tools to disable the COMP distribution", and a patch required a seven-day governance process. Everyone watched the drain.
- **May 6 2010 Sell Algorithm** (trading): a participation target with **no price bound and no time bound**; 75,000 E-Mini contracts (~$4.1bn) in 20 minutes, at a rate driven by a volume metric its own fills inflated.

**Predicate.** At least one non-overridable, business-derived ceiling on every money path that **rejects**, not warns. No per-entity exemption on a solvency, credit-limit or liquidation check, and every risk-parameter change is field-level audited (who, when, old, new). The kill switch is exercisable faster than the loss accrues and cannot be cleared without diagnosis. The safe outcome is the default: an affirmative act to send, never an affirmative act to suppress; one semantic outcome is never expressed across N independent fields without a consistency check; a confirmation interpolates the amount and the destination. Aggregate ceilings exist wherever per-item ones do. An execution algorithm carries a price bound and a time bound in addition to any participation target, and never drives its rate from a metric its own fills inflate. Anomaly signals go to a monitored channel with a named owner and an SLA. Knight emitted 97 "Power Peg disabled" emails to "a group of Knight personnel" that Knight "did not design … to be system alerts", and per SEC 34-70694 ¶19 n.6 those emails were produced by orders "distinct from the 212 incoming parent orders" that caused the loss, which is the general case for pre-open canaries.

**Owner.** `fin-money-core` MC14, principle P9. `fin-matching-and-settlement` MS10 and `references/market-access-controls.md` own the 15c3-5 form.

### F12 · Valuation & input integrity: *who produced the number you are deciding against?*

**Ask of the diff:** for every price, mark, rate or reference this code reads, what is its provenance, how old is it, and can the party who benefits from the decision move it?

**Mechanism.** The number you value, margin, liquidate, route or settle against is an input like any other, and it is wrong exactly when a single venue, a thin market, a stale cache, a test fixture, or the system's own trading is what produces it. Four shapes: single-source with no deviation guard; self-referential, where the position holder can move the mark for less than they can extract; stale, present but old with no age check; and out-of-domain, a model whose support excludes a reachable state.

- **Compound DAI oracle liquidations** (2020-11-26, on-chain): a single-venue feed (Coinbase Pro) printed DAI at $1.30 while it was ~$1.00 elsewhere. ~$89M liquidated; 124 of 225,793 users affected. The manipulation-vs-market debate is unresolved *and irrelevant*: the design is wrong either way.
- **Mango Markets** (2022-10-11, on-chain): a mark the position holder moved 13× in 30 minutes by self-trading, for far less than the value extractable against it. >$110M drained (CFTC); ~$67M returned.
- **Knight Capital LMM desk** (2011-10, trading): DR/test market data reaching a live quoting path. "Nearly $7.5 million" (SEC ¶33).
- **CME Whaley → Bachelier** (2020-04-22, trading): a model whose domain excluded a reachable state (`log(F/K)` with F ≤ 0). *Sourcing caveat: the advisory text was never read directly; re-fetch Chadv20-152 / 160 / 171 before quoting it.*
- **Robinhood PFOF routing** (2015–2018, trading): a router objective function that ranked venues by payment received. $34,100,000 of customer harm net of commission savings; $65M SEC penalty.

**Predicate.** Every price, mark, book and rate carries a timestamp and a max age, and the gate is `now − ts > max_age`, not "is the socket up". A valuation used for liquidation, margin or settlement derives from multiple independent venues with a deviation bound and a staleness bound. A rate you can move for less than you can extract is not a mark, and the notional a market's own oracle can collateralise is bounded by that market's depth. A model whose support excludes a reachable state is replaced, not clamped at the input. Test, DR and sandbox fixtures are structurally unreachable from a production valuation path. Name the provenance before you assert.

**Owner.** `fin-onchain` ON15 (oracles); `fin-exchange-integration` EX5 (freshness gates market data) and mark-vs-index-vs-last selection; `fin-verification` VF2 (provenance).

### F13 · Change, deploy & configuration: *does correct source run correctly everywhere it runs?*

**Ask of the diff:** how many hosts, regions, shards, chains or environments must agree for this to be correct, and what proves they do?

**Mechanism.** Correctness is a property of the deployed fleet, not of the diff. A flag repurposed while a deployed consumer still reads the old meaning; dead money-path code left callable; a build that reached N−1 of N hosts; a rollback that was never a tested path; two environments "specified to be identical" and configured differently; a published rulebook that no longer describes the code; a toolchain that miscompiles a safety annotation. One codebase deployed to N chains multiplies one bug by N.

- **Knight Capital** (2012-08-01, trading): all at once: dead code left callable, a flag **repurposed** rather than retired, a deploy that reached seven of eight servers with no second-technician review, and a cumulative-quantity counter moved out of the generator that used it as a termination condition. $460,000,000.
- **TSB core-banking migration** (2018-04-20, payments/banking): two data centres "were, in certain areas, configured inconsistently despite having been specified to be identical", undetected across four years of testing. £32.7M customer redress; FCA £29.75M + PRA £18.9M = £48.65M.
- **RBS / NatWest / Ulster Bank, CA-7** (2012-06-17, payments/banking): an upgrade uninstalled in production on a path never exercised; the uninstalled state was incompatible with the previous version. FCA £42M + PRA £14M = £56M; 6.5M+ customers over weeks.
- **Bitcoin BIP-50 fork** (2013-03-11, on-chain): a per-node Berkeley DB lock ceiling *inside a validation path* made validity node-dependent. ~6h of ambiguous settlement finality; one double-spend.
- **Curve / Vyper** (2023-07-30, on-chain): Vyper 0.2.15/0.2.16/0.3.0 mis-allocated named re-entrancy locks, so two functions declared with the same lock name did not share one. ~$70M gross (~$52M net). **One of the catalogue's three "an agent could not have caught it" verdicts**: there was nothing to find above the compiler.
- **NASDAQ Trade Through / SHO Through** (2011-10 and 2012-08, venue): a configuration edit turned a gate into a no-op, and an acknowledged alert about a *missing component* never re-fired on subsequent starts. 2,004 trade-throughs; >4,400 non-compliant short sales. The same drift in the other direction is EDGA/EDGX (2015-01-12, $14,000,000 joint and several): published behaviour ≠ implemented behaviour, and NASDAQ separately ran a randomization step it had removed from its rules for 5½ years.

**Predicate.** Never repurpose a flag, enum or field a deployed consumer still reads. Grep every deployed artefact for readers first. Delete dead money paths rather than leaving them callable. Verify build identity on **every** host before enabling; a deployment that succeeded on N−1 of N targets is a failed deployment. Rollback is a change and needs its own test in a production-matching environment. Assert configuration parity across regions and shards as a continuous automated check, never as a design intention. Test every shard, not a representative one. Pin and record the exact compiler/toolchain version for value-bearing artefacts and run invariant tests against compiled output. Reconcile documented and implemented behaviour by a test that fails on divergence.

**Owner.** `fin-money-core` MC16 (the rules), principle P11; `fin-verification` owns what counts as **proof** that they hold.

---

## 2. The cross-domain table

Rows are the classes; columns are the five code positions the suite recognises. Each cell names the concrete form the class takes there. A class fillable in fewer than three columns would not be in this taxonomy.

| Class | Trading (client of a venue) | Payments | Ledger | On-chain | Venue (you are it) |
|---|---|---|---|---|---|
| **F1** Representation | tick/step/minNotional quantization; `abs(price)` in a margin formula; inverse & quanto contract units | currency exponent (JPY 0, KWD 3); charge / payout / display / calculation are four scales | an amount column with no currency dimension; `int32` cents; an aggregate wider than its column | `decimals()` cached as a constant; `1e18` against USDC's 6; `uint256` wrap before the check | quantity conventions inverted between messages: OUCH replace is chain-cumulative, ITCH `Modify` is a decrement |
| **F2** Rounding & residue | gross up for round-trip fees *before* quantizing, then quantize toward validity | per-line VAT vs the total; the remainder a partial capture destroys; FX spread booked separately | largest-remainder allocation into a named residue account; FX rate side and pivot | `mulDiv` with an explicit direction per leg; first-depositor share rounding; `>` where `>=` | pro-rata rounds down and cannot allocate everything; the FIFO exception; iceberg refresh |
| **F3** Sentinel & absence | a placeholder axe price reaching an order; a stub quote read as top-of-book liquidity | an amount field pre-populated with a value that is valid on submit | a missing price marked to `0` in a valuation; `account_id = ""` | `0x00` meaning "unproven"; `address(0)`; a call to a codeless address returning success | a price band computed from a default because the reference feed was unavailable |
| **F4** Identity & replay | clOrdID scope per venue: open-orders-only on Binance/OKX/Kraken, none on Deribit | `pspReference` vs `merchantReference`; what an `Idempotency-Key` does not survive | the transaction id derived from the payment's idempotency key; `reverses_transaction_id` unique | `(chainId, from, nonce)`, never the tx hash; log key `(chainId, blockHash, txHash, logIndex)` | UserRefNum and ClOrdID day-uniqueness; PossDupFlag vs PossResend; iLink3 `(sequence, UUID)` |
| **F5** Indeterminacy | 5XX / `-1006` / `-1007` / socket timeout / 429 are UNKNOWN; `-2013` right after placement proves nothing | a cached 500 the client cannot resolve alone; a fresh key after any non-409 4xx | a transfer timeout is not a failure; resolve unresolved intents on startup | `already known` is success; a broadcast that never mines; `searchTransactionHistory: true` | an execution generated but never delivered because the transport was severed |
| **F6** Ordering & arrival | the same fill on stream *and* poll; `CumQty` across a replace chain; `(Canceled, Filled)` is legal | no webhook ordering guarantee; a late `refund.created` regressing a settled refund | `effective_at` vs `created_at`; back-dated entries into a closed period | a re-included tx at a new `logIndex`; a reorg unwind as a reversing entry | a cancel and an aggressor totally ordered by one sequencer; per-instrument sequence resets |
| **F7** Concurrency | two workers minting one client order ID; a position read, computed, written back | a refund issued twice because the check and the act sat in different transactions | lost update on a balance at Read Committed; write skew across two rows under one invariant | a nonce allocator with two writers; a lock keyed on a per-process hash | revalidation consuming one cancel per pass = livelock; single-writer core with fencing tokens |
| **F8** Settlement state | a fill reported as final and busted inside the clearly-erroneous window | `refund.created` is pending; pending refunds count against the ceiling; ACH returns at 60 days | posted / pending / available, and only `available` authorises; holds with intrinsic expiry | PENDING on observation, AVAILABLE at the finality budget; L1 batch finality, not L2 block count | error and suspense positions; confirmations that escalate rather than merely withhold |
| **F9** Conservation | `Σ child_qty(parent) <= parent.qty`; a counter and a hard bound on the emit path | every clearing account returns to zero; the refund is funded by the capture it reverses | every transaction sums to zero per currency; `Σ customer balances <= custodied assets` | every balance-mutating path ends in the health check; credit the measured delta, not the event value | issued ≤ authorised, checked inside the issuance transaction; netting that reproduces the gross |
| **F10** Divergence & recon. | position and realized PnL against the venue's own report, joined on `trade_id` | the settlement report is the money; the API is state; the webhook is only a trigger | trial balance; materialised-balance checksum recompute; three-way recon with aged breaks | `Σ credited at-or-below finalized height == Σ observed on-chain deltas to deposit addresses` | **no external authority exists: you are the oracle**; the proof moves before deploy (T3) |
| **F11** Bounds & authority | client-side pre-trade limits; a collar on every marketable order; halt on ambiguous reconciliation | a finite business-derived amount ceiling that rejects; an affirmative act to send | which accounts may go negative, enforced at reservation time; no per-account exemption | a governance kill switch measured against the drain rate; per-asset borrow caps | 15c3-5 order-by-order rejection on the last hop; price bands; a kill switch requiring diagnosis to clear |
| **F12** Valuation & inputs | mark vs index vs last for stops, sizing and liquidation distance; freshness gates market data | the FX rate's provenance, side and timestamp recorded with the conversion | a displayed figure reads the authoritative ledger; revaluation rate provenance | `updatedAt` vs heartbeat; `minAnswer`/`maxAnswer`; sequencer uptime; AMM spot is not a valuation | settlement-price discretion; a self-referential mark the backstop's own position moves |
| **F13** Change & config | one adapter change reaching one of N venue processes; a repurposed enum a live consumer reads | a processor migration where old and new keys have different retention | migration cutover with per-account balance preservation, shadow comparison, a verified reverse path | an immutable target with multi-day fix latency; one codebase × N chains = N× blast radius; a pinned toolchain | a config edit turning a gate into a no-op; published rulebook vs implemented behaviour |

---

## 3. The classes that appear in generated and hastily-written code

**Different evidence, and it has to be read differently.** Nothing below is cited to an incident, because no postmortem describes any of it: a postmortem records the loss and attributes it to whichever class the missing control belonged to, never that the control was *named in a comment* and not built. These are defects of the writing rather than of the economics, and they are read out of code as written. What they share is that each one **looks, on the page, exactly like the thing it is not**, which is why they survive the author's own review, and why they cluster in code produced fast: by a model, or by a person against a deadline. Code that has been operated for a while has had them beaten out of it by restarts, incidents and on-call.

**Two of the five are new mechanisms; three are new signatures of §1 classes.** Saying otherwise would inflate the taxonomy. A1 and A2 are genuinely new, properties of the *authoring process*, not of the economics. A3, A4 and A5 are F7, F4 and F11 wearing a shape that incident review never produces and that diff review must learn to recognise.

**A1 · The prose TODO: the named risk documented instead of implemented.** The author identifies the correct control, articulates the risk accurately, and then writes a note about it. The absence is **documented**, and that is precisely what lets it pass self-review: the diff contains a paragraph proving the risk was understood, and no code enforcing anything. The phrasings recur and are greppable: a reconciliation job marked "**not built, suggest we schedule it**" · "that's a risk-policy call, not a technical one, needs a decision from whoever owns risk" · "persisting `last_trade_id` to disk/redis closes that hole; I left it in memory" · "mitigations I'd add before this handles real money" · "I left it out rather than write it half-right" · "should probably be required at the API layer". It implies a wording doctrine for every rule in this suite: a rule must be an artifact requirement ("there MUST be a call to X from Y"), never a consideration ("consider a dead-man switch"). Considering it is the failure mode; the rule has to demand the artifact.
**Predicate.** Every control the response names carries its evidence on the `controls:` line of the `FINANCIAL CHECK`: a real `file:line`, or an explicit `UNRESOLVED: <control> (<why>)`. At T2 and above the same line also carries the test name, and a control with no `file:line` fails the run. If it will not be implemented, the path raises `NotImplementedError` **on a path that is actually reached**. **Owner:** *implemented, not described*, stated in full in `fin-money-core` and specialised by name in every other skill, because it is asked in front of every class in §1.

**A2 · The documented invariant that is false.** A comment or docstring asserts a property the code does not have, and the assertion is what makes the defect survive review: the reader checks the claim against their model of what the code ought to do, never against the code. The recognisable shapes: "it flushes so the `OrderRefund` row exists before the Stripe call returns", where the flush sits forty lines *below* the call · "reserve a local row first so we always have a record even if the call times out", contradicted four lines later by `db.rollback()` · "the monotonic guard drops anything already reflected in the snapshot, so overlap is harmless and gaps are not possible", false as implemented · "`replaces_tx_hash` keeps the old hash so the confirmer can also match a receipt for the superseded tx", where the confirmer never reads that column · "move money into `refunded_cents` exactly once", written over a branch whose body is `pass` · "sequence numbers are consumed on rejects too, so the event stream has no gaps", in a design where `let _ = tx.send(ev)` can drop an event · "we only return `NotOwner` when the requesting client *does* own something … written to avoid the information leak", where the check does exactly the opposite. Design notes are where the author's **belief** lives, and the belief is frequently unverified. Incident postmortems record what code did; none records what its comments claimed.
**Predicate.** Read the design notes and docstrings as a list of claims; for each asserted property, either point at the test that proves it or delete the sentence. **Owner:** *a comment is a claim*, stated in `fin-money-core` and mirrored as `fin-verification` VF3.

**A3 · The decorative transaction: the lock released before the section it protects (a signature of F7).** Reviewing for the *presence* of a lock passes this every time; what fails is the *extent* of the critical section. The shapes: `with db.engine.begin() as conn: SELECT … FOR UPDATE`, where the lock dies at the dedent, before the sign and broadcast it was meant to protect · `poll_batch()` doing `FOR UPDATE SKIP LOCKED LIMIT 50` and closing the transaction after `fetchall()`, so N worker instances process the same 50 rows · an admin `reject()` that drops the row lock between the status check and `reverse()`: the withdrawal is broadcast inside that window, `reverse()`'s guard still accepts `'broadcast'`, and the user keeps both the coins and the balance · a worker using `async with session_factory()` with no `session.begin()`, so `FOR UPDATE` holds nothing at all · an `asyncio.Lock()` declared and never acquired. Every one of these reads as "uses a lock" in review, and several read as "uses a transaction".
**Predicate.** Name the lock, its key, its subject and its duration; the object holding the lock is the object that performs the act; verify the transaction boundary, not the presence of a lock call. **Owner:** `fin-money-core` MC12.

**A4 · Dedupe state that evaporates on restart (a signature of F4).** The code dedupes on exactly the right key and keeps the key set in memory, `_seen_trade_ids` as a `set`, `last_trade_id` as an attribute. The double-count then happens on precisely the path that recovers from a restart: the standard REST backfill re-applies every already-counted fill. The inverse shape is the same defect from the other side: an event that could not be resolved is still committed to `processed_events`, so the provider never redelivers it and the miss is permanent: dedupe working *against* recovery. Neither shape is visible to a unit test, or to a reviewer checking that dedupe exists; the failure lives only at the intersection of a restart and a backfill. The rule therefore has to be about **durability**, not about presence: a control can be entirely present and still not survive a process death.
**Predicate.** Dedupe state is as durable as the state it protects and is written in the same transaction. Mark an event processed only when it was actually applied; unresolvable goes to a dead-letter queue with an alert, never to "consumed". **Owner:** `fin-money-core` MC8.

**A5 · The safety constraint that defeats the safety operation (a signature of F11).** `CHECK (balance_cents >= 0)` reads as an unimpeachable control, and it makes the adjacent safety operation structurally impossible: `allow_overdraft=True` on `reverse_transfer` is dead code because the constraint rejects the clawback debit, and where only `transfer()` catches `CheckViolation`, a raw `psycopg.CheckViolation` leaks past the `TransferError` hierarchy mid-clawback. Account state shows the same shape: `_apply_movement` raising `AccountNotActive` for frozen accounts blocks **the standard fraud flow** (freeze the recipient, then claw back); you must unfreeze first, creating exactly the drain window the freeze existed to close. The general form is a constraint written against the happy path that also governs the emergency path, where its author never went. Asking whether a control *exists* will not find it; only asking whether it can be **operated against a hostile counterparty** will.
**Predicate.** Every safety constraint is tested against the operations performed under duress (clawback, reversal, freeze-then-recover, forced liquidation, kill switch), not only against the happy path. **Owner:** `fin-ledger` LG4.

**Related shapes, not promoted to a class.**
- **The "no work to do" branch that still commits progress**: `if (addresses.size > 0) { …getLogs… }` guards the query while `saveCursor(tx, { lastBlock: toBlock })` runs unconditionally, so on a fresh deploy with an empty address table the cursor sprints to the safe head and every address registered afterwards can never see a deposit in a passed block. That is F10's watermark predicate, the rule *proven coverage before the cursor advances*, not a new class.
- **Ghost-order resurrection**: a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts an order that a terminal event had already popped, because the monotonic guard `if existing is not None and ts < existing.update_time: return` is skipped once the order is gone; `reconcile` then sees the client id in `live`, skips re-placing, and the bot believes it is quoting with nothing on the book. The defect is a missing `_ => Err` default arm in the transition table, not a missing absorbing-terminal rule; `nautilus_trader` keeps its terminals deliberately non-absorbing (`crates/model/src/orders/mod.rs:250`) and catches this case with an explicit deny-by-default arm (`mod.rs:295`). F6's best worked example.
- **Hedge-mode collapse**: positions keyed on symbol alone with `positionSide` dropped, so a +5/−3 pair becomes a fabricated flat −3. F4.
- **Internal-transfer minting**: nothing checks that `from` is not itself one of our own deposit addresses, so moving USDC between two owned addresses creates a credit with no debit; the schema stores `from_address` and never reads it. F9.
- **Unchecked unsigned subtraction on a published aggregate**: `level.total_qty -= qty` on `u64` guarded only by `debug_assert`; in release, drift wraps to ~1.8e19 and is **published as depth** to consumers who trade against it. F1.

**The boundary of this section.** Everything here is a defect of code as first written: one file, one process, before it meets an ORM boundary, a wire schema, an analytics boundary or a multi-host deploy. That is why freqtrade's SQL `Float` money columns belong under F1 and not here, and why F1 survives at reference placement rather than being deleted: the storage and wire boundary is not where new code fails, it is where old code has already failed and nobody has looked.

---

## 4. How to use this on a diff

*The economic-diff gate* already produces the routing signal; this walk consumes it. Each gate answer points at a family, and one class is asked unconditionally.

| Gate answer | Walk |
|---|---|
| **AMOUNT** | F1 → F2 → F3, then F12 if the number came from outside |
| **EFFECT** | F5 → F8 → F9 |
| **AUTHORITY** | F11 → F12 |
| **REPLAY** | F4 → F6 → F7 |
| **ROLLOUT** | F13 |
| *(always)* | **F10** |

**The walk, in order, with the question that opens each step.**

1. **Is anything here an amount?** → F1, F2, F3. Cheapest and most local; answerable from the diff alone.
2. **Does the diff mint, consume or compare an identity?** → F4.
3. **Does it call something that moves value?** → F5.
4. **Does it consume an event someone else emits?** → F6.
5. **Does it read then write a quantity that authorises something?** → F7.
6. **Does it make value spendable?** → F8.
7. **Does it transform value (split, fan out, convert, net, issue)?** → F9.
8. **Does it report a number to anyone?** → F10.
9. **What bound stops it, and can that bound be clicked through?** → F11.
10. **Does it decide against a number it did not produce?** → F12.
11. **Must more than one host, region, shard or chain agree for this to be correct?** → F13.

**Stopping rules.**

- **Stop before step 1** if the gate exempted the diff: numbers that are analytics and never become an obligation (backtest statistics, greeks, implied vol, Monte Carlo), with no balance, order, payment or transfer written. The gate's job is to exempt, not to admit.
- **Stop at the first class the diff fails, fix it, and re-walk from the top.** A fix in one class routinely opens another: `CHECK (balance_cents >= 0)` is an F11 fix that creates an A5 defect, and Balancer's rounding fix is a representation change.
- **Steps 1–7 are answerable from the diff alone; steps 8–11 need the repo.** With only the diff, say so rather than passing them silently.
- **Do not walk steps 8–11 for a type-only change** unless the type crosses a module, storage or wire boundary, which is exactly where F1 fails.
- **F10 is not optional and not a tiebreaker.** If the diff adds a reported economic quantity and nothing compares it to an independent authority, that is the finding regardless of what else the walk turns up. It decides whether every other failure here is caught in an hour or by a partner bank six months later.
- **Count the classes.** Two or more failing classes on one diff is this corpus's signature of a large loss (Knight F13+F9+F10; CGML F3+F1+F11; FTX F8+F9+F11). Report the count.

Before any of this, **A1 and A2 apply to your own output**: a control you named and did not implement is the defect you named, and a property your comment asserts is a claim you must point at a test for.

---

## 5. What this taxonomy does not do

- **It does not partition.** The classes overlap by design; §0.3 shows the largest incidents failing three at once. Use them as questions, not buckets.
- **It does not claim diff-review coverage.** Across the 51 catalogued incidents the verdicts are **30 Yes · 18 Partly · 3 No**: roughly two-thirds of the money-loss surface is reachable by reviewing a diff. F1–F7 and F9 are largely diff-visible; F8 and F11 partly; **F10 and F13 mostly are not.** You can see that a diff adds a reported number with no reconciliation; you cannot see the eighth server, the vendor's stale manual, the data-centre estate, or the compiler. That is why the suite is not a linter and why `fin-verification` is a separate skill.
- **It does not cover security.** Unauthorised action is a different question with a different suite. Wormhole (2022-02, ~$326M) is a signature-verification failure and is deliberately excluded from the corpus this was derived from.
- **It does not cover risk-model error.** A leveraged position against a central-bank peg is not a defect. The in-scope thread is narrow and already covered: a risk engine that cannot *represent* a market state is F1.
- **It does not weight the classes for you.** How much a class *cost* and how often it is *written* are different quantities, and they disagree: F8 is enormous in the incident corpus, while A1 is invisible to every postmortem ever written and is a defect you will meet in most of the code this suite reviews. Ranking by loss and ranking by frequency give different orders, and neither is the order in which to walk a diff. `docs/rules.md` §1.2 owns that arbitration; this file owns the classification.
