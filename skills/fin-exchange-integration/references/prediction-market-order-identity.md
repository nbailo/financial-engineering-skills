# Prediction markets: order identity, the four quantities, and the two authorities

> **Provenance**
> provider: cross-venue (Polymarket, Kalshi, Limitless) · surface: public vendor documentation, order-lifecycle, create-order and portfolio reference pages · version: Polymarket CLOB V2, Kalshi API v2, Limitless public API
> verified_at: 2026-08-25
> sources: https://docs.polymarket.com/v2-migration · https://docs.polymarket.com/concepts/order-lifecycle · https://docs.kalshi.com/api-reference/orders/create-order-v2 · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/api-reference/portfolio/get-positions · https://docs.limitless.exchange/api-reference/trading/create-order
> pinned: not applicable. No source code, contract address or client-library behaviour is cited in this file. Every claim below is from vendor documentation at the URLs above, fetched on the verified_at date.
> verified: the Polymarket V2 migration record that `taker`, `expiration`, `nonce` and `feeRateBps` left the signed order, that a millisecond `timestamp` replaces nonce for uniqueness, and that fees are determined at match time; the Polymarket order-status enum `live`, `matched`, `delayed`, `unmatched`; the absence of any documented `client_order_id` semantics on the Kalshi create-order page and that page's required-field list; the Limitless `clientOrderId` 128-character dedupe key, its `409 Conflict` on reuse and the statement that the API does not replay the earlier response; Kalshi `open_interest_fp` on `Market` and its absence from the positions response, whose field list is quoted; Kalshi `realized_pnl_dollars` and `fees_paid_dollars`.
> unverified: the Limitless client-order-id uniqueness scope, so whether it is permanent or scoped to open orders.
> revalidate_when: Polymarket adds or removes an order status, Kalshi documents idempotency for `client_order_id`, Limitless changes the 409-on-reuse behaviour or the character limit, or Kalshi moves `open_interest_fp` onto the positions response.

The general rules of operation identity and ambiguous outcomes hold here. What differs is where the uniqueness token lives,
that one venue puts it inside the bytes you sign, and that the quantity everybody calls "position" is four different numbers
answered by two different authorities on two different cadences.

**Scope.** Order identity, the recovery of an ambiguous submission, the four quantities and the reconciliation that compares
them. Outcome identifiers, the price grid and fee model, and short exposure each have their own reference. Settlement
lifecycle handling belongs in the settlement-integration reference. Venue-specific endpoints and error codes belong in that
venue's own reference.

## Contents

- Order identity and ambiguous submission on a venue whose uniqueness token you sign
- Fills, open interest, position and PnL are four quantities, not one
- Trading state and settlement state have different authorities
- Reconciliation: one authority and one join key per quantity
- Required assertions, as code
- What is verified here, and what is not

## Order identity and ambiguous submission

The general rule holds here: mint one identity from the intent instance, commit it durably before the send, and resolve an
ambiguous outcome by asking the venue about the identity you sent rather than sending again. What differs is where the
uniqueness token lives, and on one venue it lives inside the bytes you sign.

**Polymarket V2 moved uniqueness into the signed struct.** The migration page records that `taker`, `expiration`, `nonce` and
`feeRateBps` were removed from the signed order, and that `timestamp` in milliseconds was added and "replaces nonce for
uniqueness", alongside `metadata` and `builder` as bytes32 fields. Fees are, quoted, "determined by the protocol at match
time, not embedded in your signed order." The consequence for retry handling is direct, and it is an inference from those
quoted facts rather than a documented sentence: **re-signing a timed-out submission produces a new timestamp, therefore a
different signed order, therefore a second order.** The retry path and the duplicate-order path are the same path. Sign once,
persist the signed payload with its timestamp, and resend those exact bytes or query, never re-sign.

**Kalshi's client order id has no documented semantics.** The create-order page lists `client_order_id` as optional and
carries no description of it, no statement about idempotency, and no statement about what happens when the same value is sent
twice (page read 2026-08-25). Required fields are `ticker`, `side`, `count`, `price`, `time_in_force` and
`self_trade_prevention_type`, and the response carries `order_id`. Treat `client_order_id` as a correlation key only. It
deduplicates nothing until the venue documents that it does.

**Limitless does document one, and it is the strongest of the three.** Its create-order page describes `clientOrderId` as
an "Optional uniqueness and deduplication key. The submitted value may contain at most 128 characters; it is then trimmed
and must not be blank", and states the reuse behaviour: "Reuse returns `409 Conflict`; the API does not replay the earlier
response." Both halves are load-bearing. The venue really does deduplicate on the value you mint, so send one on every
order; and because it will not replay the first response, a 409 is evidence that your first attempt landed rather than a
failure to retry past. Its batch status endpoint `POST /orders/status/batch` is the query rung that turns that evidence
into the order's actual state. **UNVERIFIED:** whether the uniqueness constraint is permanent or scoped to open orders, so
do not design a scheme that reuses a value on the assumption it expires.

**An order can rest in a state that is neither working nor rejected.** Polymarket documents order statuses `live`, `matched`,
`delayed` and `unmatched`, a separate enum from its trade statuses. A `delayed` order exists and can still fill, so absence
from a list filtered on `live` is not evidence that no order was created. Enumerate the venue's full status set before
writing the query that resolves an ambiguous submit.

## Fills, open interest, position and PnL are four quantities

They are routinely all called "position" in conversation and they are not the same number.

- **Fills** are the venue's execution records. They are the input to everything below and the only one with a natural
  identity to dedupe on.
- **Position** is your net exposure. Kalshi nets it into one signed `position_fp`. A venue whose outcomes are separate
  tokens can leave you holding both legs at once, in which case a single signed number cannot represent what you hold.
- **Open interest** counts outstanding contracts without netting, so it is a different number from position by construction,
  and on Kalshi the two live on different objects. `open_interest_fp` is documented on **Market**, as "String representation
  of the number of contracts bought on this market disconsidering netting". The **positions** response does not carry it: as
  fetched on 2026-08-25 it lists `ticker`, `exchange_index`, `total_traded_dollars`, `position_fp`,
  `market_exposure_dollars`, `realized_pnl_dollars`, `fees_paid_dollars` and `last_updated_ts`. Reading a market-wide
  quantity off a positions row, or expecting a positions row to carry one, gets you a number about everybody's contracts
  where you wanted a number about yours.
- **PnL** is a function of the economic order of fills, not their arrival order, so the same fills folded in a different
  order are a different realised number. Read the venue's own totals where it publishes them. Kalshi documents
  `realized_pnl_dollars` as "Locked in profit and loss, in dollars" and `fees_paid_dollars` as "Fees paid on fill orders, in
  dollars". Limitless exposes `GET /portfolio/positions` and `GET /portfolio/history`, and a separate
  `unrealizedPnlProjectionChanged` WebSocket event whose own name says it is a projection. A projection is not a booked
  number and must not be posted as one.

Settlement and redemption are PnL components you never instructed, exactly as funding and liquidation are on a perpetuals
venue. They arrive without an order of yours behind them, so a feed filtered to "activity whose identity I generated"
excludes them.

## Trading state and settlement state have different authorities

This is the split that a single venue client tends to collapse, and it is the reason the settlement lifecycle is a separate
concern from order handling.

**Trading state** is orders, fills, the book and your working exposure. The authority is the venue's matching system, it
answers when asked, and it answers quickly.

**Settlement state** is what an outcome pays and whether you have received it. The authority may be somewhere else entirely.
On Polymarket and Limitless the value transfer is on a chain, and the venue's message about it is a notification about a fact
the chain holds. On Kalshi the venue's own ledger is the authority and the market status carries the answer.

Two authorities means two reconciliations, on different cadences, against different join keys, with different definitions of
"final". A client that reads its settled balance from the same message that told it a trade matched has one authority where
there are two.

## Reconciliation: one authority and one join key per quantity

Name both for every economic quantity you report, and ship the comparison as a scheduled entrypoint that reads through a path
independent of the writer.

| Quantity | Authority | Join key | Caveat |
|---|---|---|---|
| fills | the venue's fills endpoint | the venue's own trade or fill identity | cumulative totals where published beat summed deltas |
| position | the venue's positions endpoint | outcome identifier, per every dimension the venue separates | a netting venue and a token venue disagree on what one row means |
| fees | the venue's fee field on the fill | fill identity | a per-order accumulator makes the model an estimate only |
| realised PnL | the venue's own total where published | position key | your fold must use the venue's economic order |
| settled value | the venue's settlement record, or the chain | see the settlement-integration reference | not the same authority as the trading side |

Express tolerance in the market's own tick or contract unit rather than a percentage, choose a cadence longer than the
venue's documented replication lag, and make a break close the risk gate rather than write a log line.


## Required assertions

```python
# tests/test_prediction_market_order_identity.py
from decimal import Decimal
import pytest

def test_identity_survives_an_ambiguous_submit(client, venue):
    # resolve by asking about the identity you sent; never re-sign, never resend
    with venue.timeout_after_transmission():
        client.submit(intent_id="i-1")
    assert client.resolve(intent_id="i-1").order_count == 1
    assert client.signed_payload("i-1") == client.signed_payload("i-1")   # bytes are stable

def test_a_duplicate_identity_rejection_resolves_to_query_not_failure(client, venue):
    # the venue saying the identity is taken is evidence the first attempt landed
    venue.reject_next(status=409, message="clientOrderId already exists or is being processed")
    outcome = client.submit(intent_id="i-2")
    assert outcome.state == "UNRESOLVED"                    # never TERMINAL_FAILURE
    assert client.next_call_is_a_status_query(intent_id="i-2")

def test_absence_from_the_live_list_is_not_proof_of_non_creation(client, venue):
    # a delayed order exists and can still fill, so enumerate the full status set
    venue.place_in_status("delayed", intent_id="i-3")
    assert client.resolve(intent_id="i-3").state != "NEVER_CREATED"

def test_the_four_quantities_are_read_from_their_own_authorities(account):
    assert account.open_interest_source is not account.positions_source   # different objects
    assert account.realised_pnl == account.venue_realized_pnl_dollars     # never a local fold
    assert account.projection_is_never_posted_as_booked is True           # a projection is not a number

def test_settlement_and_redemption_reach_pnl_without_an_order(account, feed):
    # a feed filtered to "activity whose identity I generated" excludes exactly these
    feed.emit_settlement(no_client_identity=True)
    assert account.pnl_components_include("settlement")
```

## What is verified here, and what is not

The provenance block is the authoritative list. These are the items most likely to be mistaken for established facts.

- **Not established:** whether the Limitless uniqueness constraint on `clientOrderId` is permanent or scoped to open orders.
  Do not design a scheme that reuses a value on the assumption that it expires.
- **Resolved this pass, previously recorded as a disagreement:** Kalshi's positions response does not carry
  `open_interest_fp`; that field is on `Market`. The field lists above are from the pages fetched on 2026-08-25.
- **An inference, labelled as one:** that re-signing a timed-out Polymarket V2 submission produces a second order. It
  follows from the quoted facts that `timestamp` replaces nonce for uniqueness and that the timestamp is in milliseconds; no
  vendor sentence states the consequence.
- **Rates, addresses, tick sets and status enums change without notice.** Re-verify anything you hard-code, and prefer
  reading the value from the market payload over hard-coding it at all.
