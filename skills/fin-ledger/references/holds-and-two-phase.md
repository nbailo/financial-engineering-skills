# Balance states, holds, and two-phase transfers

The four balance states a ledger exposes, the formula that separates them, and the mechanics of a reservation
that survives the failure of the code that created it. Covers why inbound pending is never available, how a
hold expires on a clock the reader enforces, the release semantics of a two-phase transfer, and the upstream
authorization windows a hold is reconciled against.

## Contents

1. **Four states, four names**: states not flags; the FTX and Kraken evidence for the withdrawal rule.
2. **The available-balance formula**: the deliberate absence of `credits_pending` from the predicate.
3. **The hold table**: schema, the resolve-once constraint, the read that authorises.
4. **Reserve-time invariant checking**: why `INSERT … SELECT … WHERE available >= amount` fails at Read Committed.
5. **Intrinsic expiry**: `expires_at` enforced by the reader; expiry as a scheduled state transition.
6. **Two-phase resolution**: release in full; post ≤ reserved; void exactly; resolve-exactly-once codes.
7. **Partial capture and the remainder**: per-processor auto-release, and a phantom `refund` in the ledger.
8. **Upstream authorization windows**: the expiry table, and why your own request does not pick the row.

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
sufficient balance before any writes. Four fixes exist, tabulated in `balance-storage.md`; two fit a hold
placement. (a) Take `SELECT posted_minor FROM account_balances WHERE … FOR UPDATE` first, then recompute
available and insert the hold in the same transaction. (b) Put the predicate inside the write, on a
materialised reserved column:

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
decrement go in the **same transaction** (`balance-storage.md`). The reader predicate is what makes the
sweeper non-load-bearing: it can be late, and no balance is wrong while it is.

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
