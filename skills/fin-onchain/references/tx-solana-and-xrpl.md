# Solana and XRPL submission loops

> **Provenance**
> provider: Solana and the XRP Ledger · surface: the two submission loops: blockhash validity, signature status lookup and durable nonces on Solana; result-code finality, the persist-before-submit record and sequence handling on XRPL
> version: none recorded. Both documentation sites publish unversioned pages, and the original pass pinned no client library version either.
> verified_at: not established
> sources: https://solana.com/developers/guides/advanced/retry · https://solana.com/developers/guides/advanced/confirmation · https://solana.com/developers/guides/advanced/introduction-to-durable-nonces · https://xrpl.org/docs/concepts/transactions/reliable-transaction-submission
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The numeric claims are the ones to distrust first, since the blockhash validity window and the RPC rebroadcast thresholds are operational parameters described in prose rather than protocol constants. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: Solana changes the blockhash validity window or the RPC rebroadcast behaviour quoted here, or acts on the warning that durable nonces may be deprecated; XRPL changes a result code's finality condition, the `AccountTxnID` semantics, or its guidance on `LastLedgerSequence`.

Both ledgers make re-signing a different transaction, and both hand you a decidable terminal state if you ask
for one. Rebroadcasting identical bytes is the safe operation on each.

## Contents

- **Solana submission and retry**: rebroadcast identical bytes; re-sign only past `lastValidBlockHeight`;
  `getSignatureStatuses` with `searchTransactionHistory`; durable nonces' asymmetric failure.
- **XRPL submission**: provisional vs final by result code; the persist-before-submit record set; retry by
  varying only `Fee` and `LastLedgerSequence`; `AccountTxnID` and the two-active-senders stop condition.

## Solana submission and retry

The signature covers the message, and the message contains `recentBlockhash`, so **re-signing is a different
transaction**. The blockhash is valid while it is within the last **151** blockhashes, *"about 60 to 90
seconds"* (solana.com confirmation guide).

```python
sig = client.send_raw_transaction(signed_bytes)          # identical bytes every time
while True:
    st = client.get_signature_statuses([sig], search_transaction_history=True).value[0]
    if st is not None:
        if st.err is not None:
            raise LandedAndFailed(sig, st.err)   # fee paid, slot consumed; NOT retryable
        if st.confirmation_status in ("confirmed", "finalized"):
            return sig
    # not found, or found but not yet confirmed
    if client.get_block_height(commitment="confirmed") > last_valid_block_height:
        break                                    # ONLY now may you re-sign
    client.send_raw_transaction(signed_bytes)    # rebroadcast, idempotent
    sleep(2)
re_sign_with_fresh_blockhash()
```

Four things this encodes:

- **Rebroadcast the identical signed bytes.** The runtime dedupes identical signatures inside the validity
  window, so rebroadcast is idempotent; re-signing is not. Solana's retry guide is explicit: *"Before
  re-signing any transaction, it is very important to ensure that the initial transaction's blockhash has
  expired"*; otherwise both versions can land and the user *"unintentionally sent the same transaction
  twice."*
- **`searchTransactionHistory: true`.** Without it, `getSignatureStatuses` searches only the recent status
  cache (active slots plus `MAX_RECENT_BLOCKHASHES` rooted slots). Treating a cache miss as "not executed, safe
  to retry" is a double-spend.
- **`err != null` is landed-and-failed.** The fee was paid and the blockhash/nonce consumed. It is an
  accounting outcome, not a retry condition. And `confirmations: null` on a status means *"the transaction is
  rooted and finalized by a supermajority of stake"*; null is the strongest state, not the weakest.
- **The RPC will stop rebroadcasting for you.** RPC nodes rebroadcast every ~2 s until finality or blockhash
  expiry, but *"if the outstanding rebroadcast queue size is greater than 10,000 transactions, newly submitted
  transactions are dropped"*, and *"if an RPC node can't determine when your transaction expires, it will only
  forward your transaction one time and afterwards will then drop the transaction."* Your loop, not theirs.

**Durable nonces** replace `recentBlockhash` with a nonce-account value, and their failure semantics are
asymmetric (solana.com durable-nonces doc). `AdvanceNonceAccount` must be **instruction index 0**, or the
transaction is treated as an ordinary blockhash transaction and fails on staleness. A **validation** failure
(nonce already used, account missing, authority unsigned) means *"the entire transaction is dropped. No fees
collected, no state changes."* An **execution** failure after validation still advances the nonce and collects
fees: *"This prevents the transaction from being replayed."* So a durable-nonce transaction that "failed" may
have consumed the nonce, and the next transaction built on the old value is dropped, not retried. The docs
warn durable nonces *"may be deprecated in a future release"*.

One more identity trap: **track blocks by hash, not slot number.** A slot number does not distinguish two
blocks produced for the same slot on competing forks; the blockhash does.

## XRPL submission

XRPL publishes the clearest withdrawal-correctness spec of any chain, and it names the failure directly:
*"an application that fails to find a prior successful payment transaction might erroneously submit another
transaction, duplicating the original payment"* (XRPL, *Reliable Transaction Submission*).

**Provisional is not final.** *"A transaction that succeeded initially could still fail, and a transaction
that failed initially could still succeed."* Finality is per result code (XRPL, *Finality of Results*):

| Result | Final when |
|---|---|
| `tesSUCCESS` | Included in a **validated** ledger |
| any `tec` | Included in a validated ledger: a *failed* transaction is also final, and it **did** destroy the transaction cost |
| any `tem` | Final unless the protocol changes |
| `tefPAST_SEQ` | Another transaction with the same `Sequence` is validated |
| `tefMAX_LEDGER` | A validated ledger exceeds `LastLedgerSequence` **and** the transaction is in none of them |

*"Any other transaction result is potentially not final."* A pipeline that acts on the first result it sees
will be wrong in both directions.

**Persist before you submit**, and drive recovery from the record: `(transaction hash, LastLedgerSequence,
sender address, sender Sequence, latest validated ledger index at submission, application data)`. On restart,
for each persisted transaction with no validated result, query by hash and branch on whether the account's
current `Sequence` has already passed the transaction's `Sequence`.

**Retry by varying only `Fee` and `LastLedgerSequence`.** XRPL: *"it is more likely that a new transaction is
likely to succeed if you change only the LastLedgerSequence and possibly the Fee and submit again. **Use the
same Sequence number as the original transaction.**"* Setting `LastLedgerSequence` at all (the docs' automation
guidance is last validated + 4) is what converts "never mined" from an unbounded state into a decidable
terminal one.

**Two active senders on one account is a specifically anticipated disaster,** and the prescribed response is a
halt, not a degradation: *"If you have two or more transaction-sending systems in an active/passive failover
configuration, it's possible that the passive system mistakenly believes the active system has failed…
Any different transactions with the same Sequence numbers have failed permanently… you are in an unexpected
state and should stop processing until you have determined why that has happened; otherwise, your system might
send multiple transactions trying to do the same thing."* XRPL also offers `AccountTxnID`, which chains each
transaction to the hash of the account's previous one, a protocol-level guard that makes a split-brain fail
closed instead of double-paying.
