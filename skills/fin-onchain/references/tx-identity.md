# Transaction identity and the handles a broadcast returns

A transaction hash is a digest of one serialization of one attempt, and it is the field everyone stores. It is
not the identity of anything.

## Contents

- **Identity per chain model**: the four identities in one table; why a tx hash identifies a *serialization*.
- **The broadcast-hash set**: the schema and the confirmer that reads the whole set.
- **Request-scoped idempotency at custodians**: Fireblocks `externalTxId` vs BitGo `sequenceId`; the 24-hour
  `Idempotency-Key` and why it is a different mechanism.

## Identity per chain model

A transaction hash is a digest of one *serialization* of one *attempt*: change a fee field, a blockhash or an
input and the hash changes while the intent does not. What the consensus layer makes mutually exclusive is the
identity below, a far stronger guarantee than any application-level dedupe you can write.

| Model | Identity of the intent | What the chain guarantees | What re-signing does |
|---|---|---|---|
| EVM | `(chainId, from, nonce)` | At most one tx per `(from, nonce)` is ever canonical | Same nonce → mutually exclusive. New nonce → **both can mine** |
| UTXO (Bitcoin) | The **input set** (`(txid, vout)` outpoints) | Two txs spending a common outpoint are mutually exclusive | Same inputs → one wins. **Different inputs → both confirm** |
| XRPL | `(account, Sequence)` | A `Sequence` is consumed exactly once per account | Same `Sequence` → mutually exclusive. New `Sequence` → double payment |
| Solana | The signature over the message, which **includes `recentBlockhash`** | The runtime dedupes identical signatures while the blockhash is in the last **151** blockhashes | New blockhash → **new signature**; if the old blockhash has not expired, **both execute** |

Two consequences that people get backwards:

- On UTXO chains, "re-signing the withdrawal" is safe **only** if coin selection is deterministic and picks
  the same outpoints; a wallet that re-runs selection after a new deposit arrives may pick a different input
  set, and both transactions confirm. Fireblocks reports the losing side of a same-input race as
  `DOUBLE_SPENDING`: *"Only the initial transaction is processed in this case"* (Fireblocks sub-statuses).
- On EVM, a private-mempool or bundle submission is not visible to `eth_getTransactionByHash` until inclusion.
  Inferring "dropped, resend at a new nonce" from a null result is a double-send. The nonce check
  (`eth_getTransactionCount(from, "latest")`) still works; the mempool lookup does not.

## The broadcast-hash set

Every hash you ever broadcast for one intent belongs to a set, and the confirmer reads the set, not a column.
The measured failure: an implementation writes `replaces_tx_hash`, documents that the confirmer will *"also
match a receipt for the superseded tx"*, and never reads the column: the original never confirms, the
withdrawal is marked failed, and the user is re-credited on funds that already left.

```sql
CREATE TABLE withdrawal_intent (
  intent_id     uuid PRIMARY KEY,
  chain_id      integer     NOT NULL,
  from_address  bytea       NOT NULL,
  nonce         bigint      NOT NULL,          -- EVM nonce; XRPL Sequence; NULL on UTXO
  input_set     bytea[]     NULL,              -- UTXO: sorted (txid||vout) outpoints
  UNIQUE (chain_id, from_address, nonce)       -- the identity, enforced
);

CREATE TABLE broadcast_attempt (
  intent_id     uuid        NOT NULL REFERENCES withdrawal_intent,
  tx_hash       bytea       NOT NULL,
  broadcast_at  timestamptz NOT NULL,
  max_fee       numeric     NOT NULL,          -- wei
  max_prio_fee  numeric     NOT NULL,          -- wei
  PRIMARY KEY (intent_id, tx_hash)             -- a set, not a "current" pointer
);
```

The confirmer, in order: (1) read `latestNonce = eth_getTransactionCount(from, "latest")`; (2) if
`latestNonce <= intent.nonce`, nothing at this nonce has mined: the intent is still live, whatever the hash
lookups say; (3) otherwise the nonce is consumed, so fetch `eth_getTransactionReceipt` for **every** hash in
`broadcast_attempt` and settle on the one that returns non-null; (4) if the nonce is consumed and no hash in
your set has a receipt, someone else replaced your transaction: that is an unreconciled economic fact, not a
retry condition. Stop and page.

## Request-scoped idempotency at custodians

Two custody platforms converged on a request-scoped token and gave it **opposite** semantics. A client that
handles one as if it were the other has defeated the mechanism.

| | Fireblocks `externalTxId` | BitGo `sequenceId` | Fireblocks `Idempotency-Key` (HTTP header) |
|---|---|---|---|
| Scope | One transaction, forever | One transaction request | One HTTP request |
| On reuse | *"Fireblocks will automatically reject all future transactions with the same ID"*: **HTTP 400** | Look it up and branch on `state` | Replays the first response, *"including error responses"* |
| Lifetime | No documented expiry | No documented expiry | **24 hours**, then *"generate a new key"* |
| Resolution after an ambiguous send | `GET /transactions/external_tx_id/{id}` | If not registered, *"you can safely retry the original withdrawal"*; if registered and `state` is `pendingDelivery`, rebuild, re-sign, and send the same request | N/A |

**Treat a duplicate-id rejection as evidence the payout exists.** A 400 on `externalTxId` means the
transaction was created; Fireblocks' own motivation is *"situations where, even though a submitted
transaction responds with an error due to an internet outage, the transaction was still sent to and processed
on the blockchain."* A client that reads the 400 as a failure and re-issues under a fresh id has converted the
safety mechanism into a double payment.

The HTTP key and the transaction token are **not interchangeable**. A retry at T+25 h carrying the same
`Idempotency-Key` is a brand-new request as far as the server is concerned; only `externalTxId` still protects
it. Send both.
