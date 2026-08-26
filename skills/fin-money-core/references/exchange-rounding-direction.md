# Exchange rounding direction

Two representations of one value are traded and the counterparty chooses when and how often: shares
against assets, LP tokens against reserves, base against quote, points against cash. The per-leg direction
table, the exhaustive search that proves it, and the two ways a correct direction still loses everything.

## Contents

- Directed rounding as a security property: the pool/vault direction table
- The round-trip property test, and the exhaustive small-domain search
- Direction is necessary, not sufficient: empty denominators and the first depositor
- One helper on both legs: Balancer V2 ComposableStablePool
- Review checklist

## Directed rounding as a security property: the pool/vault direction table

EIP-4626, Security Considerations, verbatim:

> "Vault implementers should be aware of the need for **specific, opposing rounding directions across the
> different mutable and view methods**, as it is considered most secure to **favor the Vault itself during
> calculations over its users** … The only functions where the preferred rounding direction would be
> ambiguous are the `convertTo` functions … it is specified that these functions MUST both always round
> _down_."

Read the two middle columns: the **derived** side of the trade is what gets rounded, against the user, every
time.

| Operation | User supplies | User receives | Computed quantity | Direction | OZ `ERC4626.sol` |
|---|---|---|---|---|---|
| `deposit(assets)` | assets (exact) | shares (derived) | shares out | **Floor** | `_convertToShares(a, Math.Rounding.Floor)` |
| `mint(shares)` | assets (derived) | shares (exact) | assets in | **Ceil** | `_convertToAssets(s, Math.Rounding.Ceil)` |
| `withdraw(assets)` | shares (derived) | assets (exact) | shares in | **Ceil** | `_convertToShares(a, Math.Rounding.Ceil)` |
| `redeem(shares)` | shares (exact) | assets (derived) | assets out | **Floor** | `_convertToAssets(s, Math.Rounding.Floor)` |
| `convertToShares` / `convertToAssets` | n/a | n/a | view/oracle estimate, not an obligation | **Floor**, both, by spec | n/a |

The same shape recurs wherever the counterparty picks when and how often to trade the two representations: an
FX booking against a quote you publish, a points-to-cash redemption, a fee taken in a different asset from the
one quoted, a base↔quote conversion. The property is that no repeatable sequence of legs extracts value, and
the table above is one declaration of it: **round the derived quantity per leg, in the unit the counterparty
receives or supplies, flooring the leg the pool pays out and ceiling the leg it collects.** Declared per leg,
never a global default, and it yields to any scheme, statute or contract that fixes the mode for that
instrument. Where nothing fixes one, keep the liability exact and give the residue a named owner.

Note the *shape* of the preview spec: `previewDeposit` "MUST return as close to and **no more than** the
exact amount of Vault shares that would be minted"; `previewMint` and `previewWithdraw` "no fewer than";
`previewRedeem` "no more than". Those are **inequalities against the truth**, not rounding modes: the
falsifiable form, and the form to assert in tests.

## The round-trip property test, and the exhaustive small-domain search

The direction table is not an aesthetic preference; an exhaustive search over a tiny state space proves it.
`TS, TA ∈ [0,40)`, `x ∈ [1,40)`, 64,000 cases, reproduced locally against the OZ formulas:

```python
def cdiv(a, b): return -(-a // b)                            # ceil, positive ints
ok = bad = 0
for TS in range(40):
  for TA in range(40):
    for x in range(1, 40):                                   # deposit x assets, redeem what you got
      s  = (x * (TS + 1)) // (TA + 1)                        # deposit -> Floor
      if (s * ((TA + x) + 1)) // ((TS + s) + 1) > x: ok += 1
      s2 = cdiv(x * (TS + 1), TA + 1)                        # both legs Ceil == the bug
      if cdiv(s2 * ((TA + x) + 1), (TS + s2) + 1) > x: bad += 1
# correct (Floor/Floor): ok  ==      0 profitable round-trips
# inverted (Ceil/Ceil):  bad == 50,068 profitable round-trips
```

Ship that as a property test, not a comment, and ship the **multi-actor** form, which survives a real
extraction: for an adversarially ordered sequence by *different* principals, assert `Σ outputs ≤ Σ inputs` per
asset and that no user-initiated round trip returns more than it put in. A single-actor test passes on designs
that leak when A deposits and B withdraws.

**That inequality holds over a closed boundary, and the test has to close it.** It is a statement about a
sequence run against a state nothing else changes. Yield accrual, a fee credited to or taken from the pool, a
mint or burn outside the entrypoint, a reward drop, and the direct transfer described in the next section all
cross that boundary, and each one makes `Σ outputs ≤ Σ inputs` the wrong assertion rather than a failing one:
a vault that accrues between the two legs returns more than it took in and is correct. So either freeze the state
for the length of the test, no accrual step and no external transfer, or carry every crossing as its own measured
term, `Σ outputs ≤ Σ inputs + accrued + donated - fees retained`, each term read from its own source and never
inferred from the difference. An assertion whose boundary is unstated fires on correct code, gets a tolerance
bolted on, and then stops detecting the leak it was written for.

Second property test, from the Balancer post-mortem: run **N ≥ 50 minimum-magnitude operations inside one atomic
transaction** and assert the pool is not worse off: the attacker packed 65 micro-swaps (amounts as small as 17
units) into one `batchSwap`, so losses accumulated against transient internal balances before settlement and no
per-operation tolerance test could see them.

## Direction is necessary, not sufficient: empty denominators and the first depositor

Correct direction with a manipulable denominator still loses everything. OZ's `CAUTION` block on
`ERC4626.sol`, verbatim: *"In empty (or nearly empty) ERC-4626 vaults, deposits are at high risk of being
stolen through frontrunning with a 'donation' to the vault that inflates the price of a share."*

Worked in wei, `shares = assets * totalSupply / totalAssets`, **no** virtual offset:

```
attacker: deposit 1 wei      -> totalSupply = 1,      totalAssets = 1
attacker: transfer 10e18 directly to the vault (bypasses deposit(), so totalSupply unchanged)
                             -> totalSupply = 1,      totalAssets = 10e18 + 1
victim:   deposit 5e18       -> shares = 5e18 * 1 / (10e18+1) = 0        <- floor to ZERO shares
attacker: redeem 1 share     -> assets = 1 * (15e18+1) / 1  = 15,000,000,000,000,000,001
          attacker outlay 10e18+1, attacker take 15e18+1 -> +5e18 profit, victim gets nothing
```

Same arithmetic with OZ's virtual units (`+1` on `totalAssets()`, `+10 ** _decimalsOffset()` on
`totalSupply()`):

| `_decimalsOffset()` | victim's shares | attacker's redeem | attacker P&L on a 10e18 donation |
|---|---|---|---|
| none (no virtual units) | 0 | 15,000,000,000,000,000,001 | **+5e18** |
| `0` (OZ default) | 0 | 7,500,000,000,000,000,001 | **−2.5e18** |
| `3` | 999 | 5,001,667,222,407,469,157 | **−5e18** |

The default offset does not stop the victim losing; it makes the attack **unprofitable**, which is exactly
what OZ claims ("analysis shows that the default offset (0) makes it non-profitable"); a larger offset makes
it "orders of magnitude more expensive than it is profitable", at the cost that virtual shares capture a very
small part of the yield.

**The companion predicate for every share/price ratio:** *can the denominator reach 0 or 1, and can a third
party inflate the numerator by a direct transfer that bypasses the accounting entrypoint?* If yes, add
virtual shares/assets, seed-and-burn at launch, or an initialisation deposit burned to the vault. This class
recurred through **Hundred Finance → Midas Capital** (>$10M combined, 2023) and then **Onyx Protocol** (1–2
Nov 2023, $2.1M / 1,164 ETH) *after* the mitigation was documented; the Compound-V2 zero-supply
exchange-rate bug and the ERC-4626 first-depositor attack are one shape, four incidents.

## One helper on both legs: Balancer V2 ComposableStablePool

3 Nov 2025, >$120M (reported $128.6M, 8+ chains, 25+ forks inheriting it). The code documents its own bug:

```solidity
function _upscale(uint256 amount, uint256 scalingFactor) pure returns (uint256) {
    /* Upscale rounding wouldn't necessarily always go in the same direction:
       in a swap for example the balance of token in should be rounded up,
       and that of token out rounded down. This is the only place where we
       round in the same direction for all amounts, as the impact of this
       rounding is expected to be minimal. */
    return FixedPoint.mulDown(amount, scalingFactor);
}
```

OpenZeppelin's analysis: *"These functions always round-down (`mulDown`) independently from the direction of
the swap"*, and *"if amounts are orders of magnitude less than `scalingFactors` ones, the precision loss
becomes non-negligible."* The safety argument was **conditional** ("there's no rounding error unless
`_scalingFactor()` is overriden"), and `ComposableStablePool` overrode it to fold in a live exchange rate
(e.g. `1.058132408689971699e18`), silently deleting the precondition.

Three grep-able smells fall out: (1) one rounding helper (`mulDown`, `round()`, `floor()`, a shared `_q()`)
applied to **both** legs of an exchange; (2) a safety comment predicated on a value a subclass, config row or
later migration can change: `_scalingFactor()`, `decimals()`, a `rate_provider`; (3) a batch primitive that
settles only at the end of a sequence, so intermediate rounding lands on transient balances no invariant
check sees.

## Review checklist

| Check | Fails when |
|---|---|
| The exchange has two direction constants, not one helper | one `_q()` / `mulDown()` / `floor()` used on both legs |
| The share/price ratio guards its denominator | `assets * totalSupply / totalAssets` with no `totalSupply == 0` guard and no virtual offset or seed-and-burn |
| Property tests: multi-actor `Σ outputs ≤ Σ inputs`, and N ≥ 50 dust operations in one transaction | only single-actor, single-operation tests |
| The round-trip inequality names the boundary it is taken over | it spans an accrual, a fee, a mint, a burn or a donation that appears in no term |
