# Hyperliquid HIP-4: the merged book, split, merge, negate, and fees

> **Provenance**
> provider: Hyperliquid · surface: HIP-4 outcome markets on HyperCore, via the `exchange` endpoint and the market-data channels · version: experimental, unversioned, staged rollout
> verified_at: 2026-08-26
> sources: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-4-deployer-actions · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets · https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot
> pinned: GitBook publishes no revision identifier. The HIP-4 overview page rendered the footer "Last updated 2 months ago" when fetched on 2026-08-25. Every page above is also served as raw Markdown by appending `.md` to its URL; quotations below were taken from one or both forms, and where a page did not return the section being checked that is said in place rather than filled in. On 2026-08-26 the HIP-4, exchange-endpoint, nonces-and-api-wallets and WebSocket-subscriptions pages were re-fetched as raw Markdown; the exchange endpoint returned its four outcome sections that time, and the quotations taken from them carry that date. Re-fetch before relying on any of it; treat this file as dated, not current.
> verified: the name "HIP-4: Outcome markets" as a documentation page title and URL segment; the merged-book rule and price-side-time priority; the sub-deployer grant list and the 183-day deactivation rule; the deployer fee scale formula and the no-maker-rebate rule; the `(block_time, coin, tid)` trade key; and, on 2026-08-26, the four `userOutcome` action shapes with their one-line descriptions and their identical acknowledgement-only success body, the nonce set rules, and the API-wallet pruning warning.
> unverified: whether HIP-4 deployer actions are live on mainnet as opposed to testnet, since the published deployer limits are given only as testnet values; the current base outcome trading fee rate; whether an `l2Book` or `trades` subscription on a `#<encoding>` coin returns the merged book or one leg; whether any info or WebSocket record reports an applied split, merge, merge-question or negate keyed to the action's nonce, since the ledger-update union on the subscriptions page enumerates no outcome delta type; what the exchange endpoint returns for a nonce it has already used.
> revalidate_when: multi-outcome markets ship to mainnet; the "Fees are currently zero for outcome markets for initial testing" sentence disappears; a `userOutcome` delta type appears in the ledger-update union; or the `userOutcome` success body gains a field.

Outcomes are Hyperliquid's prediction-market and bounded-payoff primitive, with no leverage and no liquidations. This
file covers what happens once you are trading one: the single merged book both sides share, the balance-changing
operations whose effect no record attributes back to them, and the fee model. The surface is young and explicitly
staged: "Multi-outcome markets will be supported but are not part of the initial mainnet release. Additional features
and markets will be rolled out in stages." Write the client so that a rollout stage cannot silently change a number.

## Contents

- Scope, and what belongs in the ordinary Hyperliquid reference instead
- The record of a disagreement, and how it was closed
- The merged book: buying Yes at `p` is selling No at `1 - p`
- Split, merge, negate: attribute the effect to the operation, not to a balance delta
- Fees: a base rate times a deployer scale, and no maker rebate
- What is unverified, stated plainly
- Assertions to write

## Scope

This file covers the outcome surface only. Standard Hyperliquid spot and perpetual trading belongs in the ordinary
venue references and must not be duplicated here: order placement and `cloid`, nonce semantics and API wallets,
positions and PnL, funding, margin, and venue-originated liquidation and ADL as client-observed facts. The HIP-4 page
describes outcomes as "an alternative form of derivative trading that does not involve leverage or liquidations", so a
liquidation rule imported from the perp adapter does not apply to them.

Because the surface is experimental and GitBook exposes no revision id, the only honest pin is a fetch date beside
each fact.

## The record of a disagreement, and how it was closed

An earlier primary-source pass verified `outcomeMeta` and `settledOutcome` on the info-endpoint page and recorded
three things as **not established there**, while the working brief asserted them: the name "HIP-4" itself, outcome
asset IDs, and the `#<encoding>` coin representation. All three are established, on other pages fetched 2026-08-25 and
read again that day: the documentation carries pages titled "HIP-4: Outcome markets" and "HIP-4 deployer actions", and
the Asset IDs page carries the encoding, quoted in full in the other HIP-4 reference. The correction is worth keeping
visible: the earlier pass was right that the info-endpoint page does not establish these and right to refuse to assert
them from it, and the resolution was a different official page rather than a stronger inference.

## The merged book

The two sides of one outcome do not have two independent books. From the HIP-4 page:

```
The order books of Yes and No tokens for the same outcome are merged to share liquidity. For example,
an order to buy Yes at price `p` is equivalent to an order to sell No at price `1-p`. Under the merged
book, price-time priority generalizes to price-side-time priority. In other words, *for orders at the
same merged price level*, the resting sell orders are sorted before all resting buy dual orders.
```

Three things follow. Marketability is a two-side question: an order to buy Yes at `0.55` can execute against resting
sells of Yes or against resting buys of No at `0.45`, so depth computed from one side understates the liquidity you will
actually cross. Queue position is not price-time: at the same merged level, a resting sell of the primary token is ahead
of every resting buy of the dual, which changes the expected fill of a passive quote and cannot be modelled by a
generic price-time simulator. And the arithmetic that ties the sides is exact complementation, so a flat account selling
Yes at `p` is buying No at `1 - p` and must reserve `1 - p` per contract rather than `p`.

The abstraction is not total. The page is candid about the seam: "Most operations abstract the dual book's liquidity
from the user's perspective. However, there are a few examples whose ergonomics will be improved on a future network
upgrade. For example, historical orders can return the primary and dual orders separately if a user sends an order that
both matches and rests on the book." A reconciler that expects one historical order per submitted order will therefore
see two, for exactly the orders that both filled and rested. Key the join on your own intent identity and fold, rather
than asserting a one-to-one mapping.

## Split, merge, negate: attribute the effect to the operation, not to a balance delta

Four actions move value between quote tokens and shares without crossing the book. The HIP-4 page introduces two of
them, quoted: "Advanced users may also manually split and merge outcomes to convert between primary and dual
balances", and for a question, "users with No shares on different outcomes of the same question can redeem quote
tokens before the underlying outcomes settle". The exchange endpoint returned all four sections on 2026-08-26, and
each is a `userOutcome` action:

```json
{"type": "userOutcome", "splitOutcome":  {"outcome": Number, "amount": String}}
{"type": "userOutcome", "mergeOutcome":  {"outcome": Number, "amount": String | null}}
{"type": "userOutcome", "mergeQuestion": {"question": Number, "amount": String | null}}
{"type": "userOutcome", "negateOutcome": {"question": Number, "outcome": Number, "amount": String}}
```

Split turns "`X` quote tokens into `X` Yes and `X` No shares" and merge is its inverse; merge question turns "`X`
Yes shares from each outcome associated to the same question into `X` quote tokens"; negate converts "`X` No shares
from an outcome associated with a question into `X` Yes shares of every other outcome associated with the question".
On the two merge variants the page marks `amount` as `String | null` and notes "null means max". All four document
the same success body, quoted: `{'status': 'ok', 'response': {'type': 'default'}}`. It carries no identifier, so
nothing in the reply names the operation you sent.

**Do not decide what happened by measuring an aggregate balance.** A `-100` quote delta is produced by a split of
`100`, and equally by a fill of `250` shares at `0.40`, and equally by a transfer out, and equally by two of those
netting against a third. A delta is a quantity, and a quantity does not carry the identity of its cause. Attribute
the effect from an accounting record the venue itself keys to the operation, or do not attribute it at all. Two
properties are at stake, and they need different mechanisms.

**At most once.** The nonce is the operation's identity and the venue enforces uniqueness on it: "the 100 highest
nonces are stored per address. Every new transaction must have nonce larger than the smallest nonce in this set and
also never have been used before", and a nonce "must be within `(T - 2 days, T + 1 day)`, where `T` is the unix
millisecond timestamp on the block of the transaction". So resolve a timeout by re-sending the byte-identical signed
action under its original nonce, which the venue either applies once or rejects as used. Never sign a fresh nonce
for a value-moving action whose outcome you are unsure of: that is a second instruction, and it can apply twice. The
replay holds only while that nonce is still above the smallest of the signer's hundred and still inside the time
window, and only while the signing wallet lives, since "previously signed actions can be replayed once the nonce set
is pruned". Outside those conditions the replay is not available to you.

**Correctly attributed.** At most once still leaves the question of whether it happened, and your position and cost
basis need an answer. A measured delta answers it in one case only: where attribution over the measurement window is
provably isolated, meaning nothing but this operation could have moved those balances. Write that claim down where
you rely on it, and write down what would break it. On this surface all of the following do.

| what breaks isolation | why the same delta appears |
| --- | --- |
| any fill | the books are merged, so a resting buy of No moves the same balances as a sell of Yes |
| settlement | it "automatically converts either Yes to `settleFraction` quote tokens and No to `1 - settleFraction`" on the venue's clock, and the first market settles daily at 06:00 UTC |
| a second `userOutcome` action | any process signing for the same account; one API wallet signs for a user, a vault and a subaccount alike |
| a transfer or a fee | quote tokens arriving or leaving for an unrelated reason inside the window |
| `"amount": null` | max is resolved venue-side, so there is no expected magnitude to compare a delta against at all |

Where isolation cannot be established, there are two honest moves and no third. Read an authoritative per-operation
record, if one exists for these actions. Or mark the operation UNKNOWN, stop that account's outcome path, and
resolve it by asking: the nonce-identical replay above while its window holds, otherwise a human holding the nonce
and the signed payload. Never close an UNKNOWN by differencing balances, and never write a position or a cost basis
from a delta whose cause you inferred.

**UNVERIFIED, and it decides the shape of the recovery path:** whether such a per-operation record exists. The
ledger-update union published on the subscriptions page enumerates deposits, withdrawals, internal and sub-account
transfers, liquidations, vault deltas, spot transfers, class transfers, genesis and rewards claims, and no outcome
delta type at all. No page read on 2026-08-26 names a record that reports an applied split, merge or negate against
its nonce. Settle that question before you design the recovery path. Until it is settled, an ambiguous `userOutcome`
is UNKNOWN rather than resolvable.

Commit an intent row carrying the exact nonce, the action and an explicit amount before the send, in a transaction
that closes before the call rather than one enclosing it. Send an explicit amount on any path that can retry: with
`null` the venue picks the size, so you cannot state in advance what a correct application would even be.

## Fees: a base rate times a deployer scale, and no maker rebate

Two current statements sit side by side and both matter. The HIP-4 overview says "Fees are currently zero for outcome
markets for initial testing. However, builder codes do work the same as normal spot trading, where builders earn
builder fees on sell orders that specify their builder code." The deployer page describes the structure that the zero
base rate is currently multiplying:

```
Users trading the outcome's markets pay the base outcome trading fee rate times `scale + max(scale, 1)`:
the deployer receives the `scale` component and the protocol the rest. With `"0"`, users pay the base rate
and the deployer receives nothing. Maker rebates are never paid on outcome markets.
```

`deployerFeeScale` is "a decimal string in `[0, 10]`", it is fixed at deployment, a question's scale "applies uniformly
to its fallback and every question outcome, including ones associated later", and "Per-outcome scales are returned in
the `outcomeMeta` info request". The published multiples run from `1x` the base rate at scale `"0"` to `20x` at scale
`"10"`.

Two things to encode. Do not hard-code zero: the sentence that makes fees zero is a temporary testing statement, and a
client that treats a zero fee as structural will misprice every quote on the day it changes. And do not model a maker
rebate: "Maker rebates are never paid on outcome markets" is unconditional, so a market-making model carrying a
negative maker fee from the perp adapter overstates its edge on every passive fill.

## What is unverified, stated plainly

Each of these is a gap in what the official pages establish, not a thing I decided to omit:

- **Mainnet availability of deployer actions.** The deployer limits are published as "At most `N` active outcomes per
  deployer (N=10 on testnet)" and "`M` outcomes per day (M=50 on testnet)". No mainnet value appears, and no page read
  states that permissionless outcome deployment is live on mainnet. Do not infer it from a third-party guide.
- **The base outcome trading fee rate.** Documented only as "currently zero for outcome markets for initial testing",
  with no numeric field named as its source of truth.
- **Merged book on the market-data channels.** Whether an `l2Book` or `trades` subscription on a `#<encoding>` coin
  returns the merged book or one leg of it is not stated on any page read here. Verify before treating a one-sided
  depth snapshot as total liquidity.
- **A per-operation record for split, merge and negate.** The four action shapes and their acknowledgement-only
  success body are quoted above from the exchange endpoint. What no page establishes is any record reporting one of
  them as applied, keyed to its nonce: the ledger-update union names no outcome delta type. Until that is settled an
  ambiguous `userOutcome` resolves to UNKNOWN, never to a balance comparison.
- **A reused nonce.** What the exchange endpoint returns for a nonce it has already used is not stated on any page read
  here, so the replay is safe by the nonce set rule rather than by a documented response.

## Assertions to write

```python
# tests/test_hip4_outcome_invariants.py
from decimal import Decimal
import pytest

def test_flat_sell_of_yes_reserves_the_complement(keeper):
    keeper.place(outcome=7, side=0, action="sell", qty=Decimal("100"), px=Decimal("0.40"))
    assert keeper.reserved_collateral == Decimal("60")

def test_user_outcome_timeout_resolves_by_identity_never_by_a_delta(db, gateway):
    intent = gateway.split_outcome(outcome=7, amount=Decimal("100"))
    assert db.committed(intent.nonce)          # readable by another process, not a flush
    gateway.fail_next_response_with_timeout()
    gateway.simulate_fill(outcome=7, side=0, qty=Decimal("250"), px=Decimal("0.40"))
    assert gateway.replayed == [intent.nonce] and gateway.fresh_nonces == 0
    assert gateway.resolve(intent) == "UNKNOWN"        # at most once, still not attributable
    assert gateway.balance_snapshots_compared == 0     # the fill reproduces the split's delta
    assert db.position(outcome=7, side=0) == Decimal("250")   # not 350

def test_merge_sends_an_explicit_amount_and_never_the_null_max(gateway):
    with pytest.raises(ValueError):
        gateway.merge_outcome(outcome=7, amount=None)

def test_no_maker_rebate_is_modelled(fee_model):
    assert fee_model.maker_rate(outcome=7) >= 0

def test_a_one_sided_depth_snapshot_is_not_treated_as_total_liquidity(book):
    # whether l2Book on a #<encoding> coin returns the merged book or one leg is unverified
    assert book.depth_source_is_declared_in_config is True
    assert book.assumes_merged is False
```
