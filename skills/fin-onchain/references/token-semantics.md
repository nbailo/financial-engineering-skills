# Token semantics, amount provenance, and price reads

This reference covers the gap between the amount a sender asked to move and the amount that arrived, and the
gap between a price you read and a price you can transact at. It carries the weird-ERC20 taxonomy with the
exact guard shape for each behaviour, the runtime-metadata rules for `decimals()` and token identity, the
non-EVM instances of the same delivered-versus-requested shape (XRPL partial payments, Stellar path
payments), and the oracle-read contract for integrators.

## Contents

- [Amount provenance: measure it, do not read it](#amount-provenance-measure-it-do-not-read-it) (the block-delta reconciliation an off-chain indexer can actually run)
- [The weird-ERC20 taxonomy, with guards](#the-weird-erc20-taxonomy-with-guards): one row per behaviour, one guard per row
- [`decimals()` is runtime metadata](#decimals-is-runtime-metadata): read, cache per `(chainId, address)`, and what a constant table costs
- [Allowances](#allowances): the approve race, `forceApprove`, and the two nonce spaces
- [The same shape off EVM](#the-same-shape-off-evm): XRPL `delivered_amount`, Stellar path payments
- [Share/asset conversion an integrator must review](#shareasset-conversion-an-integrator-must-review): direction per leg, and the manipulable denominator
- [Oracle reads](#oracle-reads): the five checks, in order, with the deprecation
- [What a price is](#what-a-price-is): spot as a quantity, not a valuation

---

## Amount provenance: measure it, do not read it

`Transfer.value` is a number the sender's contract chose to emit. The amount that arrived is a property of
the balance. These are the same number for most tokens on most days, which is exactly why the divergence is
never caught in staging.

**Inside a contract you control**, the measurement is direct and there is no excuse for skipping it:

```solidity
uint256 before = IERC20(token).balanceOf(address(this));
IERC20(token).safeTransferFrom(msg.sender, address(this), amount);
uint256 received = IERC20(token).balanceOf(address(this)) - before;  // credit THIS
// `amount` is now only useful as an upper bound; never let it reach the accounting call.
```

**Off-chain, per log, you cannot do this.** `balanceOf` is a state read at a block boundary, so an indexer
crediting one `Transfer` out of a block containing three cannot attribute a per-log delta from state reads
alone. The honest design is a two-layer one, and it is what the SEAM S2(iv) assertion buys you:

1. **Credit** `Transfer.value`, but only for tokens on a record whose `fee_on_transfer` and `rebasing` flags
   are both explicitly `false`, recorded by a human at onboarding. An unrecorded token does not credit.
2. **Assert per block, per deposit address**, that `Σ credited == balanceOf(addr) delta across that block`,
   with both reads pinned by EIP-1898 block hash. On mismatch: quarantine the address, do not credit, page.

```python
BLK  = {"blockHash": blk.hash.hex(),        "requireCanonical": True}   # EIP-1898
PREV = {"blockHash": blk.parentHash.hex(),  "requireCanonical": True}   # the parent, not number-1

before = token.functions.balanceOf(addr).call(block_identifier=PREV)
after  = token.functions.balanceOf(addr).call(block_identifier=BLK)
delta  = after - before
credited = sum(int(log["data"], 16) for log in transfers_to(addr, blk))

if delta != credited:                       # fee-on-transfer, rebase, or a log you did not decode
    quarantine(addr, blk, delta, credited); return
```

`requireCanonical: true` makes a non-canonical block an error (`-32000`) instead of a wrong number; a missing
block is `-32001` (EIP-1898). Reading `balanceOf` at `latest` while processing a log from block N is the
defect EIP-1898 was written for: *"if there is a re-org in between when the balance of the sender is queried
… and when the balance of the recipient is queried, the balances may not reconcile."*

Three consequences that are schema decisions:

- **A `Transfer` with `value == 0` is a legal, normal transfer.** EIP-20: *"Transfers of 0 values MUST be
  treated as normal transfers and fire the `Transfer` event."* A zero-value credit is a no-op row, not an
  error and not a reason to skip the dedupe insert.
- **The token address must be a fixed, per-environment constant that is code-reviewed, not `os.environ`.**
  `USDC_ADDRESS` read from config is what makes the whole fee-on-transfer path reachable on a system whose
  only asset today is not fee-on-transfer. If the address is configurable, the guards are mandatory.
- **Rebasing tokens move balances with no `Transfer` at all**, so step 2 above fires spuriously on every
  rebase. For those, credit in the token's internal non-rebasing unit (a share or scaled-balance accessor)
  and store the accessor name in the token record. *(stETH's `sharesOf` and Aave's `scaledBalanceOf` are the
  commonly cited accessors; the exact signatures are **not established by the sources behind this file**;
  read them off the deployed contract before writing against them.)*

---

## The weird-ERC20 taxonomy, with guards

From `d-xo/weird-erc20`, Trail of Bits' token-integration checklist, and OpenZeppelin `SafeERC20` v5.
One row, one guard. The right-hand column is the thing to grep the diff for.

| Behaviour | Named instances | What breaks | Guard |
|---|---|---|---|
| **Fee on transfer** | STA, PAXG; USDT and USDC *can enable* one | `received == requested` | `balanceOf` delta, as above |
| **Rebasing / balance moves with no `Transfer`** | Ampleforth; airdrop-on-balance designs | cached reserves, `Σ Transfer == delta` | credit the internal share unit; never cache a raw `balanceOf` across blocks |
| **No return value** | USDT, BNB, OMG | `require(t.transfer(...))` reverts on ABI decode | `SafeERC20`: accept empty returndata **only if `extcodesize(token) > 0`** |
| **Non-reverting `false`** | ZRX, EURS | tx succeeds, no value moved → **fake deposit** (DEPOSafe, arXiv 2006.06419) | `SafeERC20`; and never credit on "tx mined" |
| **`false` on *success*** | Tether Gold | correct return handling is impossible for all tokens simultaneously | allowlist-with-quirk-record; there is no generic wrapper that is right here |
| **Codeless address** | any mainnet address copied to a chain without that deployment | `CALL` to no code returns success + empty returndata; every transfer is a silent no-op | `extcodesize(token) > 0`, asserted **per chain**, at startup |
| **Phantom function** | WETH's `function() public payable { deposit(); }` swallowing `permit(...)` | optional-interface probe "succeeds" and did nothing; Multichain/AnySwap then spent a *pre-existing* allowance, **$431M at risk**, $1M bounty ×2 (Dedaub) | check `returndatasize() >= 32`, not call success |
| **Approve race protection** | USDT, KNC | non-zero → non-zero `approve` reverts | `forceApprove`: try `approve(v)`; on failure `approve(0)` then `approve(v)` |
| **Revert on zero-value approve/transfer** | LEND | a legitimate zero clear reverts | branch on `v == 0` before the call, per token record |
| **`uint96` caps on `approve`/`transfer`** | UNI, COMP | a `type(uint256).max` allowance reverts | store the max representable amount in the token record |
| **`type(uint256).max` means "whole balance"** | cUSDCv3 | a sentinel you meant as "unlimited" moves everything | never pass `max` as an amount; pass the measured number |
| **Blocklist** | USDC, USDT | a sweep or payout reverts forever for one address; funds are held, not lost | classify the revert; route to manual, do not retry-loop |
| **Pausable** | BNB, ZIL | whole-asset outage mid-batch | per-asset kill switch and a partial-batch story |
| **Upgradeable proxy** | USDC, USDT | transfer semantics change under a live integration | watch the implementation slot; freeze the integration on change (weird-erc20's own recommendation) |
| **More than one address for one token** | proxy + implementation both callable | two entry points, one internal balance → bookkeeping splits | ToB checklist: *"The token has only one address"*; assert it, per chain |
| **Transfer hooks (ERC-777 / ERC-1820)** | AMP (Cream, 2021-08-30, ~$18.8M) | `transfer` is a reentrancy point | no state read or write may straddle a token transfer |
| **`bytes32` metadata** | MKR | `symbol()`/`name()` ABI-decode failure | decode metadata defensively or skip it; never on the money path |
| **`transferFrom` with `src == msg.sender`** | divergent implementations | allowance consumed or not, depending on token | pick one call shape per token and record it |
| **Low / high decimals** | GUSD = 2, USDC/USDT = 6, WBTC = 8, YAM-V2 = 24 | see the next section | read `decimals()` |
| **Native currency with an ERC-20 address** | Celo `0x471EcE3750Da237f93B8E339c536989b8978a438`; Polygon POL `0x0000000000000000000000000000000000001010`; zkSync Era ETH `0x000000000000000000000000000000000000800A` | `token == address(0) ? native : erc20` double-counts the same asset (critical finding in the Uniswap V4 audit) | one asset id per (chain, economic asset), mapping both representations to it |

`SafeERC20` v5's exact contract, because "we use SafeERC20" is often a claim about a file that was copied
before v5: the non-reverting-empty-return path is accepted as success **only** when `extcodesize(token) > 0`;
`forceApprove` is the zero-then-set pattern; `tryGetDecimals` is a raw `staticcall` requiring
`returndatasize() >= 32` **and** a value `< 0x100`.

**The two failure modes worth naming in a review, both diff-visible:**

```solidity
// (1) fake deposit: the tx mined, receipt.status == 1, and no value moved.
require(IERC20(t).transfer(to, amt));      // reverts on USDT (no return data)
IERC20(t).transfer(to, amt);               // ignores ZRX/EURS returning false
// correct:
IERC20(t).safeTransfer(to, amt);           // + extcodesize check inside

// (2) phantom permit: success is not existence.
try IERC20Permit(t).permit(o, s, v, d, r, vs) {} catch {}
IERC20(t).safeTransferFrom(o, address(this), v);   // spends whatever allowance already existed
```

---

## `decimals()` is runtime metadata

`decimals` is **optional** in EIP-20 and returns `uint8`. It is per-token data with a per-chain deployment,
not a constant and not a table you write by hand.

| Getting it wrong | Concrete error |
|---|---|
| `1e18` against USDC (6) | `amount * price / 1e18` under-reports by **10¹²**: a collateral position reads as worthless (spurious liquidation); the same expression on a payout leg overpays by 10¹² |
| `1e18` against GUSD (2) | 10¹⁶ |
| `1e18` against YAM-V2 (24) | 10⁻⁶, i.e. a million-fold under-credit |
| A hand-written constant table | correct on mainnet, wrong for the bridged deployment of the same symbol on another chain |

**The rule with the cache key spelled out:** read `decimals()` from the contract at runtime; cache it under
`(chainId, tokenAddress)`, never under `symbol`; refuse to price a token whose `decimals()` call did not
return ≥32 bytes of returndata. For an upgradeable token (USDC, USDT), invalidate the cache entry when the
implementation address changes, the same watcher the taxonomy table already requires.

The scaling itself is an obligation computation, not an estimate: do it in integer/`Decimal` arithmetic in
the token's own unit. In Solidity that means `Math.mulDiv(a, b, c, Rounding)` with an explicit direction, not
`a * b / c`; the intermediate `a * b` overflows `uint256` long before the quotient does ("phantom
overflow"), and `a * b / c` has no place to state which way the last unit goes.

---

## Allowances

EIP-20 states the race in the spec itself: *"clients SHOULD … set the allowance first to `0` before setting
it to another value for the same spender."* The attack is that the spender front-runs the change transaction,
spends the old allowance, and then spends the new one.

| Resolution | Correct? | Why |
|---|---|---|
| `approve(0)` then `approve(v)` (`SafeERC20.forceApprove`) | **yes** | compatible with USDT/KNC, which revert on non-zero → non-zero |
| `permit` / Permit2: signed, amount-scoped, deadline-scoped | **yes** | the allowance never sits idle between two transactions |
| `approve(spender, type(uint256).max)` | **no** | it removes the race by removing the bound. It is also not universally accepted (UNI/COMP cap at `uint96`) and on cUSDCv3 `max` is a *sentinel meaning "whole balance"* |
| `increaseAllowance` / `decreaseAllowance` | partial | fine where the token has them; not in EIP-20, and `decreaseAllowance` underflows/reverts against a concurrently-spent allowance |

**Two nonce spaces, and they are not interchangeable.** A backend that issues signatures concurrently races
itself in both, but at different granularity:

| | EIP-2612 `permit` | Permit2 `AllowanceTransfer` |
|---|---|---|
| Nonce scope | **one sequential nonce per `owner`, per token, across all spenders** | per `(owner, token, spender)` |
| Advance | `nonces[owner]` must equal the signed nonce, then `++` | `_updateApproval` requires equality, then `+1` |
| Clocks | one: `deadline` | **two**: `expiration` (allowance validity) and `sigDeadline` (signature validity); expiry surfaces as `AllowanceExpired` |
| Bulk invalidation | none | `invalidateNonces`, requires `newNonce > oldNonce`, rejects a jump `> type(uint16).max` (`ExcessiveInvalidation`) |
| Field widths | n/a | `PermitDetails{token, uint160 amount, uint48 expiration, uint48 nonce}` |

So: serialize permit issuance per `(owner, token)` for EIP-2612 and per `(owner, token, spender)` for
Permit2. Two permits signed at the same nonce cannot both be valid; under load this presents as *random*
permit failures with no useful error, and the retry re-signs at a nonce that has since moved.

EIP-2612's security considerations also bind the domain: `owner != address(0)` must be checked, because
`ecrecover` on a malformed message returns `0` and creates zombie approvals; and *"if `DOMAIN_SEPARATOR`
embeds `chainId` at deployment rather than reconstructing it per signature, future chain splits could enable
replay attacks across chains."* DAI's permit predates EIP-2612 and has a different signature; check which
one the token implements before generating a signature against it.

---

## The same shape off EVM

The requested-versus-delivered split is not an ERC-20 quirk. It is a protocol feature on two of the chains
most commonly used for exchange deposits.

**XRP Ledger.** A `Payment` carries `Amount` (renamed `DeliverMax` in rippled API v2 *"to make the field
name more specific to its behavior and help prevent the misunderstandings and exploit described below"*) and
the transaction metadata carries `delivered_amount`. With `tfPartialPayment` set, `Amount` is a **maximum**,
and the transaction returns `tesSUCCESS` having delivered an arbitrarily small fraction. XRPL publishes the
exploit as a numbered procedure, verbatim:

> 1. The malicious actor sends a Payment transaction to the institution. This transaction has a large
>    `Amount` field and has the `tfPartialPayment` flag enabled.
> 2. The partial payment succeeds (result code `tesSUCCESS`) but actually delivers a very small amount of the
>    currency specified.
> 3. The vulnerable institution reads the transaction's `Amount` field without looking at the `Flags` field
>    or `delivered_amount` metadata field.
> 4. The vulnerable institution credits the malicious actor in an external system … for the full `Amount`,
>    despite only receiving a much smaller `delivered_amount` in the XRP Ledger.
> 5. The malicious actor withdraws as much of the balance as possible to another system before the vulnerable
>    institution notices the discrepancy.

Two traps behind the obvious one:

- `delivered_amount` is *"generated on-demand for the request, and is not included in the binary format for
  transaction metadata, nor is it used when calculating the hash of the transaction metadata."* A pipeline
  that re-derives state from stored binary metadata **will not have the field at all**, and the fallback
  someone writes is `Amount`, which is the exploit.
- For partial payments in ledgers before **2014-01-20** the field is the literal string `"unavailable"`.
  A parser that coerces it gets `0` or throws. **Quarantine, never coerce, never fall back to `Amount`.**

The remediation XRPL itself names is the reconciliation invariant, not a field fix: *"Never process a
withdrawal if the total balance you hold in the XRP Ledger does not match your expected assets and
obligations."* That is the same assertion as SEAM S2(iv), stated by the protocol maintainers.

**Cross-currency and Stellar.** An XRPL cross-currency payment delivers through order books where *"the
exchange rate when trading currencies may vary"*; a Stellar path payment can deliver a **different asset**
than the one the sender named. In both cases the field naming the sender's intent and the field naming the
delivery are different fields. The generalisation for every chain in this file: **credit the observed delta
in your own balance, or the protocol's own delivery-metadata field, never the amount the sender requested.**

---

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

---

## Oracle reads

`AggregatorV3Interface.latestRoundData()` returns
`(uint80 roundId, int256 answer, uint256 startedAt, uint256 updatedAt, uint80 answeredInRound)`.
Chainlink push feeds update *"when the value deviates beyond a specified threshold **or** when the heartbeat
idle time has passed"*, and the docs state flatly that they *"do not provide streaming data."* **Between
updates the feed is stale by design**, so a read with no freshness check is a read of an unknown-age price.

Five checks, in the order they must run:

| # | Check | Failure it catches |
|---|---|---|
| 1 | **Never `latestAnswer()`** | *"No timestamp is included to check data freshness"*, the whole class |
| 2 | `answer > 0` and `updatedAt != 0` | uninitialised / absent round |
| 3 | `block.timestamp - updatedAt <= heartbeat + slack`, where `heartbeat` is **that feed's published heartbeat read from configuration** | a low-volatility feed that has not moved for hours, liquidating healthy positions at a stale mark |
| 4 | answer is **strictly inside** the aggregator's `minAnswer` / `maxAnswer` bounds | Venus (BSC) and Blizz (Avalanche), 2022-05-13: the LUNA/USD aggregator's `minAnswer` floor of **$0.10** kept reporting $0.10 as LUNA went to ~$0, so LUNA stayed borrowable at ~100× its market value. **−$13.5M and −$8.3M**; Blizz could not pause in time because of its timelock |
| 5 | **On an L2**, the sequencer-uptime feed, with a grace period | see below |

Two things the check list does not contain, deliberately:

- **`answeredInRound` is documented as "Deprecated — Previously used when answers could take multiple rounds
  to be computed"** in Chainlink's current API reference. A large body of audit boilerplate still demands
  `require(answeredInRound >= roundId)`. Treat it as optional and note the deprecation in the PR; do not
  build the freshness guarantee on it.
- **A heartbeat constant you chose.** The heartbeat is a per-feed, per-chain published parameter. A single
  `MAX_STALENESS = 3600` applied to every feed is either too tight (spurious reverts on a 24h-heartbeat feed)
  or useless (a 1h-heartbeat feed 20 hours stale passes).

The `minAnswer`/`maxAnswer` bounds live on the **underlying aggregator**, not on `AggregatorV3Interface`;
you reach them through the proxy's current aggregator, and their values are per-feed configuration you must
record alongside the heartbeat. *(The exact accessor names on the deployed aggregator are not established by
the sources behind this file; read them off the contract.)*

**L2 sequencer uptime.** Chainlink's prescribed pattern: read the uptime feed, where `answer == 0` means the
sequencer is **up** and `1` means **down**; after recovery, require
`block.timestamp - startedAt > GRACE_PERIOD_TIME` (the docs' worked example uses 3600 s) before trusting any
L2 price. The failure this prevents: sequencer down two hours, feed frozen, sequencer returns, liquidation
bots fire in the first block against pre-outage prices before any user could top up collateral.
**Arbitrum-specific:** *"The `startedAt` variable returns `0` only on Arbitrum when the Sequencer Uptime
contract is not yet initialized"* (on other L2s `startedAt` is never 0), so `block.timestamp - 0` passes a
naive grace check trivially. Reject `startedAt == 0` explicitly.

**Feed decimals are not token decimals.** ETH/USD is 8; some feeds are 18; the token is whatever the token
says. Scale by the **feed's** `decimals()` and the **token's** `decimals()` as two separate reads, both
cached by address. Mixing them is a silent 10ⁿ error in a collateral computation, the same shape as the
`1e18`-against-USDC row above, and just as invisible.

---

## What a price is

An AMM spot price, or any single-venue price, is **a quantity you can buy at the margin**, not a valuation.
Using one as the input to a solvency, collateral or liquidation decision means the position holder can set
their own collateral value if the venue is thin enough.

The worked case is Compound's DAI feed, **2020-11-26**. The Open Price Feed took DAI from **Coinbase Pro
alone**. DAI briefly printed ~$1.30 there while Kraken and Huobi stayed near $1.00, and **~$89M of positions
were liquidated across 124 of 225,793 users** (dYdX took ~$8M more). Nobody has to have attacked it: *the
"adverse market conditions vs manipulation" debate is unresolved and irrelevant; the design is wrong either
way.* A single-venue feed is a design defect on the day it ships.

Mango Markets, **2022-10-12**, is the same failure with the opposite label: the reported price was
**correct**. MNGO spot moved $0.03 → $0.91 on roughly $5M of buying because the market was thin, unrealised
PnL on that mark was accepted as collateral, and $115M of bad debt followed. No oracle check in the previous
section would have fired. What is missing there is a **liquidity-aware haircut** and a cap on how much of a
position's collateral value may derive from a mark the holder can move.

TWAPs bound *cost*, not *truth*. A TWAP over window `W` forces an attacker to hold the manipulated price for
a meaningful fraction of `W`, which prices the attack; it does not make the resulting number a valuation,
and it makes the feed lag by construction, which is its own liquidation hazard on a real move. State the
window, state what holding the price for that window costs on the specific venue, and compare that to the
maximum extractable value. If you cannot write those two numbers down, the feed is not sized.

**Two parameters bound every transaction whose ordering you do not control**, and both are correctness
parameters rather than UX ones: an explicit, non-zero, caller-supplied `amountOutMin` (or maximum-input), and
a bounded `deadline`. `amountOutMin = 0` is "give the sandwicher whatever they want"; the loss shows up as
market impact in the P&L and is never diagnosed. `deadline = type(uint256).max` is what lets a swap evicted
from the mempool at a low fee mine hours later against a market that has moved; the deadline is the thing
that makes it fail instead.
