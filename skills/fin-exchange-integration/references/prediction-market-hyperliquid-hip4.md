# Hyperliquid HIP-4 outcome markets, experimental and version sensitive

> **Provenance**
> provider: Hyperliquid · surface: HIP-4 outcome markets on HyperCore, via the `info` and `exchange` endpoints · version: experimental, unversioned, staged rollout
> verified_at: 2026-08-26
> sources: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-4-deployer-actions · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/nonces-and-api-wallets · https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
> pinned: GitBook publishes no revision identifier. The HIP-4 overview page rendered the footer "Last updated 2 months ago" when fetched on 2026-08-25. Every page above is also served as raw Markdown by appending `.md` to its URL; quotations below were taken from one or both forms, and where a page did not return the section being checked that is said in place rather than filled in. On 2026-08-26 the HIP-4, exchange-endpoint, nonces-and-api-wallets and WebSocket-subscriptions pages were re-fetched as raw Markdown; the exchange endpoint returned its four outcome sections that time, and the quotations taken from them carry that date. Re-fetch before relying on any of it; treat this file as dated, not current.
> verified: the name "HIP-4: Outcome markets" as a documentation page title and URL segment; the outcome asset-id encoding and its three spellings; `outcomeMeta`, `settledOutcome`, `outcomeTemplates` and `outcomeMetaUpdates` as request and subscription types; the `outcome`, `name`, `description`, `sideSpecs`, `quoteToken`, `settleFraction` and `details` fields with their example values; the merged-book rule and price-side-time priority; the deployer settlement actions, the `settleFraction` range and the question settlement constraint; the sub-deployer grant list and the 183-day deactivation rule; the description encoding and its keyword hint formats; the deployer fee scale formula and the no-maker-rebate rule; the `(block_time, coin, tid)` trade key; and, on 2026-08-26, the four `userOutcome` action shapes with their one-line descriptions and their identical acknowledgement-only success body, the nonce set rules, and the API-wallet pruning warning.
> unverified: whether HIP-4 deployer actions are live on mainnet as opposed to testnet, since the published deployer limits are given only as testnet values; the tick and lot rules for outcome assets, which the Tick and lot size page does not mention; whether `cloid` is accepted on an outcome order, which no page read here states; the complete `outcomeMeta` response schema, since the deployer page names fields the published example omits; the current base outcome trading fee rate; whether an `l2Book` or `trades` subscription on a `#<encoding>` coin returns the merged book or one leg; whether any info or WebSocket record reports an applied split, merge, merge-question or negate keyed to the action's nonce, since the ledger-update union on the subscriptions page enumerates no outcome delta type; what the exchange endpoint returns for a nonce it has already used; how protocol-deployed recurring outcomes are settled, beyond the one example sentence quoted below.
> revalidate_when: multi-outcome markets ship to mainnet; the `outcomeMeta` example gains or loses a field; `settleQuestion2` is superseded the way `settleQuestion` was; the "Fees are currently zero for outcome markets for initial testing" sentence disappears; the asset-id encoding formula changes; a `userOutcome` delta type appears in the ledger-update union; or the `userOutcome` success body gains a field.

Outcomes are Hyperliquid's prediction-market and bounded-payoff primitive. The docs describe them as "fully
collateralized contracts that settle within a fixed range", useful "for applications such as prediction markets and
bounded options-like instruments", with no leverage and no liquidations. The surface is young and explicitly staged:
"Multi-outcome markets will be supported but are not part of the initial mainnet release. Additional features and
markets will be rolled out in stages." Write the client so that a rollout stage cannot silently change a number.

## Contents

- Scope, and what belongs in the ordinary Hyperliquid reference instead
- The record of a disagreement, and how it was closed
- Asset identity: one encoding, three spellings, no inference
- The merged book: buying Yes at `p` is selling No at `1 - p`
- Settlement is a fraction, not a bit, and two different parties set it
- The description is a pipe-delimited encoding whose values may contain a colon
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

Two properties of the surface should shape every design decision below. It is **experimental**: features arrive in
stages and one action variant has already been discontinued in place. And it is **version sensitive**: GitBook exposes
no revision id, so the only honest pin is a fetch date beside each fact, which is why this file carries one.

## The record of a disagreement, and how it was closed

An earlier primary-source pass in this repository recorded a gap. On the official info-endpoint page it verified the
request types `outcomeMeta` and `settledOutcome` and the fields `outcome`, `sideSpecs`, `name`, `description`,
`settleFraction` and `details`, including the pipe-delimited description encoding. It recorded three things as **not
established on that page**, while the working brief asserted them: the name "HIP-4" itself, outcome asset IDs, and the
`#<encoding>` coin representation. The instruction was to record the disagreement rather than assert the three.

All three are now established, from pages other than the info-endpoint page, fetched 2026-08-25:

- **The name.** The documentation carries a page titled "HIP-4: Outcome markets" at
  `/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets`, and a second titled "HIP-4 deployer
  actions" at `/hyperliquid-docs/for-developers/api/hip-4-deployer-actions`. The name is the venue's own.
- **Outcome asset IDs and the `#` spelling.** Both are on the Asset IDs page, quoted in full in the next section. That
  page, not the info-endpoint page, is where the outcome API representation is documented, and the HIP-4 overview page
  points at it: "The outcome trading API is similar to spot, with key differences highlighted here" followed by the
  Asset IDs URL.

Both pages were fetched again on 2026-08-25 and still say this, so the resolution rests on two independent reads rather
than one. The correction is worth keeping visible rather than quietly absorbing: the earlier pass was right that the
info-endpoint page does not establish these, and right to refuse to assert them from it. The resolution was a different
official page, not a stronger inference. The `outcomeMeta` and `settledOutcome` shapes recorded in that pass are
unchanged and are reproduced below from the Spot info-endpoint page.

## Asset identity: one encoding, three spellings

The Asset IDs page states the outcome representation directly:

```
Outcomes share most implementation details with spot trading, with a different token representing
each outcome side. However, the API representation of outcomes is different from both spot and perps.

Outcome assets are derived from an `outcome` id plus a binary `side`. These are found in the
`outcomeMeta` info response.

For an outcome with id `outcome` and side `side`:

    encoding = 10 * outcome + side

Only side `0` and side `1` are valid.

The same `encoding` is used in three different representations:

* Outcome spot coin: `#<encoding>`
* Outcome token name: `+<encoding>`
* Outcome asset ID: `100_000_000 + encoding`

Example:

* `#10` = outcome `1`, side `0`
* `+10` = the corresponding token name
* `100000010` = asset ID
```

Three consequences for a client. First, the three spellings are not interchangeable strings: `#10`, `+10` and
`100000010` denote the same thing in the market-data, token and order-entry vocabularies respectively, and a lookup
table keyed on one of them will silently miss the others. Derive all three from `(outcome, side)` in one place and never
parse one spelling into another by hand.

Second, the encoding multiplies by ten while only two sides are legal, so eight of every ten integers in the range are
not assets at all. That gap is a free assertion: reject any `encoding` whose last digit is not `0` or `1` before it
reaches an order.

Third, `outcome` is "the numeric index assigned at creation", so it is a venue-assigned identifier you read, never one
you compute. Get it from `outcomeMeta`, whose published example is:

```json
{
  "outcomes": [
    {
      "outcome": 123,
      "name": "Recurring",
      "description": "class:priceBinary|underlying:HYPE|expiry:20260310-1100|targetPrice:34.5|period:3m",
      "sideSpecs": [ { "name": "Yes" }, { "name": "No" } ]
    }
  ]
}
```

The set of outcomes changes under a running process, so subscribe to `{ "type": "outcomeMetaUpdates" }`, whose data
format the subscriptions page gives as `WsOutcomeMetaUpdates`; the page lists the subscription without a prose purpose,
so treat the name as the whole of what is documented. Note also that the market-data `coin` for an outcome is the
`#<encoding>` string, so the composite trade key the WebSocket page prescribes, quoted, "For a globally unique trade id,
use (block_time, coin, tid)", is per outcome side rather than per market.

**The side names are not reliably "Yes" and "No".** The HIP-4 page says the tokens are "labeled by `sideSpecs` in the
`outcomeMeta` info endpoint, often `Yes` and `No`", and the deployer page confirms the exception: for standalone
outcomes the side names are "`template:` plus the template's side names (e.g. `template:Over` / `template:Under`)",
while question outcomes "use the defaults `Yes` / `No`". Read `sideSpecs` by index, treat index `0` as the side that
receives `settleFraction`, and never match on the literal string "Yes".

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

## Settlement is a fraction, and two parties set it

From the HIP-4 page: "Settlement automatically converts either Yes to `settleFraction` quote tokens and No to
`1 - settleFraction` quote tokens. In particular, `settleFraction = 1` for 'binary yes' and `settleFraction = 0` for
'binary no' settlement." `settledOutcome` returns it as a **string**, alongside the spec and a `details` string:

```json
{
  "spec": {
    "outcome": 95,
    "name": "Recurring",
    "description": "class:priceBinary|underlying:BTC|expiry:20260526-0600|targetPrice:77363|period:1d",
    "sideSpecs": [ { "name": "Yes" }, { "name": "No" } ],
    "quoteToken": "USDC"
  },
  "settleFraction": "0.0",
  "details": "price:76876.9"
}
```

Parse it as an exact decimal, never as a boolean and never through a float. The deployer page is explicit that
intermediate values are legal: "`settleFraction` is a decimal in `[0, 1]`. Standalone outcomes may settle to any
fraction (e.g. `"0.66"` for scalar payouts); outcomes that belong to a question must settle to exactly `"0"` or `"1"`."
A payout model that branches on a yes-or-no result is correct only for question outcomes and is wrong for a standalone
scalar.

**Two settlement authorities exist and they are not the same counterparty.** Deployer-created outcomes are settled by
their deployer, through the `settleOutcome` and `settleQuestion2` actions, or by a sub-deployer the deployer authorised
for that variant through `setSubDeployers`; the grant list is `registerStandaloneOutcomeFromTemplate`,
`registerQuestionFromTemplate`, `registerAndAssociateNamedOutcomeFromTemplate`, `settleOutcome` and `settleQuestion`,
and "The `settleQuestion` grant authorizes the `settleQuestion2` action." A deployer carries a staking obligation:
"Active deployers must maintain the staking requirement for as long as it remains an outcome deployer", and
"Deactivation requires that the minimum deployer staking duration (183 days) has elapsed and that the deployer has no
active outcomes."

Some outcomes are instead deployed and settled by the protocol on a cadence. The HIP-4 page's example is, quoted: "The
first market is a recurring binary outcome that settles daily at 06:00 UTC to the BTC mark price on HyperCore mark
prices." **UNVERIFIED, and deliberately not restated here:** the settlement rule for protocol-deployed recurring
outcomes, including any interpolation formula and any uniqueness constraint over series. An earlier pass recorded a
specific formula for these; re-fetching the HIP-4 and deployer pages on 2026-08-25 did not find it on either, and the
HIP-4 page points at a separate specification this pass did not read. Do not implement a recurring settlement rule
from this file. Read `settledOutcome` from the venue instead, which is the design rule below in any case.

So the answer to "who decides what this contract pays" is a property of the individual outcome, and a client crediting
a settlement should record which authority set it. For a question, settlement is sequential and constrained: outcomes
"may settle to `"0"` in any order", exactly one settles to `"1"`, and that one settling "automatically settles the
fallback to 0 and settles the question".

Treat a settlement observation the way any externally decided fact is treated: read `settledOutcome` from the venue
before crediting, dedupe the credit on the `outcome` id durably, and express a correction as a reversing entry.

## The description is an encoding, and a colon does not delimit it

The `description` field is a machine-readable specification, and the deployer page defines its construction: "the
keyword-value pairs sorted by keyword and joined as `keyword:value|keyword:value`, e.g.
`expiry:20260801-0600|target:100|underlying:BTC`. Descriptions are at most 2000 characters."

The parsing hazard is stated on the same page: values "are at most 100 characters and cannot contain `{`, `}`, or `|`
(`:` is allowed, e.g. for HIP-3 coin names)". So `|` is the only safe delimiter, and within a pair you must split on
the **first** colon only. A naive `split(":")` breaks on an `hlPerp` keyword whose value is a builder-perp coin such as
`test:ABC`, and the failure is a wrong underlying rather than an exception. The `priceThresholds:81538,81783` form used
by multi-price recurring outcomes shows values may also contain commas.

Keyword values are typed by a template `hint`, with formats the page fixes: `dateTime` as `%Y%m%d-%H%M`, `date` as
`YYYYMMDD` meaning end of day, `hlPerp` as an existing perp coin name, `uInt` as a nonnegative u64 with "no sign or
leading zeros", and `uDecimal` as a nonnegative decimal with "no sign, exponent, or leading/trailing zeros". Both
`dateTime` and `date` "must be within the next year". The `name` field is separately structured: template deployments
produce `template:<template_id>`, and "The `template:` prefix is reserved. Only template deployments can produce it."

Parse the description into typed fields, assert the keys you require are present, and refuse to trade an outcome whose
description you could not parse. A silently missing `expiry` is a position with no known end.

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
- **Tick and lot rules for outcome assets.** The Tick and lot size page covers perps (`MAX_DECIMALS` 6) and spot
  (`MAX_DECIMALS` 8) and does not mention outcomes. The HIP-4 page says outcomes "share most implementation details
  with spot trading", which is not the same as saying the spot price rule applies. Read the constraint from a rejection
  in a controlled test, or from a page that states it, before quantizing.
- **`cloid` on an outcome order.** No page read here states whether a client order id is accepted on an order against
  an outcome asset id. Until it is confirmed, do not build the recovery path on it.
- **The complete `outcomeMeta` schema.** The deployer page asserts the response "includes non-null outcome deployers"
  and that "Per-outcome scales are returned in the `outcomeMeta` info request", yet the published example on the Spot
  info-endpoint page shows only `outcome`, `name`, `description` and `sideSpecs`. The example is therefore not the
  schema. Parse defensively and do not require a field the example omits.
- **The base outcome trading fee rate.** Documented only as "currently zero for outcome markets for initial testing",
  with no numeric field named as its source of truth.
- **Merged book on the market-data channels.** Whether an `l2Book` or `trades` subscription on a `#<encoding>` coin
  returns the merged book or one leg of it is not stated on any page read here. Verify before treating a one-sided
  depth snapshot as total liquidity.
- **`settleQuestion`.** The original `settleQuestion` variant is discontinued in favour of `settleQuestion2`, while the
  `setSubDeployers` grant is still spelled `settleQuestion` and "authorizes the `settleQuestion2` action". Read that as
  evidence that action names in this surface change in place, and pin the variant you send.
- **A per-operation record for split, merge and negate.** The four action shapes and their acknowledgement-only
  success body are quoted above from the exchange endpoint. What no page establishes is any record reporting one of
  them as applied, keyed to its nonce: the ledger-update union names no outcome delta type. Until that is settled an
  ambiguous `userOutcome` resolves to UNKNOWN, never to a balance comparison.
- **Protocol-deployed recurring settlement.** How a recurring outcome's `settleFraction` is determined is not restated
  here. The only sentence this pass could verify is the daily-06:00-UTC BTC example quoted above, and the HIP-4 page
  refers to a separate specification for the rest.

## Assertions to write

```python
# tests/test_hip4_outcome_invariants.py
from decimal import Decimal

def test_encoding_round_trips_and_rejects_illegal_sides(asset):
    assert asset.encoding(outcome=1, side=0) == 10
    assert asset.coin(1, 0) == "#10" and asset.token_name(1, 0) == "+10"
    assert asset.asset_id(1, 0) == 100_000_010
    for bad_side in (2, -1):
        with pytest.raises(ValueError):
            asset.encoding(outcome=1, side=bad_side)

def test_side_names_come_from_sidespecs_not_from_a_literal(meta):
    # standalone outcomes carry template: side names such as template:Over / template:Under
    spec = meta.outcome(123)
    assert spec.side_name(0) == spec.side_specs[0]["name"]
    assert "Yes" not in client.hardcoded_side_names

def test_settle_fraction_is_an_exact_decimal_in_range(settled):
    f = Decimal(settled["settleFraction"])
    assert Decimal(0) <= f <= Decimal(1)
    assert payout(side=0, qty=Decimal("100"), fraction=f) == Decimal("100") * f
    assert payout(side=1, qty=Decimal("100"), fraction=f) == Decimal("100") * (Decimal(1) - f)

def test_description_splits_on_pipe_then_first_colon(parser):
    d = "class:priceBinary|underlying:test:ABC|expiry:20260801-0600|targetPrice:34.5"
    assert parser(d)["underlying"] == "test:ABC"
    assert parser(d)["expiry"] == "20260801-0600"

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
```
