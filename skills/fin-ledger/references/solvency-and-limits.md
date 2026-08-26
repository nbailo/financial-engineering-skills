# Floors, freezes, closure, and the solvency chokepoint

The controls that sit between a posting and a balance: overdraft as a number rather than a flag, the two
constraints that block the fraud response they were written to protect, what closing an account does and does
not resolve, and the per-asset solvency invariant with the single writer that evaluates it.

## Contents

1. **Overdraft modelled explicitly**: a floor column, not a flag; the FTX `borrow` counter-example.
2. **The control that defeats the safety operation**: the floor versus clawback, `AccountNotActive` versus freeze.
3. **Account closure**: sweep, close, and the holds that outlive the closure.
4. **The solvency chokepoint**: the per-asset invariant, the single writer, and Euler's `donateToReserves`.

## 1 · Overdraft modelled explicitly

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

## 2 · The control that defeats the safety operation

Two constraints that look obviously correct block the fraud response they exist to enable; both are easy to
add, and their interaction with clawback is easy to miss.
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
| reversal / clawback / chargeback | allow | **allow** | reopen, then post (§3) |
| fee, interest, and other system postings | allow | allow | deny |

Two supporting artifacts. A **uniqueness constraint on `reverses_transaction_id`**, with
`reversed_by_transaction_id` on the original, so one transaction cannot be reversed twice by two operators or by
an operator plus a retry. And the test that is the point of this section,
`test_clawback_posts_against_a_spent_and_frozen_account`: zero the balance, freeze the account, run the clawback,
assert the entries exist, the balance is negative, and no unfreeze occurred.

## 3 · Account closure

Closure is a sweep plus a state change, committed as one construct. TigerBeetle's recipe is a linked chain of
(a) an `AMOUNT_MAX` balancing transfer sweeping the residual to a control account and (b) a zero-amount
*pending* transfer carrying `closing_debit`/`closing_credit`; reopening is **voiding that pending transfer**,
which is why the matrix above can say "reopen, then post" as a mechanism rather than a hope.

**Closing does not resolve already-pending holds.** They remain and can still time out on their own clock:
correct, because a hold mirrors an upstream authorization whose window you do not control. So a closed
account can still see a reservation released after closure: the closure path must be idempotent against a
later expiry event, and the solvency assertion must keep counting that account's outstanding holds until
terminal.

## 4 · The solvency chokepoint

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
