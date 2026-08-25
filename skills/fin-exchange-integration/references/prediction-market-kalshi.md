# Kalshi: direction vocabulary, book normalisation, fixed point, fee authority and settlement

> **Provenance**
> provider: Kalshi · surface: Predictions REST v2, WebSocket, FIX order entry · version: OpenAPI `info.version: 3.29.0`, AsyncAPI `info.version: 2.0.0`
> verified_at: 2026-08-25
> sources: https://docs.kalshi.com/openapi.yaml · https://docs.kalshi.com/asyncapi.yaml · https://docs.kalshi.com/getting_started/order_direction · https://docs.kalshi.com/getting_started/orderbook_responses · https://docs.kalshi.com/getting_started/fixed_point_migration · https://docs.kalshi.com/getting_started/fee_rounding · https://docs.kalshi.com/getting_started/market_lifecycle · https://docs.kalshi.com/getting_started/market_settlement · https://docs.kalshi.com/getting_started/exchange_sharding · https://docs.kalshi.com/getting_started/rate_limits · https://docs.kalshi.com/fix/order-entry · https://docs.kalshi.com/changelog
> pinned: `openapi.yaml` 3.29.0 and `asyncapi.yaml` 2.0.0 as served on 2026-08-25; the Fixed-Point Representation page carries "Last Updated: August 20, 2026"; the newest changelog entries read were labelled August 27, 2026. No client SDK source was read in this pass, so nothing below is a claim about SDK behaviour.
> verified: the direction vocabularies and their equivalence table; the REST orderbook shape and sort order; `use_yes_price` and its migration plan; `price_ranges` and `price_level_structure`; the fixed-point dollar and count types and their precisions; the three fee components and the per-order accumulator; the series/event/market fee-authority chain and the `FeeType` enum; the market status enum and its transitions; the settlement record fields; the FIX reject vocabulary; exchange sharding and per-shard collateral; the amend `count` semantics.
> unverified: whether resending an order with a `client_order_id` already used creates a second order on REST (no idempotency statement exists for that field in the spec fetched); the numeric fee rate tables, which live in a PDF that returned HTTP 429 on 2026-08-25 and was not re-read; whether a settlement row carries any stable unique id; the WebSocket `seq` gap-recovery procedure, which the AsyncAPI describes only as a number to check.
> revalidate_when: `use_yes_price` changes default or is removed; the legacy `action`/`side` fields pass their stated removal floor of May 14, 2026; a new `price_level_structure` name appears; `FeeType` gains a member; a new `exchange_index` shard is announced; the OpenAPI `info.version` moves off 3.29.0.

Kalshi is a binary event exchange whose API has moved twice under integrators in the last year: integer cents became
fixed-point dollar strings, and a two-field direction vocabulary replaced `action` plus `side`. Both migrations leave code
that still parses and still computes, and returns a different number. This file is the client side: reading the book,
choosing a direction, quantizing a price, predicting a fee, keeping a position and receiving a settlement.

## Contents

- Prices are not integer cents any more, and two endpoints are the exception
- Three direction vocabularies, and which one each surface speaks
- The book returns bids only, on both legs, ascending
- `price_ranges` is the tick contract; `price_level_structure` is a label
- Fee authority is a four-level lookup you cannot compute from a rate
- Fee rounding: three components per fill and an accumulator per order
- Order identity, the ambiguous response, and the cancel you cannot send
- Amend takes a total, not a delta
- Position, open interest and netting are three different numbers
- Lifecycle: closed, determined, disputed, amended, finalized
- Shards: collateral lives inside one matching engine
- Assertions to write
- What is verified here, and what is not

## Prices are not integer cents any more

The old `trading-api.readme.io` documentation host now 302-redirects to `docs.kalshi.com` (checked 2026-08-25). Anything
you remember from it about integer cents should be re-read rather than trusted.

The changelog entry announcing the cutover states that legacy integer count fields (those with `_fp` equivalents) and
integer cents price fields (those with `_dollars` equivalents) would be **removed** from all REST and WebSocket response
payloads on **March 12, 2026**. The Fixed-Point Representation page describes the two current types:

- `*_dollars` is a fixed-point dollar string. Requests accept 2 to 4 decimal places; responses emit up to 6.
- `*_fp` is a fixed-point contract count string. Requests accept 0 to 2 decimal places; responses always emit 2. The
  minimum granularity is 0.01 contracts, so contract counts are fractional.
- Where a request carries both an integer field and its `_fp` twin, "they must match".

The page is explicit that the old fields cannot carry the new prices: "Integer-cent fields cannot represent sub-cent
prices; on markets with sub-cent ticks, read prices from the `*_dollars` fields." Parse both types as `Decimal` from the
string. Passing a `_dollars` string through `float` and back is how a valid price becomes an off-grid rejection.

**Two documented exceptions still speak cents, and they are both on the money path.** `GET /portfolio/settlements`
returns `revenue` as an integer, described as "Total revenue earned from this settlement in cents (winning contracts pay
out 100 cents each)", and `value` as an integer, "Payout of a single yes contract in cents"; the cost basis fields beside
them (`yes_total_cost_dollars`, `no_total_cost_dollars`, `fee_cost`) are fixed-point dollar strings. `GET
/portfolio/balance` returns both `balance` (integer cents) and `balance_dollars`; the changelog says the integer field
"truncates any sub-cent amount, so use `balance_dollars` for exact values", and direct member balances are aligned to
`$0.0001`. A solvency check reading `balance` under-reports by up to a cent per read, permanently, in your favour when
you are buying.

## Three direction vocabularies

The Order direction page defines two canonical fields carrying "the same bit in two vocabularies":

- `outcome_side` in `{yes, no}` is the outcome the user is positioned for.
- `book_side` in `{bid, ask}` is that bit in book vocabulary. The page states the identity as an axiom:
  "**`bid ≡ yes`, `ask ≡ no`**, always."

The equivalence with the deprecated `action` plus `side` pair collapses four spellings onto two: `(buy, yes)` and
`(sell, no)` are both `outcome_side=yes`; `(buy, no)` and `(sell, yes)` are both `outcome_side=no`. On public `Trade`
objects and the `trade` channel the fields are `taker_outcome_side` and `taker_book_side`, because a public trade has no
user perspective. The legacy `action` and `side` fields on `Order` and `Fill` are marked deprecated and, per their own
schema descriptions, "will not be removed before May 14, 2026".

Direction does not move the price: "An order at price `p` with `outcome_side=no` is matched by an order at the same
price `p` with `outcome_side=yes`". A position keeper that treats `outcome_side=no` as a negative quantity at the same
price has inverted the economics.

**The V2 order endpoints speak a third thing.** `CreateOrderV2`, `AmendOrderV2` and the V2 cancel take `side` as a
`BookSide`, whose schema says: "For event markets, this refers to the YES leg only: `bid` means buy YES, `ask` means
sell YES. (Selling YES is economically equivalent to buying NO at `1 - price`, but this endpoint quotes everything from
the YES side.)" So on V2 order entry there is one price scale, the YES scale, and `ask` at `0.40` is an instruction to
sell YES at `0.40`, which is buying NO at `0.60`. A flat account sending that instruction reserves `1 - p` per contract,
not `p`. The reserve for selling a YES contract you do not hold is `(1 - p) - p = 1 - 2p` more than the naive `p`
figure, and that difference changes sign at `p = 0.5`, so an under-reserving keeper passes half its fixtures.

Read direction from `outcome_side` or `book_side` on responses, send it as `BookSide` on V2 requests, and never infer it
from a signed quantity.

## The book returns bids only, on both legs

`GET /markets/{ticker}/orderbook` returns `orderbook_fp` holding `yes_dollars` and `no_dollars`, each an array of
`[price_dollars, count_fp]` string pairs. The page states the rules a normaliser must encode:

- Both arrays are bids. There are no asks: "Kalshi's orderbook only returns bids, not asks."
- "Arrays are sorted by price in **ascending order**" and "The **highest** bid (best bid) is the **last** element".
- The complement is the ask: a YES bid at `$0.60` is a NO ask at `$0.40`, and best YES ask is `$1.00` minus the highest
  NO bid.

Take `max` over the levels rather than indexing an end, and derive each ask from the opposite leg. The one-book
no-arbitrage check that a generic CLOB adapter runs is meaningless here, because a crossed state on a single Kalshi leg
is not representable. The invariant that does mean something spans both legs: the best YES bid plus the best NO bid must
stay below one, since a pair summing to one or more is a matchable state.

**The WebSocket has a second price scale, behind a flag.** `orderbook_delta` and `orderbook_snapshot` carry
`yes_dollars_fp` and `no_dollars_fp` on the snapshot, and a delta carries `price_dollars`, `delta_fp` and
`side` in `{yes, no}`. The subscribe command accepts `use_yes_price`, documented in the AsyncAPI as:

```
Orderbook channel only. When true, no-side `orderbook_delta` and `orderbook_snapshot` updates
are reported in yes-leg pricing instead of no-leg pricing — so a single `price_dollars` scale
applies to both sides. Default false (no-side reported in no-leg pricing, the existing
long-standing behavior).

**Migration plan.** The default will be flipped to `true` in a future release, and the flag
will then be removed entirely in a subsequent release — at which point unified yes-leg
pricing becomes the only supported behavior and `use_yes_price: false` will no longer toggle
the legacy no-leg pricing.
```

Send the flag explicitly with the value your parser assumes. A client that relies on the default inverts every no-side
level on the day the default flips, silently, with a book that still looks well formed.

The snapshot and delta frames carry `sid` and `seq`, described only as a "Sequential number that should be checked if
you want to guarantee you received all the messages". The AsyncAPI I read does not state what to do on a gap, so the
recovery move has to be the one the channel does document: `update_subscription` with the `get_snapshot` action, which
"returns an `orderbook_snapshot` for the requested `market_tickers` without modifying the subscription". Treat a `seq`
gap as a discarded book, not a repairable one. A delta also carries your own `client_order_id` when you caused the
change, which makes the depth stream a weak echo of your own order activity but not a substitute for the fill channel.

## `price_ranges` is the tick contract

Two fields on `Market` describe the price grid, and only one of them is a contract:

- `price_ranges` is an array of `{start, end, step}` bands in fixed-point dollars. The docs call it "the source of truth
  for valid prices: any price on the grid is valid, and any off-grid price is rejected", and say to "Consume it
  dynamically per market and snap order and quote prices to the relevant band's `step`."
- `price_level_structure` is "a human-readable label for the grid. Do not key pricing logic off this name; new
  structures are introduced over time, and a client that reads `price_ranges` is automatically compatible with all of
  them."

Thirteen structures are named on that page, and they are not variations on one tick. `linear_cent` is uniform at
`$0.01`; `tapered_deci_cent` is `$0.001` below `$0.10`, `$0.01` in the middle and `$0.001` above `$0.90`;
`center_deci_edge_centi_cent` is `$0.0001` below `$0.01`, `$0.001` in the middle and `$0.0001` above `$0.99`. The
newer names follow `center_{center}_edge_{edge}_cent`, where `whole` is `$0.01`, `half` is `$0.005`, `quint` is
`$0.002`, `deci` is `$0.001` and `centi` is `$0.0001`. The stated reason for tapering is that near the bounds "small
absolute price differences represent large relative changes in implied probability". Whole-cent prices are valid in
every structure, which is why a cent-only client works right up until it wants to quote inside a taper.

Two consequences. First, the grid is per-market and non-uniform, so a single `tick_size` field in your model is already
wrong; the quantizer needs the band containing the price. Second, the grid **changes under a live market**: "When a
market's structure changes, the `price_level_structure_updated` event on the market lifecycle WebSocket channels carries
the new `price_ranges`." Subscribe to it, or discover the change as a run of rejections.

Requests and responses also disagree on precision. `FixedPointDollars` says requests accept 2 to 4 decimal places while
responses emit up to 6, so echoing `last_price_dollars` straight back as an order price can be both off-grid and
over-precision. Requantize against the live `price_ranges` after every round trip.

## Fee authority is a four-level lookup

There is no single fee rate to hard-code. The authority resolves in this order, and every level is readable from the API
except the last:

1. **Series.** `Series` carries `fee_type` and `fee_multiplier`. `FeeType` is an enum of `quadratic`,
   `quadratic_with_maker_fees`, `quadratic_with_combo_maker_fees` and `flat`, and `fee_multiplier` is "a floating point
   multiplier applied to the fee calculations".
2. **Event override.** `GET /events/fee_changes` documents that "Event fees are an override layered on top of the parent
   series' fee structure. If `fee_type_override` and `fee_multiplier_override` are null, that indicates the override is
   cleared." The `market_lifecycle_v2` channel emits `event_fee_update` when an event-level override is set or cleared.
3. **Market waiver.** `Market` carries `fee_waiver_expiration_time`, "Time when this market's fee waiver expires".
4. **Scheduled change.** `GET /series/fee_changes` and `GET /events/fee_changes` return rows carrying `scheduled_ts`,
   "Timestamp when this fee change is scheduled to take effect". A fee you resolved this morning can be a different fee
   this afternoon by design.

The numeric rates behind the enum are outside the API. The `fee_type` schema description says the structures "can be
found at https://kalshi.com/docs/kalshi-fee-schedule.pdf", maps `quadratic` to the General Trading Fees Table,
`quadratic_with_maker_fees` to that table plus the Maker Fees section, `quadratic_with_combo_maker_fees` to "the same
maker-fee structure with a 0.5 maker multiplier instead of 0.25", and `flat` to the Specific Trading Fees Table.
**That PDF returned HTTP 429 on 2026-08-25 and was not read in this pass, so no numeric rate appears in this file.**
The shape of the quadratic family is what the name says and what the settlement page implies, but treat any specific
percentage as unverified until you read the schedule yourself.

Two structural facts survive without the rates. A quadratic fee in the contract price is symmetric under the venue's own
identity, so `fee(p)` and `fee(1 - p)` are the same number and a router carrying a basis-points-of-notional model will
systematically prefer the cheap-notional leg of an economically identical trade. And a `flat` series exists, so a model
that assumes every Kalshi fee is quadratic is wrong for at least one `FeeType`.

Book the venue's own number rather than yours. Realised fees arrive as `fee_cost` on each `Fill`, as
`taker_fees_dollars` and `maker_fees_dollars` on `Order`, as `average_fee_paid` on the create and amend responses, and
as `fee_cost` on each settlement row. Use your model as a pre-trade estimate and a reconciliation tolerance only.

## Fee rounding: three components and an accumulator

The Fee Rounding page states that a user balance has "a target precision before and after every fill": `$0.0001` for
direct members and `$0.01` for everyone else. Sub-cent prices and fractional contracts produce balance changes finer
than that, so every fill carries three components:

- **Trade fee**, from the fee model, rounded **up** to the nearest `$0.0001`.
- **Rounding fee**, the adjustment that restores the target precision. The mechanics are: compute
  `balance_change = revenue - trade_fee`, floor it **toward negative infinity** to the target precision, and set
  `rounding_fee = balance_change - floor(balance_change)`.
- **Rebate**, "Refund from accumulated rounding overpayment (always a multiple of $0.01)".

Net fee is `trade fee + rounding fee - rebate`, and the page states it is "always >= $0.00". The accumulator is the part
that breaks a per-fill fee model: it "tracks cumulative rounding overpayment across all fills of an order", issues a
whole-cent rebate once accumulated rounding exceeds `$0.01`, and is "maintained per order across all fills regardless of
whether the fills are taker or maker. If an order initially takes (matching resting orders) and then becomes a resting
maker order, the accumulated rounding carries over to subsequent maker fills."

So the net fee on fill `k` is a function of fills `1..k-1` on the same order, not of `(price, quantity)`. The worked
example on that page makes it concrete: three identical 1-lot fills at `$0.055` cost `$0.0150`, `$0.0050` and `$0.0150`
in net fee. Any test that asserts equal fees for equal fills will fail correctly. Reconcile total fee per order, never
per fill, and let the venue's `fee_cost` be the record.

## Order identity, the ambiguous response, and the cancel you cannot send

`client_order_id` is a free string field on `CreateOrderV2Request`. **The OpenAPI document fetched on 2026-08-25
contains exactly one use of the word "idempotency", and it is on `client_transfer_id` for intra-account transfers, not
on `client_order_id`.** Nothing in that spec says resending a create with a repeated `client_order_id` returns the
original order rather than creating a second one, so on REST it is a correlation key and you must not treat it as a
deduplicator. The create endpoint does document a `409` response, "Conflict - resource already exists or cannot be
modified", but the spec does not tie that status to a repeated client id, so do not build the branch on an inference.

Resolution after a lost create response is constrained by what you can look up:

- `GET /portfolio/orders/{order_id}` takes the venue's `order_id` only, which you do not have.
- `GET /portfolio/orders` has no `client_order_id` query parameter in the spec fetched. Its filters are `ticker`,
  `event_ticker`, `min_ts`, `max_ts`, `status`, `limit`, `cursor`, `subaccount` and `exchange_index`.
- `DELETE /portfolio/events/orders/{order_id}` cancels by `order_id`. There is no documented REST cancel by client id.

The consequence is specific and worth stating plainly: on REST, the usually-safe move of "cancel the identity you sent"
is not available. Recovery is a bounded list scan of `GET /portfolio/orders` over the ticker and time window, matching
`client_order_id` in the returned rows, and only then a cancel by the `order_id` you found. Size the window from your
own send timestamp, and never resend.

**FIX is the surface where client identity does work.** The FIX order-entry page documents tag 11 `ClOrdID` as "Client
order ID for idempotency. UUID preferred, max 64 chars. Must not match any open order." The uniqueness scope is open
orders, so the reuse window closes when the order fills or cancels. Order Cancel Request (35=F) and Order Cancel/Replace
Request (35=G) both take tag 41 `OrigClOrdID`, "ClOrdID of the order to cancel" or "to modify", so cancel-by-client-id
exists on FIX and not on REST V2.

FIX also publishes an unusually honest classification of ambiguity in the Text (58) field, which is worth mirroring
into your own outcome enum:

- `EXCHANGE_UNAVAILABLE`: "the gateway could not confirm whether the order was applied (exchange unreachable, request
  timed out, or interrupted after the order may have been accepted). Reconcile the order's state, or retry with the same
  ClOrdID." This is UNKNOWN, and the venue names the two permitted responses.
- `INTERNAL_ERROR`: "a reject from a healthy exchange that could not be mapped to a specific reason. The order was not
  applied, so it is safe to fix and resubmit." This is the rare documented definite-not-applied.
- `ORDER_ALREADY_EXISTS` maps to OrdRejReason "Duplicate order": evidence the first instruction landed, so branch on the
  text rather than on a numeric code.
- `MARKET_ALREADY_CLOSED`, `MARKET_INACTIVE`, `EXCHANGE_PAUSED` and `TRADING_PAUSED` all map to "Exchange closed" and
  are venue state, not your error.
- `SELF_CROSS_ATTEMPT`, `TAKER_CANCEL_FOR_SELF_TRADE_PREVENTION` and `MAKER_CANCEL_FOR_SELF_TRADE_PREVENTION` map to an
  ExecutionType of "Canceled", so a self-trade-prevention cancel arrives as a cancel and not as a reject.

Self-trade prevention is a required field on V2 create: `self_trade_prevention_type` is one of `taker_at_cross`, which
"cancels the taker order when it would trade against another order from the same user; execution stops and any partial
fills already matched are executed", or `maker`, which "cancels the resting maker order and continues matching". These
are different economic outcomes for the same instruction. Choose deliberately and test the one you chose.

Fills are deduplicated on `fill_id`. Note that `trade_id` on the same object is documented as a "Unique identifier for
this fill (legacy field name, same as fill_id)", and `market_ticker` is likewise a legacy alias for `ticker`. Treating
`fill_id` and `trade_id` as two independent keys double-counts nothing but proves nothing either; treating `trade_id` as
a two-sided trade identity is simply wrong.

## Amend takes a total, not a delta

`AmendOrderV2Request.count` is "Updated total/max fillable count for the order. Set this to the order's already filled
count plus the desired resting remaining count after the amend." The page adds that this "matches the v1 amend
endpoints". Sending the remaining size you want, as most venues expect, silently shrinks the order by the filled
quantity. The FIX equivalent is the same shape and states the boundary: tag 38 `OrderQty` is the "New total quantity. If
equal to filled qty, order is canceled. If less, rejected."

The response is equally particular. `remaining_count` is "the actual post-amend resting quantity, not the request's
total/max fillable count", and both it and `fill_count` are "Only present when the amend caused a fill or changed the
resting size". An amend can therefore cross the book and fill: it returns `fill_count`, `average_fill_price` and
`average_fee_paid`, which means amend is a value-moving call and belongs inside the same identity and reserve discipline
as create. Queue position is a second cost: "Amending a resting order preserves queue position only when the amendment
decreases size."

## Position, open interest and netting

`MarketPosition.position_fp` is "String representation of the number of contracts bought in this market. Negative means
NO contracts and positive means YES contracts", so the account nets YES against NO on one market and one signed number
carries both legs. `Market.open_interest_fp` is "the number of contracts bought on this market disconsidering netting".
These are two different quantities that casual usage calls the same word, and only the first is yours.

`MarketPosition` also carries `total_traded_dollars`, `market_exposure_dollars` ("Cost of the aggregate market position
in dollars"), `realized_pnl_dollars` and `fees_paid_dollars`, alongside an `exchange_index`. `EventPosition` aggregates
per event with `total_cost_shares_fp` covering "both YES and NO contracts". Key your position store on
`(exchange_index, ticker, subaccount)`; `ticker` alone collapses rows the venue keeps apart.

`Market.notional_value_dollars` is "The total value of a single contract at settlement in dollars". Read it rather than
assuming `$1.00`, and read `Market.settlement_value_dollars` after determination, "The settlement value of the YES/LONG
side of the contract in dollars. Only filled after determination", which is how a scalar market pays something in
between.

## Lifecycle: closed, determined, disputed, amended, finalized

The market status enum is `initialized`, `active`, `inactive`, `closed`, `determined`, `disputed`, `amended`,
`finalized`. Trading stopped and outcome final are four states apart. From the lifecycle page:

- `closed` means "Past `close_time`. No new orders accepted. Awaiting determination."
- `determined` means "Result is known. Settlement timer is running", and the timer length is
  `settlement_timer_seconds`. "During this window the market remains at `determined` and the result may be disputed."
- `disputed` means "Result has been challenged. May be re-determined." `amended` means "Re-determined after a dispute.
  Settlement timer restarts."
- `finalized` means "Settlement complete. Positions have been paid out. Terminal state."

So a result you observed at `determined` is not final, and an `amended` market restarts the clock on a result you may
already have booked. Book determination as provisional, book `finalized` and the settlement row as the payout, and
express a correction as a reversing entry rather than an edit.

Two transition details that break state machines. Reactivation is destructive: "`inactive` → `active`: exchange
reactivates a paused market. Event: `activated`. **All resting orders are cancelled on this reactivation.**" And a
closed market can reopen: "`closed` → reopened `active`: `close_time` is moved into the future. Events:
`close_date_updated`, then `activated`." A terminal-looking close is therefore not terminal, while `initialized →
active` and `active → closed` are implicit and emit no WebSocket event at all, so a listener that waits for an event to
learn a market opened waits forever.

After close, "all order operations, including cancellations, are rejected with `MARKET_INACTIVE`. Resting orders are
cancelled shortly after close, and cancellation updates are published on the usual user channels." Your cancel is not
the thing that removes your exposure at close; the exchange's own sweep is, and it arrives asynchronously.

The settlement page states the payout rule and the fee position: yes holders receive `$1` per contract on a yes outcome,
no holders on a no outcome, "Only net positions are settled (after netting)", and "Settlement fees are zero for simple
yes/no determinations but may apply for sub-cent scalar settlement. The actual payout (`CollateralAmountChange`) is
rounded to whole cents. `CollateralAmountChange + MiscFeeAmt` equals the pre-rounding settlement value."

A settlement row carries `ticker`, `exchange_index`, `event_ticker`, `market_result` in `{yes, no, scalar}`,
`yes_count_fp`, `no_count_fp`, the two cost-basis fields, `revenue`, `settled_time`, `fee_cost` and a nullable `value`.
**No unique settlement id appears in that schema**, so a durable dedupe key for a payout credit has to be composed
locally, from `(exchange_index, ticker, settled_time)` at minimum, and that composition is your assumption rather than
the venue's guarantee. Say so in the code that writes it.

## Shards: collateral lives inside one matching engine

Kalshi is "splitting trading across multiple matching engines", identified by `exchange_index`, and the consequences are
economic rather than cosmetic. "Kalshi's collateralization checks will continue to run within the matching engine.
Programmatic traders must preallocate collateral on a given exchange shard before order placement." Balances are
per-shard, "Subaccount balances are local to a specific exchange instance", and "Order groups do not function across
exchange instances". Funds move between shards through `POST /portfolio/intra_account_transfer`, whose
`client_transfer_id` is the one field in the spec documented "for idempotency".

Routing is implicit unless you make it explicit: `exchange_index >= 0` routes directly, `-1` auto-routes by ticker, and
an omitted value auto-routes when a ticker is present or otherwise defaults to `0`. Market ticker formats are unchanged,
so "The `exchange_index` field is the authoritative source of truth" and you cannot infer the shard from the symbol.
Auto-routing has a cost the rate-limit page makes explicit: auto-routed single writes are "billed to every shard's Write
bucket", so a client that omits `exchange_index` spends its budget everywhere at once.

Carry `exchange_index` on every order, fill, position and settlement row you persist, and iterate shards when you
reconcile balance. `GET /portfolio/balance` returns a combined figure when `exchange_index` is omitted and a
`balance_breakdown` per instance; a solvency gate reading only the combined number will authorise an order the target
shard cannot collateralise.

## Assertions to write

```python
# tests/test_kalshi_client_invariants.py
from decimal import Decimal

def test_prices_are_parsed_as_decimal_strings(market):
    # responses emit up to 6 dp; requests accept 2-4; float round-trips fall off the grid
    assert isinstance(market.last_price_dollars, str)
    px = Decimal(market.last_price_dollars)
    assert on_grid(client.quantize_to_grid(px, market.price_ranges), market.price_ranges)

def test_best_prices_come_from_max_not_from_an_index(orderbook):
    # both arrays are bids, ascending, best is last; the ask is the complement of the other leg
    yes_bid = max(Decimal(p) for p, _ in orderbook["yes_dollars"])
    no_bid = max(Decimal(p) for p, _ in orderbook["no_dollars"])
    assert yes_bid + no_bid < Decimal("1")
    assert client.best_yes_ask(orderbook) == Decimal("1") - no_bid

def test_flat_sell_reserves_the_complement(keeper):
    # BookSide.ask on V2 is sell YES, which is buy NO at 1 - price
    keeper.place(ticker="T", side="ask", count=Decimal("100"), price=Decimal("0.40"))
    assert keeper.reserved_collateral == Decimal("60")

def test_amend_count_is_a_total_including_fills(client, resting_order):
    # request count = already filled + desired resting remainder
    client.amend(resting_order, desired_resting=Decimal("8"))
    assert client.last_request["count"] == str(resting_order.fill_count_fp + Decimal("8"))

def test_fee_is_reconciled_per_order_not_per_fill(order):
    # the rounding accumulator is per order across taker and maker fills
    assert sum(f.fee_cost for f in order.fills) == order.taker_fees_dollars + order.maker_fees_dollars

def test_determined_is_not_final(ledger, market_events):
    ledger.apply(market_events.determined(result="yes"))
    assert ledger.payout_posted is False          # settlement_timer_seconds still running
    ledger.apply(market_events.amended(result="no"))
    ledger.apply(market_events.settled())
    assert ledger.reversals == 1                  # corrected by reversal, not by edit

def test_position_keys_include_the_shard(store, positions):
    assert {(p.exchange_index, p.ticker) for p in positions} == set(store.keys())
```

## What is verified here, and what is not

Verified by direct fetch on 2026-08-25 from the URLs in the provenance block: every field name, enum member, quoted
sentence and schema description above. `openapi.yaml` self-reported `info.version: 3.29.0` and `asyncapi.yaml` reported
`info.version: 2.0.0`. The old `trading-api.readme.io/reference/getting-started` URL returned `302` to
`docs.kalshi.com/welcome`.

Explicitly unverified, and labelled as such wherever it appears above:

- **`client_order_id` deduplication on REST.** The spec fetched documents idempotency only for `client_transfer_id`.
  Whether a repeated `client_order_id` creates a second order, and whether the documented `409` is how a repeat
  surfaces, is not established. Do not resend to find out on a live account.
- **Numeric fee rates.** `https://kalshi.com/docs/kalshi-fee-schedule.pdf` returned HTTP 429 on 2026-08-25 and was not
  read. No percentage from it is quoted here. The `FeeType` enum, the multiplier chain and the override mechanism are
  verified; the rates are not.
- **A stable settlement identity.** The `Settlement` schema shows no unique id. Any dedupe key you use for a payout
  credit is composed by you.
- **WebSocket gap recovery.** The AsyncAPI describes `seq` as a number to check and documents `get_snapshot`, but states
  no recovery procedure. The re-snapshot approach above is the conservative reading, not a quoted rule.
- **Anything dated.** The deprecation floors quoted (May 14, 2026 for `action`/`side`, May 6 and May 21, 2026 for the
  legacy order endpoints) are the venue's own "no earlier than" language, not commitments to act on those dates.
