# Share and asset conversion in a pooled vault

If your integration deposits into or redeems from a pooled vault, that vault's rounding direction and its
denominator are both yours to review, and both are readable from the source in a few minutes.

## Share/asset conversion an integrator must review

If your integration deposits into or redeems from a pooled vault, you are exposed to that vault's rounding
direction and to its denominator. Both are reviewable from the source in a few minutes.

**Direction, per leg.** OpenZeppelin's ERC-4626 (`contracts/token/ERC20/extensions/ERC4626.sol`):

```solidity
function previewDeposit (uint256 assets) … { return _convertToShares(assets, Math.Rounding.Floor); }
function previewMint    (uint256 shares) … { return _convertToAssets(shares, Math.Rounding.Ceil ); }
function previewWithdraw(uint256 assets) … { return _convertToShares(assets, Math.Rounding.Ceil ); }
function previewRedeem  (uint256 shares) … { return _convertToAssets(shares, Math.Rounding.Floor); }
```

| Operation | User supplies | User receives | Quantity computed | Direction |
|---|---|---|---|---|
| `deposit(assets)` | assets (exact) | shares (derived) | shares out | **Floor** |
| `mint(shares)` | assets (derived) | shares (exact) | assets in | **Ceil** |
| `withdraw(assets)` | shares (derived) | assets (exact) | shares in | **Ceil** |
| `redeem(shares)` | shares (exact) | assets (derived) | assets out | **Floor** |

The **derived** side is the rounded side, and it rounds against the user every time. Stated as a property
you can run rather than a maxim: *for all reachable `(totalSupply, totalAssets)`, no user-initiated round
trip returns more than it put in.* An exhaustive small-domain search over `TS, TA ∈ [0,40)`, `x ∈ [1,40)`
(64,000 cases) finds **0** profitable round trips with the correct directions and **50,068** with `Ceil` on
both legs; the smallest counterexample is `TS=0, TA=1, deposit 1 → redeem returns 2`.

One rounding helper on both legs of an exchange is the diff-visible smell, and it is not hypothetical:
Balancer V2's `_upscale` documented its own bug in a comment: *"in a swap for example the balance of token
in should be rounded up, and that of token out rounded down. This is the only place where we round in the
same direction for all amounts"*, with a safety argument (*"there's no rounding error unless
`_scalingFactor()` is overriden"*) that `ComposableStablePool` then overrode. >$120M, 2025-11-03
(OpenZeppelin's analysis).

**Direction is necessary and not sufficient: check the denominator.** OZ's own `CAUTION` block:

> "In empty (or nearly empty) ERC-4626 vaults, deposits are at high risk of being stolen through
> frontrunning with a 'donation' to the vault that inflates the price of a share."

An attacker needs only `u + 1` assets to steal a deposit of `u` in a fresh vault. `shares = assets *
totalSupply / totalAssets` with `totalSupply == 1` and a donated, inflated `totalAssets` floors the victim to
**0 shares**, with every rounding direction correct. The mitigation in OZ v4.9+ is the `+1` on
`totalAssets()` and `+10 ** _decimalsOffset()` on `totalSupply()` (virtual assets and shares); the offset
raises the attacker's minimum loss to `10^δ × u`. Alternatives in production: a non-trivial seed deposit, or
an initialisation deposit burned to the vault itself.

**The companion predicate, for any pool, not only ERC-4626:** *is the denominator reachable at 0 or 1, and
can a third party inflate the numerator by a direct transfer that bypasses the accounting entrypoint?* If
`totalAssets()` is implemented as `asset.balanceOf(address(this))`, the answer to the second half is yes by
construction; an accounted total that only the entrypoint can move is the fix. The same Compound-V2
zero-supply `exchangeRate` bug cost Hundred Finance and Midas Capital >$10M in 2023 and then Onyx Protocol
$2.1M (1,164 ETH) on 1–2 November 2023, *after* the mitigation was publicly documented; Sonne Finance lost ~$20M to
the same fork bug in May 2024.
