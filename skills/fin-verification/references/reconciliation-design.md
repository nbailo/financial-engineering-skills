# Reconciliation design

The shapes a production reconciliation takes, per system class, and the decisions that determine whether it
finds anything: which external authority is the counterparty for each economic quantity, which identifier the
join runs on, how often it runs relative to the authority's own publication lag, how a difference is
classified, how long a break may age before it escalates, and what the system stops doing while a break is
open. Written for the case where the reconciliation already exists and finds nothing, which is the common one.

## Contents

- Naming the authority: one row per reported economic quantity, and what to do when two authorities disagree
- Join keys: the counterparty's identifier vs yours, and why `merchantReference` / `clientOrderId` are not unique
- Deriving a deterministic identifier for an event you inferred rather than observed
- Reading through a path independent of the writer, and the cache-against-itself anti-pattern
- Cadence: report availability, statement cutover, replication lag, and the first-run backfill
- Tolerance: the instrument's own unit, not a fixed epsilon
- Break classification: six classes, and the action each one implies
- The break record schema, the aged bucket, and the periodic sweep
- Suspense and clearing accounts: keeping the trial balance balanced while a break is open
- Fail-closed policy: what stops, what must keep working, who reopens the gate
- Halt levels: naming the blast radius in the code, and the six prohibitions
- Alert routing and the detect-test
- Worked shape 1: exchange position and balance
- Worked shape 2: payment processor payout
- Worked shape 3: on-chain deposit crediting
- Worked shape 4: internal double-entry ledger
- Canary and tripwire probes
- Migration reconciliation: version-gap detection during a dual-write cutover

## Naming the authority

Write one row per economic quantity you report, before writing any SQL. A reconciliation without this table
reconciles whatever was convenient to query, which is usually your own cache.

| Quantity you report | External authority | Read it from | Join key | Not authoritative |
|---|---|---|---|---|
| Open orders, position size | The venue | the venue's position endpoint (Binance USD-M: `positionRisk`) and order-status endpoint | venue `orderId`, `tradeId` | your fill accumulator; the WS snapshot you cached at connect |
| Realized PnL / fees | The venue | per-fill fee fields + the venue's own realized figure | `tradeId` | your recomputation alone |
| Payment money movement | The processor's ledger | Stripe balance transactions; Adyen **Settlement details report** | `pspReference` / balance-transaction id | `PaymentIntent.status`, the webhook payload |
| Payout / bank credit | The bank's own statement or cash-position report | statement line | payout batch id | the processor's payout object alone |
| On-chain custody balance | The chain | archive node or indexer over block data | `(chainId, blockHash, txHash, logIndex)`; `(hash, traceId)`; `(txid, vout)`; `(account, Sequence)` | the custodian's API balance |
| Customer liability | Your own journal, replayed | `SUM(amount)` over `entries` | `account_id` | the materialised `balances` row |

Three rules this table encodes:

1. **Payments state is not money.** Stripe: *"Subsequent refunds, disputes, and outcomes are reflected on the
   Charge… even though the PaymentIntent remains `succeeded`"*, and it names balance transactions as the
   recommended starting point for balance reporting. Adyen names the Settlement details report as *the*
   transaction-level reconciliation record: *"all balance movements that explain the financial standing of
   your merchant account"*. A system that closes its books on lifecycle state alone is structurally wrong.
2. **Never reconcile a vendor against the same vendor.** For custody, the independently checkable identity is
   `Σ(observed inbound) − Σ(observed outbound) − Σ(fees paid) == current on-chain balance`, computed from
   block data, not from the custodian's balance endpoint, which cannot detect vendor-side bugs.
3. **When two authorities disagree, the one that moved the money wins, and the disagreement is itself a
   break.** Do not pick the larger, the newer, or the one that makes the totals work. Record both values in
   the break row (`source_a`, `source_b`) and let the aged bucket force a human decision.

## Join keys

**Join on the counterparty's identifier. Never on yours.**

| Your identifier | The counterparty's | Why yours fails |
|---|---|---|
| `merchantReference` (Adyen) | `pspReference` | Adyen does not enforce uniqueness. One retried attempt produces two `pspReference`s under one `merchantReference`; the join drops or duplicates a row silently |
| `clientOrderId` / `newClientOrderId` (Binance) | `orderId`, `tradeId` | unique only among **open** orders; a re-sent create after a timeout carries the identical id (CCXT does exactly this, `ts/src/binance.ts:6969`) |
| your `Idempotency-Key` | the created object's id | the key is an input, not a record; Adyen scopes keys to the company account, max 64 chars, valid a documented **minimum of 7 days**; beyond that horizon it identifies nothing |
| `txHash` | `(chainId, blockHash, txHash, logIndex)` | one tx carries many transfers; a reorg reuses the hash on a different block |
| ARN (card) | the processor's refund/reversal object id | a refund processed as a **reversal produces no ARN at all**; the charge drops off the statement instead |

Keep your identifier as a **secondary, non-unique attribute** on the break row so a human can find the intent.
Never make it the join predicate.

**Your own order ledger is the durable audit path, not the venue's history.** Binance archives cancelled or
expired orders with no executed quantity after 90 days (`-2026 ORDER_ARCHIVED`), so a reconciliation that
re-derives history by querying the venue by client id goes blind at the 90-day boundary and reports the orders
as missing-there.

## Deriving a deterministic identifier for an inferred event

A reconciliation that *infers* a fill, a fee or a credit the venue never gave you an id for must mint one, and
minting it from a UUID4 means the same inference after a restart produces a second, different record; the
reconciliation double-counts itself. Derive it from venue-supplied fields only, including a venue-supplied
timestamp. `nautilus_trader`, `crates/execution/src/reconciliation/ids.rs:71-101`:

```rust
let mut seed = String::from("reconciliation-fill");
append_seed_part(&mut seed, account_id.as_str());
// instrument_id, client_order_id, venue_order_id, side, type, filled_qty, last_qty, last_px, position_id
append_seed_part(&mut seed, &ts_last.as_u64().to_string());   // venue-provided
TradeId::new(deterministic_uuid_from_seed("reconciliation-fill", &seed))
```

The rationale is documented at `ids.rs:103-107`: the `account_id` *"scopes the ID to the venue account,
preventing cross-account collisions"*, and the venue-provided `ts_last` *"ensures that successive
reconciliation incidents with the same shape get distinct IDs, while the same logical event replayed after
restart still hashes the same (venue re-reports identical ts)"*.

Two consequences worth stating in the code: the seed must contain **no local clock, no local sequence, no
random component**; and if the venue does not supply a timestamp for that event, you cannot mint a stable id
and the event belongs in a break row, not in the ledger.

## Reading through a path independent of the writer

Independent means the read reaches the same underlying facts by a different mechanism than the one that wrote
them. Concretely:

| Read path | Independent of the writer? | What it can find |
|---|---|---|
| Re-read the cache the writer populated | **No** | arithmetic bugs only. Never a missing write |
| Re-read the writer's own materialised `balances` row | **No** | nothing the writer got wrong |
| `SELECT SUM(amount) FROM entries WHERE account_id = ?` vs `balances.amount` | Yes, within your store | drift between journal and materialisation |
| A fresh REST/report query against the counterparty | Yes | missing writes, extra writes, amount and attribution errors |
| A replay of the append-only log by a separate process | Yes | writer logic errors, ordering errors |

The failure is not subtle. The "reconciliation" re-reads the cache the writer populated (the first row of
that table) and reports agreement forever. Even `nautilus_trader` computes the continuous check and discards
the result: `crates/execution/src/engine/mod.rs:1737`

```rust
let _ = check_position_reconciliation(report, cached_signed_qty, size_precision);
```

under a module docstring (`reconciliation/positions.rs:19-21`) calling it *"the core invariant maintained
here"*. Its **startup** gate, by contrast, is the strongest form shipped anywhere: `crates/live/src/node/mod.rs:440-447`
aborts startup on reconciliation failure and never reaches `start_trader()` at `:454`.

## Cadence

State the cadence against the authority's own publication lag, and say which lag you mean in a comment. A
reconciliation that runs faster than the lag oscillates and gets muted; one that runs slower than the
correction window discovers breaks after the money is gone.

| Authority | Documented lag | Usable cadence |
|---|---|---|
| Binance REST position/order reads | endpoints are labelled **Matching Engine / Memory / Database**; *"the API system is asynchronous, so some delay in the response is normal and expected"*. The private stream is ME-sourced; many REST reads are not | slower than the observed Memory→Database propagation; treat `-2013 NO_SUCH_ORDER` immediately after placement as *not yet visible*, never as *not created* |
| Stripe balance transactions | `pending → available` transition; funds unreconciled by Stripe are swept to `payment_unreconciled` after **90 days** | daily on `available`, plus a long tail job over the 90-day sweep horizon |
| Adyen Settlement details report | per-batch; settlement lag by scheme and method is explicitly acknowledged, with weekend consolidation | per payout batch, keyed on the batch, never on wall-clock days |
| Card dispute tail | ~120 days from payment, 180 for many LPMs, and *from the event date* for future-dated services; a `lost` dispute can later flip to `won` with no bound | a reversal-tail job, not a daily diff |
| ACH unauthorized return | **60 calendar days** | ditto |
| Refund failure | `REFUND_FAILED` can arrive **days** later; Stripe documents refund failure up to **30 days** | ditto |
| Chain (finalized view) | chain-specific; run a fast unfinalized view for UX and a settled finalized view for accounting, and **reconcile money only against the settled view** | on finality, not on block arrival |

**The first run must survive un-backfilled history.** A per-account reconciliation *broken on day one by
opening balances nobody backfilled* is the usual first-run failure, and it guarantees the alert is muted
before it ever fires. Either backfill opening balances before the first run, or make the job's window start at
a recorded `reconciled_through` watermark that is initialised, not defaulted to epoch.

## Tolerance

A fixed epsilon is wrong for both a 0-decimal asset and an 18-decimal one. Express tolerance in the
instrument's own unit. `nautilus_trader`, `crates/execution/src/reconciliation/positions.rs:40`:

```rust
const DEFAULT_TOLERANCE: Decimal = Decimal::from_parts(1, 0, 0, false, 4); // 0.0001 = 0.01%
```

paired with a **single-unit** tolerance keyed on the instrument's size precision
(`is_within_single_unit_tolerance`, called at `positions.rs:423`). The relative bound catches proportional
drift; the single-unit bound stops a one-tick difference on a coarse instrument from paging every night.

Three prohibitions:

- **Never let an allocation remainder be absorbed by a tolerance.** `sum(parts) != total` after splitting an
  amount is a bug in the allocator, not a rounding difference; assert `Σ legs == total` exactly at the split.
- **Never use a float relative tolerance on decimal money.** `math.isclose` coerces `Decimal` operands to
  `float` and applies a relative `1e-9`, so it silently decides terminality on a value that is no longer the
  ledger's.
- **A tolerance is a bound on the *break threshold*, never on the *posting*.** Within tolerance still means the
  two numbers differ; the ledger posts the exact observed value either way.

## Break classification

Six classes. The class decides the action; a job that emits one undifferentiated "mismatch" cannot route.

| Class | Detection predicate | Action |
|---|---|---|
| **Timing difference** | present in A, absent in B, and B's documented lag has not elapsed | hold in an `aging` state, re-evaluate next run; escalate only past the lag |
| **Missing-here** | authority has a record you do not | ingest it idempotently by the authority's key, then re-run. A processor-side reconciliation object surfaces **only via webhooks** on Stripe, so a job that never polls will never see it |
| **Missing-there** | you have a record the authority does not, past the lag | you may have acted on an effect that did not happen. Fail-closed on that scope; never re-send |
| **Amount mismatch** | same key, different amount beyond tolerance | post the authority's value, book the delta to suspense, escalate |
| **Attribution mismatch** | amounts net to zero but land on different accounts/instruments/currencies | the trial balance still balances, so nothing else will catch it. Always a break, never auto-corrected |
| **Duplicate** | two local records for one authority key, or vice versa | the join key was yours, not theirs. Fix the key; the duplicate itself goes to suspense pending reversal |

Two shapes that masquerade as amount mismatches and are not:

- **Partial capture.** Stripe emits a `charge` balance transaction for the **full authorized amount** plus a
  `refund` balance transaction for the uncaptured portion. A reconciler that reads `type=refund` as "customer
  was refunded" double-counts revenue reductions. Reconcile on `reporting_category`, not `type`; `adjustment`
  alone is overloaded across dispute debits, dispute reversals and refund failures, disambiguated only by
  `description`.
- **Force post / late presentment.** A clearing record can arrive with no matching authorization, or long
  after it, with amounts and dates that do not line up; the processor manufactures and backs out an
  authorization entry to make it post. Auth↔clearing matching must tolerate this by design.

## The break record schema and the aged bucket

```sql
CREATE TABLE recon_break (
  id              bigserial PRIMARY KEY,
  detected_at     timestamptz  NOT NULL DEFAULT now(),
  recon_name      text         NOT NULL,           -- which job
  class           text         NOT NULL CHECK (class IN
                    ('timing','missing_here','missing_there','amount','attribution','duplicate')),
  source_a        text         NOT NULL,           -- 'ledger:entries'
  source_b        text         NOT NULL,           -- 'adyen:settlement_details'
  authority_key   text         NOT NULL,           -- the counterparty's identifier
  local_ref       text,                            -- yours, non-unique, for humans
  amount          numeric(38,0) NOT NULL,          -- minor units, signed: a - b
  currency        char(3)      NOT NULL,
  account_id      bigint       REFERENCES accounts(id),
  status          text         NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','aging','escalated','resolved','swept')),
  suspense_txn_id bigint       REFERENCES ledger_transactions(id),
  resolved_at     timestamptz,
  resolution      text,
  UNIQUE (recon_name, authority_key, class)        -- re-running the job must not multiply rows
);
CREATE INDEX ON recon_break (status, detected_at) WHERE status <> 'resolved';
```

`amount` is `numeric(38,0)` in minor units, never `float`; mature projects still declare `Float` money
columns (freqtrade does), and the storage boundary is where the type survives or is lost.

**Aging.** `open → aging` when the authority's lag has not elapsed; `→ escalated` at a hard threshold stated in
the same unit as the cadence (not "soon", not "a few days"); `→ swept` by a **periodic sweep with a fixed
schedule** that expenses the residue to a named account and leaves the audit trail. The Federal Reserve's own
Difference account is swept monthly and expensed; the sweep is not an admission of defeat, it is what stops
the bucket from becoming an unbounded liability nobody reads.

**A break is never resolved by an automatic corrective write.** Repair is a separate, reviewed job with its own
entrypoint. An automatic corrective writer that is itself wrong writes the error into the authority's shape.

## Suspense and clearing accounts

The delta posts to a real `suspense`/`clearing` account **in the chart of accounts** (not a nullable column,
not a log line), so the trial balance still balances while the break is open. Square's Books is append-only:
*"there are no update statements for the tables presented on the diagram, only inserts"*, and errors are
corrected by **new balancing entries**.

Steady state is the assertion. Stripe: *"At steady state, terminal (nonclearing) reservoirs are full, and
intermediate (clearing) pipes are empty"*, so *"a single missing, late, or incorrect transaction immediately
creates a detectable accuracy issue with a simple query."* That converts reconciliation from a batch job into a
continuously queryable invariant:

```sql
SELECT account_id, currency, SUM(amount) AS bal
FROM entries JOIN accounts USING (account_id)
WHERE accounts.kind = 'clearing'
GROUP BY 1,2 HAVING SUM(amount) <> 0;   -- every row here is a break, per currency
```

`HAVING SUM(amount) <> 0` must be evaluated **per currency**: the accounting equation holds per currency, and
naive FX booking breaks it. Each clearing account carries a declared expected settlement window; a nonzero
balance older than that window escalates.

Reconcile on three axes, not one; Stripe's Data Quality Platform names them: **clearing** (did the flow reach
a terminal state?), **timeliness** (did the data arrive on time?), **completeness** (do we have all of it?).
Balance equality alone catches neither lateness nor self-consistent missing data. Prove completeness with
**order-independent checksums over bounded time windows** between the system of record and each derived store,
not row-by-row diffs: a single omission breaks the checksum (Uber's model).

## Fail-closed policy

The reconciliation result is an input to a risk gate, not just to a dashboard. Name the scope and the level in
the code, at the call site.

| Break state | What stops | What must keep working | Reopened by |
|---|---|---|---|
| Position/balance mismatch on one instrument | new and increasing exposure **on that instrument** | `cancel_all`, `flatten`, `close`, settle, with a test proving they work while the gate is closed | a **successful reconcile**, never a timer, and never the code path that closed the gate |
| Overfill (venue reports more filled than ordered) | trading on that instrument | reads, cancels | successful reconcile after the fill is booked |
| Break on a customer-facing balance | debits from that account | reversals and clawbacks against the frozen account | reviewed repair job |
| Reconciliation job itself failed to run or errored | treat as **unknown**, not as clean | everything else | next successful run |

**An overfill is an unreconciled economic fact: record it as a break and stop trading that instrument. Never
silently drop it and never silently clamp it.** `nautilus_trader` ships the opposite default:
`allow_overfills: bool` with `#[serde(default)]` ⇒ `false` (`execution/src/engine/config.rs:61-65`), and with
`false` the fill report is **discarded entirely** (`return None`, `reconciliation/orders.rs:785-796`) while
the live path `anyhow::bail!`s (`engine/mod.rs:3600-3624`). The venue sent you units; your model now disagrees
with the venue by exactly that amount, traced only by a `WARN`.

Startup is the one place to be strictly harsher: gate `start_trader()` on a successful startup reconciliation
and abort if it fails, rather than starting and reconciling concurrently.

## Halt levels: naming the blast radius in the code, and the six prohibitions

"Halt" names six different actions with wildly different blast radii. Name which one, in the code, at the
call site. This applies to any runtime invariant that fires on a money path, not only to a reconciliation
break.

| # | Halt level | What it does | Obligations |
|---|---|---|---|
| 1 | Reject the operation | this call fails, typed; process fine | untouched |
| 2 | Freeze one aggregate | writes to that account/symbol refused; reads still serve | untouched |
| 3 | Fail-closed (risk-off) | no new or increasing exposure; cancel, close, flatten, settle stay hot | actively managed |
| 4 | Cancel-all plus disconnect the emitter | withdraw resting orders, sever order entry; risk and drop-copy stay up | actively managed |
| 5 | Quiesce | stop accepting *and* producing; drain; deliver or explicitly void everything already produced | drained, then frozen |
| 6 | Process abort | `panic` / `exit` | **abandoned** |

Evaluate the predicates in order; first match wins. The last column names who designs the response;
`fin-verification` proves the response exists, is at the smallest scope that provably contains the breach,
and is reachable by a test.

| Observable predicate | Response | Designed in |
|---|---|---|
| No external effect yet, and the check runs in the same transaction as the write | **Level 1**, typed, terminal for that idempotency key. Never `log.warn` and proceed; never clamp into range | `fin-money-core`, `fin-ledger` |
| Wrong value in your own store; no counterparty acted on it; no open position | **Level 2**, write path only. **No automatic corrective write**; repair is a separate reviewed job | `fin-ledger` |
| An external record disagrees with yours, and money left / a fill happened / a customer saw the balance | Neither halt nor silent reversal: book to a named, aged, reversible **suspense** account, keep operating, escalate on a clock | `fin-ledger` |
| A position, working order or obligation exists whose value moves without you acting | **Level 3.** Record the true value, alert, close the risk gate for that scope. `cancel_all` and `flatten` MUST work while it is closed, with a test proving it. Reopen only on a successful reconcile, never on a timer, never by the code path that closed it | `fin-exchange-integration` |
| Own output exceeded its bound relative to its input (`orders_out` vs `orders_in`, `shares_issued` vs authorised, `payouts` vs instructions) | **Level 4**, automatic. The bound is checked **on the emit path before the send**, not by a monitor, and the flag must not be resettable by the component that tripped it | the venue-side matching and settlement design |
| Recomputable from an append-only log that passes its own checksums | Mark the view stale; return a typed `Stale{as_of}`, never a stale number, never zero; rebuild | `fin-ledger` |
| Every value in the relation was produced by this process, with no network, file, config or clock | **Level 6.** Crash. See *assertion placement* in `evidence-by-risk.md` | `fin-verification` |

Six prohibitions, each traceable to an incident, and each a discipline failure rather than an ignorance
one:

- **Never abort a process holding unmanaged obligations.** Ariane 501: *"It was the decision to cease the
  processor operation which finally proved fatal."*
- **Never let the failure path create state while the system is live and aberrant**: no retry, no
  resubmit, no hot rollback. Knight ¶27: *"This action worsened the problem."*
- **Never disable the failing check as the mitigation.** NASDAQ 2012 removed the validation code from the
  failover path to get the cross out, and that is what created the error position.
- **Never silently drop the violating event, and never clamp a reported quantity into range.** The units
  were really received. nautilus's `allow_overfills` defaults to `false`, which discards the fill report
  entirely.
- **Never gate the risk-reducing path on the same flag that gates the risk-increasing path.**
- **Never implement a halt by severing the transport.** A halt means the engine is quiesced AND everything
  already produced is delivered or explicitly voided.

Where the invariant can be transiently false by design, give it a self-heal window before escalating. LULD
waits 15 seconds in Limit State before pausing. A check that halts on a momentarily-inconsistent
intermediate state is itself an availability bug.

## Alert routing and the detect-test

The alert destination is a **config key with no default that raises at import if unset**. "Page a human" and
"a channel with a named owner" are not things code can contain; they become comments, which is the exact
failure this rule exists to prevent.

```python
ALERT_SINK = os.environ["RECON_ALERT_SINK"]   # KeyError at import, not at 03:00 on a break
```

The alert also has to arrive somewhere a human reads. Knight emitted 97 "Power Peg disabled" emails in the
89 minutes before the open and nobody read them: the signal existed, and nothing proved it reached a
reader. A destination with no owner is the same defect as no destination.

Then prove it detects. The job running is not the same claim as the job finding anything.

```python
def test_recon_detects_seeded_amount_mismatch(fresh_migrated_db, fake_authority, alert_sink):
    # freshly migrated: an un-backfilled opening balance FAILS here instead of muting prod
    seed_local_entry(account="cust:42", amount_minor=10_00, currency="USD")
    fake_authority.record(psp_reference="PSP123", amount_minor=9_00, currency="USD")

    run_reconciliation(name="processor_settlement", as_of=date(2026, 3, 1))

    b = one(select_breaks(recon_name="processor_settlement"))
    assert (b.class_, b.amount, b.currency) == ("amount", 100, "USD")
    assert (b.source_a, b.source_b) == ("ledger:entries", "processor:settlement")
    assert b.authority_key == "PSP123"
    assert trial_balance_is_zero_per_currency()      # suspense posting kept it balanced
    assert len(alert_sink.messages) == 1             # exactly one, not zero and not one per row

def test_recon_clean_run_produces_no_break_and_no_alert(fresh_migrated_db, fake_authority, alert_sink):
    ...
    assert select_breaks() == [] and alert_sink.messages == []
```

Both halves are required: the clean-run assertion is what stops a job that alerts on everything from passing.

## Worked shape 1: exchange position and balance

Authority: the venue. Join key: the venue's `tradeId` and `orderId`. Assertions, per `(symbol, positionSide)`:

```
Σ(signed filled qty from your fill store, deduped on venue tradeId) == venue position size
local free balance per asset                                       == venue free balance
Σ(fee from each fill)                                              == venue's reported fee per fill
local realized PnL                                                 == venue's own realized figure
```

Where the venue publishes its own realized figure on the execution event, cross-check it **on every event**,
not only on the schedule. Receiving Binance's `rp` realized-PnL field on every event and never comparing it
to anything is the common failure: the authority's own number arrives free and goes unread. Cadence must
exceed the venue's replication lag (ME/Memory/Database). Tolerance in the instrument's tick. On mismatch:
record the venue's value, level-3 fail-closed on that instrument, reopen only on a successful reconcile.

## Worked shape 2: payment processor payout

Authority: the processor's money ledger, then the bank statement. Two joins, in order:

1. **Your ledger ↔ balance transactions / Settlement details.** Join on `pspReference` (Adyen) or the balance
   transaction id (Stripe). Group by `reporting_category`, never `type`. Expect and classify rows that have no
   payment-lifecycle counterpart at all: fees, InvoiceDeductions, DepositCorrections,
   `connect_collection_transfer` (a negative connected balance recoverable for 180 days),
   `payment_unreconciled`.
2. **Settlement/payout total ↔ bank statement line.** Join on the payout batch id. This is the leg that catches
   a processor-side balance you believed but never received.

Platform-specific breaks that are *by design* and must be modelled, not tolerated: *"refunding a charge has no
impact on any associated transfers. It's up to your platform to reconcile any amount owed back to it"*, and,
for asynchronous payment methods, *"Stripe doesn't automatically reverse a transfer if the associated async
payment fails… your platform's balance is debited for the transfer amount. You must then manually reverse the
transfer."* Both produce a clearing account that never drains unless the reconciliation opens a break.

## Worked shape 3: on-chain deposit crediting

Authority: the chain, read from block data, not the custodian's API, and not your indexer's own cache.

```
per address you control, over the FINALIZED view only:
  Σ(inbound) − Σ(outbound) − Σ(fees paid) == on-chain balance
per credit:
  idempotency key = (chainId, blockHash, txHash, logIndex)     -- never txHash alone
```

Traps this shape must encode: `(hash, traceId)` is the key for an EVM **internal** transfer (a deposit
detector iterating `block.transactions[].to` misses every contract-initiated transfer); `(txid, vout)` for
UTXO; `(account, Sequence)` for XRPL. Read contract state at the log's own block:
EIP-1898's stated motivation is *"if there is a re-org in between when the balance of the sender is queried via
`eth_getBalance` and when the balance of the recipient is queried, the balances may not reconcile"* (errors
`-32000` non-canonical, `-32001` block not found). And gas-tank fees are paid from an enterprise address that
is neither leg of the sweep, so a "sum the deltas of the two wallets" reconciliation balances perfectly while
house fee expense is understated without bound; post the fee leg explicitly or it is invisible forever.

## Worked shape 4: internal double-entry ledger

No external oracle exists: the ledger *is* the authority (authority SELF). The reconciliation therefore compares
independent derivations of the same facts inside your own store, and the proof burden shifts before deployment.

```sql
-- 1. conservation, globally and per transaction
SELECT txn_id FROM entries GROUP BY txn_id, currency HAVING SUM(amount) <> 0;
-- 2. materialised balance vs replay of the journal  (order-independent, per currency)
SELECT b.account_id, b.currency
FROM balances b JOIN (SELECT account_id, currency, SUM(amount) s FROM entries GROUP BY 1,2) e
  ON (e.account_id, e.currency) = (b.account_id, b.currency)
WHERE b.amount <> e.s;
-- 3. clearing pipes empty  (see the suspense section)
-- 4. completeness checksum per bounded window vs each derived store
```

The materialised `UPDATE` belongs in the **same transaction** as the entry `INSERT` and carries a monotonic
version; the recompute above runs on a schedule and **does not fix the balance in place**: it raises a break.
Uber's version of this runs *"every 24hrs"* and checks *"whether we collected and disbursed on every order"*.

## Canary and tripwire probes

A low-value probe transaction pushed through the **real** money path on a schedule, with an expected terminal
balance asserted afterwards, tests the parts a comparison job cannot: that the path is reachable, that the
webhook still arrives, that the report still contains the row. It is a **SHOULD**, not a MUST: treat it as a
liveness probe for the pipeline, never as a substitute for the comparison. Keep the canary account in the
chart of accounts, exclude it from customer-facing aggregates by account kind (never by hardcoded id), and
alarm on *any* deviation, since its expected balance is known exactly.

## Migration reconciliation

A dual-write cutover needs three mechanisms, and teams routinely ship only the first:

1. **Shadow (dual-read) validation on live traffic**: catches divergence on recent, frequently-read data only.
2. **A full offline comparison of historical data**, feeding an **incremental backfill loop**, iterated until
   clean. Shadow traffic never touches cold rows; this is the leg that finds them.
3. **Version-gap detection on the change log.** Uber's `EntityChangeLog` consumer detects **version gaps** and
   back-fills; i.e. the migration's safety net is a reconciliation, not a test.

Retain fallback to the old store through cutover, and gate the cutover on a pre/post reconciliation asserting
per-account **and** aggregate balance equality plus referential binding. The absence of exactly this discipline
is the TSB story.

Emit the migration's own break rows into the same `recon_break` table with a distinct `recon_name`, so the
cutover cannot be declared done while breaks are open.
