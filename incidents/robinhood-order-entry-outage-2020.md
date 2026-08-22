# Robinhood — an untested counterparty protocol change leaving 166,000 orders in an uncancellable "pending" state (2020-03-09)

**Domain:** Retail brokerage, venue adapters, order lifecycle | **Loss:** ~166,000 customer orders stuck "pending"; $5,213,557.98 of outage restitution, within the FINRA action's $57,000,000 fine plus $12,598,445.16 total restitution | **Failure class:** Indeterminate outcome (with a change-management failure) | **Skill:** fin-exchange-integration

## What happened

On 9 March 2020 — a day on which the S&P 500 fell about 7% and the market-wide circuit breaker
tripped — Robinhood's order-entry system stopped processing messages and remained inoperable for 45
minutes. Customers could not submit new orders, **existing orders could not be cancelled**, and it
was not clear to customers whether the orders they already had were executing. Approximately
166,000 orders sat in a "pending" state.

This is the second of two March 2020 outages. The 2–3 March outage was different in kind: "a key
firm system was overloaded, which caused a cascading failure of other systems", with capacity
planning that had not accounted for growth or extreme conditions, and a business continuity plan
"unreasonably limited to events that impacted the firm's physical location" and therefore never
invoked. **It was not a leap-year bug** — that explanation circulated widely and is supported by no
primary source.

## Root cause, in code terms

FINRA's AWC states the chain precisely:

> "This outage was caused by **a third-party execution venue's change to the messaging protocol**
> used to communicate with Robinhood. **Robinhood's parent company did not test the change to the
> messaging protocol before implementation.** Once the protocol went live, **Robinhood's order entry
> system was unable to process incoming messages due to an internal coding error.** This caused the
> firm's order entry system to shut down and remain inoperable for **45 minutes**. During the
> outage, Robinhood customers could not submit new orders, **existing orders could not be
> canceled**, new orders could not be routed, and **it was unclear to Robinhood's customers whether
> existing orders were being executed.** Furthermore, **approximately 166,000 customer orders
> temporarily were stuck in a 'pending' state.**"

Three separable defects:

**1. A counterparty's wire protocol changed and nothing verified conformance before go-live.** The
venue adapter is a contract with an external party, and the contract changed without a test
exercising the new message shapes against the client. An adapter that has no conformance suite is an
adapter whose correctness is asserted by the counterparty's release notes.

**2. The parser or dispatcher failed closed on the *whole system*, not on the message.** An
"internal coding error" that renders the order-entry system "unable to process incoming messages"
means one unparseable message class took down the message loop rather than being rejected,
quarantined and counted. The blast radius of an unrecognised message should be that message.

**3. The order state model had no INDETERMINATE state, and no way out of "pending".** This is the
finding that generalises. "Pending" was being used as though it meant "not yet executed", when what
it actually meant was "we do not know". Those are different, and the difference is the whole of
order-lifecycle correctness. A customer whose order is in an unknown state must be able to resolve
it by querying authoritative state at the venue, keyed by an identifier the *client* generated
before sending — a client order ID. Absent that key, there is nothing to ask the venue about, so
there is no recovery path, so "pending" is terminal until a human intervenes.

The same shape appears elsewhere in this catalogue with the roles reversed: at the Tokyo Stock
Exchange on 1 October 2020, executions accumulated inside the matching engine after the participant
network was cut, so participants held fills they had no way to learn about. In both cases the wire
went silent and the economic state kept moving.

## The invariant that was violated

```
# the core one
timeout | disconnect | error  =>  state = INDETERMINATE
NOT:                              state = NOT_CREATED   (and NOT = CANCELLED)

resolve(INDETERMINATE) := query(venue, authoritative_state, key = client_generated_order_id)
therefore: client_order_id is generated BEFORE the request leaves, is durable, and is
           stable across every retry of the same intent

# cancel availability
forall reachable order states s: cancel(s) is defined
     and returns either success or an accurate terminal status

# adapter change safety
counterparty_protocol_change => conformance_suite passes before go-live

# message handling
unparseable(message) => reject(message) AND count(message)
NOT: unparseable(message) => halt(message_loop)
```

## Could an AI coding agent reviewing the diff have caught it?

**Yes, for the part that turned a 45-minute outage into 166,000 indeterminate orders.**

**The missing INDETERMINATE state** is visible in the order state machine itself. An agent can
enumerate the states and ask two questions: (a) which state does an order enter when the submit
request times out or the connection drops, and (b) from that state, what code path resolves it
against the venue? If the answer to (a) is a state that also means "the venue has it and has not
filled it", and the answer to (b) is "none", the model is wrong. This is a static property of an
enum and its transition table.

**The missing client-generated idempotency key** is equally static. If the order record acquires its
identity from the venue's response, then there is no key with which to query the venue when the
response never arrives. An agent reviewing an order-submission function should look for the
client-side ID being minted, persisted, and sent — before the network call — and should flag its
absence.

**The uncancellable state** is a reachability check over the same state machine: a state with no
outgoing cancel transition, or with a cancel transition that can fail silently.

**The untested adapter change** is visible in the diff when it lands: a change to a venue adapter's
message handling with no corresponding fixture or conformance test. It is one of the clearest cases
where "where is the test for this?" is the whole review.

The 2–3 March capacity cascade is **partly** detectable at best — an agent can note the absence of
load shedding or backpressure, but not that the firm's growth had outrun its capacity model.

## The rule

> **MUST — Never infer from a timeout, disconnect, or error response that an order was not created,
> modified, or cancelled.** Treat the outcome as indeterminate and resolve it by querying
> authoritative state keyed by a client-generated idempotency key (client order ID) that is minted
> and persisted before the request is sent.

> **MUST — Cancel must be a total function over the order state machine**, with a test for every
> reachable state, returning either success or an accurate terminal status.

> **MUST — A change to a counterparty's protocol must pass a conformance suite before go-live**, and
> the adapter must not depend on the counterparty having tested it.

> **MUST — An unparseable or unrecognised message must be rejected and counted, not permitted to
> halt the message loop.**

> **MUST — A business continuity plan for a money path must cover logical failure**, not only
> physical-site events.

## Sources

- **FINRA AWC No. 2020066971201, *Robinhood Financial LLC*, 30 June 2021** — AWC PDF:
  <https://www.finra.org/sites/default/files/fda_documents/2020066971201%20Robinhood%20Financial%20LLC%20CRD%20165998%20AWC%20rjr.pdf>;
  (The 30 June 2021 FINRA press release is no longer served at its original URL — HTTP 404 as of
  this pass; the AWC PDF above is live and is the authoritative text.)
  **Primary.** Establishes at pp.21 the full March 9 2020 chain verbatim: the third-party venue's
  messaging-protocol change, that Robinhood's parent company did not test it, the internal coding
  error, the 45-minute outage, that existing orders could not be cancelled and customers could not
  tell whether orders were executing, and the ~166,000 orders stuck "pending". Also establishes the
  March 2–3 outage cause ("a key firm system was overloaded, which caused a cascading failure of
  other systems") and the physical-site-only business continuity plan.
- **Correction applied.** The widely repeated claim that the 2 March 2020 outage was a leap-year or
  date-handling bug is unsupported by any primary source. FINRA's AWC — the authoritative account —
  attributes it to capacity overload.
- **Companion case, same mechanism from the venue side:** JPX/Tokyo Stock Exchange, *Report on the
  Cash Equity Trading System Failure on Oct. 1*, 19 Oct 2020 —
  <https://www.jpx.co.jp/english/corporate/news/news-releases/0060/20201019-01.html> — where cutting
  the participant network left matched executions undelivered inside the engine.
