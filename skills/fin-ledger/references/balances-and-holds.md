# Balances, holds, and two-phase transfers

The four balance states a ledger exposes, the formula that separates them, and the mechanics of a reservation
that survives the failure of the code that created it. Covers why inbound pending is never available, how a
hold expires on a clock the reader enforces, the release semantics of a two-phase transfer, the upstream
authorization windows a hold is reconciled against, the materialised balance and its checkpoint, and the
concurrency shape of hot accounts. It closes on controls: overdraft as a number rather than a flag, and the two
constraints that block the fraud response they were written to protect.

## Contents

1. **Four states, four names**: states not flags; the FTX and Kraken evidence for the withdrawal rule.
2. **The available-balance formula**: the deliberate absence of `credits_pending` from the predicate.
3. **The hold table**: schema, the resolve-once constraint, the read that authorises.
4. **Reserve-time invariant checking**: why `INSERT … SELECT … WHERE available >= amount` fails at Read Committed.
5. **Intrinsic expiry**: `expires_at` enforced by the reader; expiry as a scheduled state transition.
6. **Two-phase resolution**: release in full; post ≤ reserved; void exactly; resolve-exactly-once codes.
7. **Partial capture and the remainder**: per-processor auto-release, and a phantom `refund` in the ledger.
8. **Upstream authorization windows**: the expiry table, and why your own request does not pick the row.
9. **Materialised balances**: why they exist, how they drift, the checkpoint pattern, how to rebuild.
10. **Opening balances**: the un-backfilled opening that breaks per-account reconciliation on day one.
11. **Hot-account contention**: the four fixes, their costs, and single-writer partitioning.
12. **Balance-read latency as a correctness property**: stand-in processing.
13. **Overdraft modelled explicitly**: a floor column, not a flag; the FTX `borrow` counter-example.
14. **The control that defeats the safety operation**: the floor versus clawback, `AccountNotActive` versus freeze.
15. **Account closure**: sweep, close, and the holds that outlive the closure.
16. **The solvency chokepoint**: the per-asset invariant, the single writer, and Euler's `donateToReserves`.

## 1 · Four states, four names

Four separately-derivable numbers, four names, four questions. Never one `balance` column plus boolean flags.

| state | question it answers | changes when |
|---|---|---|
| `posted` | what has settled, irreversibly enough to report | a posting commits |
| `pending` | where the balance will end up if everything in flight completes | inbound credit observed but not final; outbound authorised but not captured |
| `reserved` | how much of `posted` is already encumbered | a hold is placed; released on post, void or expiry |
| `available` | **the only number that authorises a withdrawal or an onward transfer** | any of the above |

**Nothing authorises a withdrawal or onward transfer against anything but `available`.** Two failures:

- **FTX.** Customer fiat was wired to bank accounts owned by Alameda (some named "North Dimension");
  *"Alameda personnel manually credited FTX customer accounts with the corresponding amount of fiat currency
  on FTX internal ledger system"*, and the aggregate sat in an internal account `fiat@ftx` holding up to
  **$8 billion**. The credit came from an operator action, not a settlement event, and it was spendable; after
  FTX opened its own FBO accounts around August 2020 the historical `fiat@ftx` balances were never transferred.
  (CFTC v. Bankman-Fried, 1:22-cv-10503-PKC, Am. Compl. ¶¶46–50.)
- **Kraken, June 2024.** A deposit-flow change let a user *"initiate a deposit onto our platform and receive
  funds in their account without fully completing the deposit"*, then trade and withdraw against it. A
  researcher credited $4 as proof; two associates then drew roughly $3M across three accounts over about five
  days. Patched in 47 minutes. (Kraken CSO Nick Percoco, 2024-06-19; secondary.)

Same shape both times: a balance became authorising before the event that made it real. The rule is
`credit → PENDING (unavailable) → AVAILABLE at finality`, where finality is the settlement system's own event,
never a UI confirmation, an operator action, or a mempool sighting.

## 2 · The available-balance formula

`available = posted_credits − posted_debits − pending_debits`. Inbound pending is absent from the right-hand
side, deliberately (TigerBeetle, `src/tigerbeetle.zig:34-42`):

```zig
pub fn debits_exceed_credits(self: *const Account, amount: u128) bool {
    return (self.flags.debits_must_not_exceed_credits and
        self.debits_pending + self.debits_posted + amount > self.credits_posted);
}
```

`credits_pending` does not appear. Modern Treasury states the same asymmetry independently: available balance
assumes *"all outgoing transactions are debited immediately while incoming funds won't arrive."* The check also
counts `debits_pending`; **a balance check that reads only `posted` double-spends against an unresolved
hold.** And the floor is gated on `flags.debits_must_not_exceed_credits`: an account without that flag has **no
floor at all** and may go arbitrarily negative, correct for a revenue or equity account and catastrophic for a
customer wallet. The constraint is opt-in per account, so a test must assert it is set on every
customer-liability account you create.

## 3 · The hold table

```sql
CREATE TYPE hold_state AS ENUM ('active', 'posted', 'voided', 'expired');

CREATE TABLE holds (
  id                      uuid        PRIMARY KEY,
  account_id              uuid        NOT NULL REFERENCES accounts(id),
  currency                char(3)     NOT NULL,
  amount_minor            bigint      NOT NULL CHECK (amount_minor > 0),
  state                   hold_state  NOT NULL DEFAULT 'active',
  expires_at              timestamptz NOT NULL,     -- intrinsic, set at reserve time
  resolved_at             timestamptz,
  resolved_by_txn_id      uuid        REFERENCES ledger_transactions(id),
  resolution_amount_minor bigint,
  CONSTRAINT resolve_once CHECK ((state = 'active') = (resolved_at IS NULL)),
  CONSTRAINT post_within_reservation
    CHECK (resolution_amount_minor IS NULL OR resolution_amount_minor <= amount_minor)
);
CREATE UNIQUE INDEX holds_one_resolution ON holds (resolved_by_txn_id) WHERE resolved_by_txn_id IS NOT NULL;
```

`expires_at` is `NOT NULL` because a nullable expiry is a hold that leaks; `post_within_reservation` and
`resolve_once` are the schema forms of §6's two guarantees. The read that authorises:

```sql
SELECT b.posted_minor - COALESCE((SELECT SUM(h.amount_minor) FROM holds h
                                   WHERE h.account_id = b.account_id AND h.currency = b.currency
                                     AND h.state = 'active' AND h.expires_at > now()), 0) AS available_minor
  FROM account_balances b WHERE b.account_id = $1 AND b.currency = $2;
```

`AND h.expires_at > now()` in the reader is the point of §5.

## 4 · Reserve-time invariant checking

Check the invariant **at reserve time**, so no committed reservation can later be un-postable. The property to
test is negative-space: *no reachable state contains a committed reservation that cannot be posted.* The naive
form, `INSERT INTO holds (…) SELECT … WHERE (SELECT available_minor FROM v_available WHERE …) >= $4`, is
wrong at Read Committed: two concurrent reserves see the same `available_minor` and both insert.
PostgreSQL documents that Read Committed does not prevent lost updates and that each command takes a fresh
snapshot; Modern Treasury names the identical hazard for balances: *N* concurrent debits each read a
sufficient balance before any writes. Four fixes exist (§11); two fit a hold placement. (a) Take
`SELECT posted_minor FROM account_balances WHERE … FOR UPDATE` first, then recompute available and insert the
hold in the same transaction. (b) Put the predicate inside the write, on a materialised reserved column:

```sql
UPDATE account_balances
   SET reserved_minor = reserved_minor + $3, version = version + 1
 WHERE account_id = $1 AND currency = $2
   AND posted_minor - reserved_minor - $3 >= floor_minor
RETURNING version;
```

Zero rows returned is a **typed decline** (`InsufficientAvailable { account_id, currency, requested, available }`),
not an exception and not a retry. If you pick Repeatable Read or Serializable instead, PostgreSQL requires a
*generalized* `SQLSTATE 40001` retry (you cannot predict which transactions will conflict) and that retry
must itself be idempotent.

## 5 · Intrinsic expiry

A hold carries `expires_at`, set at reserve time. Release must not depend on a callback, a cron run, or the
happy path completing: **callback-driven release strands funds precisely when the callback path is the one that
failed** (the auth expired at the network after 7 days; the capture webhook was dropped). TigerBeetle makes
`timeout` a field on the pending transfer for exactly this reason. Two rules, and they differ:

**The reader filters.** Every path computing `available` carries `AND expires_at > now()`, so expiry is correct
the instant the clock passes, with no job in the loop.

**Expiry is also a scheduled state transition.** Do not leave `now()` comparisons scattered across readers as the
only expression of expiry: one reader that forgets the predicate re-encumbers the account, and the `holds` table
never reaches a terminal state, so it cannot be audited, aged or reconciled. Ship one sweeper:

```sql
UPDATE holds
   SET state = 'expired', resolved_at = now()
 WHERE state = 'active' AND expires_at <= now()
RETURNING id, account_id, currency, amount_minor;
```

The sweeper is a state transition, not a money movement: an active hold never touched `posted`, so expiring it
posts no entries. If you materialise `reserved_minor`, the `UPDATE holds` and the matching `reserved_minor`
decrement go in the **same transaction** (§9). The reader predicate is what makes the sweeper non-load-bearing:
it can be late, and no balance is wrong while it is.

## 6 · Two-phase resolution

TigerBeetle `src/state_machine.zig:4240-4252` is the reference implementation:

```zig
dr_account_new.debits_pending  -= p.amount;     // p = the PENDING transfer, t = the resolving one
cr_account_new.credits_pending -= p.amount;
if (t.flags.post_pending_transfer) {
    assert(amount_actual <= p.amount);
    dr_account_new.debits_posted  += amount_actual;
    cr_account_new.credits_posted += amount_actual;
}
if (t.flags.void_pending_transfer) { assert(amount_actual == p.amount);
```

Read the first two lines carefully. The reservation is decremented by **`p.amount` (the pending transfer's own
amount), never by `t.amount`, the amount named on the resolving request**: released in full, unconditionally,
before anything is posted. Everything else follows:

| operation | reservation | posted | code |
|---|---|---|---|
| post full / partial | released in full | `+ amount_actual` (remainder simply not posted) | – |
| post more than reserved | – | rejected | `exceeds_pending_transfer_amount` |
| void | released in full | unchanged | must be exact: `assert(amount_actual == p.amount)` |
| expire by timeout | released in full | unchanged | – |
| resolve twice | – | rejected | `pending_transfer_already_posted = 33`, `pending_transfer_already_voided = 34` |

The resolving transfer is a **new record with its own id** and a `pending_id` back-reference; it never edits the
pending one. An over-post is rejected, never clamped; clamping turns a caller bug into a silent difference
between what was asked for and what the books say.

## 7 · Partial capture and the remainder

Posting less than reserved restores the remainder, and on most upstream rails **you cannot capture that
remainder later**; a second capture needs a new authorization.

| processor | after a partial capture | source |
|---|---|---|
| Stripe (default) | *"A partial capture automatically releases the remaining amount"*; *"If you partially capture a payment, you can't perform another capture for the difference."* | docs.stripe.com/payments/place-a-hold-on-a-payment-method |
| Stripe multicapture | up to **50 non-final captures plus one final capture**, total ≤ authorized; requires `capture_method=manual`; `final_capture` defaults to **true** | docs.stripe.com/payments/multicapture |
| Adyen, single partial capture | *"Any unclaimed amount that is left over after partially capturing a payment is automatically cancelled."* | docs.adyen.com/online-payments/capture |
| Adyen, multiple partial captures | *"The unclaimed amount after an initial partial capture is not automatically cancelled"*, and this mode is **disabled by default**, enabled only by Adyen Support | same |

The trap that reaches the ledger directly: **Stripe's partial capture emits a `charge` balance transaction for
the full authorized amount plus a `refund` balance transaction for the uncaptured portion.** Derive postings from
balance transactions and you book a refund that never happened, to a customer who never asked for one. Key the
ledger transaction off the capture, and treat that `refund` BT as the release of a reservation.

## 8 · Upstream authorization windows

When your hold mirrors an upstream authorization, `expires_at` is reconciled against the network's window, not
invented. Stripe publishes the table (docs.stripe.com/payments/place-a-hold-on-a-payment-method):

| rail | window |
|---|---|
| Visa | card-not-present CIT **7 d**; card-not-present MIT **5 d** (documented as exactly 4 d 18 h); card-present **5 d** |
| Mastercard / Amex / Discover | card-not-present **7 d**; card-present **2 d** |
| any card, JP / JPY | up to **30 d** |
| non-card methods | Klarna **28 calendar days** (to midnight); Affirm **30 d**; Afterpay **13 d**; Cash App **7 d**; PayPal **10 d**, auto-extended to 20 |

Three mechanics that decide whether your `expires_at` can be right:

- **The CIT/MIT classification is made by network signals, not by your `off_session` flag**; you cannot
  compute which row applies from your own request. Read the field the processor returns (Stripe puts
  `capture_before` on the charge) and store *that* as `expires_at`.
- **Incremental authorization does not extend the validity window.** `amount` on `increment_authorization` is
  the **new total**, not a delta, and must exceed the current authorization; max 10 attempts; per-increment cap is
  the greater of 500 USD or 500% of the previously authorized amount. On decline you get `card_declined` and the
  PaymentIntent **remains capturable for the previously authorized amount**. An increment therefore raises
  `amount_minor` on your hold and leaves `expires_at` alone.
- On expiry the upstream releases the funds and the PaymentIntent becomes `canceled`; a `requires_capture`
  PaymentIntent must be **cancelled, not refunded**.

## 9 · Materialised balances

You cannot fold the whole journal on every read. Monzo defines a balance as the sum of entries over an address
group `(legal_entity, namespace, name, currency, account_id)`; at Uber's volume (a trillion entries) that fold
is not a read path at all. Materialisation is legitimate; the constraint is *how*. **The write** goes in the
**same transaction** as the entry `INSERT`, carrying a monotonic version; Square Books caches the balance on the
book row inside the same Cloud Spanner transaction as the journal entry, with a version counter. The drift shape
is `INSERT INTO ledger_entries …; COMMIT;` then a separate `UPDATE account_balances …`. A crash or a partial
retry between them is permanent and silent.

**The verifier** is a scheduled job that recomputes order-independently and compares:

```sql
SELECT b.account_id, b.currency, b.posted_minor, COALESCE(SUM(e.amount_minor), 0) AS recomputed_minor
  FROM account_balances b LEFT JOIN ledger_entries e USING (account_id, currency)
 GROUP BY b.account_id, b.currency, b.posted_minor
HAVING b.posted_minor <> COALESCE(SUM(e.amount_minor), 0);
```

Uber runs offline order-independent checksums over time windows comparing source-of-truth to derived tables; a
single missing entry breaks the checksum. **The recompute does not fix the balance in place.** It raises a
`break` row and posts the difference to a suspense account; an in-place repair destroys the evidence of how the
drift arose and silently absorbs the next one.

**The checkpoint pattern.** Recomputing from inception does not scale either, so Monzo materialises *blocks of
consecutive entries with a stored running sum*, only for hot balances:

```sql
CREATE TABLE balance_checkpoints (
  account_id uuid NOT NULL, currency char(3) NOT NULL,
  through_seq bigint NOT NULL,          -- append sequence, NOT effective_at
  running_sum_minor bigint NOT NULL,
  PRIMARY KEY (account_id, currency, through_seq)
);
-- rebuild = latest checkpoint + the tail after it
SELECT c.running_sum_minor + COALESCE(SUM(e.amount_minor), 0)
  FROM balance_checkpoints c
  LEFT JOIN ledger_entries e ON e.account_id = c.account_id
        AND e.currency = c.currency AND e.seq > c.through_seq
 WHERE (c.account_id, c.currency, c.through_seq) IN (
         SELECT account_id, currency, MAX(through_seq) FROM balance_checkpoints
          WHERE account_id = $1 AND currency = $2 GROUP BY 1, 2)
 GROUP BY c.running_sum_minor;
```

**Checkpoints are keyed on the append sequence, never on `effective_at`.** A back-dated entry arrives after a
checkpoint whose `through_seq` already covers it in append order but not in economic order: correct for the
current balance, wrong for an as-of balance, which folds entries with
`WHERE effective_at <= T AND (discarded_at IS NULL OR discarded_at >= T)` and uses no checkpoint. Uber's entity
changelog can recreate an entity's ledger since inception; **if you cannot drop `account_balances` and rebuild
it from `ledger_entries`, you do not have a materialised balance, you have a second source of truth.**

## 10 · Opening balances

A per-account reconciliation compares `SUM(entries)` against an independently-read figure. If the ledger's
history starts at a migration cutover and the pre-cutover balance was never posted, **every account with a
non-zero legacy balance breaks on the first run**: the job fires thousands of alerts on day one, someone mutes the
channel, and the control is dead before it detects anything. The reconciliation SQL can be flawless and the
control still dead on arrival: the defect is in the opening data the job reads, never in the query.

The fix is a posting, not a special case in the reconciler. Beancount's `Equity:Opening-Balances` exists so a
truncated history still balances: one balanced transaction per account, dated at the cutover.

```sql
-- migration, runs BEFORE the reconciliation job is first scheduled; two legs per transaction
-- (customer account, equity:opening-balances), idempotency key 'opening:<account>:<currency>'
INSERT INTO ledger_transactions (id, effective_at, posting_type, idempotency_key)
SELECT gen_uuid(), '2026-01-01T00:00:00Z', 'opening_balance',
       'opening:' || l.account_id || ':' || l.currency
  FROM legacy_balances l WHERE l.balance_minor <> 0;
```

Two properties to test: after the migration `SUM(entries) == legacy_balance` for **every** account, and the
opening transactions themselves net to zero per currency against the equity account. Then run the
reconciliation in CI against a **freshly-migrated** database seeded with one known discrepancy, and assert it
produces exactly one `break` row and one alert, so an un-backfilled opening fails the test rather than muting
production.

*Measured: the near-miss wrote flawless reconciliation SQL, ran it nowhere, and its per-account comparison
was broken on day one by un-backfilled openings. Transfer and reversal arithmetic is written correctly
unaided; journals that do not balance and reconciliations that never run ship at close to 100%.*

## 11 · Hot-account contention

Fee, tax, FX-liquidity and clearing accounts appear in a large fraction of transactions, so contention is
structural. TigerBeetle states it directly: business transactions *"don't shard well"* and row locks on hot
accounts *"bring the system's performance to a crawl."* Sharding by account id does not help; the hot account
sits on one side of nearly every transfer.

| fix | mechanism | cost |
|---|---|---|
| Pessimistic | `SELECT … FOR UPDATE` on the account row, then update | serialises every transfer touching the hot account; the throughput ceiling |
| Higher isolation | Repeatable Read / Serializable | obliges a **generalized** `SQLSTATE 40001` retry of the whole transaction; PostgreSQL warns you cannot predict which will conflict |
| Optimistic version | caller passes the expected `lock_version` per entry; mismatch → rollback and fail (Modern Treasury) | pushes the retry to the caller; needs a typed failure (`balance_lock_failure`) the caller can act on |
| Predicate in the write | conditional `UPDATE … WHERE`, TigerBeetle `balancing_debit`/`balancing_credit`, or the 3-transfer balance-conditional linked chain | no read-then-write at all; limited to predicates the write can express |

**Single-writer partitioning** is the fifth answer and the one that scales for the hot side. Uber uses
**serialized batch writes** on hot ledger entities; TigerBeetle executes all transfers sequentially on one core
under strict serializability, the only isolation level it offers. Route every posting touching a hot account
through one writer keyed on `(account_id, currency)`, let it accumulate N postings in a short window, and apply
**one** balance mutation per batch; the journal still receives N immutable entry rows, because the batching is
on the materialised balance only. Beyond throughput, that writer is the natural home for the per-currency
conservation check on the set, and one writer per key makes the balance row's monotonic version trivially
correct.

## 12 · Balance-read latency as a correctness property

Monzo's stated reason for materialising: delayed balance reads force card **stand-in processing**, which risks
*"unauthorized negative balances or missed fraud checks."* When the authoritative read misses the network's
deadline the fallback is not "a slower answer"; it is *approve without checking*, by a component you do not
control. Latency on the authorising read is a correctness budget; the response to blowing it is a decline policy
you chose, not a stand-in you inherited.

## 13 · Overdraft modelled explicitly

If an account may go negative that is a credit product with a limit, not a flag on a code path. Give the balance
row a floor (`ALTER TABLE account_balances ADD COLUMN floor_minor bigint NOT NULL DEFAULT 0 CHECK (floor_minor
<= 0)`) and compare every debit against it. `floor_minor = 0` for an ordinary customer wallet; a negative value
is an extended credit line, and the drawn amount is a receivable that must appear on the asset side; an
overdraft existing only as a negative liability balance is invisible to every credit-exposure report you run.

The counter-example is FTX's `borrow`, a per-customer field controlling how far negative an account could go
before auto-liquidation: most retail 0, preferred market makers up to $150M, **Alameda alone
$65,000,000,000**; alongside `allow_negative = true` (2019-07-31) and `can_withdraw_below_borrow`
(2019-07-23), which let a flagged account withdraw unlimited assets while net-negative and exempted it from
auto-liquidation. Because database logs were not kept the debtors **could not determine when or by whom the
$65bn value was set** (Ray First Interim Report, Case 22-11068-JTD Doc 1242-1). A per-account override on a
solvency or liquidation check is an unbounded liability generator; ship one only with field-level audit
logging of every change.

## 14 · The control that defeats the safety operation

Two constraints that look obviously correct block the fraud response they exist to enable. A blanket
`CHECK (balance_cents >= 0)` silently makes `allow_overdraft=True` **dead code** in a reversal path: the
constraint is easy to add, and its interaction with clawback is easy to miss.
*Measured: `CHECK (balance_cents >= 0)` made `allow_overdraft=True` dead code in a shipped reversal path.*

**The floor versus the clawback.** The fraud you are responding to is money already spent, so a clawback must
drive the balance below the floor; that is what "claw back already-spent funds" means. A blanket
`CHECK (balance_minor >= 0)` makes it structurally impossible and the `allow_overdraft` branch unreachable.
**Do not delete the constraint**: that is worse than either failure, because ordinary debits then overdraw
silently. Condition the floor on the posting type. A row-level `CHECK` on `account_balances` cannot see which
posting is writing it, so the predicate belongs in the chokepoint's conditional `UPDATE`, and a schema-level
backstop must sit on a row that carries the posting type (the entry, with the floor denormalised onto it):

```sql
UPDATE account_balances
   SET posted_minor = posted_minor - $amount_minor, version = version + 1
 WHERE account_id = $account AND currency = $ccy
   AND ($posting_type IN ('reversal', 'clawback', 'chargeback')
        OR posted_minor - $amount_minor >= floor_minor)
RETURNING posted_minor, version;
-- schema-level backstop, on the entry row because it is the row that carries the posting type:
ALTER TABLE ledger_entries ADD CONSTRAINT ordinary_debits_do_not_overdraw
  CHECK (posting_type <> 'ordinary_debit' OR balance_after_minor >= floor_minor_at_write);
```

A raw `CheckViolation` from any remaining constraint must not escape the typed error hierarchy mid-clawback: a
compensating path that dies on an unhandled database exception halfway through has written some legs, not others.

**The status check versus the freeze.** The standard fraud flow is **freeze the recipient, then claw back**. An
account-status check raising `AccountNotActive` on a frozen account blocks the second step, forcing an unfreeze
that reopens exactly the drain window the freeze existed to close, while a hostile counterparty watches for it.
Gate on `(posting_type, account_status)`, not on status alone:

| posting type | active | frozen | closed |
|---|---|---|---|
| customer-initiated debit (withdrawal, transfer out) | allow | **deny** | deny |
| customer-initiated credit (deposit) | allow | allow, into the frozen balance | route to suspense |
| reversal / clawback / chargeback | allow | **allow** | reopen, then post (§15) |
| fee, interest, and other system postings | allow | allow | deny |

Two supporting artifacts. A **uniqueness constraint on `reverses_transaction_id`**, with
`reversed_by_transaction_id` on the original, so one transaction cannot be reversed twice by two operators or by
an operator plus a retry. And the test that is the point of this section,
`test_clawback_posts_against_a_spent_and_frozen_account`: zero the balance, freeze the account, run the clawback,
assert the entries exist, the balance is negative, and no unfreeze occurred.

## 15 · Account closure

Closure is a sweep plus a state change, committed as one construct. TigerBeetle's recipe is a linked chain of
(a) an `AMOUNT_MAX` balancing transfer sweeping the residual to a control account and (b) a zero-amount
*pending* transfer carrying `closing_debit`/`closing_credit`; reopening is **voiding that pending transfer**,
which is why the matrix above can say "reopen, then post" as a mechanism rather than a hope.

**Closing does not resolve already-pending holds.** They remain and can still time out on their own clock:
correct, because a hold mirrors an upstream authorization whose window you do not control (§8). So a closed
account can still see a reservation released after closure: the closure path must be idempotent against a later
expiry event, and the solvency assertion must keep counting that account's outstanding holds until terminal.

## 16 · The solvency chokepoint

The system-level invariant is not "balances are non-negative". It is `Σ customer balances <= custodied assets`,
**per asset**, asserted continuously against the custodian's own figure rather than against your own record of
what you believe you hold. Write it down as an expression the code evaluates, name the account set on each
side, and state the window in which the two sides may legitimately differ (in-flight settlement, an unconfirmed
deposit, a pending withdrawal already debited internally). An invariant with no stated disagreement window
either alerts constantly or is written loose enough to never alert.

**Enumerate every function that can change a balance and show that each one terminates in the single chokepoint
that evaluates the invariant before the write.** That enumeration is worth doing once, and it is worth not
relying on afterwards, because the next contributor adds the path that skips it.

**Make the bypass unrepresentable rather than provable.** The application role loses the ability to write a
balance at all, so the only writer is the chokepoint's own role or a `SECURITY DEFINER` function, and a test
asserts the grant is absent:

```sql
REVOKE UPDATE, DELETE ON balances FROM app_role;
GRANT EXECUTE ON FUNCTION post_group(jsonb) TO app_role;   -- SECURITY DEFINER, evaluates solvency
```

"Prove that each path terminates in the chokepoint" is an architecture-review question answered with a
paragraph, and the paragraph ages badly. A revoked grant is a fact a test can read on every run.

**The precedent.** Euler's `donateToReserves` was the single value-moving path that did not run the health
check. Every other entrypoint did. The result was roughly **$197M**, and the shape of the bug is worth stating
plainly: the missing control was not missing from the design, it was missing from *one function*, and no
document listing the controls would have caught it. Only an enumeration of writers, or an inability to write
without passing through the chokepoint, would have.

**Per-account overrides.** A per-account override on a solvency, credit-limit or liquidation check is an
unbounded liability generator: it converts a system-wide invariant into a per-row opinion, and the row is
usually edited during an incident by someone under time pressure. Where one exists, it raises the evidence bar
for the whole path: field-level audit logging of every change to the override, an approver distinct from the
requester, and an expiry on the override itself so the exception does not outlive the incident.
