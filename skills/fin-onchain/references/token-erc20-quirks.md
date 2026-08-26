# ERC-20 call semantics, decimals, and allowances

> **Provenance**
> provider: ERC-20 token implementations, via the weird-erc20 taxonomy, Trail of Bits' token integration checklist, OpenZeppelin, and the EIP texts · surface: transfer and approve call semantics, `decimals()` as runtime metadata, the approve race, and the two permit nonce spaces
> version: OpenZeppelin `SafeERC20` v5 and the EIP texts as cited in the body. No commit or release tag was pinned for the taxonomy or the checklist.
> verified_at: not established
> sources: https://github.com/d-xo/weird-erc20 · https://secure-contracts.com/development-guidelines/token_integration.html · https://docs.openzeppelin.com/contracts/5.x/api/token/erc20 · https://eips.ethereum.org/EIPS/eip-20 · https://eips.ethereum.org/EIPS/eip-2612 · https://github.com/Uniswap/permit2
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. Two kinds of claim here rot faster than the rest and should be treated as the oldest: the per-token rows, because an upgradeable token can change behaviour without changing address, and the named incidents and their figures, none of whose reports was reopened. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: OpenZeppelin ships a `SafeERC20` whose empty-returndata or `forceApprove` behaviour differs from v5; Permit2 changes the field widths or the invalidation rule; a token named in the taxonomy changes implementation behind its proxy; you add a chain where a listed address is not the same economic asset.

Most tokens behave the same way on most days, which is exactly why an integration that assumes one shape
survives staging. One row per behaviour, one guard per row.

## Contents

- **The weird-ERC20 taxonomy, with guards**: one row per behaviour, one guard per row.
- **`decimals()` is runtime metadata**: read, cache per `(chainId, address)`, and what a constant table costs.
- **Allowances**: the approve race, `forceApprove`, and the two nonce spaces.

## The weird-ERC20 taxonomy, with guards

From `d-xo/weird-erc20`, Trail of Bits' token-integration checklist, and OpenZeppelin `SafeERC20` v5.
One row, one guard. The right-hand column is the thing to grep the diff for.

| Behaviour | Named instances | What breaks | Guard |
|---|---|---|---|
| **Fee on transfer** | STA, PAXG; USDT and USDC *can enable* one | `received == requested` | measured delta where attribution is isolated; the token's own accounting where it is not |
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
