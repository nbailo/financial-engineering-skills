# Euler Finance `donateToReserves` — the one balance-mutating function that did not end in the solvency check (2023-03-13)

**Domain:** Lending protocol solvency, DeFi | **Loss:** approximately $197M across ETH, WBTC, USDC and DAI — among the largest DeFi losses of 2023; substantially returned by the attacker over the following weeks | **Failure class:** Missing conservation — a state-changing path that skips the invariant every sibling enforces | **Skill:** fin-onchain

## What happened

On 13–14 March 2023 an attacker drained roughly $197 million from Euler Finance. Every access
control worked. Every price feed was accurate. The attacker used only public functions, with their
own capital plus flash loans, and did nothing the protocol had not authorised. What they did was
make themselves deliberately insolvent using the one function that did not check whether they could
afford to be, and then liquidate themselves at the protocol's own liquidation discount.

## Root cause, in code terms

**`donateToReserves`, added in protocol upgrade EIP-14, transferred a user's eTokens into protocol
reserves without running the account health check that every other balance-changing path ran.**

Euler's design, like every lending protocol's, rests on a single global predicate: after any
operation that changes a user's collateral or debt, the account must still be healthy. Deposits
check it. Withdrawals check it. Borrows check it. Repayments check it. Liquidations check it.
`donateToReserves` moved eTokens — collateral — out of the caller's account and did not.

The attack composes cleanly from that one omission:

1. Use flash-loaned capital and Euler's own leverage primitive (`mint`) to build a large,
   inflated eToken/dToken position.
2. Call `donateToReserves` to push enough eTokens into reserves that the position is underwater —
   **by choice**, which is a state the protocol's design assumes is unreachable because every path
   that could produce it checks first.
3. Liquidate the position from a second contract, capturing the protocol's liquidation discount,
   which is a subsidy the protocol pays precisely because it assumes liquidations are involuntary
   and time-sensitive.

Omniscia's characterisation is exact: "an incorrect donation mechanism [that] did not account for
the donator's debt health, permitting them to create an unbacked DToken debt that will never be
liquidated."

The general shape is what matters far beyond DeFi. A system has an invariant — solvency, balance
non-negativity, position limits, reserve adequacy — and enforces it at the end of every operation
that could break it. Enforcement is by convention: each function author remembers to call the check.
The invariant then holds for as long as everyone remembers. The first function that forgets is not a
degradation of the invariant; it is its complete removal, because an attacker only needs one path.

The identical structure appears in the ledger world as a balance-adjustment endpoint that writes a
correction without re-running the account's overdraft check, and in the exchange world as an
administrative position adjustment that bypasses margin.

## The invariant that was violated

```
# the protocol invariant
forall account a, after every state change:
    healthScore(a) >= 1

# and the structural rule that makes it hold
forall function f that mutates a balance:
    f terminates in checkLiquidity(a)      # or the shared apply() that does
enforced STRUCTURALLY, not by convention:
    balances are private; the only mutator is one internal function whose last statement
    is the assertion; every public entry point routes through it
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes. "A new function mutates a balance and does not call the health check while its siblings do"
is exactly a differential-review finding, and it is one of the strongest arguments for reviewing a
diff against its neighbours rather than in isolation.**

The reviewing procedure:

1. **Identify the invariant the module enforces.** Read three or four sibling functions in the same
   file. If `deposit`, `withdraw`, `borrow` and `repay` all end in `checkLiquidity(account)`, then
   `checkLiquidity` is the module's post-condition and this is not a matter of interpretation.
2. **Check the new function against it.** `donateToReserves` mutates the same state and does not
   call it. That asymmetry, on its own, is the finding — and it is visible in the diff without
   understanding lending mathematics, liquidation discounts, or flash loans.
3. **State the consequence concretely, because that is what makes the finding actionable:** "this
   function allows a caller to reduce their own collateral without a health check, so a caller can
   make themselves insolvent on purpose."

The stronger, structural version of the review comment is the one worth teaching: *why is it
possible to write a balance-mutating function that skips the check at all?* If balances are only
mutable through one internal function whose final statement is the assertion, the class of bug
cannot be written. Convention-based enforcement of a critical invariant is the defect; this function
is only the instance.

## The rule

> **MUST — Route every balance-mutating path through a single function that ends by asserting the
> account-level and system-level solvency invariant.** A function that moves value without that
> assertion is the bug.

> **MUST — Enforce a critical invariant structurally, not by convention.** Make the state private,
> make one internal mutator the only way to change it, and put the assertion in that mutator. If a
> new function *can* skip the check, eventually one will.

> **MUST — Review a new state-changing function against its siblings, not in isolation.** Where
> every neighbouring function ends in the same post-condition and the new one does not, that
> asymmetry is the review finding.

> **MUST — Assert the global conservation invariant** (`sum(all balances) + fees == sum(deposits) −
> sum(withdrawals)`, or `total_debits == total_credits`) **in a test and in production
> reconciliation**, not only at the entity level.

## Sources

- **rekt.news, "Euler rekt"** — <https://rekt.news/euler-rekt/>. **Secondary, code-level.**
  Establishes: 13–14 March 2023; approximately $197M drained across ETH, WBTC, USDC and DAI; that
  `donateToReserves` (added in EIP-14) let a user push eTokens into reserves **without a health
  check**, creating an underwater position by choice; and that a second contract then liquidated it
  at the protocol's liquidation discount. Quotes Omniscia: "an incorrect donation mechanism [that]
  did not account for the donator's debt health, permitting them to create an unbacked DToken debt
  that will never be liquidated."
- **On the return of funds — not established by rekt.news.** That article was written immediately
  after the attack and concludes the opposite ("the prospects of returned funds aren't looking
  good"). The attacker's return of substantially all of the funds happened over the following weeks
  and is established by Euler Labs' own announcements and the on-chain returns, not by the source
  above. Any skill repeating the return must cite those separately. The *mechanism* — the missing
  post-condition — does not depend on it.
- **Note on scope.** This incident is included despite involving an attacker because the mechanism
  is a financial-correctness defect, not an access-control one: every function called was public and
  permissionless, and the defect was a missing post-condition on a value-moving path. That is the
  same shape as a ledger correction endpoint that skips the overdraft check.
