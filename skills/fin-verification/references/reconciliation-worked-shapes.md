# Worked reconciliation shapes

What is compared, on which key, against which authority, and what happens on a mismatch, per system class.
Then the canary probe and the reconciliation a dual-write cutover needs.

## Contents

- Worked shape 1: exchange position and balance
- Worked shape 2: payment processor payout
- Worked shape 3: on-chain deposit crediting
- Worked shape 4: internal double-entry ledger
- Canary and tripwire probes
- Migration reconciliation: version-gap detection during a dual-write cutover

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
-- 3. clearing pipes empty  (see reconciliation-breaks.md)
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
