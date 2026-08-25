# Hyperliquid HIP-4 outcome markets, experimental and version sensitive

> **Provenance**
> provider: Hyperliquid · surface: HIP-4 outcome markets on HyperCore, via the `info` and `exchange` endpoints · version: experimental, unversioned, staged rollout
> verified_at: 2026-08-25
> sources: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/exchange-endpoint · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-4-deployer-actions · https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
> pinned: GitBook publishes no revision identifier. The HIP-4 overview page rendered the footer "Last updated 2 months ago" when fetched on 2026-08-25. Every page above is also served as raw Markdown by appending `.md` to its URL, which is how each quotation below was taken. Re-fetch before relying on any of it; treat this file as dated, not current.
> verified: the name "HIP-4: Outcome markets" as a documentation page title and URL segment; the outcome asset-id encoding and its three spellings; `outcomeMeta`, `settledOutcome`, `outcomeTemplates` and `outcomeMetaUpdates` as request and subscription types; the `outcome`, `name`, `description`, `sideSpecs`, `quoteToken`, `settleFraction` and `details` fields with their example values; the merged-book rule and price-side-time priority; the four `userOutcome` actions and their acknowledgement-only responses; the deployer settlement actions and the `settleFraction` range; the deployer fee scale formula; the recurring-outcome settlement formula.
> unverified: whether HIP-4 deployer actions are live on mainnet as opposed to testnet, since the published deployer limits are given only as testnet values; the tick and lot rules for outcome assets, which the Tick and lot size page does not mention; whether `cloid` is accepted on an outcome order, which no page read here states; the complete `outcomeMeta` response schema, since the deployer page names fields the published example omits; the current base outcome trading fee rate; whether an `l2Book` or `trades` subscription on a `#<encoding>` coin returns the merged book or one leg.
> revalidate_when: multi-outcome markets ship to mainnet; the `outcomeMeta` example gains or loses a field; `settleQuestion2` is superseded the way `settleQuestion` was; the "Fees are currently zero for outcome markets for initial testing" sentence disappears; the asset-id encoding formula changes.

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
- Split, merge, negate: four value-moving actions that return no identifier
- Fees: a base rate times a deployer scale, and no maker rebate
- What is unverified, stated plainly
- Assertions to write

## Scope

This file covers the outcome surface only. Standard Hyperliquid spot and perpetual trading belongs in the ordinary
venue references and must not be duplicated here: order placement and `cloid`, nonce semantics and API wallets,
positions and PnL, funding, margin, and venue-originated liquidation and ADL as client-observed facts. Outcomes have
"no leverage or liquidations" at all, so a liquidation rule imported from the perp adapter does not apply to them.

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

The correction is worth keeping visible rather than quietly absorbing: the earlier pass was right that the info-endpoint
page does not establish these, and right to refuse to assert them from it. The resolution was a different official page,
not a stronger inference. The `outcomeMeta` and `settledOutcome` shapes recorded in that pass are unchanged and are
reproduced below from the Spot info-endpoint page.

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
format is `WsOutcomeMetaUpdates` and whose documented purpose is "Changes to the outcome meta". Note also that the
market-data `coin` for an outcome is the `#<encoding>` string, so the composite trade key the WebSocket page prescribes,
`(block_time, coin, tid)`, is per outcome side rather than per market.

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

**Two settlement authorities exist and they are not the same counterparty.** Protocol-deployed recurring outcomes are
"automatically deployed and settled by the protocol on a fixed cadence", and the current binary series settles by
interpolation: the contract "settles to YES if and only if `markPrice0 + (settlementTime - t0) / (t1 - t0) *
(markPrice1 - markPrice0) >= targetPrice` where `t0` and `t1` are the timestamps of mark price updates immediately
before and after `settlementTime`". The multi-price variant uses "the same interpolation as binary markets". There is
"guaranteed to be at most one recurring series for each `(seriesType, underlying, period)` combination".

Deployer-created outcomes are settled by their deployer, through the `settleOutcome` and `settleQuestion2` actions, or
by a sub-deployer the deployer authorised for that variant through `setSubDeployers`. The deployer is a permissionless
party subject to a staking requirement and a 183-day minimum staking duration, not the protocol. So the answer to "who
decides what this contract pays" is a property of the individual outcome, and a client crediting a settlement should
record which authority set it. For a question, settlement is sequential and constrained: outcomes "may settle to `"0"`
in any order", exactly one settles to `"1"`, and that one settling "automatically settles the fallback to 0 and settles
the question".

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

## Split, merge, negate: value-moving actions that return no identifier

Four `userOutcome` actions on `POST https://api.hyperliquid.xyz/exchange` move value without an order:

| Action | Effect as documented |
| --- | --- |
| `splitOutcome` `{ "outcome": Number, "amount": String }` | "Split `X` quote tokens into `X` Yes and `X` No shares." |
| `mergeOutcome` `{ "outcome": Number, "amount": String or null }` | "Merge `X` Yes and `X` No shares into `X` quote tokens." `null` means max. |
| `mergeQuestion` `{ "question": Number, "amount": String or null }` | "Merge `X` Yes shares from each outcome associated to the same question into `X` quote tokens." |
| `negateOutcome` `{ "question": Number, "outcome": Number, "amount": String }` | "Convert `X` No shares from an outcome associated with a question into `X` Yes shares of every other outcome associated with the question." |

These are how a holder converts between collateral and shares without crossing the book, and how "users with No shares
on different outcomes of the same question can redeem quote tokens before the underlying outcomes settle".

**Every one of them is documented as returning `{'status': 'ok', 'response': {'type': 'default'}}`.** There is no
identifier in the success response: no operation id, no receipt, nothing to query afterwards. The only identity the
request carries is the `nonce`, documented on each action as "Recommended to use the current timestamp in
milliseconds". A timeout on any of these four is therefore ambiguous in the hardest way, because the acknowledgement
you did not receive contained nothing you could have looked up.

Treat them exactly as an ambiguous external effect. Commit an intent row carrying the exact nonce and amount before the
send, in a transaction that closes before the call rather than one enclosing it. On an unknown outcome, resolve by
reading the balances the action would have changed, comparing them against the pre-send snapshot in the intent row, and
never by resending with a fresh nonce. `mergeOutcome` and `mergeQuestion` accept `null` for "max", which makes a blind
retry unsafe in a second way: the same request submitted twice against a changed balance is not the same instruction.
Send an explicit amount on any path that can retry.

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
- **`settleQuestion`.** The page says "The original `settleQuestion` variant is discontinued" while the
  `setSubDeployers` grant is still spelled `settleQuestion` and "authorizes the `settleQuestion2` action". Read that as
  evidence that action names in this surface change in place, and pin the variant you send.

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

def test_user_outcome_action_commits_intent_before_the_send(db, gateway):
    # the success response carries no identifier; the nonce in the committed row is the only handle
    intent = gateway.split_outcome(outcome=7, amount=Decimal("100"))
    assert db.committed(intent.nonce)          # readable by another process, not a flush
    gateway.fail_next_response_with_timeout()
    outcome = gateway.resolve(intent)
    assert outcome in {"APPLIED", "NOT_APPLIED"} and gateway.resend_count == 0

def test_no_maker_rebate_is_modelled(fee_model):
    assert fee_model.maker_rate(outcome=7) >= 0
```
