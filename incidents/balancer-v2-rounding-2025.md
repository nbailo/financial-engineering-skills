# Balancer V2 Composable Stable Pools — one rounding direction applied to both legs of a swap, composed many times inside a single transaction (2025-11-03)

**Domain:** AMM arithmetic, DeFi | **Loss:** approximately $128M across 8+ chains; reported figures range from ">$120M" (OpenZeppelin) to "$128.64M" (Check Point) to "$116M" | **Failure class:** Rounding & precision | **Skill:** fin-onchain

## What happened

Beginning around 07:40 UTC on 3 November 2025, Balancer V2 Composable Stable Pools were drained on
Ethereum, Base, Arbitrum, Avalanche, Optimism, Gnosis, Polygon, Berachain and Sonic within roughly
thirty minutes, along with 25+ forks that had inherited the same code. The mechanism was not an
access-control bypass and not a flawed invariant formula. It was a rounding direction that was safe
under an assumption a subclass had quietly removed, exploited at magnitudes small enough that each
individual operation looked like a no-op.

## Root cause, in code terms

**A single rounding direction applied regardless of which side of the trade was being computed.**
`_upscale` calls `FixedPoint.mulDown(amount, scalingFactor)` and **always rounds down**, independent
of the swap direction. That is safe as long as the scaling factor is exactly `1e18` — under a
unitary factor, `mulDown` is the identity and there is nothing to lose.

**The safety assumption was documented — and then the caveat was deleted before the subclass broke
it.** The `_upscale` docstring in force at the time says only that rounding in one direction here
is "expected to be minimal". OpenZeppelin records that **a prior version of that comment** carried
the operative caveat: "…(and there's no rounding error unless `_scalingFactor()` is overriden)."
StablePhantomPool — introduced 20 September 2021 and later renamed ComposableStablePool — does
exactly that override, folding in a live exchange rate, for example `1.058132408689971699e18`. The
moment the factor is non-unitary, `mulDown` truncates, and it truncates in the protocol's favour on
one leg and the user's favour on the other. The precondition outlived the sentence that recorded
it.

**At small magnitudes, truncation swallows the entire operation.** With `amount = 17` and a factor
of `1.0581…`, `17 × 1.0581… = 17.98`, which truncates back to `17` — a swap that moves value on one
side of the books and zero on the other. The attacker maximised `amount * scalingFactor mod 1e18` to
sit exactly on that boundary.

**Composition inside one atomic transaction is what turned dust into $128M.** The attacker used
`_swapGivenOut` (EXACT_OUT) inside `batchSwap`, so a long run of micro-swaps — OpenZeppelin
describes "repeating triplets" of prime / exploit / reset, run for "a high number of iterations" —
settled against *transient internal balances* within a single transaction, driving pool token
balances down to very low levels (≲100k) where rounding dominates the arithmetic entirely.
The pool's invariant check still passed at every point, because it was measured against balances
that had not yet settled. The BPT price was artificially suppressed and value extracted.

Three properties compose here, and no one of them is a bug on its own:

1. a rounding helper applied to both directions of an exchange;
2. a safety argument predicated on a value a subclass can override;
3. a batch primitive that settles only at the end of a multi-operation sequence.

**And the blast radius was multiplied by deployment topology.** One codebase on nine chains plus
25+ forks meant one bug produced simultaneous, uncoordinated losses everywhere, with no possibility
of a staged response.

## The invariant that was violated

```
# rounding is an economic decision, per leg
forall swap s:
    amount_user_owes_pool  is rounded UP    (mulUp)
    amount_pool_owes_user  is rounded DOWN  (mulDown)
NOT: one global direction applied to both legs

# the invariant must be measured on settled state
pool_invariant(settled_balances_after) >= pool_invariant(settled_balances_before)
NOT: measured against transient internal balances mid-batch

# a safety argument predicated on a value must assert that value
if the correctness argument is "safe because scalingFactor == 1e18":
    then assert scalingFactor == 1e18
    or re-derive the argument for the general case
NOT: state the condition in a comment and allow subclasses to violate it
```

## Could an AI coding agent reviewing the diff have caught it?

**Partly — and being precise about which parts is the useful answer.**

**What an agent cannot do:** re-derive AMM invariant mathematics. Establishing that a particular
sequence of EXACT_OUT swaps at a particular tick of precision extracts value from a stable-pool
invariant is a research result, not a review comment. Nobody should claim otherwise.

**What an agent can flag, mechanically, from the source:**

1. **One rounding helper applied to both directions of an exchange.** `mulDown` (or `mulUp`) used on
   both the in-leg and the out-leg of a swap is a direct finding. The reviewing question — "which
   party benefits from truncation here, and is that the party we intend?" — must be answered
   separately for each leg. A helper that does not take the direction as a parameter is suspicious
   by construction.
2. **A correctness precondition that lives in a comment, on a value a subclass can override.**
   "…no rounding error unless `_scalingFactor()` is overriden" is a precondition written in prose
   rather than in an assertion — which is why deleting the sentence deleted the only record of it,
   and why the surviving comment ("the impact of this rounding is expected to be minimal") reads as
   reassurance rather than as a condition. Two findings follow, and both are mechanical: a
   conditional safety claim about an overridable method must be restated as an assertion or
   re-derived for the general case; and a diff that *removes* a caveat from a safety comment on a
   money path is a change to the safety argument and must be reviewed as one. Checking whether any
   subclass overrides the method is a two-line grep, and one did.
3. **A batch primitive that settles only at the end of a multi-operation sequence.** Any construct
   that lets N operations run against transient internal balances before a single settlement, with
   the invariant checked against those transient balances, is a review finding. The signal is an
   invariant assertion whose inputs are the working balances rather than the committed ones.
4. **The absence of a composition test.** Per-operation tolerance tests would have shown nothing
   here; each swap lost dust. The finding is the missing test: N ≥ 50 minimum-magnitude operations
   inside one atomic transaction, asserting the pool is not worse off at the end.
5. **Deployment topology.** A value-bearing change to a codebase deployed on nine chains is a change
   with a 9× blast radius and no staged rollout. That is visible in the deployment configuration.

## The rule

> **MUST — Choose rounding direction per operation as a function of who is credited.** Round in the
> protocol's or the house's favour on every leg, and never apply one global rounding direction to
> both the in-leg and the out-leg of an exchange.

> **MUST — Write a conservation test that runs N ≥ 50 minimum-magnitude operations inside a single
> atomic transaction and asserts the pool or ledger is not worse off.** Per-operation tolerance
> tests do not detect composed precision loss.

> **MUST — Measure a conservation invariant against settled state, never against transient internal
> balances inside a batch.**

> **MUST — Make the scaling factor or decimals of every token or account an explicit, validated
> input to arithmetic, and re-verify any code whose safety argument depends on a scaling factor
> equalling 1.** A correctness precondition stated in a comment must be restated as an assertion.

> **SHOULD — Where the same contract or service is deployed to N chains or regions, model the blast
> radius as N× and stage rollouts of value-bearing changes accordingly.**

## Sources

- **OpenZeppelin, "Understanding the Balancer v2 Exploit"** —
  <https://www.openzeppelin.com/news/understanding-the-balancer-v2-exploit>. **Secondary but
  code-level and authoritative.** Establishes: 3 November 2025 ~07:40 UTC; that `_upscale` uses
  `FixedPoint.mulDown(amount, scalingFactor)` and **always rounds down regardless of swap
  direction**; that this is safe only while `_scalingFactor()` is unitary; that Composable Stable
  Pools override it to fold in an exchange rate (e.g. `1.058132408689971699e18`); that with tiny
  amounts (17 units) the product truncates back to the input, yielding zero net change; that the
  attacker maximised `amount*scalingFactor mod 1e18`, used `_swapGivenOut` (EXACT_OUT) inside
  `batchSwap`, running "repeating triplets" of swaps for "a high number of iterations" that settled
  transiently, drove pool balances to "very low levels (≲ 100k)", and extracted value while the
  invariant check still passed; and that **a prior version** of the `_upscale` comment carried the
  "no rounding error unless `_scalingFactor()` is overriden" caveat that the shipped comment did
  not.
- **Not established by OpenZeppelin.** The article states only "$120+ million across Balancer and
  its forks" and does not enumerate chains, does not give a count of forks, and gives no swap count.
  The nine-chain list, the "25+ forks" figure and any specific number of micro-swaps come from
  incident trackers and should be attributed to them or dropped. This entry does not assert a swap
  count.
- **Loss figure.** Reported totals differ by source: OpenZeppelin ">$120M", Check Point "$128.64M",
  other reports "$116M". This catalogue states "~$128M across 8+ chains" and records the range
  rather than picking one.
