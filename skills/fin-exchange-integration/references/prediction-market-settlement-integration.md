# Prediction markets: client-side settlement integration

> **Provenance**
> provider: cross-venue (Limitless, Polymarket, Kalshi) · surface: public WebSocket event reference, order-lifecycle and market-lifecycle documentation, portfolio REST endpoints · version: Limitless public API, Polymarket CLOB V2, Kalshi API v2
> verified_at: 2026-08-25
> sources: https://docs.limitless.exchange/developers/websocket-events · https://docs.limitless.exchange/developers/programmatic-api · https://docs.polymarket.com/concepts/order-lifecycle · https://docs.polymarket.com/trading/positions/manage · https://docs.kalshi.com/getting_started/market_lifecycle · https://docs.kalshi.com/getting_started/market_settlement · https://docs.kalshi.com/api-reference/market/get-market · https://docs.kalshi.com/api-reference/portfolio/get-settlements
> pinned: not applicable. No source code or client-library behaviour is cited in this file; every claim below is from vendor documentation at the URLs above, read on the verified_at date.
> verified: the Limitless `orderEvent` `source` discriminator and its SETTLEMENT types, the `isEstimate` flag on a provisional match, the cross-source ordering statement and the 60-second dedupe window; the Polymarket trade-status enum with its per-status descriptions and which statuses are terminal; the Polymarket order-status enum; the Polymarket redemption function and the sentence that there is no redemption deadline; the Kalshi market status set and the transitions quoted below; `settlement_timer_seconds`; `notional_value_dollars`; the full field list of the Kalshi settlements response and the absence of a per-settlement identifier in it.
> unverified: which states accept a cancel on any of these venues as a market closes; whether Limitless MINED can be followed by any later frame; whether a Kalshi settlement that is re-paid after an amendment produces a second settlements row, a changed row, or neither; whether any composite of the documented Kalshi settlement fields is unique; the Limitless EIP-712 domain parameters and its client-order-id convention; whether Polymarket exposes a market-level resolution status through the same surface as its trade statuses.
> revalidate_when: Kalshi adds or removes a market status or changes the settlements response fields, Polymarket changes its trade-status enum or documents a market-level resolution status, Limitless documents a terminal frame after MINED or changes the 60-second dedupe window, or any of these venues publishes a per-settlement identifier.

Trading and settlement are two different problems with two different authorities. The trading side answers when you ask it and
answers fast. The settlement side is a sequence of provisional facts that become terminal at an instant the venue names, and
the client's job is to hold each fact at the right confidence until then. The characteristic failure is not a crash. It is a
balance credited from a provisional message, never revisited, and never wrong enough to alarm anyone.

**Scope, and a hard boundary.** This file is the client side only: what a customer's process must do with the settlement
messages a venue sends it. It does **not** cover how a venue selects or operates an oracle, how an operator decides a result
or adjudicates a challenge, the regulatory treatment of any of that, or any procedure for compensating a party after a bad
result. Those are venue-side and policy questions. Integrate as though the answer arrives from outside, you cannot influence
it, and it may change once before it stops changing.

## Contents

- Trading stopped is not outcome final
- Five distinct states: matched, mined, settled, finalized, redeemed
- The same word is terminal on one venue and not on another
- Open orders as a market closes
- Open positions at settlement, and why the payout is not a boolean
- An amendment is a new fact, not a correction to an old one
- Durable dedupe for a payout credit the venue gave no identity to
- Redemption is an instruction that moves value
- Reconciling settlement against cash and against the chain
- Required assertions, as code
- What is verified here, and what is not

## Trading stopped is not outcome final

At least three separate instants exist, and collapsing any two of them is a bug that only shows up on markets that do not
resolve cleanly.

Kalshi's market lifecycle makes the separation explicit and is quoted here as one worked instance. Statuses are
`initialized`, `inactive`, `active`, `closed`, `determined`, `disputed`, `amended` and `finalized`. Both `active` and
`inactive` move to `closed` when `close_time` passes, and `closed` moves to `determined` when, quoted, "result is set". A
settlement timer then runs for `settlement_timer_seconds`, documented on the market payload as "The amount of time after
determination that the market settles", during which the market "remains at `determined` and the result may be disputed".
Only `finalized` is terminal, described as, quoted, "Settlement complete. Positions have been paid out. Terminal state."

Read that as three instants: **trading stops** at `closed`, **a result exists** at `determined`, and **the result stops being
revisable** at `finalized`, with money moving somewhere between the second and the third. A client that credits at
`determined` has credited a revisable number. That is allowed, and is often what a user expects to see, but only if the
credit is booked as provisional and the reversal path exists before the first credit is written.

Do not carry Kalshi's status names to another venue. What generalises is the shape: the instant trading stops, the instant a
result first exists, and the instant the venue says it can no longer change, are three instants and you must find all three
in each venue's own documentation.

## Five distinct states

| State | The question it answers | Who can still change it |
|---|---|---|
| `matched` | the venue paired your order against a counterparty | the venue, until the match is carried by something durable |
| `mined` | a transaction carrying that match is in a block | the chain, and the venue's own terminal signal |
| `settled` | the market's result exists and has been applied to your position | the venue, while its result is still revisable |
| `finalized` | the result can no longer change by any path the venue documents | nobody, by the venue's own statement |
| `redeemed` | the value is in a balance you control | nobody |

Each row is a different confidence level about a different quantity, and the transitions are not all monotonic in the same
direction. A match can fail after it is reported. A settled result can be amended. A finalized market can still hold value
you have not redeemed. Storing one status column that mixes these is how a client ends up unable to answer "is this money
mine yet" for a specific position.

**Worked example, Limitless, attributed and not universal.** The Limitless WebSocket documentation gives a concrete instance
of the provisional-then-terminal shape on the match side. An `orderEvent` carries a `source` discriminator. Under
`source: "SETTLEMENT"`, `type` is one of `MATCHED`, `MINED` or `FAILED`; `MATCHED` is provisional and pre-chain, carrying
`isEstimate: true` and no `txHash` yet, and the documented flow is to wait for the provisional `MATCHED` and then for a
terminal `MINED` or `FAILED`, correlating by `clientOrderId` or `tradeEventId`. Under `source: "OME"`, `type` is one of
`PLACEMENT`, `UPDATE`, `CANCELLATION` or `EXECUTION`, where a `CANCELLATION` may carry `reason: "STP_MAKER_CANCELLED"` and an
`EXECUTION` carries `status` in `FILLED`, `PARTIALLY_FILLED` or `KILLED`. A delayed match surfaces as
`execution.settlementStatus: "DELAYED"` with an `eligibleAt` timestamp.

Three properties of that surface are worth carrying into any venue's handler, because they are properties of the shape rather
than of Limitless:

- **The two sources are not ordered against each other.** Quoted: "OME and SETTLEMENT events for the same order can arrive in
  either order within a few seconds." A handler that assumes an execution precedes its settlement frame will process a
  settlement for a trade it has not booked.
- **A provisional frame is flagged.** `isEstimate: true` is the venue telling you the number is not yet a fact. Persist the
  flag with the row rather than dropping it, because it is the only thing that distinguishes a credit you may have to reverse.
- **Server-side dedupe is a window, not a guarantee.** Quoted: "Repeated emissions within a 60-second sliding window are
  dropped, so retries and replays will not double-deliver." Sixty seconds is a convenience. Your own dedupe must be durable
  and must survive a restart, because the venue also documents that "Subscriptions are not persisted server-side across
  disconnects", so a reconnect after a long gap is exactly the case the window does not cover.

## The same word is terminal on one venue and not on another

This is the reason to take terminality from the venue's own enum rather than from the English word.

| Venue and enum | Documented values | Terminal by the venue's own words |
|---|---|---|
| Polymarket, trade status | `MATCHED`, `MINED`, `CONFIRMED`, `RETRYING`, `FAILED` | `CONFIRMED` and `FAILED`. `MINED` is explicitly **not** terminal |
| Limitless, `orderEvent` with `source: "SETTLEMENT"` | `MATCHED`, `MINED`, `FAILED` | `MINED` and `FAILED`. `MATCHED` carries `isEstimate: true` |
| Kalshi, market status | `initialized`, `inactive`, `active`, `closed`, `determined`, `disputed`, `amended`, `finalized` | `finalized` |

Polymarket's per-status descriptions, quoted: `MATCHED` is "Trade matched, sent to executor for onchain submission",
`MINED` is "Transaction mined into the blockchain", `CONFIRMED` is "Trade achieved finality, successful", `RETRYING` is
"Transaction failed, being retried", `FAILED` is "Trade failed permanently". A trade that reached `MINED` on Polymarket has
not reached finality by the venue's own description; the same word on Limitless is where the venue stops sending. A shared
adapter that maps the string `MINED` to a single internal `SETTLED` state books one venue's trades a step too early.

**A second enum on the same venue is a second trap.** Polymarket documents order statuses `live`, `matched`, `delayed` and
`unmatched`, which is a different enum from the trade statuses above and uses `matched` in a different case and a different
meaning. An order at `matched` is not a trade at `CONFIRMED`. Key the internal state machine on `(surface, status)`, never on
the status string alone.

## Open orders as a market closes

Two things must be established per venue before a market closes on you, and neither is safe to infer.

**What happens to resting orders.** Kalshi documents a reactivation transition from `inactive` back to `active` which,
quoted, "cancels all resting orders", so a market can round-trip through a state that clears your book without any
instruction from you. Whether the transition to `closed` does the same is a separate question in the same document, and your
handler needs an answer for both. Treat a resting order that vanishes without a cancellation you sent as an event to book,
not an inconsistency to reconcile away.

**Which states accept a cancel.** A cancel is normally the always-safe action for an ambiguous order, and near settlement it
may stop being available at all. **UNVERIFIED:** this pass did not establish, for any of the three venues, the exact set of
market states in which a cancel is accepted. Until you have read it for your venue, treat a cancel rejection in a closing
market as an expected outcome with its own handling branch rather than as an error to retry, and do not let a failed cancel
block the settlement handler.

## Open positions at settlement, and why the payout is not a boolean

A `bool won` cannot store what these venues publish.

- **Limitless** documents two distinct shapes on one resolved market: `status: RESOLVED` with a `winningOutcomeIndex` where
  `0` is YES and `1` is NO for a winner-take-all result, **or** `winningOutcomeIndex: null` together with `payoutNumerators`
  for a split payout. The second shape is a vector, and it is a documented normal case rather than an error.
- **Kalshi** documents `market_result` values of `yes`, `no` and `scalar`, where `scalar` means "scalar market settled at a
  specific value", and a `value` field described as "Payout of a single yes contract in cents". The market payload separately
  carries `notional_value_dollars`, "The total value of a single contract at settlement in dollars". The settlement page
  notes that "actual payout (`CollateralAmountChange`) is rounded to whole cents" and that "fees may apply for sub-cent scalar
  settlement".
- **Polymarket** documents the redemption arithmetic on the position-management path as, quoted, "Each winning token returns
  1 pUSD, while losing tokens return 0".

Persist the venue's own representation: the vector where it publishes a vector, the per-contract value where it publishes a
value, and the outcome index where it publishes an index. Derive a display boolean from that if you want one. A schema whose
settlement column is a boolean cannot represent a split payout at all, and the migration to fix it happens after the split
payout has already been credited wrongly.

Note also that a settlement value is a per-contract number and the credit is a product of it with a quantity the venue also
publishes. Kalshi's settlement record carries `yes_count_fp` and `no_count_fp`, documented as the number of contracts "owned
at the time of settlement", which is not necessarily the position your process last observed. Reconcile the count the venue
settled against the count you held, and treat a difference as a break rather than as rounding.

## An amendment is a new fact, not a correction to an old one

Kalshi's status set includes `disputed` and `amended`, with `amended` described as re-determined after a dispute, reached
from `determined` by way of `disputed`. Nothing about why a dispute succeeds belongs in a client. What belongs in a client is
this: **a result you have already credited can be replaced by a different result before the market is terminal.**

The handling rule is the ledger rule, and it is not specific to prediction markets. **Reverse, then post the new fact. Never
edit the original.** The original posting is the record of what you believed and acted on at the time, and a system that
edits it cannot answer what a user's balance was yesterday, cannot explain a statement that has already been sent, and cannot
tell a genuine amendment apart from a replayed message. Post a reversal carrying the identity of the entry it reverses, then
post the new settlement as its own entry with its own identity, and let the net be the balance.

Two guards make this safe rather than merely correct in principle.

**Legality.** Enumerate the legal transitions of the venue's own status set and reject everything else with an explicit
error rather than a silent ignore. `finalized` accepts nothing. `determined` accepts a move to `disputed` and to the terminal
state. A status message that would re-open a terminal state is a bug in your mapping or a message you should never have
received, and either way it must raise.

**Version.** A settlement message is a notification whose arrival order you do not control. Keep a watermark keyed on the
market identifier, stored independently of the live position object, and make the guarded write itself the guard, in the same
transaction as the credit. Then re-read the result from the venue before moving value, rather than acting on the payload.

## Durable dedupe for a payout credit the venue gave no identity to

A settlement credit is a value-moving effect with no instruction of yours behind it, so the usual dedupe key, the identity you
minted for your own instruction, does not exist. You need one anyway, and on at least one venue you have to construct it.

**Verified, and it is the awkward part.** The Kalshi settlements response documents `ticker`, `exchange_index`,
`event_ticker`, `market_result`, `yes_count_fp`, `yes_total_cost_dollars`, `no_count_fp`, `no_total_cost_dollars`, `revenue`,
`settled_time`, `fee_cost` and `value`. There is no field in that list that identifies the settlement record itself. Any
dedupe key over this response is a composite you invented, and **UNVERIFIED:** whether any such composite is unique, and in
particular whether a market that is amended and re-paid produces a second row, a changed row, or no new row at all.

That uncertainty dictates the design, and the design is the same wherever a venue publishes no identity:

- **Key on the venue's own identifier where one exists.** Where none exists, construct a composite from the fields that
  define the economic event, and write down in the code why you believe it is stable.
- **Store the whole payload beside the key.** Then a second arrival with the same key and different content is detectable.
- **Raise on a key collision with differing content. Never drop it.** Dropping is the failure mode that turns an amendment
  into a silently ignored message; a naive `(ticker, settled_time)` key drops the re-payment. Raising turns the ambiguity
  into an alert on a path a person reads.
- **Write the dedupe record in the same transaction as the credit it protects.** An in-memory set re-credits every settlement
  it had already seen after a restart.
- **Reconcile the credit against the position that was settled,** using the counts the venue published, so a dedupe key that
  turns out not to be unique is caught by an independent check rather than trusted.

## Redemption is an instruction that moves value

On the venues where value sits in tokens, being settled and being paid are different states, and the step between them is an
instruction you send. It gets the same treatment as an order.

Polymarket documents `redeemPositions()` as the redemption call, alongside `splitPosition()` and `mergePositions()` on the
position-management path, and states, quoted, "There is no redemption deadline. Winning tokens remain redeemable at any time
after resolution." Limitless exposes `POST /portfolio/redeem`, and documents that a position is redeemable only after the
on-chain conditional token payout is reported. Kalshi's settlement page describes payout as automatic: "Positions are
automatically resolved and funds transferred", with no client instruction in the path.

Three obligations follow.

**Treat a redeem as an ambiguous external call.** Mint an identity from the redemption intent, commit the intent row before
the call, and resolve a timeout by querying rather than by calling again. A second redeem submitted against a state you
cannot observe is the same duplicate-effect risk as a second order.

**Carry unredeemed winnings on the balance sheet.** Between `finalized` and `redeemed` the value exists and is yours and is
not in your cash balance. It is an asset with a counterparty, and the absence of a redemption deadline on Polymarket means
that asset can sit there indefinitely with nothing prompting anyone to look at it. Report it as its own line, and age it.

**A failed redemption is not a lost payout.** Because the position remains redeemable, a `FAILED` outcome on the redeem path
means retry the instruction under a fresh intent after confirming through a query that the first one did not land, not write
off the value.

## Reconciling settlement against cash and against the chain

Settlement reconciliation is the control that catches the failures of every rule above, and it needs a path independent of
the code that wrote the credits.

| Quantity | Authority | Join key | What a break means |
|---|---|---|---|
| markets you should have been settled on | the venue's market status feed | market identifier | a settlement you never received, which is silent under-crediting |
| settled amount per market | the venue's settlement record | market identifier plus your constructed composite | your payout arithmetic or your position count disagrees with the venue's |
| positions at settlement | the venue's counts in the settlement record | market identifier and outcome identifier | you settled a different quantity than you held |
| cash received | the venue's balance or the chain | the venue's balance change, or the transaction identity | settled but not paid, or paid twice |
| unredeemed winnings | the token balance on the chain | outcome identifier | value you have credited and never collected |

The first row is the one usually missing, and it is the one that fails silently. Enumerate the markets you held a position in
that have reached a terminal status, and assert that each has a settlement row on your side. A watermark over settlement
records advances only past a range you verifiably covered: a page at the documented cap, a provider error, or a truncated
result is a hole, not the end of the list, and a branch that skips the work skips the advance.

The alert destination for these comparisons is a config key with no default, so an unset destination raises at import rather
than sending the break to nowhere.

## Required assertions

```python
# tests/test_prediction_market_settlement.py
from decimal import Decimal
import pytest

def test_matched_is_not_settled(handler):
    # a provisional match must not move a balance
    handler.on_event({"source": "SETTLEMENT", "type": "MATCHED", "isEstimate": True,
                      "clientOrderId": "i-1"})
    assert handler.ledger.balance_change("i-1") == Decimal(0)
    assert handler.state("i-1") == "PROVISIONAL"

def test_terminality_comes_from_the_venue_enum():
    # MINED is terminal on one venue and not on the other; the word decides nothing
    assert is_terminal("limitless", "SETTLEMENT", "MINED") is True
    assert is_terminal("polymarket", "TRADE", "MINED") is False
    assert is_terminal("polymarket", "TRADE", "CONFIRMED") is True

def test_sources_may_arrive_in_either_order(handler):
    # the venue documents no ordering between OME and SETTLEMENT for the same order
    a = replay(handler, [OME_EXECUTION, SETTLEMENT_MINED])
    b = replay(handler, [SETTLEMENT_MINED, OME_EXECUTION])
    assert a.final_state == b.final_state and a.balance == b.balance

def test_payout_is_stored_as_published(store):
    store.record_resolution({"status": "RESOLVED", "winningOutcomeIndex": None,
                             "payoutNumerators": [1, 1]})
    assert store.payout_vector == [1, 1]          # a bool cannot hold a split payout
    with pytest.raises(NotRepresentable):
        store.as_bool()

def test_amendment_reverses_rather_than_edits(ledger, venue):
    original = ledger.credit_settlement(market="X", result="yes", amount=Decimal("100"))
    venue.amend(market="X", result="no")
    ledger.apply_amendment(market="X")
    assert ledger.entry(original.id).amount == Decimal("100")    # untouched
    assert ledger.reversal_of(original.id) is not None
    assert ledger.balance("X") == Decimal("0")

def test_terminal_status_rejects_a_reopening_message(handler):
    handler.on_status("X", "finalized")
    with pytest.raises(IllegalTransition):
        handler.on_status("X", "determined")

def test_settlement_credit_is_deduped_durably(handler, db):
    handler.on_settlement(SETTLEMENT_ROW)
    db.simulate_restart()
    handler.on_settlement(SETTLEMENT_ROW)                        # exact replay
    assert db.credit_count("X") == 1
    with pytest.raises(DedupeKeyCollision):                      # same key, different content
        handler.on_settlement({**SETTLEMENT_ROW, "revenue": 999})

def test_unredeemed_winnings_are_carried_and_aged(report):
    assert report.line("unredeemed_winnings").amount > 0
    assert report.line("unredeemed_winnings").oldest_age_days is not None

def test_every_terminal_market_has_a_settlement_row(reconciler):
    breaks = reconciler.run()                                    # runs in production, on a schedule
    assert breaks == [] or reconciler.alert_destination is not None
```

## What is verified here, and what is not

Every quoted sentence and every enum above was read from the vendor pages in the provenance block on 2026-08-25. The
provenance block's `verified` and `unverified` lists are the authoritative statement. The items below are the ones most
likely to be mistaken for established facts.

- **Attributed, not universal.** The Limitless `MATCHED` then `MINED` or `FAILED` sequence with `isEstimate: true` is one
  venue's documented instance of the provisional-then-terminal shape. It is used here as a worked example. Polymarket's
  five-value trade-status enum is a second instance with a different terminal point. Neither is the model every venue follows.
- **Not established:** which market states accept a cancel, on any of the three venues.
- **Not established:** whether a Kalshi settlement that is re-paid after an amendment produces a second settlements row, a
  changed row, or no new row, and therefore whether any composite dedupe key over that response is stable. The absence of a
  per-settlement identifier in the documented field list is verified; the consequences for a re-payment are not.
- **Not established:** whether a Limitless `MINED` frame can be followed by any later settlement frame for the same trade.
- **Not established:** whether Polymarket publishes a market-level resolution status alongside its trade statuses, and if so
  on which surface.
- **Out of scope by design, and deliberately absent:** how any venue selects or runs an oracle, how an operator decides or
  re-decides a result, the regulatory treatment of a resolution, and any procedure for compensating a party after one. If a
  reader needs those, they are not client-integration questions.
