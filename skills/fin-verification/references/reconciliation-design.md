# Reconciliation design: authority, join key, read path, cadence, tolerance

Which external authority is the counterparty for each economic quantity, which identifier the join runs on,
whether the read path could see a missing write at all, how often it runs, and how large a difference has to be
before it is one. Written for the case where the reconciliation already exists and finds nothing.

## Contents

- Naming the authority: one row per reported economic quantity, and what to do when two authorities disagree
- Join keys: the counterparty's identifier vs yours, and why `merchantReference` / `clientOrderId` are not unique
- Deriving a deterministic identifier for an event you inferred rather than observed
- Reading through a path independent of the writer, and the cache-against-itself anti-pattern
- Cadence: report availability, statement cutover, replication lag, and the first-run backfill
- Tolerance: the instrument's own unit, not a fixed epsilon

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
