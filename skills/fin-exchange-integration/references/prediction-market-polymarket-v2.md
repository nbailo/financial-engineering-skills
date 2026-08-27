# Polymarket CLOB V2: signing, identity, fees, ticks and settlement as a client

> **Provenance**
> provider: Polymarket · surface: CLOB V2 REST, Gamma market data, EIP-712 order signing · version: V2
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/v2-migration ·
> https://docs.polymarket.com/trading/fees ·
> https://docs.polymarket.com/market-data/market-details ·
> https://docs.polymarket.com/api-reference/markets/get-clob-market-info ·
> https://docs.polymarket.com/api-reference/trade/get-single-order-by-id ·
> https://docs.polymarket.com/concepts/order-lifecycle ·
> https://docs.polymarket.com/trading/place-orders ·
> https://docs.polymarket.com/concepts/pusd · https://docs.polymarket.com/programs/builders/fees ·
> https://docs.polymarket.com/llms.txt
> pinned: `Polymarket/py-clob-client-v2@v1.1.0`, commit `215fc63a8fd6ec3a10c7edb73997c9772d8686d3`. On
> 2026-08-25 that commit was confirmed through the GitHub API to be both the target of tag `v1.1.0` and the
> current tip of `main`. Every code behaviour below was read from a clone checked out at that commit.
> verified: the two exchange addresses and their EIP-712 domain; the eleven signed struct members and their
> order; the salt and timestamp construction; the six accepted tick sizes, the price bound and the documented
> multiples rule; the fee curve as the pinned client computes it; the `fd`, `mbf`, `tbf` and `feeSchedule` field
> names and their documented meanings; the builder-fee rate model, its basis-point formula and its caps; the
> order and trade status vocabularies; the GTD offset rule; the POST response members. Read on 2026-08-26 at the
> same pinned commit and against the order-lookup page listed above, and verified: that the order lookup is
> `GET /data/order/{orderID}` with its path parameter documented as "Order ID (order hash)"; that
> `ExchangeOrderBuilderV2.build_order_hash` derives that hash locally as the EIP-712 digest over the typed data
> being signed, whose domain carries the `verifyingContract`.
> unverified: everything in the closing section, which lists each gap and why it is open. The largest is the
> relationship between the three fee-parameter surfaces. Two more sit on the recovery path: whether a repeated
> POST of the identical signed payload is idempotent, and what the order lookup returns for a hash the venue has
> never seen.
> revalidate_when: a V3 migration notice appears; the order lookup moves off `/data/order/` or stops keying on
> the order hash; the `fd` or `feeSchedule` members change; a new tick size is
> accepted; `py-clob-client-v2` publishes a tag past `v1.1.0`; the builder-fee caps move off 100 and 50 bps; or
> work starts on `Polymarket/py-sdk`, which the pinned repository's own README recommends over this client for
> new projects.

## Contents

- What V2 changed, and why a V1 mental model produces a wrong number rather than an error
- Packages, the two exchanges, and the domain that did not change
- Collateral is pUSD
- The signed struct, the order identity you can compute, and how a lost response is resolved
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

pUSD replaces USDC.e as the collateral token. The docs describe it, quoted: "pUSD (Polymarket USD) is the
collateral token used for all trading on Polymarket. It's a standard ERC-20 token on Polygon, backed by USDC."
Backing is enforced onchain, and the token carries 6 decimals. An API trader converts by calling
`wrap(address _asset, address _to, uint256 _amount)` on the CollateralOnramp at
`0x93070a847efEf7F70739046A929D47a521F5B8ee`, and reverses it with `unwrap` on the CollateralOfframp, whose
address the page does not give.

Two consequences for a client that already held a balance. A balance check that reads the USDC.e contract
reports a number that can no longer be traded with, and reports it as a healthy balance rather than as an error.
And a wrap is a value-moving external call in its own right: it needs the same durable intent row, the same
committed identity and the same query-on-timeout treatment as an order, not a bare retry loop.

## The signed struct, the identity you can compute, and the one V2 does not give you

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
`str(int(random.random() * (time.time_ns() // 1_000_000)))`, and the high-level `OrderBuilder` stamps the
timestamp from `time.time_ns() // 1_000_000` at build time. `OrderArgsV2` carries no timestamp member, so the
supported path gives you nothing to pin it with. Sign the same intent twice and at least two members differ, so
the digest differs, so you have signed two economically distinct orders. There is no
`clientOrderId` anywhere in the V2 payload: `order_to_json_v2` emits the eleven signed members plus the unsigned
`expiration`, plus `signature`, `owner`, `orderType`, `deferExec` and `postOnly`. No member of that payload is
supplied by you as an idempotency key, and the place-orders page states no idempotency guarantee at all. Treat
the absence as the fact it is: **the venue deduplicates nothing for you, so a second signature is a second
order.**

**`metadata` is the field you control.** It is a caller-supplied bytes32 inside the signed struct, so a hash of
your intent id fits there and comes back attached to the order. It is a correlation key, which is what you fall
back on to sweep up an order whose hash you failed to keep. Nothing in the docs says the venue rejects a
repeated `metadata`, so it is not a dedupe key, and treating it as one is exactly the V1 `nonce` mistake in new
clothing. Note also that `MarketOrderArgsV2` has no `metadata` member at the pinned commit, so a market order
built through the high-level builder carries `bytes32(0)` and no correlation tag.

### Recovering a lost response

The venue does have an order identity, and you can compute it yourself. `GET /data/order/{orderID}` documents
its path parameter as "Order ID (order hash)", and `cancel_orders` takes order hashes, so the hash is what the
venue knows your order by. `ExchangeOrderBuilderV2.build_order_hash` derives it as the EIP-712 digest,
`keccak(0x19 0x01 || domainSeparator || hashStruct(order))`, over the same typed data you sign. That is local
arithmetic over bytes you already hold, with no network call in it. The client never calls it on the posting
path, so computing it is your job. That this digest is the same string the venue returns as `orderID` follows
from the two facts above and is not stated as an equality on any page fetched, so assert it once against a live
order you did receive a response for, and keep the assertion.

**Persist two things durably before the POST, never after it.**

- The exact signed payload, byte for byte, as it will go on the wire.
- The order hash you computed locally from that signed struct.

Both belong in the same committed row as your intent id, flushed before the request leaves the process. The row
survives `ROLLBACK` and is readable by another process after a crash. A row written once the response comes
back is a row that does not exist in the one case it was written for.

**After a lost response, resolve the ambiguity by asking the venue about that stored hash. Never by signing
again.** A re-signature over a struct with any differing member, and the timestamp on its own is enough,
produces a different digest, so the venue treats it as a new instruction and you can hold two positions where
you intended one. Query the stored hash first, and let the answer decide. If the venue knows the hash, you are
done, and its state is the state. **If the venue does not know it, the operation is UNKNOWN and stays there.**
Do not resend the stored bytes to find out. No page says what a second POST of an identical payload does, so
any belief that a resend is harmless is a guess, and the case it is wrong in is the case that costs you a
second position. Hold the exposure as though the order filled, stop sending for that market, and escalate. The
unknown is resolved by a later query, by the venue, or by a human; it is never resolved by sending.

**The hash is meaningful only together with the contract it was signed for.** `verifyingContract` sits in the
EIP-712 domain, and the standard and negative-risk exchanges carry different addresses, so one struct signed
against each yields two different hashes. Store the exchange address beside the hash and resolve the pair. A
hash checked against the wrong exchange comes back unknown, and a client that reads unknown as "never placed"
resends and doubles the position by a second route.

`random.random()` draws from the process-global Mersenne Twister. A forked worker inherits that state, so two
children forked from one parent can generate the same salt sequence. Seed per process, or inject a salt
generator: `ExchangeOrderBuilderV2.__init__` takes `generate_salt` as a parameter. That makes the salt a
function of your intent id, and it does nothing for the timestamp, so a rebuild is still a different order. The
persisted hash stays the thing you query.

One retry path lives inside the client. `create_and_post_order` wraps the build and the post in
`_retry_on_version_update`, which calls the whole closure a second time if the resolved API version changed
across the first call. The second call builds a **new** order, with a new salt and a new timestamp, and posts
it. The trigger is an explicit `order_version_mismatch` error in the response body, so it is guarded, but the
version it compares against comes from `get_version()`, which swallows every exception and returns `2`. A
transport failure on `/version` is therefore indistinguishable from an answer. If you need certainty about how
many orders exist, build and hash the order yourself, persist both, post through `post_order`, and resolve every
ambiguity against the stored hash.

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

**That formula is the special case of the real one.** The pinned client computes, in
`fees.py::adjust_buy_amount_for_fees`, verbatim at the pinned commit:

```python
platform_fee_rate = fee_rate * (price * (1 - price)) ** fee_exponent
effective_platform_fee_rate = platform_fee_rate * (1 + fee_slippage / 100)
fee_base_amount = min(amount, user_usdc_balance)
platform_fee = (fee_base_amount / price) * effective_platform_fee_rate
builder_fee = fee_base_amount * builder_taker_fee_rate
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

**The platform fee is symmetric; the builder fee is not, and the venue says so itself.** `(p(1-p))**e` is
unchanged by swapping p and 1 - p for any exponent, so the platform fee on the two legs of the venue's own
YES/NO identity is the same number. The fees page states that property in its own words: "The fee amount in
USDC is symmetric around 50% probability — a trade at 30¢ incurs the same dollar fee as a trade at 70¢."

The builder fee has the opposite shape. The builder-fees page describes it as "a flat percentage of notional",
gives the formula `builder_fee = notional × builder_fee_rate_bps / 10000`, caps the taker rate at "100 bps (1%)"
and the maker rate at "50 bps (0.5%)" with 1 bp granularity and both defaulting to 0, and states that builder
and platform fees are "additive — they stack on top of platform fees, never replace them". Proportional to
notional means proportional to price: buying 100 shares at 0.99 pays a builder fee 99 times the fee for buying
100 at 0.01, while the platform fee at those two prices is identical. **So attaching a builder code re-breaks
the symmetry the platform curve was built to preserve,** and a router that compares total taker cost across the
two legs of one economic trade will drift toward the cheap-notional leg on every trade once one is attached.

The pinned client agrees: `builder_fee = fee_base_amount * builder_taker_fee_rate`, rates read from
`builder_maker_fee_rate_bps` and `builder_taker_fee_rate_bps` over `BUILDER_FEES_BPS = 10000`. One client
behaviour has no venue-side counterpart and is worth guarding: `__ensure_builder_fee_rate_cached` catches every
exception and logs a warning, leaving the rate at 0, which silently under-reserves the whole builder fee.

**On the source, because an earlier pass got this wrong.** `/programs/builder-fees` returns 404, which that pass
recorded as the builder-fee documentation being absent. It is not absent, the path is different: `llms.txt`
indexes `/programs/builders/fees`, which fetched on 2026-08-25 and is the source of every rate, cap and formula
above. A 404 is evidence about one URL, never about a fact.

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
endpoint documents none.

Note that `mbf` and `tbf` are *not* the builder rates: those are a separate per-builder value from
`/programs/builders/fees`, which the client divides by `BUILDER_FEES_BPS`. Four basis-point-or-rate spellings
now exist across three endpoints, and no page relates any pair of them.

**Resolve this at runtime, from the market you are about to trade.** The live venue metadata for that market is
the authority; this file is not, and neither is a constant. Read one surface, say in a comment which one and
why, and reconcile the estimate against the fee the venue actually charged on a fill. Do not build a fee model
that reads one surface and validates against another as though they were the same number, and do not let a
disagreement between two surfaces resolve silently in favour of whichever your code happened to read first.

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
gives 0.123, which has the right decimal count and is not a multiple of 0.005.

**The venue documents the stricter rule, and the client does not implement it.** The market-details page states
"Prices must be multiples of this value", and the place-orders page states "The CLOB rejects an order whose
price does not conform to the market's current tick size." Multiples, not decimal places. So on the two tick
sizes that are not powers of ten, `0.005` and `0.0025`, the client's decimal rounding is not the venue's
acceptance test and can hand you a price the venue will reject. Quantise to an integer multiple of the tick
yourself before signing, in `Decimal`, and assert it. The guard is two lines.

`orderMinSize` is documented as "Minimum order size in USDC. The CLOB rejects orders below this threshold."
Check it in the same place as the tick check, after rounding, because rounding size down can cross it.

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

def test_the_signed_bytes_and_the_hash_are_persisted_before_the_post(store, venue, client, intent):
    venue.post_times_out_after_transmitting()
    client.submit(intent)
    saved = store.load(intent.id)                  # flushed before the request left the process
    assert saved.signed_bytes == venue.last_request_body
    assert saved.order_hash == client.hash_locally(saved.signed_bytes, saved.exchange_address)

def test_a_lost_response_resolves_against_that_hash_and_never_by_signing_again(store, venue, client, intent):
    venue.post_times_out_after_transmitting()
    client.submit(intent)
    saved = store.load(intent.id)
    assert venue.lookups == [(saved.order_hash, saved.exchange_address)]
    assert client.signatures_produced == 1         # recovery signed nothing new
    assert venue.orders_created == 1               # so the position was not doubled

def test_the_hash_is_bound_to_the_exchange_it_was_signed_for(standard_builder, neg_risk_builder, order):
    # verifyingContract is a domain member, so the same struct hashes differently per exchange
    assert standard_builder.hash(order) != neg_risk_builder.hash(order)

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

The provenance block lists what was verified, and on which date. Explicitly unverified, and not to be built on
without checking first:

- Whether Gamma `feeSchedule.rate`, `.exponent` and `.takerOnly` carry the same values as CLOB `fd.r`, `.e` and
  `.to` for one market at one instant. Neither page relates the two surfaces.
- Whether `feeSchedule.rebateRate` has any CLOB counterpart. The pinned client reads none.
- How `mbf` and `tbf`, both in basis points, relate to the `fd` curve on the same response.
- Which exponent production markets carry. The docs formula is the `e == 1` form; the client's own tests use 2.
- The token in which a fee debit is denominated, given pUSD collateral and a fees page that says fees are
  calculated in USDC.
- What token the pinned client's `collateral` constant `0xC011a7E12a19f7B1f670d46F03B03f3342E82DFB` is, and why
  chain 137 and Amoy chain 80002 carry the same value. The pUSD page gives no token address.
- Whether the venue constrains or deduplicates a repeated `metadata` value.
- Whether a repeated POST of the identical signed payload is idempotent. The place-orders page states no
  idempotency guarantee, and no page fetched says what a second identical POST does. **Do not resend while that
  is unestablished.** Persist the exact signed bytes and the order hash you compute locally from them BEFORE the
  POST, then resolve by querying that hash. If the query cannot establish whether the order exists, the
  operation stays UNKNOWN: hold the exposure as though it filled, stop sending for that market, and escalate.
  An unverified resend under a struct the venue may treat as a second order is how one intent becomes two
  positions.
- What `GET /data/order/{orderID}` returns for a hash the venue has never seen. Whether that is a 404, an empty
  body or an error decides how your recovery path is allowed to read "never placed", so establish it against
  the venue before a recovery path depends on it.
- That the digest `build_order_hash` produces equals the `orderID` the POST response returns. The lookup page
  calls its parameter the order hash and the client computes an EIP-712 digest, but no page states the two are
  one value. Assert it on a live order rather than assuming it.
- Whether a flat account can sell an outcome it does not hold on the V2 exchange, and what it returns if it
  cannot. No page fetched documents a borrow, a short facility or a rejection. The reserve arithmetic for a
  flat sell, `q * (1 - p)` rather than `q * p`, holds wherever those semantics hold; that they hold on V2 was
  not established, so gate the send on inventory you can prove rather than on an expected venue error.

Three items that appeared in an earlier version of this list are gone rather than carried. Two were deleted for
want of a fetchable source: a rule attributed to V1 exchange contract source about two buys crossing at a price
sum of one, and a `"0.5"` default attributed to a V1 last-trade-price endpoint. The third, whether the CLOB
tests tick divisibility, is resolved above, because the market-details page states that prices must be
multiples of the tick.
