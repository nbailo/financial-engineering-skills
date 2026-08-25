# FIX: the two replay flags and the session layer

> **Provenance**
> provider: FIX Trading Community, plus Binance and Kraken for the venue rows · surface: the FIX 4.4 session layer and the order-entry application messages
> version: FIX 4.4; Binance spot API documentation at repository commit `976cc580553890e92031b77306147c0ed1de5a46`, dated 2026-08-14, cloned 2026-08-25
> verified_at: 2026-08-25
> sources: https://www.onixs.biz/fix-dictionary/4.4/compBlock_StandardHeader.html
> · https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html · https://www.onixs.biz/fix-dictionary/4.4/msgType_8_8.html
> · https://www.onixs.biz/fix-dictionary/4.4/msgType_9_9.html · https://www.onixs.biz/fix-dictionary/4.4/tagNum_11.html
> · https://www.onixs.biz/fix-dictionary/4.4/tagNum_19.html · https://www.onixs.biz/fix-dictionary/4.4/tagNum_41.html
> · https://github.com/binance/binance-spot-api-docs · https://docs.kraken.com/api/docs/guides/spot-clordid/
> · https://www.fixtrading.org/standards/
> pinned: the Binance quotes are from `fix-api.md` in the spot documentation repository at `976cc580`; the FIX 4.4 text is a dictionary edition served on 2026-08-25 and carries no revision number of its own.
> verified: read in the FIX 4.4 Standard Message Header on 2026-08-25, verbatim, both tag comments and both receiver-processing rules for `PossDupFlag(43)` and `PossResend(97)`, and the `OrigSendingTime(122)` comment; in the SequenceReset page, the disaster-only text, the lost-messages warning and the rule that a reset can only increase the sequence number; `ClOrdID(11)` on uniqueness within a trading day and on embedding a date; `OrigClOrdID(41)` on the previous order rather than the first of the day; in Order Cancel Reject, the unknown-order literal and "Filled orders cannot be changed"; in Execution Report, the `OrdStatus(39)` precedence ordering, the rule that execution information is not carried in the same report as a state change, the `OrderQty = CumQty + LeavesQty` identity, and `ExecRefID(19)` pointing at the last corrected report. Binance's "Resend requests are currently not supported." at `fix-api.md:597`, its monotonic sequence sentence at `:179` and the `MessageHandling(25035)` values at `:514` were read in the clone. Kraken's sentence extending the uniqueness check across open orders and the FIX session was read on the client-order-id guide.
> corrected: two claims failed this check. The literal `NONE` in Order Cancel Reject belongs to `OrderID(37)`, not to `OrigClOrdID(41)`, and the same message sets `OrdStatus` to Rejected in that case; and the quantity identity is not unconditional, since the dictionary names exceptions when `ExecType` or `OrdStatus` is Canceled, DoneForTheDay, Expired, Calculated or Rejected. Both are fixed in the prose below.
> unverified: the FIX 4.4 text was re-read in a third-party dictionary rather than in the FIX Trading Community's own specification, which is behind a login, and the b2bits edition the file originally cited was not re-opened, so a venue-specific dictionary that overrides these comments would not be visible here. Deribit's tags `9001` and `9003` and its graceful-logout behaviour, the CME iLink 3 and FIXP quotations, the business-layer sequence note, and FIX 4.4 Appendix D were not read in this pass; the Appendix D caveat already stated in the body still stands.
> revalidate_when: your counterparty moves above FIX 4.4 or publishes a dictionary that overrides the header comments; Binance's FIX API starts supporting Resend Request; Kraken changes the `cl_ord_id` uniqueness scope; a venue you trade implements or drops the application-layer `PossResend` check.

An abstraction layer that adds failures the venue does not have. FIX splits the ambiguous-outcome problem
into two orthogonal flags (`PossDupFlag(43)` is a session-layer assertion, `PossResend(97)` an
application-layer one) and handling either with the other's mechanism drops orders in one direction and
re-books fills in the other.

## Contents

- `PossDupFlag(43)` vs `PossResend(97)`: which layer owns each
- ResendRequest, GapFill, SequenceReset-Reset, and venues that disable resend entirely
- `ClOrdID(11)` / `OrigClOrdID(41)` chains, and `CxlRejReason` = Unknown Order
- `OrderQty = CumQty + LeavesQty`, chain-cumulative across replaces
- `ExecRefID(19)`: Trade Cancel and Trade Correct
- Cancel-on-disconnect, logon sequencing, and iLink 3 / FIXP UUID identity

## `PossDupFlag(43)` vs `PossResend(97)`

The two flags are orthogonal, and the split is exactly "retry" vs "duplicate". Dictionary text
(FIX 4.4 Standard Message Header, b2bits fixopaedia):

| Tag | Comment | Receiver processing rule |
|---|---|---|
| `PossDupFlag(43)` | "Always required for retransmitted messages, whether prompted by the sending system or as the result of a resend request." | "**if a message with this sequence number has been previously received, ignore message, if not, process normally**" |
| `PossResend(97)` | "Required when message may be duplicate of another message sent under a **different** sequence number." | "**forward message to application and determine if previously received (i.e. verify order id and parameters)**" |
| `OrigSendingTime(122)` | "Required for message resent as a result of a Resend Request (2). If data is not available set to same value as SendingTime (52)" | n/a |

- **`PossDup=Y` is a session-layer assertion at the *same* `MsgSeqNum`.** Your engine dedupes it by sequence
  number and it never reaches business logic. Passing it to the application re-books the fill.
- **`PossResend=Y` is an application-layer assertion at a *new* `MsgSeqNum`.** The spec pushes the decision
  up and names the discriminator: *verify order id and parameters*. This is FIX telling you, in normative
  text, that `ClOrdID` is the retry-vs-duplicate discriminator **and that a human has to write the
  comparison**. Nothing compels your counterparty to have written theirs.

Handling either with the other's mechanism is the classic pair of bugs: discarding a `PossResend=Y` order
because "we've seen this ClOrdID" drops a legitimately new order; passing a `PossDup=Y` execution report to
the fill handler double-books the position.

FIX has vocabulary for collision (`OrdRejReason(103) = 6` "Duplicate Order (e.g. dupe ClOrdID<11>)",
`CxlRejReason(102) = 6` "Duplicate ClOrdID<11> received"), but note what those are: **rejections**. FIX has no
"return the original order" semantic anywhere.

## ResendRequest, GapFill, and the venues that have neither

The session-layer replay path is `ResendRequest(35=2)`, answered with the original messages carrying
`PossDup=Y`, or with `SequenceReset(35=4)` in **Gap Fill** mode (`GapFillFlag=Y`) for administrative messages
that should not be replayed. `SequenceReset` in **Reset** mode is the disaster hatch, and the FIX 4.4
dictionary says why not to reach for it: it "should ONLY be used to recover from a disaster situation which
cannot be recovered via Gap Fill", "may result in the possibility of lost messages", "should NOT be used as a
normal response to a Resend Request", can only *increase* the sequence number, and its receipt at an
out-of-sequence `MsgSeqNum` must **not** trigger a further ResendRequest. An engine that answers every
ResendRequest with a Reset has silently converted a recoverable gap into permanent data loss, with no error
anywhere.

**And the mechanism is not universal.** Binance's SPOT FIX API states:

> "### Resend Request `<2>`: **Resend requests are currently not supported.**"
> Source: <https://github.com/binance/binance-spot-api-docs/blob/master/fix-api.md>

The same document requires strict monotonic sequencing (the client's `MsgSeqNum(34)` "must increase
monotonically, with each subsequent message having a sequence number that is exactly 1 greater than the
previous message") and offers `MessageHandling(25035)` = `UNORDERED(1)` / `SEQUENTIAL(2)`.

**Consequence, and this is the sentence to act on:** a FIX venue that disables ResendRequest removes the
session-layer replay mechanism, and a venue that has not implemented the `PossResend` application check
removes the application-layer one. With neither, FIX gives you **no** replay path and you are back to the
same posture as a REST venue: query-first by `ClOrdID`, never resend.

| Venue / protocol | `ClOrdID` uniqueness window | Safe recovery move |
|---|---|---|
| Kraken FIX | "across open orders **and FIX session**" (docs.kraken.com/api/docs/guides/spot-clordid/) | Resend the same `ClOrdID`: safe for the life of that session, and only that session |
| Binance SPOT FIX | open orders only; ResendRequest unsupported | Query by `ClOrdID`. Never resend. |
| Generic FIX counterparty | sender's obligation only (`ClOrdID(11)`: "Uniqueness must be guaranteed within a single trading day") | New `MsgSeqNum`, same `ClOrdID`, `PossResend=Y`: **only** with written confirmation the venue implements the application check; otherwise query-first |

`ClOrdID(11)`'s dictionary text is a constraint on the **sender**, not a promise from the venue: it says
firms "should ensure uniqueness across days, for example by embedding a date within the ClOrdID field". It
says nothing about what the venue retains or does on collision.

## `ClOrdID` chains, and the reject that says "NONE"

`OrigClOrdID(41)` is "ClOrdID of the **previous** order (**NOT the initial order of the day**)". So the
identity of a live FIX order is a **chain** of ClOrdIDs across every cancel and cancel-replace, and a client
that persists only the latest one cannot resolve a fill that was in flight across a replace. Persist the
chain, and reconcile against the whole chain.

`OrderCancelReject(35=9)` echoes the request's `ClOrdID(11)` and the `OrigClOrdID(41)` of the order being
cancelled or replaced. The field that goes strange on an unknown order is **`OrderID(37)`**, not
`OrigClOrdID`: the dictionary requires *"If `CxlRejReason(102)` = 'Unknown order', specify 'NONE'"*, and the
same message sets `OrdStatus(39)` to Rejected in that case. So a lookup keyed on the echoed `OrderID` will
search for an order called `NONE`, and a client that reads `OrdStatus` off this message learns the state of
the *reject*, not of an order it has. The same message notes "Filled orders cannot be changed."

## `OrderQty = CumQty + LeavesQty`

The identity holds on an `ExecutionReport(35=8)` reporting a live order, and **`CumQty` and `AvgPx` are
cumulative across the whole replace chain**: they reflect all versions of the order, not the current one. A
client that resets `CumQty` to zero on a replace mis-states its own position by exactly the pre-replace fills.

It is not unconditional, and asserting it as though it were turns ordinary terminal reports into false breaks.
The dictionary states the exceptions in the same sentence as the rule: *"There can be exceptions to this rule
when `ExecType` and/or `OrdStatus` are Canceled, DoneForTheDay (e.g. on a day order), Expired, Calculated, or
Rejected"*, where `LeavesQty` goes to zero without `CumQty` rising to meet `OrderQty`. Assert the identity on
every report whose `ExecType`/`OrdStatus` is outside that set, treat a violation there as a break rather than
a rounding issue, and book the terminal reports from `CumQty` alone.

`OrdStatus(39)` has a precedence ordering: `PendingCancel` is highest, then `PendingReplace`, `DoneForDay`,
`Calculated`, `Filled`, `Stopped`, `Suspended`, `Canceled`/`Expired`, `PartiallyFilled`,
`New`/`Rejected`/`PendingNew`, `AcceptedForBidding`. An order that is simultaneously partially filled and
pending cancel reports `PendingCancel`, so **`OrdStatus` alone does not tell you your filled quantity**;
`CumQty` does. FIX additionally specifies that execution information "should not be communicated in the same
report as one which communicates other state changes", so a fill and a state change arrive as two reports and
your handler must not assume one message carries both.

*(FIX 4.4 Appendix D's per-scenario order-state-change matrices were **not** read as raw text for this file;
treat scenario-level claims about them as unverified until you read
`FIX-Latest-as-of-EP284-Order-State-Changes.pdf` yourself.)*

## `ExecRefID(19)`: trade cancel and trade correct

`ExecRefID(19)` is required on `ExecType` = Trade Cancel and Trade Correct, and it points at **the last
corrected `ExecID`**, not the original. A correction chain therefore has to be walked, and applying a
correction against the original `ExecID` re-applies an amount that was already superseded. The rule this
implies: **position and PnL must be revisable after the fact** (the venue can cancel or correct a trade you
already booked) **while the book must not be**, because a break has no effect on current book state.

Same shape as the terminal-state rule: a terminal order state accepts exactly the events by which the venue
corrects a fact you already booked, and nothing else.

## Cancel-on-disconnect and session lifecycle

Arm the venue-native switch at logon, not in your process. Deribit's FIX interface uses tag `9001`
`CancelOnDisconnect` and tag `9003` `DontCancelOnDisconnect`; Deribit also documents that a **graceful**
logout does *not* cancel orders even with COD enabled, while an unexpected disconnect, inactivity timeout or
heartbeat failure does. That asymmetry means your clean-shutdown path must cancel explicitly; the switch
will not do it for you. Reconnect preserving sequence numbers so the gap-fill path can run; every
sequence-reset variant trades recoverability for liveness, and after any reset you have no replay window and
must reconcile by querying order state.

## iLink 3 / FIXP: identity is `(sequence number, UUID)`

CME's iLink 3 binary order entry moves the identity of a recoverable message to the **pair** (sequence
number, UUID). Sequence numbers reset to 1 per UUID and per week; the UUID must be monotonically increasing
(microseconds since epoch is the recommended construction) "to prevent usage of duplicate UUID's intraweek,
which can affect subsequent retransmission of those messages." The spec then warns against the reflex move:

> "**Do not Terminate the FIXP session and Re-Negotiate with a new UUID as a normal response to a Not Applied
> message.** Re-Negotiate with a new UUID should be used only to recover from a disaster situation…
> Re-Negotiating with a new UUID will mean recovering messages sent by the exchange in the previous FIXP
> session with the previous UUID" (CME iLink Binary Order Entry, Session Layer)

That is, "just reconnect fresh" permanently strands exactly the messages you were trying to recover. Handle
`NotApplied` in place. Relatedly (CME iLink 3 business-layer docs, read as summary only): when a Business
Message Reject omits the sequence number of the rejected message, the exchange did **not** increment its
inbound sequence for it and the client must not either.
