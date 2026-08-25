# Polymarket CLOB V2: signing, identity, fees, ticks and settlement as a client

> **Provenance**
> provider: Polymarket · surface: CLOB V2 REST, Gamma market data, EIP-712 order signing · version: V2
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/v2-migration ·
> https://docs.polymarket.com/trading/fees ·
> https://docs.polymarket.com/market-data/market-details ·
> https://docs.polymarket.com/api-reference/markets/get-clob-market-info ·
> https://docs.polymarket.com/concepts/order-lifecycle ·
> https://docs.polymarket.com/trading/place-orders ·
> https://docs.polymarket.com/concepts/pusd · https://docs.polymarket.com/llms.txt
> pinned: `Polymarket/py-clob-client-v2@v1.1.0`, commit `215fc63a8fd6ec3a10c7edb73997c9772d8686d3`,
> which was the tip of `main` when this file was written. Every code behaviour below was read at that commit.
> verified: the two exchange addresses and their EIP-712 domain; the eleven signed struct members; the salt and
> timestamp construction; the six accepted tick sizes and the price bound; the fee curve as the pinned client
> computes it; the `fd` and `feeSchedule` field names and their documented meanings; the order and trade status
> vocabularies; the GTD offset rule; the POST response members.
> unverified: everything in the closing section, which lists each gap and why it is open. The largest is the
> relationship between the three fee-parameter surfaces.
> revalidate_when: a V3 migration notice appears; the `fd` or `feeSchedule` members change; a new tick size is
> accepted; `py-clob-client-v2` publishes a tag past `v1.1.0`; or work starts on `Polymarket/py-sdk`, which the
> pinned repository's own README recommends over this client for new projects.

## Contents

- What V2 changed, and why a V1 mental model produces a wrong number rather than an error
- Packages, the two exchanges, and the domain that did not change
- Collateral is pUSD
- The signed struct, and the fact that V2 gives you no order identity
- Fees: one curve, an exponent the documented formula omits, and three surfaces nobody reconciles
- Ticks: six sizes, a bound that is not a grid, and a cache that no longer expires
- Order types, the GTD offset, and what the POST response proves
- Trade statuses, and why a transaction hash is not settlement
- Required assertions, as code
- What is verified here, and what is not

## What V2 changed

Four members left the signed order and three joined it, the collateral token changed, and the fee stopped being
something you sign. A V1 client does not fail loudly against V2. The V1 packages do fail loudly, and that is the
easy case. The hard case is a house client that kept the V1 order model, kept a fee estimator built on
`feeRateBps`, and now reserves the wrong amount of collateral on a path where every arithmetic step is correct.

The migration page is explicit that the V1 packages are out: `@polymarket/clob-client` and `py-clob-client`
"only work against V1 and no longer work against production CLOB V2." The supported packages are
`@polymarket/clob-client-v2` (TypeScript) and `py-clob-client-v2` (Python).

## The two exchanges, and the domain that did not change

Two verifying contracts, selected by whether the market is a negative-risk market:

| Market kind | `verifyingContract` |
|---|---|
| standard | `0xE111180000d2663C0091e4f400237545B87B996B` |
| negative risk | `0xe2222d279d744050d28e00520010520000310F59` |

Both sign under the same EIP-712 domain shape: `name` "Polymarket CTF Exchange", `version` "2", `chainId` 137.
Only the address differs. The V1-era claim that the negative-risk flag also selects a different collateral token
is **wrong for V2**: the migration page states that only the Exchange domain changes, and the pinned client's
`get_contract_config` carries one `collateral` value per chain, not one per exchange.

API authentication did not move with it. Quoted: "Only the Exchange domain changes. The `ClobAuthDomain` used
for L1 API authentication stays at version `\"1\"`." Two domains, two versions, in the same process. A single
`DOMAIN_VERSION` constant shared by the signer and the authenticator signs one of them wrong.

In the pinned client, `get_contract_config` returns the same `exchange_v2` and `neg_risk_exchange_v2` addresses
for chain 137 and for Amoy chain 80002. Read the address from the network you are actually on and assert it,
rather than trusting a constant that does not vary by chain.

## Collateral is pUSD

pUSD replaces USDC.e as the collateral token. The docs describe it as "a standard ERC-20 on Polygon backed by
USDC, with backing enforced onchain by the smart contract", with 6 decimals, and as "the collateral token used
for all trading on Polymarket". An API trader converts by calling `wrap(address _asset, address _to, uint256
_amount)` on the CollateralOnramp at `0x93070a847efEf7F70739046A929D47a521F5B8ee`, and reverses it with
`unwrap` on the CollateralOfframp.

Two consequences for a client that already held a balance. A balance check that reads the USDC.e contract
reports a number that can no longer be traded with, and reports it as a healthy balance rather than as an error.
And a wrap is a value-moving external call in its own right: it needs the same durable intent row, the same
committed identity and the same query-on-timeout treatment as an order, not a bare retry loop.

## The signed struct, and the identity V2 does not give you

The V2 EIP-712 `Order` type has eleven members, in this order, read from
`order_utils/model/ctf_exchange_v2_typed_data.py` at the pinned commit:

```
salt uint256, maker address, signer address, tokenId uint256, makerAmount uint256,
takerAmount uint256, side uint8, signatureType uint8, timestamp uint256,
metadata bytes32, builder bytes32
```

Gone from the signed struct: `taker`, `expiration`, `nonce`, `feeRateBps`. `expiration` still travels, in the
POST body, for GTD handling; the migration page states it "is **not** part of the V2 EIP-712 signed struct", and
the pinned typed-data module confirms it is absent from both the type and the signed message. `timestamp` is
documented as "order creation time in milliseconds", and the order-lifecycle page calls it "Timestamp (in
milliseconds, used for order uniqueness)". It replaces `nonce`.

**Neither the salt nor the timestamp is derived from your intent.** `generate_order_salt()` is
`str(int(random.random() * (time.time_ns() // 1_000_000)))`, and `OrderBuilder.build_order` sets the timestamp
from `time.time_ns() // 1_000_000` at build time and ignores any value the caller put on the order args. So the
order hash, which is what the venue and the chain know your order by, is a fresh random value on every build.
Build the same intent twice and you have signed two economically distinct orders. There is no
`clientOrderId` anywhere in the V2 payload: `order_to_json_v2` emits the eleven signed members plus `signature`,
`owner`, `orderType`, `deferExec` and `postOnly`, and the place-orders page states no idempotency guarantee at
all. Treat the absence as the fact it is: **the venue offers you no deduplication, so a resend is a second
order.**

Two practical consequences.

- **Mint and commit your own intent identity before the build.** The row survives `ROLLBACK` and is readable by
  another process after a crash. Recovery after a timeout is a query against open orders and trades for that
  intent, never a rebuild and repost, because a rebuild mints a new salt and a new order hash and the venue has
  no way to recognise it as the same instruction.
- **`metadata` is the field you control.** It is a caller-supplied bytes32 inside the signed struct, so a hash of
  your intent id fits there and comes back attached to the order. It is a correlation key. Nothing in the docs
  says the venue rejects a repeated `metadata`, so it is not a dedupe key, and treating it as one is exactly the
  V1 `nonce` mistake in new clothing. Note also that `MarketOrderArgsV2` has no `metadata` member at the pinned
  commit, so a market order built through the high-level builder carries `bytes32(0)` and no correlation tag.

`random.random()` draws from the process-global Mersenne Twister. A forked worker inherits that state, so two
children forked from one parent can generate the same salt sequence. Seed per process, or inject a salt
generator: `ExchangeOrderBuilderV2.__init__` takes `generate_salt` as a parameter, which is the seam for making
the order hash a deterministic function of your committed intent id.

One retry path lives inside the client. `create_and_post_order` wraps the build and the post in
`_retry_on_version_update`, which calls the whole closure a second time if the resolved API version changed
across the first call. The second call builds a **new** order with a new salt and posts it. The trigger is an
explicit `order_version_mismatch` error in the response body, so it is guarded, but the version it compares
against comes from `get_version()`, which swallows every exception and returns `2`. A transport failure on
`/version` is therefore indistinguishable from an answer. If you need certainty about how many orders exist,
post through `post_order` yourself and resolve ambiguity by query.

`signatureType` is an enum, not a boolean: `EOA` 0, `POLY_PROXY` 1, `POLY_GNOSIS_SAFE` 2, `POLY_1271` 3. Under
`POLY_1271` the signer is the funder rather than the key address, and the signature is a nested Solady
`TypedDataSign` envelope rather than a plain ECDSA signature. Fix the type in config and assert it, because it
is a signed member and a wrong value invalidates every order silently.

## Fees

The documented formula is `fee = C × feeRate × p × (1 - p)`, where C is shares traded and p is the share price.
Fees "are rounded to 5 decimal places. The smallest fee charged is 0.00001 USDC." Makers pay nothing:
"Makers are never charged fees. Only takers pay fees." Category taker rates run from 0 for Geopolitics to 0.07
for Crypto, with maker rates uniformly 0. The worked example on that page is 100 Crypto shares at 0.50, giving
`100 × 0.07 × 0.50 × 0.50 = $1.75`.

**That formula is the special case of the real one.** The pinned client computes, in `fees.py`:

```python
platform_fee_rate = fee_rate * (price * (1 - price)) ** fee_exponent
platform_fee = (fee_base_amount / price) * platform_fee_rate * (1 + fee_slippage / 100)
```

`fee_base_amount / price` is shares, so this is `C × rate × (p(1-p))**e`. The documented formula is that
expression at `e == 1`. The exponent is a per-market parameter: `/clob-markets/{condition_id}` documents `fd.e`
as "Fee curve exponent", Gamma documents `feeSchedule.exponent` as "Exponent applied to price component", and
the pinned repository's own fee tests use `fee_exponent = 2`. Hard-coding the documented formula is therefore a
bet that every market you touch has `e == 1`. **Which exponent production markets actually carry is not
documented anywhere I could read, and is UNVERIFIED.** Read it per market and reconcile the estimate against
the venue's own charged fee.

**Two defaults in the client fail toward silence.** `get_clob_market_info` builds `FeeInfo(rate=fd.get("r",
0.0), exponent=fd.get("e", 0.0))` from `result.get("fd") or {}`. A response with no `fd` yields rate 0, so the
estimated fee is zero and the reserve is short by the whole fee. A response with `r` but no `e` yields exponent
0, and `(p(1-p))**0 == 1` removes the price dependence entirely: at p = 0.50 with rate 0.07 that estimates
`$7.00` per 100 shares against the documented `$1.75`, and at p = 0.99 it estimates `$7.00` against `$0.0693`.
Neither case raises. Assert that both members are present and that the exponent is what you expect before you
size anything.

**The platform fee is symmetric; the builder fee is not.** `(p(1-p))**e` is unchanged by swapping p and 1 - p
for any exponent, so the platform fee on the two legs of the venue's own YES/NO identity is the same number.
The builder fee in the same function is `fee_base_amount * builder_taker_fee_rate`, proportional to collateral.
Buying 100 shares at 0.99 pays a builder fee 99 times the fee for buying 100 at 0.01, while the platform fee at
those two prices is identical. A router that compares total taker cost across the two legs will drift toward one
of them once a builder code is attached. Builder rates come from `builder_maker_fee_rate_bps` and
`builder_taker_fee_rate_bps` divided by `BUILDER_FEES_BPS = 10000`, and `__ensure_builder_fee_rate_cached`
catches every exception and logs a warning, leaving the rate at 0, which under-reserves. **The builder-fees
documentation page listed in `llms.txt` returned 404 at both URL forms on 2026-08-25, so every builder-fee
statement here comes from the pinned client only and the venue-side semantics are UNVERIFIED.**

`fee_slippage` is a percentage buffer, validated as 0 or a value between 1 and 100, that inflates the estimated
platform fee. It exists because the fee is "determined by the protocol at match time, not embedded in your
signed order". You sign a price and a size; you do not sign what you will pay. Size against the buffered
estimate and book the venue's own fee number.

### The fee-parameter surface gap, stated plainly

Three surfaces name fee parameters, and no page I read relates any of them to any other.

| Surface | Members | Documented meaning |
|---|---|---|
| CLOB `GET /clob-markets/{condition_id}`, member `fd` | `r`, `e`, `to` | "Fee rate", "Fee curve exponent", "Whether fees apply to takers only" |
| the same response, top level | `mbf`, `tbf` | "Maker base fee in basis points", "Taker base fee in basis points" |
| Gamma `GET /markets/slug/{slug}`, member `feeSchedule` | `rate`, `exponent`, `takerOnly`, `rebateRate` | "Base rate in fee calculation", "Exponent applied to price component", "Fees charged to taker only when true", "Fraction of taker fees rebated to makers" |

What is verified: the names, the documented meanings above, and that the pinned client feeds `fd.r` and `fd.e`
into the curve. What is **UNVERIFIED**: that `feeSchedule.rate` and `fd.r` carry the same value for the same
market at the same instant; that `feeSchedule.exponent` and `fd.e` do; that `takerOnly` and `to` do; how the
basis-point members `mbf` and `tbf` relate to a rate that is not in basis points; and whether
`feeSchedule.rebateRate` has any CLOB counterpart at all, since the pinned client reads none and the CLOB
endpoint documents none. Do not build a fee model that reads one surface and validates against another as
though they were the same number. Pick one surface, say in a comment which one and why, and reconcile against
the fee the venue actually charged on a fill.

The pinned client also exposes `get_fee_rate_bps`, which reads `base_fee` from a separate `/fee-rate` endpoint.
At the pinned commit that value is passed only to V1 order building and is `None` for V2 orders. It is a fourth
spelling of "the fee rate" and it is not the one V2 uses.

Fee denomination is its own small gap. The fees page states fees are calculated in USDC while collateral is
pUSD. Whether a fee debit is denominated in pUSD units is not stated on either page and is **UNVERIFIED**.

## Ticks, the price bound, and a cache that no longer expires

Six tick sizes are accepted: 0.1, 0.01, 0.005, 0.0025, 0.001, 0.0001. The pinned client's `TickSize` literal and
its `ROUNDING_CONFIG` keys are exactly that set, and 0.005 and 0.0025 are the two that a V1-era client does not
know about. The market-data page is direct about where the value comes from: "Always read the active value from
the market rather than assuming a fixed increment." Read `orderPriceMinTickSize` from Gamma or `mts` from the
CLOB, per market.

`price_valid(price, tick_size)` is `float(tick_size) <= price <= 1 - float(tick_size)`. That is a **bound, not a
grid**. It says 1% and 99% are the extreme quotable probabilities on a 0.01-tick market. It says nothing about
whether the price sits on a multiple of the tick.

Nothing in the client puts it on one either. `ROUNDING_CONFIG` maps tick "0.005" to 3 price decimals and tick
"0.0025" to 4, and `create_order` applies `round_normal(price, decimals)`. Rounding 0.1234 to three decimals
gives 0.123, which has the right decimal count and is not a multiple of 0.005. The place-orders page states
"The CLOB rejects an order whose price does not conform to the market's current tick size." So on the two new
tick sizes the client's rounding is not the venue's acceptance test. Quantise to an integer multiple of the tick
yourself before signing, in `Decimal`, and assert it. Whether the CLOB tests divisibility or only the decimal
count is not documented and is **UNVERIFIED**; the guard is two lines and the failure it prevents is a rejected
order at best.

`orderMinSize` is documented as "Minimum order size in USDC; CLOB rejects orders below". Check it in the same
place as the tick check, after rounding, because rounding size down can cross it.

**The tick cache has no expiry.** `tickSizeTtlMs` is gone: the migration page says it is "no longer
configurable", and the constructor no longer accepts it. What replaced it is not a shorter TTL but no TTL.
`get_clob_market_info` writes `__tick_sizes`, `__neg_risk` and `__fee_infos` per token, `get_tick_size` returns
the cached value whenever the key is present, and `__ensure_market_info_cached` returns immediately if the token
is already in `__fee_infos`. Nothing evicts. Under V1 a tick change cost you up to five minutes of rejected
orders; under V2 a long-lived process keeps the stale tick, the stale negative-risk flag and the stale fee
parameters until it restarts. If you hold a client for longer than a market's parameters are stable, refresh
market info on a schedule you own, and refresh it before any decision that reserves collateral.

## Order types, the GTD offset, and what the POST response proves

GTC and GTD for limit orders, FAK and FOK for market orders. The pinned client rejects `post_only` for FOK and
FAK. `expiration` is `"0"` for GTC and a Unix timestamp in seconds otherwise.

The GTD arithmetic has an offset that a naive client gets wrong in the dangerous direction: "GTD orders expire
one minute before their stated expiration as a security threshold. To set an effective lifetime of N seconds,
use `now + 60 + N`." An order written as `now + N` therefore dies `60` seconds early, and a hedge leg that
expires before its pair leaves a one-sided position. Expirations must also be "at least **3 minutes** in the
future"; anything sooner is rejected.

A successful POST carries `orderID`, `status`, `makingAmount`, `takingAmount`, `transactionsHashes` and
`tradeIDs`. A failure carries `"success": false` and an `errorMsg`. Order `status` takes four documented values:
`live`, "Order is resting on the book"; `matched`, "Order matched immediately"; `delayed`, "Marketable order
accepted into an asynchronous delay window on configured seconds-delay markets"; and `unmatched`, "Marketable
order placed on the book after the delay expired without a match". `delayed` is the one that breaks a two-state
client: it is neither resting nor filled, and it resolves later without another instruction from you.

## Trade statuses, and why a transaction hash is not settlement

Five trade statuses, with terminality documented: `MATCHED`, "Trade matched, sent to executor for onchain
submission", non-terminal; `MINED`, "Transaction mined into the blockchain", non-terminal; `RETRYING`,
"Transaction failed, being retried", non-terminal; `CONFIRMED`, "Trade achieved finality, successful",
terminal; `FAILED`, "Trade failed permanently", terminal.

The pinned client does not use that vocabulary. `_is_trade_resolved` returns true when the status is `FAILED`
**or** the trade carries any `transaction_hash`, and `_resolve_transactions_hashes` collects the hashes of every
trade that is not `FAILED` into `transactionsHashes`. A `MINED` trade has a hash and is not terminal, and a
`RETRYING` trade can have one too. **A hash in `transactionsHashes` is therefore evidence of submission, not of
settlement.** Book value on `CONFIRMED`, read from the trades endpoint, and treat everything before it as
provisional.

The polling that fills that member is best effort by design: `RESOLVE_TRADES_TIMEOUT_SECONDS = 30.0` at a 0.25
second interval, exceptions inside the loop are swallowed as "not resolved yet", and on timeout the response is
returned with whatever resolved, "which may be none". So an **empty** `transactionsHashes` is not evidence that
nothing filled. It is the unresolved case, and the resolution is a query on `tradeIDs`.

## Required assertions

```python
# tests/test_polymarket_v2_client.py
from decimal import Decimal

def test_the_order_hash_is_not_the_intent_identity(client, intent):
    a = client.build(intent)
    b = client.build(intent)                       # same intent, rebuilt
    assert a.salt != b.salt and a.order_hash != b.order_hash
    assert intent.id == a.intent_id == b.intent_id  # our identity is stable across builds

def test_a_timeout_resolves_by_query_and_never_by_rebuild(client, venue, intent):
    venue.post_times_out_after_transmitting()
    client.submit(intent)
    assert venue.orders_created == 1               # the intent row was committed before the post
    assert venue.query_calls_for(intent.id) >= 1   # resolved by asking, not by resending

def test_the_fee_estimate_uses_the_market_exponent(estimator):
    # the documented formula is the e == 1 case; the client generalises it
    assert estimator(shares=100, p=Decimal("0.50"), rate=0.07, e=1) == Decimal("1.75000")
    assert estimator(shares=100, p=Decimal("0.99"), rate=0.07, e=1) == \
           estimator(shares=100, p=Decimal("0.01"), rate=0.07, e=1)   # the curve is symmetric

def test_absent_fee_parameters_raise_rather_than_defaulting_to_zero(client, market_info):
    del market_info["fd"]
    try:
        client.fee_parameters(market_info)
    except ValueError:
        return
    raise AssertionError("a missing fee curve must not estimate a zero fee")

def test_price_is_quantised_to_a_multiple_of_the_tick(quantise):
    assert quantise(Decimal("0.1234"), tick=Decimal("0.005")) == Decimal("0.125")
    assert quantise(Decimal("0.1234"), tick=Decimal("0.0025")) == Decimal("0.1225")
    q = quantise(Decimal("0.1234"), tick=Decimal("0.005"))
    assert q % Decimal("0.005") == 0 and Decimal("0.005") <= q <= Decimal("0.995")

def test_a_transaction_hash_is_not_booked_as_settlement(book, trade):
    trade["status"], trade["transaction_hash"] = "MINED", "0xabc"
    assert not book.is_settled(trade)              # only CONFIRMED is terminal success
    trade["status"] = "CONFIRMED"
    assert book.is_settled(trade)

def test_market_info_is_refreshed_because_nothing_evicts_it(client, clock):
    client.tick_size(token)                        # populates a cache with no TTL
    clock.advance(hours=6)
    assert client.tick_size(token, max_age_seconds=300) and client.market_info_fetches == 2
```

## What is verified here, and what is not

Verified on 2026-08-25 by reading the pages listed in the provenance block and the pinned client at
`215fc63a8fd6ec3a10c7edb73997c9772d8686d3`: the package names, the two exchange addresses, the domain name and
both domain versions, the eleven signed members and the four removed ones, the salt and timestamp construction,
the six tick sizes, `price_valid`, the `ROUNDING_CONFIG` decimals, the fee curve as the client computes it, the
category rate table, the `fd`, `mbf`, `tbf` and `feeSchedule` member names and their documented meanings, the
pUSD description and the onramp address and function signature, the order and trade status vocabularies, the GTD
offset and the three-minute floor, and the POST response members.

Explicitly unverified. Do not build on any of these without checking first.

- Whether Gamma `feeSchedule.rate`, `.exponent` and `.takerOnly` carry the same values as CLOB `fd.r`, `.e` and
  `.to` for one market at one instant. Neither page relates the two surfaces.
- Whether `feeSchedule.rebateRate` has any CLOB counterpart. The pinned client reads none.
- How `mbf` and `tbf`, both in basis points, relate to the `fd` curve on the same response.
- Which exponent production markets carry. The docs formula is the `e == 1` form; the client's own tests use 2.
- The token in which a fee debit is denominated, given pUSD collateral and a fees page that says USDC.
- What token the pinned client's `collateral` constant `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` is, and why
  chain 137 and Amoy chain 80002 carry the same value. The pUSD page I read gives no token address.
- Whether the CLOB tests tick divisibility or only the decimal count.
- Whether the venue constrains or deduplicates a repeated `metadata` value.
- Every builder-fee statement above. It comes from the pinned client; the builder-fees documentation page
  returned 404 on 2026-08-25.
- Whether the V2 exchange keeps the V1 rule that two buys cross at `priceA + priceB >= 1` and mint the pair. The
  V1 contract source is not evidence about a different deployed address, and the V2 source was not read.
- Whether a flat account can sell an outcome it does not hold. The V1 exchange moved outcome tokens out of the
  maker's balance on a SELL, which makes a flat-account YES sell a NO buy reserving `1 - p` per share rather
  than `p`, a shortfall of `1 - 2p` per share while `p < 0.5`. The arithmetic holds wherever the semantics do;
  the semantics were not re-verified against the V2 exchange.
- The V1-era `"0.5"` default on the last-trade-price endpoint. Not re-checked for V2.
