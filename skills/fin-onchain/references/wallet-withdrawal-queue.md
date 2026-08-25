# Withdrawal orchestration, gates, and the outbound queue

A payout leaves through a queue whose ordering you assign, past gates that reject hours later for reasons that
have nothing to do with the chain. Each gate needs a reversal path bound to it.

## Contents

- **Chain reserves and minimums**: why "withdraw max" is wrong; the error codes; coinbase maturity.
- **Withdrawal orchestration**: gross vs net; the two batch architectures; per-recipient idempotency.
- **Asynchronous rejecting gates**: screening, freezes, authorisation expiry, returned VASP transfers.
- **The sequence allocator and the outbound queue**: the single-writer lock and its span; the two ways a
  lock key stops being a lock; the three preconditions a broadcast must clear before it is sent.

## Chain reserves and minimums

"Withdraw max" is `spendable − reserve − (fee if the withdrawal is net)`; the reserve is per-chain
configuration. Omitting it fails the withdrawal; a wrong one strands funds or reaps the account.

| rule | chains | surfaced as |
|---|---|---|
| destination account must be created with a minimum amount | Fireblocks: "Only the Polkadot, Kusama, and Westend (testnet) blockchains have this limitation" | `NEED_MORE_TO_CREATE_DESTINATION` |
| a minimum must remain in the source wallet at all times | same family | `INSUFFICIENT_RESERVED_FUNDING`: "Resubmit the transaction with an amount that leaves at least one DOT" |
| destination does not exist and the payment is below the account reserve | XRP Ledger | `tecNO_DST_INSUF_XRP` |
| coinbase outputs unspendable for **100 confirmations** | Bitcoin | `SPEND_COINBASE_TOO_EARLY`; Core's separate `immature` balance bucket |

## Withdrawal orchestration

**Gross versus net is an explicit statement, never an inference.** Fireblocks `treatAsGrossAmount` defaults to
`false`: the recipient receives `amount` and the sender pays the fee on top. With `true` the fee comes out of
`amount`, and the degenerate case is `AMOUNT_TOO_SMALL`: "the transaction fee is higher than the net transfer
amount on a gross transaction". Inverting the flag produces a per-withdrawal shortfall or overpayment that is
invisible per transaction and unbounded in aggregate: store the choice on the payout row and assert the
recipient's observed delta against it in reconciliation.

The two batching architectures have opposite failure modes, and one rule survives both:

| architecture | atomicity | the failure | what it breaks |
|---|---|---|---|
| **one transaction, many outputs** (UTXO batch, EVM multicall) | atomic on-chain: all recipients paid or none | one txid covers N ledger rows; an RBF replacement or a drop invalidates all N at once | if each row independently times out and re-issues, N double payouts |
| **one request, many transactions** (Fireblocks aggregated) | **not** atomic. Fireblocks `PARTIALLY_FAILED`: "One or more aggregated transactions submitted as a single operation have failed… aggregated transactions are submitted to the blockchain network individually and can be partially or fully completed or fail" | some recipients paid, some not | retrying the *request* re-pays everyone who succeeded |

**The idempotency key is per-recipient-payout, and the batch is a grouping over those keys, never a
replacement for them.**

```sql
CREATE TABLE payouts (
  payout_id       uuid PRIMARY KEY,
  customer_id     bigint        NOT NULL,
  chain           text          NOT NULL,
  asset           text          NOT NULL,
  amount_minor    numeric(38,0) NOT NULL,          -- integer minor units; never a float column
  treat_as_gross  boolean       NOT NULL,          -- stated, not inferred
  destination     text          NOT NULL,
  memo            text,                            -- NULL only where the chain has no tag concept at all
  external_tx_id  text          NOT NULL UNIQUE,   -- custodian-side key, one per PAYOUT, never per batch
  batch_id        uuid          REFERENCES payout_batches(batch_id),   -- grouping only
  txid            text, vout integer,              -- txid may change (RBF); it is never the identity
  CONSTRAINT one_leg_per_payout UNIQUE (batch_id, customer_id, destination, amount_minor)
);
```

Reconcile each leg independently against `(txid, vout)` or the leg's own `externalTxId`, and retry only unpaid
legs under their own keys.

## Asynchronous rejecting gates

Between "customer requested" and "broadcast" sit gates that reject asynchronously, hours later, for reasons
that have nothing to do with the chain. Each one needs a reversal path bound to it.

| status / sub-status | meaning | required ledger action |
|---|---|---|
| `PENDING_AML_SCREENING` | screening runs before signing; the debit is already taken | the clearing account holds it; alert on age |
| `REJECTED_AML_SCREENING` | terminal | reverse the customer debit |
| `AUTO_FREEZE` / `FROZEN_MANUALLY` | "Any associated assets will not be available until an Admin-level user unfreezes them" | asset is not spendable; exclude from the withdrawable-reserve assertion |
| `PENDING_AUTHORIZATION` | "If no action is taken for two hours, the transaction will fail" | **a withdrawal can fail for a calendar reason, not a chain reason**; expiry is a first-class outcome with its own reversal |
| `QUEUED` behind `PENDING_SIGNATURE` | one unsigned request head-of-lines every later payout on the account | alarm on queue-head age, not on individual payout age |
| `BLOCKED_BY_POLICY` | carries the policy rule number, matched by "the first-match principle" | log the rule id with the payout; inserting a rule silently changes the outcome of existing paths |
| `COMPLETED_BUT_3RD_PARTY_REJECTED` | "completed on the blockchain as shown on block explorers but rejected by the associated third-party platform" | you paid and were not credited: book an **open receivable**, not a settled transfer |

The screening clock and the transaction clock run independently (Fireblocks: "Under certain circumstances, a
transaction's screening status can appear as Pending even when its transaction status shows as Completed"), so
neither may be used to infer the other.

**A returned travel-rule transfer is not a deposit.** When a counterparty VASP rejects and returns funds, the
inbound arrives at an address you control and looks exactly like a customer deposit; crediting it leaves the
ledger holding both a debit for the original withdrawal and an unrelated credit for the same money. Match
returned transfers against the originating payout (amount, asset, counterparty address,
`travelRuleMessageId`) before any crediting rule runs.

## The sequence allocator and the outbound queue

Where the external ledger orders your instructions by a number you assign (an EVM nonce, an XRPL `Sequence`),
that number is authoritative state with exactly one writer. The lock must span **allocation through to the
durable record of what was sent**, because everything between those two points is the window in which a second
writer allocates the same number.

```python
# WRONG, in two independent ways
with engine.begin() as cx:                              # (2) the span ends at the dedent
    cx.execute("SELECT pg_advisory_xact_lock(:k)", {"k": hash(chain) & 0x7FFFFFFF})   # (1) the key
    nonce = cx.execute("SELECT next_nonce FROM wallet WHERE chain = :c FOR UPDATE", ...).scalar()
signed = sign(build_tx(nonce))                          # outside the lock
broadcast(signed)                                       # outside the lock
```

**(1) The key is not a lock.** Python's `hash()` is salted per interpreter for `str`, so `hash('ethereum')`
differs in every process while `hash(1) == 1` everywhere; each replica therefore takes a *different* advisory
lock and all of them succeed at once. Derive the key from a stable digest of the UTF-8 key bytes, never from
the language's own hash of a string.

**(2) The span ends before the effect.** `with engine.begin():` commits and releases at the dedent, so
`SELECT … FOR UPDATE` is released before the sign and the broadcast it exists to protect. Hold the transaction
across the broadcast, and write the record of what was sent inside it.

Fireblocks solves the same problem by serialising and documents the cost: it "can only process a single
transaction per blockchain standard per vault account". The throughput ceiling is therefore one in-flight
transaction per account, and **sharding across accounts must happen before nonces are assigned, never after**:
a shard chosen after allocation splits one number space across two writers, which is the original bug with
extra steps.

### Three preconditions, checked before every broadcast

A queue that keeps submitting into a condition that guarantees failure converts one stuck instruction into a
silent outage. Each precondition halts the queue and pages; none of them may be a warning.

| precondition | the assertion | what it prevents |
|---|---|---|
| **ordering continuity** | the pending-minus-latest gap is zero, or has been non-zero for less than a configured interval | queued transactions behind a gap are capped at geth's `AccountQueue` (64) and evicted after `Lifetime` (3h). One stuck low-fee transaction at nonce N blocks every withdrawal behind it and then silently drops them |
| **fee-paying reserve** | the broadcasting account's native-gas balance covers a configured multiple of the current worst-case fee for the queued depth | a hot wallet out of gas stops all withdrawals with no error anyone reads |
| **absolute fee ceiling** | every constructed transaction carries a cap denominated in the asset, not only a fee-rate cap | a rate cap cannot bound the loss when the *input amount* is wrong; this is the Paxos shape, where the fee is a residual and the whole wrong-input discrepancy becomes fee |

The remediation for a nonce gap is to **replace the lowest unmined nonce, not to submit more transactions**;
the detector is the pending-minus-latest nonce gap, and geth's pool defaults and BitGo's `fillNonce`
equivalent are the mechanics behind it. The interval and the gas multiple are configuration with
no default. The absolute ceiling comes from your own worst-case payout size; Bitcoin Core ships both
shapes, `DEFAULT_TRANSACTION_MAXFEE` (absolute) and `DEFAULT_MAX_RAW_TX_FEE_RATE` (rate), and either one
would have refused the Paxos transaction.
