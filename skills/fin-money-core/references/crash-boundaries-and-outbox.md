# Crash boundaries and the outbox

The durability half of *operation identity*: an external money call is three phases and the first one
commits. This is the second window in which correct-looking code produces an economically wrong outcome,
between an external effect and the local record of it, plus the two writes that are not one transaction. The
read-modify-write window is in `isolation-and-locking.md`.

## Contents

- [Persist intent → external effect → persist outcome](#persist-intent--external-effect--persist-outcome)
- [The dual-write problem and the transactional outbox](#the-dual-write-problem-and-the-transactional-outbox)
- [Compensation is not rollback](#compensation-is-not-rollback)
- [Artefact template: crash points](#artefact-template-crash-points)

## Persist intent → external effect → persist outcome

Two Generals: there is exactly one sound shape, and it is not a bigger transaction.

```
1. BEGIN; INSERT intent(id, idem_key, target, request_bytes, state='INFLIGHT'); COMMIT;   ← COMMIT, not flush()
2. response = provider.call(request_bytes, idempotency_key=idem_key)                      ← outside any txn block
3. BEGIN; UPDATE intent SET state='DONE'/'FAILED', provider_ref=…; <state change>; COMMIT;
```

- **`flush()` is not persistence.** Two control-experiment reps wrote the intent row inside an open
  transaction and then `db.rollback()` on the exact ambiguous timeout the row exists for: one with the
  docstring *"Reserve a local row first so we always have a record even if the Stripe call times out"*,
  contradicted four lines later by `db.rollback()`.
- **No `with session.begin()` / `engine.begin()` / `@transaction.atomic` may lexically enclose step 2.** Such
  a block rolls back on exception with no `rollback` token anywhere in the diff, so the defect is invisible to
  a grep and invisible to review. This is why step 6 of the boundary check is a separate check.
- **Crash points, and the recovery action for each** (this is the crash-point artefact):

| Crash between | State on disk | Recovery action |
|---|---|---|
| 1 and 2 | `INFLIGHT`, no effect | Query the provider by `idem_key`; not found ⇒ re-send the stored bytes under the same key |
| inside 2 (timeout / socket close / 5xx) | `INFLIGHT`, effect **unknown** | Query by `idem_key`. Never re-send blind, never mark failed |
| 2 and 3 | `INFLIGHT`, effect **happened** | Query by `idem_key`, converge to the recorded outcome |
| inside 3 | `INFLIGHT`, effect happened | Same as above; step 3 is idempotent because it is keyed on `idem_key` |

- **Every field written pre-effect is read by the recovery path.** A startup
  `resolve_unresolved_intents()` that loads each `INFLIGHT` row, queries the counterparty with the persisted
  identity, and converges to exactly one effect. The common shape of this bug: `phase=BUY_PLACED` is journalled
  before the POST and `buy_order_id` written *after*, so resume calls `get_order(None)` → `ValueError`. The persisted
  client id (the entire point) was never read on the crash it existed for. **A persisted identity no code
  path reads back is the same defect as not persisting it.**
- **Bound the retries and terminate in a state, not a loop.** Jepsen flagged TigerBeetle clients that
  "continuously retry requests until they receive a reply" as an unresolved hazard: infinite retry converts
  definite errors into indefinite ones. The terminal state is `UNRESOLVED` plus an alert, not `FAILED`.

## The dual-write problem and the transactional outbox

A database write and a message publish as two independent operations fails two ways, **neither raising an
exception**:

```python
with db.transaction():          # FM-12
    debit_account(...)
publish("payment.debited", ...) # process dies here: the ledger moved, nothing downstream knows

publish("payment.debited", ...) # FM-13
with db.transaction():
    debit_account(...)          # constraint violation ⇒ rollback; downstream credits a debit that never happened
```

Kleppmann on the reordering variant: the two datastores "are inconsistent with each other, and they will
permanently remain inconsistent", and "you probably won't even notice … because no errors occurred."

The outbox moves the atomicity boundary inside one transaction:

```sql
CREATE TABLE outbox (
  id bigserial PRIMARY KEY, aggregate text NOT NULL,
  aggregate_id text NOT NULL,                 -- the business identity consumers dedupe on
  event_type text NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz                    -- NULL ⇒ unpublished
);
BEGIN;                                        -- one transaction, all three writes
  UPDATE accounts SET balance_minor = balance_minor - :amt WHERE id = :src AND balance_minor >= :amt;
  INSERT INTO entries (...) VALUES (...);
  INSERT INTO outbox (aggregate, aggregate_id, event_type, payload) VALUES ('transfer', :transfer_id, 'transfer.posted', :payload);
COMMIT;
```

**What the outbox does not do: remove duplicates.** The relay reads unpublished rows, publishes, then marks
them published, and can crash between the two, so delivery is at-least-once forever. Consumers must dedupe on
the **business identity of the effect**, not the transport's message id: Stripe states that two separate
`Event` objects can describe the same underlying fact, so dedupe on `data.object.id` + `event.type`. That
dedupe row commits in the same transaction as the balance mutation it protects; an in-memory `_seen_ids` set,
an `@lru_cache`, a module-level `set()` or any process-local dict evaporates on restart, which is exactly
when the redelivery arrives.

Do not reach for XA/2PC instead without an operator-owned transaction manager: PostgreSQL states
`PREPARE TRANSACTION` "is not intended for use in applications or interactive sessions", that a lingering
prepared transaction "continues to hold whatever locks it held" and blocks `VACUUM` to the point that it
"could cause the database to shut down to prevent transaction ID wraparound", and recommends
`max_prepared_transactions = 0`.

## Compensation is not rollback

A saga is a sequence of local transactions where a business-rule failure triggers "a series of compensating
transactions that undo the changes." microservices.io states the deficiency on the same page: sagas are **ACD,
not ACID**: "Lack of isolation (the 'I' in ACID) … concurrent execution of multiple sagas and transactions
can [cause] data anomalies." A balance can be observed and acted on in a state later compensated away.

Three properties that separate compensation from rollback:

1. **A compensation is a new economic fact, appended after the original and visible to anyone who looked in
   between**, with its own fees, FX, tax and timestamp. Never model it as an `UPDATE`/`DELETE` of the original
   posting; the original was observable, and erasing it corrupts history and breaks reconciliation.
2. **A compensation is delivered at-least-once like everything else**, so it carries its own idempotency key
   *derived from the action it compensates*. `refund(order)` executed twice refunds twice.
3. **For an economically irreversible effect there is no compensating action at all**: only a new transfer in
   the opposite direction, requiring the counterparty still to hold the funds, be solvent, and be reachable.
   After settlement finality, an on-chain send, or a cleared card capture, none of that is guaranteed, and
   the saga reaches a state with no terminal transition.

The design decision must be made **before** the irreversible step. Helland's formulation: use a *tentative
operation* with an explicit right to cancel: "Essential to a tentative operation, is the right to cancel …
Every tentative operation eventually confirms or cancels." That is **reserve → confirm/cancel**, not
**do → undo**. TigerBeetle implements exactly this natively: a `pending` transfer reserves into
`debits_pending`/`credits_pending` and leaves posted balances untouched; resolution is post, void, or expiry
by timeout and happens **exactly once** (`pending_transfer_already_posted` / `pending_transfer_already_voided`
/ `pending_transfer_expired`), the resolving transfer being a *new* record carrying a `pending_id`
back-reference. And "compensation failed" is a reachable state needing a terminal transition and a human
escalation path, not a retry loop.

## Artefact template: crash points

The recovery artefact uses the crash-point table above as its four canonical rows. A row with no recovery action says the
money is unrecoverable.
