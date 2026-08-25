# The order state machine

The full `(state, event)` transition table with a deny-by-default arm, and the in-flight categories that sit
underneath it. Terminal states are not absorbing: a cancel acknowledgement and a fill cross on the wire
constantly, so `(Canceled, Filled)` is legal and the fill is real money, while `(Canceled, Accepted)` is not
and must raise. What a terminal state accepts is exactly the events by which the venue corrects a fact you
already booked (a late fill, a fill void) and nothing else. This file also carries the fill-dedupe and
overfill mechanics the table depends on.

## Contents

- The transition table: every legal `(state, event)` pair, with the `_ => Err(InvalidStateTransition)` arm
- What stays absorbing, and why it is absent from the table rather than special-cased
- Late fills and fill voids: the fifteenth status (`Voided`), `(Filled, FillVoided)`, and the bust window
- The ghost-order resurrection: the exact buggy predicate, and the watermark that fixes it
- The committed intent row's seven fields, and the transaction block that must not enclose the send
- In-flight categories: `INTENT_RECORDED`, `SENT_UNCONFIRMED`, `INFLIGHT_UNKNOWN`, and the wall-clock budget
- `leaves_qty` is venue-authoritative and is not `order_qty − cum_qty`
- Fill dedupe: `trade_id` plus a field comparison, and why the dedupe set shares the position row's transaction
- Overfills: unclamped recording, `overfill_qty` as a field, the inequality invariant, and the risk gate
- Reconciliation-synthesised event ids, deferred fill reports, and orders you never sent
- Non-fills that look like fills: STP outcomes, restatements, priority updates, broken trades
- FIX: `ExecType` vs `OrdStatus`, precedence, `OrigClOrdID` chains, `CumQty` across a replace, Cancel Reject
- `PossDupFlag` (session layer) vs `PossResend` (application layer)
- The same lifecycle in five venues' vocabularies

## The transition table

Two variables, not one. **`OrdStatus`-shaped values are the state; execution reports are the events.** Model
them separately or you will end up assigning the venue's status string straight onto your object, which is
exactly hummingbot's `self.current_state = order_update.new_state` (`in_flight_order.py:342`): unconditional,
no legality table, no version guard, and a late REST `OPEN` overwrites a websocket `FILLED`.

The reference implementation is `nautilus_trader`, `crates/model/src/orders/mod.rs:214-296`, an exhaustive
match over `(state, event)` ending in a deny arm at `:295`:

```rust
pub fn transition(&mut self, event: &OrderEventAny) -> Result<Self, OrderError> {
    let new_state = match (self, event) {
        ...
        (Self::Accepted,  OrderEventAny::Filled(_))     => Self::Filled,
        (Self::Canceled,  OrderEventAny::Filled(_))     => Self::Filled,  // Real world possibility
        (Self::Canceled,  OrderEventAny::FillVoided(_)) => Self::Canceled,
        (Self::Canceled,  OrderEventAny::Updated(_))    => Self::Canceled,
        ...
        (Self::Filled,    OrderEventAny::FillVoided(_)) => Self::Voided,
        (Self::Expired,   OrderEventAny::FillVoided(_)) => Self::Expired,
        (Self::Voided,    OrderEventAny::FillVoided(_)) => Self::Voided,
        _ => return Err(OrderError::InvalidStateTransition),
    };
    Ok(new_state)
}
```

The table below is the enumeration to write. Rows marked ✔ are verbatim from that source; the rest are the
recommended enumeration for a venue client and you should trim them to the events your venue actually emits.
**A row you do not list is a row that raises; that is the whole mechanism.**

| State | Event | → | Notes |
|---|---|---|---|
| `Initialized` | `Submitted` | `SENT_UNCONFIRMED` | intent row already committed (client ID, payload, `sent_at`) |
| `Initialized` | `Denied` | `Denied` | local pre-trade block; never reached the venue |
| `SENT_UNCONFIRMED` | `Accepted` | `Accepted` | venue ack; venue order id now known |
| `SENT_UNCONFIRMED` | `Rejected` | `Rejected` | business rejection: a *definite* answer, not UNKNOWN |
| `SENT_UNCONFIRMED` | `Filled` | `Filled` / `PartiallyFilled` | IOC or marketable limit filled before any ack |
| `SENT_UNCONFIRMED` | `Timeout`/`5XX`/`429` | `INFLIGHT_UNKNOWN` | never `Rejected`, never a resubmit |
| `Accepted` | `Filled` | `Filled` ✔ | refine to `PartiallyFilled` from venue `leaves_qty`, not from arithmetic |
| `Accepted` | `PendingUpdate` / `PendingCancel` | same | request sent, not a fact |
| `Accepted` | `Updated` | `Accepted` | quantity/price amend acked |
| `Accepted` | `Expired` / `Canceled` / `Triggered` | as named | |
| `PartiallyFilled` | `Filled` | `Filled` / `PartiallyFilled` | decided by `leaves_qty == 0`, from the venue |
| `PendingCancel` | `Filled` | `Filled` | the fill was already matched when you asked |
| `PendingCancel` | `CancelRejected` | `Accepted` | `-2011`-shaped: usually means it filled; re-read state |
| `PendingUpdate` | `Filled` | `Filled` | same crossing as above |
| `PendingUpdate` | `ModifyRejected` | `Accepted` | the *old* order is still live at the old size |
| `Canceled` | `Filled` | `Filled` ✔ | annotated `// Real world possibility`: real money |
| `Canceled` | `FillVoided` | `Canceled` ✔ | |
| `Canceled` | `Updated` | `Canceled` ✔ | a stale amend ack, absorbed without changing state |
| `Filled` | `FillVoided` | `Voided` ✔ | `enums.rs:1452`, `Voided = 15`; transition at `mod.rs:290` |
| `Expired` | `FillVoided` | `Expired` ✔ | |
| `Voided` | `FillVoided` | `Voided` ✔ | idempotent under redelivery |
| **anything else** | | `Err(InvalidStateTransition)` | `mod.rs:295`: raise, alert, do not mutate |

## What stays absorbing

`(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)` and `(Rejected, *)` are **absent from the
table**, not special-cased. They hit the deny arm. That is the correct expression of "terminal": the state is
never re-opened by a *status* message, and it may be corrected by an *economic* one. Do not write "terminal
states are absorbing" as a rule; `verified-source-code.md` §4.1 falsifies it against `mod.rs:250`, and a
system built on it discards a real fill that crossed a cancel ack.

The two sentences to keep in your head while writing the default arm:

1. A cancel is a **request**. The venue may have matched the order microseconds before it processed your
   cancel, and the execution report for that match can be delivered after the cancel ack. Nasdaq OUCH is
   explicit that there is **no "too late to cancel" message**; you find out by getting the execution.
2. A fill is a **fact you booked**, and facts get corrected. Venues bust trades. ITCH: *"A trade break is
   final; once a trade is broken, it cannot be reinstated"*; OUCH: *"You will always get an Executed Order
   Message prior to getting a Broken Trade Message for a given execution"*, with reasons E/C/S/X (erroneous,
   consent, supervisory, external). **Position and PnL must be revisable after the fact; the book must not.**

## Late fills and fill voids

`Voided` exists because "the order finished" and "every fill that constituted it is still valid" are different
propositions. Model the correction as a *negative-quantity event against a specific `trade_id`*, never as an
edit of the original fill row: `avg_px` is recomputed by folding the surviving fills and subtracting the voided
quantity per trade id (`mod.rs:1355-1382`, `removed_fill_qty(fill, corrections.get(&fill.trade_id))`). An
in-place edit destroys the audit trail and is not idempotent under redelivery of the void.

## The ghost-order resurrection

The staleness guard below is the one nearly everyone writes, and it resurrects a dead order. The bug is two
defects that compose:

```python
# WRONG: both halves
existing = self.live.get(event.client_order_id)          # the live object, deleted on terminal
if existing is not None and event.update_time < existing.update_time:
    return                                               # guard skipped when existing is None
self.live[event.client_order_id] = build_order(event)    # re-inserts a phantom open order
```

A terminal event already popped `existing`, so `existing is None`, so the guard does not run; a replayed
pre-snapshot `PARTIALLY_FILLED` re-creates the order in `live`; `reconcile` then sees the client ID in `live`
and declines to re-place it. **The bot believes it is quoting, the book holds nothing, and the hedge leg is
naked and invisible.** Note also `<` rather than `<=`, and that this shape is routinely shipped under a comment
asserting the opposite conclusion ("overlap is harmless and gaps are not possible").

Both halves of the fix:

```sql
-- (1) the watermark is keyed on the ID, stored independently of the order, and the guard IS the write
UPDATE order_watermarks SET v = :event_version
 WHERE client_order_id = :cid AND v < :event_version;
-- proceed only if rowcount == 1, in the same transaction as the effect
```

```python
# (2) legality is a table lookup with an explicit default arm
try:
    next_state = TRANSITIONS[(order.state, event.type)]     # dict of the table above
except KeyError:
    raise InvalidStateTransition(order.state, event.type)   # NOT `return`, NOT `pass`
```

The in-process spelling of the same guard, `if last_seen[client_id] >= event.update_time: return`, is
acceptable only as a fast path in front of the guarded write and never instead of it: the dictionary is lost
on restart and shared by nothing, so it protects one process for as long as that process lives.
`if seen_version(cid) >= v: return` followed by a separate write is a TOCTOU that two concurrent redeliveries
both pass. `>=` is correct only where the version is a total order. Where the venue publishes no version,
derive one from its own sequence; where the only clock is coarse, the watermark is `(ts, applied_event_ids)`.
Give **balance events the same guard**, not only orders.

## The committed intent row, and the transaction that must not enclose the send

Before the socket write there is a row, and it is **committed**, not flushed. `flush()` inside an open
transaction is not persistence: a `rollback()` on the exact timeout the row exists for erases the identity and
the next attempt mints a fresh one, which buys twice. Mint the identity from the **intent instance**, from a
value that survives `ROLLBACK`, never from a bar timestamp, a `strategy+symbol+side` tuple or a wall-clock
second, each of which repeats on its own.

Seven fields belong on that row:

| Field | Why |
|---|---|
| client order ID | the correlation key you will query by |
| full economic intent: instrument, side, qty, price, TIF, reduce-only, `positionSide` | so the recovery path can prove the order it found is the order you meant |
| venue, account and API-key identity | uniqueness is per-account, so the identity is only meaningful with them |
| the venue's own sequence or nonce where one exists: OUCH `UserRefNum`, Hyperliquid nonce, FIX `MsgSeqNum` | the venue's replay guard, which is often stronger than the client ID |
| the exact signed payload bytes, on signature-authenticated venues | a replay with a different body is not a retry |
| `sent_at`, with a `not_before` / `not_after` bracket | every history endpoint is time-windowed, and the bracket is what makes the scan bounded |
| `state`, `INTENT_RECORDED` flipped to `SENT_UNCONFIRMED` after the write | the in-flight categories below read it |

**No transaction block may lexically enclose the send.** Grep for `session.begin()`, `engine.begin()` and
`@transaction.atomic` around the call site: if the external call sits inside one, the row it depends on can be
rolled back by the same failure the row exists to survive.

Query-first is the default, and the per-venue table is the exception list: the retention bounds that decide
how far back a query can reach live in the venue files. The malign outcome when this is skipped is a strategy
that hedges and exits one lot while carrying a naked residual it cannot see.

## In-flight categories

In-flight commands resolve into exactly three evidence categories, and there is no fourth called "assume it
didn't happen": definitive local failure → denied; definitive venue result → apply it; **unknown live outcome →
stays in flight, pending reconciliation**.

| Category | Meaning | Risk treatment | Exit |
|---|---|---|---|
| `INTENT_RECORDED` | row committed, socket not yet written | full notional | flips on the write returning |
| `SENT_UNCONFIRMED` | write returned, no venue ack | full notional | ack, rejection, or the ladder |
| `INFLIGHT_UNKNOWN` | ladder exhausted; venue answer unknown | **full notional**, instrument gate closed | successful query, or the budget fires |

`INFLIGHT_UNKNOWN` must be a **real state on the order row**, not a `None` and not an exception in a log.
Anything that sizes, hedges or flattens reads position including in-flight notional; an order in this state
that risk cannot see is exactly the naked residual the client-order-ID rule exists to prevent. The state
carries a **wall-clock budget declared as a config value**, and what the budget expires into is ordered by
whether the action reduces risk in every state still possible. Stopping the sends and cancelling by client ID
is always safe, because a cancel against an order that never existed is a no-op and a cancel against one that
rests removes exposure you did not intend. Querying that identity and reconciling against the venue's own
position number is safe once the venue answers. A hedge or a flatten is conditional: only where the venue has
confirmed a position, or exposure is bounded so the hedge cannot invert the sign. Flattening on the expiry of
the timer alone is not a risk-reducing action, because the instruction may never have filled, and flattening
against a position that does not exist opens the opposite one.

Timeouts resolve asymmetrically. Unconfirmed-submit past its retries can be settled as rejected only after a
**single targeted order query** returns a definite answer; `PendingCancel`/`PendingUpdate` past their retries
cannot be settled at all under open-orders-only polling, because a *missing* order does not distinguish
"cancelled" from "filled and aged out of the open-orders view". nautilus deliberately leaves those unresolved
rather than guessing.

## `leaves_qty` is venue-authoritative

Do not compute `leaves = order_qty − cum_qty` and treat non-zero as "still working". FIX 4.4 tag 151: *"If the
OrdStatus is 'Canceled', 'DoneForTheDay', 'Expired', 'Calculated', or 'Rejected' … then LeavesQty could be 0,
otherwise LeavesQty = OrderQty - CumQty."* Binance STP adds a second exception:
`origQty − executedQty − preventedQty` is the quantity available for further execution. Read the venue's field;
use the subtraction only as a cross-check that raises on disagreement.

Do not decide terminality by a float tolerance either. hummingbot's `is_done`
(`in_flight_order.py:177-183`) is `math.isclose(self.executed_amount_base, self.amount)`, which coerces both
`Decimal` operands to `float` at rel_tol `1e-9`: an order 0.9999999995 filled reads as done, and on a large
notional the residue is real money at the exact point the bot stops managing the order.

## Fill dedupe, and the fold

Dedupe on the venue's `trade_id`, **before** the state transition, and **reject** the duplicate rather than
ignoring it: nautilus runs three ordered checks (identity → duplicate → transition), with the duplicate check
at `mod.rs:845-852` returning `OrderError::DuplicateFill(fill.trade_id)`, and the same guard independently in
the reconciliation path at `reconciliation/orders.rs:776`.

Then add the field comparison, which is the half that is usually missing. Compare `trade_id`, `order_side`,
`last_px`, `last_qty`:

| Same `trade_id`, fields… | Meaning | Action |
|---|---|---|
| identical | benign redelivery: the stream and the poll both carried it | skip, silently, it is expected |
| **different** | the venue restated a fill, or you are keying on the wrong id | **reject as a data-integrity error** |

Applying the second case silently is how a restatement gets booked twice at two prices. The dedupe set is
written **in the same transaction as the position row**: an in-memory `_seen_trade_ids` re-applies every
counted fill after a restart, and that is precisely when the double-count happens.

Recompute `avg_px` as a fold over the persisted fill set on every update, never as an accumulator, in
`Decimal`. Two projects reached this independently: nautilus `avg_px_from_fills` (`mod.rs:1355-1382`) and
freqtrade `recalc_trade_from_orders` (`trade_model.py:1265`), which walks the orders from scratch on each
call, because recomputation is a pure function of a *set*, so it is correct under out-of-order delivery,
redelivery and voids, while a fold over a *sequence* is not. Ship
`test_avg_px_invariant_to_fill_arrival_order`: ascending and descending arrival produce byte-identical output.

Fills and statuses are different report types with different reconciliation rules: a status report may carry a
*cumulative* filled quantity (`z`, `Z`, `cumExecQty`) while a fill report is a *single* trade (`l`, `L`, `t`).
Assign from the cumulative field, which the venue also spells `executedQty` (Binance) and `cumQty` (FIX);
never `+= l`. A fill report that arrives before any order state exists must
be **deferred**, not dropped.

## Overfills

A venue can report a fill larger than the order's remaining quantity. Observed on Polymarket: `last_qty` of
`5.012345` against `quantity` `5.000000` with `filled_qty` `0.000000`. Clamping to `min(raw_qty, remaining)`
(the obvious fix) makes the position wrong, because the extra 0.012345 units really were received.

**The most rigorous OSS platform ships the dangerous default here.** `execution/src/engine/config.rs:61-65`
declares `allow_overfills: bool` with `#[serde(default)]` ⇒ **false**, and with false
`reconciliation/orders.rs:785-796` logs a `WARN` and `return None`: **the fill report the venue sent you is
discarded**, leaving your model short by exactly that amount. The live path `anyhow::bail!`s
(`engine/mod.rs:3600-3624`). The capability exists; the default withholds it.

What to write instead: record the venue's quantity **unclamped**, accumulate the excess into an `overfill_qty`
**field** (`mod.rs:1261-1268`), alert, and close the risk gate for that instrument. State the completeness
invariant as an **inequality** so an overfill does not trip it (`mod.rs:1341-1352`):

```
filled_qty + voided_qty + leaves_qty >= quantity
```

The gate blocks `submit_order` and any size-increasing amend **only**. `cancel_all(scope)`, `flatten(scope)`,
position, PnL and margin keep working while it is closed, and a test proves that they do. It reopens on a
successful reconciliation against the venue, never on a timer and never from the code path that closed it.

## Synthesised events, deferred reports, orders you never sent

Reconciliation will infer events the venue never sent as events: a missed fill discovered by diffing a
snapshot against persisted state. **Synthesised ids must be deterministic over venue-supplied fields including
a venue timestamp** (`reconciliation/ids.rs:103-107`), so the same inference after a restart dedupes against
itself instead of double-booking. Where the inference cannot be made safely, return nothing and warn; never
substitute a zero (`orders.rs:1058-1065`).

Orders you never sent will arrive. nautilus emits "external order" events for unrecognised client order IDs,
tags them strategy `EXTERNAL` / `VENUE`, and lets a strategy declare `external_order_claims` to adopt them.
Knight had the right structure and no automated control attached to it: the "33 Account" *"temporarily held …
positions resulting from executions that Knight received back from the markets that its systems could not
match to the unfilled quantity of a parent order"* (SEC order, ¶23). A bucket nobody reads is not a control.

## Non-fills that look like fills

| Event | Venue surface | Why it is not a fill |
|---|---|---|
| Self-trade prevention | Binance `ExecType` `TRADE_PREVENTION`, status `EXPIRED_IN_MATCH`; prevented matches at `GET /api/v3/preventedMatches` | *"not to be confused with a trade, as no orders will match"*; quantity is consumed and the order leaves the book, but nothing traded |
| OKX STP | `cancel_maker` (default) / `cancel_taker` / `cancel_both`, set at master-account level and applied across sub-accounts | a cancellation, booked as a cancellation |
| System restatement | FIX `ExecType` = Restated; OUCH `Order Restated` (type R) | order parameters changed with no client action and no execution |
| Priority change | OUCH `Order Priority Update` (type T): *"a new order reference number will be assigned"* | your venue-side handle changed; the order did not trade |
| Trade break | FIX Trade Cancel / Trade Correct with `ExecRefID(19)` → the **last corrected** `ExecID`; OUCH Broken Trade | a fill *void*, routed to `FillVoided`, not a new fill |

A state machine that reads "the order left the book and quantity was consumed" as a fill books phantom trades
and diverges from the venue by the prevented quantity, permanently.

## FIX: `ExecType` is the event, `OrdStatus` is the state

`OrdStatus(39)` is your state variable; `ExecType(150)` tells you which event this `ExecutionReport(35=8)` is.
Switch on `ExecType`, then assert the resulting state matches `OrdStatus`; a mismatch is a parser bug or a
message you dropped, and it should raise.

- **Precedence.** When an order is simultaneously several things, `OrdStatus` reports the highest-precedence
  value: PendingCancel highest, then PendingReplace, DoneForDay, Calculated, Filled, Stopped, Suspended,
  Canceled/Expired, PartiallyFilled, New/Rejected/PendingNew, AcceptedForBidding. So a `PartiallyFilled` order
  under a cancel request reports `PendingCancel`, and code that derives "am I still working?" from `OrdStatus`
  alone loses the partial. Derive it from `LeavesQty`.
- **`OrderQty = CumQty + LeavesQty`**, and `CumQty`/`AvgPx` *"should be calculated to reflect the cumulative
  result of all versions of an order"*, **cumulative across the entire replace chain**. Resetting `CumQty` on
  a replace mis-states your own position by everything filled before the amend. This is the single most common
  FIX-side position error.
- **`OrigClOrdID(41)`.** A cancel or cancel/replace carries the new `ClOrdID(11)` and the `OrigClOrdID(41)` of
  the order being acted on. Your state machine is keyed on the **chain**, not on one ID: keep a
  `chain_id → [ClOrdID…]` map so a report naming any link resolves to one order.
- **Order Cancel Reject (`35=9`)** echoes the request's `ClOrdID` and the `OrigClOrdID`; when
  `CxlRejReason` = Unknown Order, `OrigClOrdID` is set to the literal string `"NONE"`; a parser expecting an
  ID gets `"NONE"` and must not treat it as one. *"Filled orders cannot be changed."* A cancel reject is the
  normal outcome of racing a fill; it is a transition back to the pre-request state, not an error path.
- **Execution and state changes do not share a report.** FIX specifies that execution information *"should not
  be communicated in the same report as one which communicates other state changes"*, so a handler that only
  applies fills when the status also changed will miss fills.
- Pending states are a genuine model split, not a detail: FIX makes PendingCancel/PendingReplace the two
  highest-precedence statuses, while OUCH has no general pending-cancel at all (its `Cancel Pending`, type P,
  is emitted only for a cross order in the pre-cross late period, at most once per `UserRefNum`). A
  cross-venue OMS must pick one model and adapt the other; no source in the corpus reconciles them.

## `PossDupFlag` vs `PossResend`

These are two different layers and handling either with the other's mechanism fails in opposite directions:
dropped orders one way, duplicated fills the other.

| | `PossDupFlag(43)` | `PossResend(97)` |
|---|---|---|
| Sequence number | **same** `MsgSeqNum` | **different** `MsgSeqNum` |
| Spec text | *"if a message with this sequence number has been previously received, ignore message, if not, process normally"* | *"forward message to application and determine if previously received (i.e. verify order id and parameters)"* |
| Who decides | session layer, by sequence number | **application**, by order ID and parameters |
| Your code | dedupe in the FIX engine; never reaches business logic | reaches the state machine; resolve via the client-ID/trade-ID dedupe above |

Session dedupe cannot handle `PossResend`, and application dedupe must not be relied on for `PossDup`.

## The same lifecycle in five vocabularies

One order: placed, partially filled, cancel requested, the cancel races a final fill.

| Step | Binance spot | OKX | Bybit | Hyperliquid | FIX 4.4 |
|---|---|---|---|---|---|
| accepted | `NEW` | `state: live` | order id + `orderLinkId` returned | status `resting` (carries `oid`) | `ExecType=New`, `OrdStatus=New` |
| partial | exec type `TRADE`, cumulative `z`/`Z`, last `l`/`L`/`t` | `state: partially_filled`, `accFillSz` | `cumExecQty` | n/a | `ExecType=Trade`, `OrdStatus=PartiallyFilled`, `CumQty` |
| cancel sent | REST ack ≠ cancelled | **only** the orders channel showing `"state":"canceled"` confirms it | n/a | n/a | `OrdStatus=PendingCancel` (outranks PartiallyFilled) |
| late fill | another `TRADE` after the cancel ack | `partially_filled` → `filled` | another `cumExecQty` step | status `filled` (`totalSz`, `avgPx`) | `ExecType=Trade` after the cancel ack |
| terminal | fully-filled / cancelled statuses per `enums.md` for **your** product line | `filled`, `canceled`, `mmp_canceled` | `Cancelled`, `Rejected`, `Deactivated` among others | `filled` / `error` | `Filled`, `Canceled`, `Expired`, `Rejected` |

Two cross-venue warnings. OKX documents that *"Successful response only means the request has been accepted by
the exchange"*; the REST 200 on a cancel is not the cancel. And terminal-status **spelling differs between a
venue's own product lines** (Binance spot vs USDⓈ-M do not share every enum value); read the enum page for the
product you are trading rather than reusing a mapping that worked on the other one.
