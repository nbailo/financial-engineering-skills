# Hyperliquid HIP-4: outcome identity, the side specs, and what settlement pays

> **Provenance**
> provider: Hyperliquid · surface: HIP-4 outcome identity and settlement, via the `info` endpoint and the deployer settlement actions · version: experimental, unversioned, staged rollout
> verified_at: 2026-08-26
> sources: https://hyperliquid.gitbook.io/hyperliquid-docs/hyperliquid-improvement-proposals-hips/hip-4-outcome-markets · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/asset-ids · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/info-endpoint/spot · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/hip-4-deployer-actions · https://hyperliquid.gitbook.io/hyperliquid-docs/trading/contract-specifications · https://hyperliquid.gitbook.io/hyperliquid-docs/for-developers/api/websocket/subscriptions
> pinned: GitBook publishes no revision identifier. The HIP-4 overview page rendered the footer "Last updated 2 months ago" when fetched on 2026-08-25. Every page above is also served as raw Markdown by appending `.md` to its URL; quotations below were taken from one or both forms, and where a page did not return the section being checked that is said in place rather than filled in. On 2026-08-26 the HIP-4 and WebSocket-subscriptions pages were re-fetched as raw Markdown. Re-fetch before relying on any of it; treat this file as dated, not current.
> verified: the name "HIP-4: Outcome markets" as a documentation page title and URL segment; the outcome asset-id encoding and its three spellings; `outcomeMeta`, `settledOutcome`, `outcomeTemplates` and `outcomeMetaUpdates` as request and subscription types; the `outcome`, `name`, `description`, `sideSpecs`, `quoteToken`, `settleFraction` and `details` fields with their example values; the deployer settlement actions, the `settleFraction` range and the question settlement constraint; the description encoding and its keyword hint formats.
> unverified: the tick and lot rules for outcome assets, which the Tick and lot size page does not mention; whether `cloid` is accepted on an outcome order, which no page read here states; the complete `outcomeMeta` response schema, since the deployer page names fields the published example omits; how protocol-deployed recurring outcomes are settled, beyond the one example sentence quoted below.
> revalidate_when: multi-outcome markets ship to mainnet; the `outcomeMeta` example gains or loses a field; `settleQuestion2` is superseded the way `settleQuestion` was; the asset-id encoding formula changes; or the Tick and lot size page starts naming outcomes.

Outcomes are Hyperliquid's prediction-market and bounded-payoff primitive. The docs describe them as "fully
collateralized contracts that settle within a fixed range", useful "for applications such as prediction markets and
bounded options-like instruments", with no leverage and no liquidations. The surface is young and explicitly staged:
"Multi-outcome markets will be supported but are not part of the initial mainnet release. Additional features and
markets will be rolled out in stages." Write the client so that a rollout stage cannot silently change a number.

**Scope.** Identifying an outcome asset, reading what its sides mean, and reading what settlement pays. The merged book,
the split, merge and negate operations, and the fee model are in the other HIP-4 reference. Standard Hyperliquid spot and
perpetual trading belongs in the ordinary venue references and must not be duplicated here. Because the surface is
experimental and GitBook exposes no revision id, the only honest pin is a fetch date beside each fact.

## Contents

- Asset identity: one encoding, three spellings, no inference
- Settlement is a fraction, not a bit, and two different parties set it
- The description is a pipe-delimited encoding whose values may contain a colon
- What is unverified, stated plainly
- Assertions to write

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

## What is unverified, stated plainly

Each of these is a gap in what the official pages establish, not a thing I decided to omit:

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
- **`settleQuestion`.** The original `settleQuestion` variant is discontinued in favour of `settleQuestion2`, while the
  `setSubDeployers` grant is still spelled `settleQuestion` and "authorizes the `settleQuestion2` action". Read that as
  evidence that action names in this surface change in place, and pin the variant you send.
- **Protocol-deployed recurring settlement.** How a recurring outcome's `settleFraction` is determined is not restated
  here. The only sentence this pass could verify is the daily-06:00-UTC BTC example quoted above, and the HIP-4 page
  refers to a separate specification for the rest.

## Assertions to write

```python
# tests/test_hip4_outcome_identity.py
from decimal import Decimal
import pytest

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

def test_outcome_meta_is_parsed_defensively(meta):
    # the published example is not the schema; a field it omits may still arrive
    assert meta.parse({"outcome": 1, "name": "x", "description": "", "sideSpecs": []}) is not None
    assert meta.parse({"outcome": 1, "name": "x", "description": "", "sideSpecs": [],
                       "quoteToken": 0, "details": {}}) is not None
```
