# Publishing a market-data feed

What a venue owes the consumers of the feed it originates: a sequencing scheme that a receiver can gap-detect
without false positives, a recovery path that terminates, a snapshot that states what it is a snapshot *of*,
and timing that is fair by architecture rather than by intent. The consuming side of every one of these
mechanisms lives in `fin-exchange-integration`; this file is what you must publish so that a correct consumer
exists at all. Nothing here is recoverable after the fact — a consumer that mis-built a book from your feed
traded on it, and you are the only record of what you sent.

## Contents

1. **Sequence numbering schemes** — per-message vs per-packet (MoldUDP64's implicit numbering), per-instrument
   vs per-channel (`RptSeq` alongside the packet sequence), and the receiver arithmetic each one forces.
2. **Control packets that carry sequence** — heartbeat (`count == 0`) and end-of-session (`0xFFFF`), and why
   they must advance the receiver's expectation without payload.
3. **Channel reset and per-instrument sequence resets** — CME MDP 3.0 `35=X` / `269=J`, what is cleared, what
   is *not* resent, and why a monotonicity assertion on `RptSeq` freezes a book instead of crashing.
4. **Snapshot and incremental** — the join key, the as-of sequence, the direction of the join, the snapshot
   loop, and what the snapshot does not carry.
5. **A/B line arbitration** — two independent paths, byte-identity per sequence number, and why
   duplicate-tolerant consumers are a publisher requirement.
6. **Gap recovery: four mechanisms and the client contract each implies** — retransmission request, snapshot
   channel, natural refresh, session restart; truncation and termination.
7. **Two filters on one feed, and deliberately-constant fields** — printable flags, book-vs-volume filters,
   and the maintenance hazard a hard-coded field creates.
8. **Book-state assertions on the publish path** — the unchecked `u64` depth trap found in 3 of 3, crossed
   books, and depth conservation against the executions you emitted.
9. **Conflation and backpressure** — state-encoded vs delta-encoded feeds, what conflation destroys, and what
   must be published alongside it.
10. **Timestamp semantics** — exchange time vs send time vs receive time, which one a consumer may use for
    staleness, and the epoch ambiguity you must resolve in your own spec.
11. **Timing fairness** — Reg NMS 603(a), the NYSE architectural breach, and the evidence you must retain.
12. **Feed specification checklist** — what a consumer needs stated in writing before it can be correct.

## 1. Sequence numbering schemes

Three independent counters routinely coexist on one feed and they are not interchangeable. Decide which one
a consumer gap-detects on, publish that decision, and never change it without a versioned feed.

| Scheme | Scope | What the header carries | Receiver arithmetic | Reset event |
|---|---|---|---|---|
| MoldUDP64 (Nasdaq) | per **message**, per session | 20-byte header: `Session[10]`, `SequenceNumber[8]`, `MessageCount[2]`; the sequence is that of the **first** message in the packet, the rest implicitly numbered | `expected += message_count` | new 10-byte Session id |
| CME MDP 3.0 packet sequence | per **channel**, per packet | packet sequence number | `expected += 1` per packet | Channel Reset (`35=X`) |
| CME `83-RptSeq` | per **instrument**, per book update | inside each MD entry repeating group | per-instrument continuity check | resets to **1 per instrument** on `269=J` |
| FIX `34-MsgSeqNum` | per session, per message | message header | `+= 1`; gap ⇒ ResendRequest | Logon `ResetSeqNumFlag`, SequenceReset |
| Binance-style `U`/`u`/`pu` | per symbol, per (possibly conflated) event | first/last update id covered, previous `u` | `U == prev_u + 1`, or futures `pu == prev_u` | re-snapshot |

**The publisher's obligation is arithmetic compatibility, not just monotonicity.** On MoldUDP64 the message
count is load-bearing: a receiver that does `expected += 1` per datagram drifts the instant you batch, which
is exactly what you do under load. So batching is a *published* behaviour, not an internal optimisation — a
consumer written against your spec must be told the sequence is per message.

**Per-instrument and per-channel sequences answer different questions and you need both.** The packet sequence
detects transport loss for the whole channel; `RptSeq` detects that *this instrument's* book updates are
contiguous. CME MDP 3.0 Recovery Services is explicit about the consequence of only having the first: on a
packet gap "it should be assumed that **all** books maintained in the client system may no longer have the
correct, latest state". If you publish only a channel sequence, you have forced every consumer to invalidate
every book on any loss. If you publish `RptSeq` too, a consumer can recover per instrument — but only if you
also publish the reset semantics in §3.

## 2. Control packets that carry sequence

MoldUDP64 gives `MessageCount` two magic values, and both carry the **next expected** sequence number rather
than payload:

| `MessageCount` | Meaning | Publisher duty |
|---|---|---|
| `0` | Heartbeat | Emit during quiet periods at a stated interval, carrying next-expected, so loss is detectable when nothing is trading |
| `0xFFFF` | End of Session | Keep emitting while the session drains: "While the End of Session messages persist, re-requests may be made on the current session. This is the last chance to ensure that all messages have been received." |

The heartbeat is the only thing that distinguishes "no trading" from "your multicast group stopped delivering".
A feed with no heartbeat forces every consumer to guess, and the guess is always "the market is quiet". Publish
the interval in your specification — a consumer's liveness timeout is derived from it, and if you tighten the
interval later without a version bump you have changed every consumer's false-positive rate.

An end-of-session packet is a **commitment that the sequence is closed**. Do not emit it and then emit more
messages on the same session; mint a new session id instead. The session id is the thing that scopes
retransmission (§6), so re-using it across a logical break strands the ranges a consumer is still requesting.

## 3. Channel reset and per-instrument sequence resets

CME MDP 3.0 Channel Reset (`35=X` with `269-MDEntryType=J`, Empty Book, plus tag `1180 ApplID`) signals
corrupted books. It is the model to copy because it enumerates exactly what a reset invalidates:

| Cleared by the reset | **Not** resent by the reset |
|---|---|
| The book (Empty Book) | Settlement prices |
| Session trade volume | Instrument definitions (separate feed) |
| Session high / low | Statistics not carried on the incremental |
| Indicative opening price | — |
| `83-RptSeq` → **1 per instrument** | — |
| The channel's Market Recovery snapshots (deleted) | — |

**Publish the reset explicitly; never let a consumer infer it from a sequence going backwards.** The failure
mode is asymmetric and the *silent* one is worse:

```rust
// Consumer code your feed will be parsed by. Both arms are wrong after a reset.
if rpt_seq != last_rpt_seq + 1 { panic!("gap"); }   // crashes on the reset
if rpt_seq <= last_rpt_seq      { return; }         // silently drops EVERY post-reset update
```

The second is the one that reaches production: the book freezes at its pre-reset state while trading continues,
and nothing anywhere logs an error. That is a consumer bug, but you cause it by resetting a counter without a
message that says so. Emit the reset **before** the first message carrying the new numbering, in the same
ordered stream — not on a side channel, not as an operations email.

The publisher-side mirror of the same hazard: any assertion you hold internally over `RptSeq` must be written
with the reset in the state machine, and the reset must clear derived aggregates (volume, high/low, IOP) in
the same commit that resets the counter. A reset that clears the book but leaves session volume standing
publishes a volume figure with no trades behind it.

## 4. Snapshot and incremental

**A snapshot is not "the book right now". It is "the book as of a stated point in the incremental stream".**
Without that point it is unusable, because the consumer cannot know which buffered incrementals it already
contains. CME states the join key precisely: the snapshot's `369-LastMsgSeqNumProcessed` "corresponds to the
packet sequence number on the Incremental feed."

The consumer algorithm your snapshot must support (CME MDP 3.0, MBP/MBOFD Market Recovery):

1. Subscribe to the incremental and **queue** it. Subscribe to Instrument Definition separately.
2. Join the snapshot loop; process **one full iteration starting at snapshot sequence 1** — loop order is not
   guaranteed, so a partial iteration is not a recovery.
3. Per instrument, compare tag `369` against the queued incremental packet sequence numbers.
4. If a `SecurityID` appears in both, compare tag `60-TransactTime`; if unequal, **defer that instrument to the
   next snapshot cycle**.
5. **Drop all cached incremental updates with packet sequence < 369.** Then apply the remainder in order.
6. Books go live **per instrument** as each recovers; all books are assumed incorrect until they do.

Three publisher obligations fall out of this:

- **Stamp the as-of atomically with the book copy.** Reading the channel sequence *after* cloning the level
  book stamps a snapshot as newer than it is, and step 5 then makes the consumer delete real updates:

  ```rust
  // WRONG — as_of is read after the copy, so [copy_point, as_of) is lost by every consumer
  let entries = book.snapshot_entries();
  let as_of   = channel_seq.load(Ordering::Relaxed);

  // RIGHT — one critical section produces both
  let (entries, as_of) = book.snapshot_with_seq();   // returns (entries, seq applied at copy time)
  ```
  If you genuinely cannot make it atomic, err **old**, never new — and then state in your spec whether
  re-applying an already-included update is idempotent. It is only idempotent on an absolute-quantity feed;
  on a delta-encoded feed erring old double-applies.
- **Say what the snapshot does not carry.** CME's snapshot loop does not recover statistics. Every field a
  consumer can see on the incremental but not on the snapshot is a field they will silently carry stale.
- **State the direction of the join in the specification, not just in the code.** "All book updates in the
  latest Market Data Incremental Refresh message must be processed before the order book can be considered
  valid" is a sentence a consumer needs in writing, because it is what makes their book valid *at all*.

## 5. A/B line arbitration

Publish two independent paths carrying the same sequence space. CME's Incremental Feed Arbitration states the
consumer contract: any packet "can arrive first on either feed depending upon network carrier and independent
of CME Group"; discard a packet whose sequence has already been processed; a gap means the packet was lost on
**both**.

This makes duplicate tolerance a **publisher requirement**, not a consumer nicety, and it constrains what you
are allowed to send:

- **Byte-identical per sequence number on both lines.** Arbitration is by sequence number alone — the consumer
  keeps whichever arrived first and discards the other unread. If line A and line B ever differ in content for
  the same sequence (different conflation, different batching boundaries producing different message groupings
  under the same first-message sequence, a field populated on one path only), the consumer's book becomes a
  function of network jitter. Generate once, at one sequencer, and fan out bytes.
- **No apply-once semantics beyond the sequence number.** Every message must be safely discardable as a
  duplicate on the basis of its sequence alone. A message whose effect depends on being seen exactly once but
  which cannot be identified as a duplicate from its sequence is unimplementable on an A/B feed.
- **A gap is declared after arbitration, never before.** If your spec does not say this, consumers will run
  single-line, treat normal single-path loss as a gap, and generate a recovery storm — against your
  re-request server, at the moment of highest load, on a path that has never been exercised at that rate.

## 6. Gap recovery: four mechanisms and the client contract each implies

| Mechanism | What the consumer does | What you must guarantee | Terminates when |
|---|---|---|---|
| **Retransmission request** | Unicast request naming (session, first sequence, count) to a re-request server | A store keyed by (session, sequence) with a **published depth**; responses on the consumer's normal socket | The requested range is fully received — possibly after several requests |
| **Snapshot / recovery channel** | Join the loop, run one full iteration from snapshot sequence 1, join per §4 | A cycle that covers every instrument, with a **published maximum cycle period** | One full iteration completes for that instrument |
| **Natural refresh** | Wait for the next full state message for the affected key | A bounded refresh interval per key, and absolute (not delta) state in the refresh | The next refresh for that key arrives |
| **Session restart** | Drop everything, re-establish, re-snapshot | A new session identity and a clean sequence space | Immediately, at the cost of everything unrecovered |

Two properties of the MoldUDP64 retransmission path change how a consumer must be written, and both are yours
to document:

- **Retransmissions arrive on the live socket.** The re-request server answers with a normal Downstream Packet
  sent unicast to the requester's multicast socket: "This allows downstream MoldUDP64 users to read the
  retransmitted Downstream Packet in their multicast processing socket… even though the retransmissions are
  not multicast." So a consumer sees already-requested ranges interleaved with live data on one socket. Your
  spec must say so, or every consumer's first recovery attempt reorders their book.
- **One request does not necessarily close a gap.** "If the total size of the requested messages exceeds the
  maximum payload size of one UDP packet, only the number of messages that completely fit will be returned."
  A recovery loop that assumes one request per gap stalls forever at the truncation boundary. Publish the
  truncation rule and any request rate limit; a rate limit you enforce but do not publish turns a recoverable
  gap into a silent stall.

**Session restart is the mechanism of last resort, and the reason is recoverability scoping.** CME iLink 3
makes the same point on the order-entry side: business messages are recoverable only for the (sequence number
+ UUID) pair, and "Do not Terminate the FIXP session and Re-Negotiate with a new UUID as a normal response to
a Not Applied message" — a new identity permanently strands everything unrecovered under the old one. The
market-data equivalent is a new MoldUDP64 session id. The FIX-session analogue is answering a ResendRequest
with `SequenceReset-Reset`, which is defined as lossy: it "should ONLY be used to recover from a disaster
situation which cannot be recovered via Gap Fill", "may result in the possibility of lost messages", and
"should NOT be used as a normal response to a Resend Request."

## 7. Two filters on one feed, and deliberately-constant fields

A single feed carries messages that belong to different downstream computations. Publish which filter applies
to which use, because the two are not the same set:

| Downstream use | Include | Exclude | Why |
|---|---|---|---|
| Book construction | Add / Executed / Executed With Price / Cancel / Delete / Replace | **Trade messages** | Trades on ITCH are prints, not book events; including them double-counts depth |
| Published volume | Printable executions | **`Printable=N` executions** | Non-printable executions are followed by a later bulk print; counting both double-counts volume |
| Published volume | Matches between distinct beneficial owners | **Self-matched prints** | CFTC v. Coinbase, March 2021, $6.5M: self-matched volume propagated into the CME Bitcoin Real Time Index, CoinMarketCap and the NYSE Bitcoin Index |

**Deliberately-constant fields.** Nasdaq TotalView-ITCH 5.0 carries two:

| Field | Constant value | Effective | What a consumer wrongly infers |
|---|---|---|---|
| Trade (non-cross) `Order Reference Number` | `0` | 2010-12-06 | That the print can be linked to a resting order |
| Trade (non-cross) `Buy/Sell Indicator` | `"B"` | 2014-07-14 | Aggressor side — "regardless of the resting side" |

The maintenance hazard is one-directional and permanent. Once you have shipped a constant, consumers have
written `if side == 'B'` branches whose else-arm is dead code and TCA reports built on the constant with no
error surfaced anywhere. **You cannot un-constant the field on the same feed version** — the day it carries
real data, every consumer that special-cased the constant silently produces different output, and nothing in
the message tells them the semantics changed. Two rules follow: state the constant *and its effective date* in
the spec (Nasdaq does), and populate it from a named encoder constant with a test asserting constancy, not
from a live value that "happens to" be constant today. A field that is constant by accident becomes variable
by accident.

## 8. Book-state assertions on the publish path

**The default shape of a hand-written price level is wrong on this path**: a level aggregate decremented with
unchecked unsigned subtraction, guarded only by a debug assertion, on the path that publishes depth.

```rust
struct PriceLevel { price: i64, total_qty: u64, orders: VecDeque<OrderId> }

impl PriceLevel {
    fn fill(&mut self, qty: u64) {
        debug_assert!(qty <= self.total_qty);   // "qty <= total_qty by construction"
        self.total_qty -= qty;                  // release: wraps to ~1.8e19, published as depth
    }
}
```

Two independent reasons the guard is not in the shipped binary: `debug_assert!` compiles to nothing unless
`debug_assertions` is on — nautilus_trader sets `debug-assertions = false` in **both** `[profile.dev]` and
`[profile.release]`, so its three `debug_assert!`s in the whole order model are compiled out everywhere — and
Cargo's release profile disables integer overflow checks, so the `-=` wraps rather than panicking. Naming the
failure correctly in a comment — *"a classic drift bug"* — and then answering it with a debug-only check is the
standard mistake. The rationalisation is always *"`qty ≤ total_qty` by construction"*. It was by construction;
drift is the bug you are hunting.

```rust
#[derive(Debug)]
pub struct DepthBreach { pub price: i64, pub have: u64, pub take: u64 }

impl PriceLevel {
    fn fill(&mut self, qty: u64) -> Result<u64, DepthBreach> {
        let remaining = self.total_qty
            .checked_sub(qty)
            .ok_or(DepthBreach { price: self.price, have: self.total_qty, take: qty })?;
        self.total_qty = remaining;
        Ok(remaining)
    }
}
```

On `Err`: **halt that transformation at the smallest scope** (freeze the symbol), **do not publish the level**,
and **do not clamp to zero** — a clamp is a fabricated depth with no exception attached. If you saturate
instead of checking (the right choice where a panic would abandon in-flight obligations — nautilus uses
`saturating_sub` at `orders/mod.rs:1366` for exactly that reason), you must **emit the saturation as an event**
on the same feed. TigerBeetle's opposite choice — assertions live in release, ~1 per 10.6 lines of state
machine, every accumulator overflow-checked before mutation — is correct where nothing is in flight:
"Assertions downgrade catastrophic correctness bugs into liveness bugs." A `debug_assert` on a published
aggregate is neither of the two.

Assertions worth running on the emit path, with the halt level each implies:

| Assertion | Expression | On breach |
|---|---|---|
| Level conservation | `published_qty(level) == Σ leaves_qty of resting orders at that level` (recomputed, not accumulated) | Freeze the symbol; do not publish |
| No underflow | `checked_sub` on every aggregate that leaves the process | Freeze the symbol; do not publish |
| Not crossed | `best_bid ≤ best_ask` on every top-of-book publish | Freeze the symbol; do not publish |
| Depth vs executions | `Δ total_qty(level) == Σ executed qty at that level this cycle` | Freeze the symbol; escalate |
| Snapshot join point | `snapshot.as_of ≤ last_applied_channel_seq` at emit | Withhold the snapshot cycle |

`bid ≤ ask` is one line and NASDAQ published its violation to the world: on 18 May 2012 the Facebook cross was
marked in error and the proprietary feed published a stale **crossed** quote at top of book. A crossed book is
an arithmetically impossible state; there is no excuse for shipping it.

## 9. Conflation and backpressure

A slow consumer forces a choice, and the choice is a correctness decision, not a performance one.

| Strategy | What the consumer can still reconstruct | Legitimate when | Must be published |
|---|---|---|---|
| Block the publisher | Everything | Never on a feed — it back-pressures the matching engine | — |
| Disconnect the consumer | Nothing, until they recover | Always acceptable; a gap is visible and recoverable | The disconnect reason, and the recovery entry point |
| Conflate **state** (replace the pending update for a key with its latest absolute value) | The book at each observed instant; **not** the path between instants | The feed is absolute-quantity per key | That intermediate states are unobservable, plus a covered-range identifier per event |
| Conflate **deltas** (drop some) | Nothing correct, ever | Never | — |

**Conflation is only safe on a state-encoded feed.** Dropping one delta on a delta-encoded feed corrupts the
book permanently with no gap visible anywhere — the sequence is still contiguous, so no consumer can detect it.
Binance's depth stream is the worked example of doing it right: "The data in each event is the absolute
quantity for a price level", each event carries the range of update ids it covers (`U`..`u`), and the futures
stream adds `pu` — the previous event's `u` — precisely because `U`/`u` ranges can look contiguous across a
server-side coalescing boundary. The `pu` field exists because conflation defeats the naive gap check.

Three consequences to state in your spec:

- **Trades are never conflatable.** Each print is an economic fact with its own match number; collapsing two
  prints into one destroys volume, VWAP and any trade-based signal. Conflate quotes and levels only.
- **Conflation changes what the feed is.** A conflated feed has last-value-cache semantics. Anything derived
  from the *count* or *order* of updates — message-rate signals, queue-position estimates, order-level event
  reconstruction — is invalid on it, and consumers will build those things unless you say so.
- **Conflation must not silently vary.** If depth is conflated under load and not otherwise, the feed's
  semantics are a function of load, and a consumer's backtest built on quiet-period data does not describe the
  feed they trade against at the open.

## 10. Timestamp semantics

Three timestamps, three jobs. Publish all three where you can, and say which one is authoritative for what.

| Timestamp | Set by | Set when | Example field | Valid consumer use | Invalid use |
|---|---|---|---|---|---|
| **Exchange / transact time** | The matching engine | At the moment the book event occurred | CME `60-TransactTime`; ITCH "nanoseconds since midnight" | Ordering, cross-feed joins (CME defers an instrument when `60` differs between snapshot and incremental), **content staleness** | Measuring your own network latency |
| **Send time** | The feed handler, at serialisation | When the packet left the publisher | FIX `52-SendingTime` | Publisher-internal latency, 603(a) evidence (§11) | **Staleness** — see below |
| **Receive time** | The consumer's NIC/kernel | On arrival | hardware timestamp | Transport latency, arrival ordering, liveness | Anything about when the event happened |

**Staleness is `now − exchange_time > max_age`, and only the exchange timestamp belongs in it.** Send time is
disqualified by retransmission: a resent message carries a *fresh* send time for *old* content — which is why
FIX requires `OrigSendingTime(122)` on a resend alongside `PossDupFlag(43)`. Compute staleness from send time
and every retransmitted message reports itself as fresh. Receive time is disqualified for the opposite reason:
it exists only when a message arrives, so it can never detect a publisher that has gone quiet. That is the
heartbeat's job (§2) — and the reason the heartbeat carries the next expected sequence rather than nothing.

Two things a publisher must supply for `now − exchange_time` to be computable at all:

- **An unambiguous epoch.** ITCH and OUCH define timestamps as "nanoseconds since midnight" without, in the
  specification text read for this suite, defining *which* midnight or how DST transitions are handled — this
  is recorded as an **unverified gap**, not as a claim about Nasdaq's actual behaviour. Whatever your feed
  does, write the epoch, the timezone and the DST rule into the spec; a consumer that must infer them will
  infer them wrong twice a year.
- **A stated clock discipline.** The consumer is subtracting your timestamp from their clock. If you do not
  say what yours is disciplined to, the resulting age is a number with an unbounded constant error, and a
  `max_age` gate built on it either never fires or always fires.

## 11. Timing fairness

Reg NMS Rule 603(a) "prohibits an exchange from releasing data relating to quotes and trades to its customers
through proprietary feeds before it sends its quotes and trade reports for inclusion in the consolidated
feeds." SEC Rel. 34-67857 (2012-09-14) penalised NYSE / NYSE Euronext $5M — the first-ever SEC financial
penalty against an exchange — for breaching it **architecturally**, with no intent involved:

| NYSE finding | The code shape that produces it |
|---|---|
| "NYSE's internal architecture gave its real-time depth-of-book proprietary feed a path to customers that was faster than the path used to send quotes to the Network Processor" | Two sinks fed from separate queues after a fan-out, with different serialisation cost |
| A second proprietary feed "was structured to operate independently of the system that sent data to the Network Processor" | A sink that does not inherit the shared path's delays, so it wins whenever that path is slow |
| A load-dependent software defect delayed the consolidated path under high volume | Backpressure on one sink only — the one that must not be last |
| NYSE could not prove compliance: it had not retained the transmission-timing files | No durable per-message record of when each sink was handed the bytes |

Disparities ranged "from single-digit milliseconds to, on occasion, multiple seconds."

The publisher rules that follow: **fan out from one point, after the sequence number is assigned**; timestamp
each sink hand-off at that point; make the consolidated sink no later than any proprietary sink by
construction (same buffer, same ordering, consolidated first if either must be); and **retain the
transmission-timing records**, because the evidentiary failure was charged separately from the timing failure.
A fan-out to two sinks with different queueing or serialisation is a reviewable defect on sight, independent
of whether either sink is currently slow.

## 12. Feed specification checklist

A consumer cannot be correct against an underspecified feed. Every line below is something a real consumer
must know and cannot derive from the wire format:

```
SEQUENCING
  [ ] Which counter gap-detection runs on (per-message / per-packet / per-instrument), and its arithmetic
  [ ] Whether packets carry multiple messages, and how the count is encoded
  [ ] Every additional counter published, its scope, and what it is NOT valid for
CONTROL
  [ ] Heartbeat interval and what a heartbeat carries
  [ ] End-of-session semantics and how long re-requests remain serviceable
RESET
  [ ] The reset message and its trigger
  [ ] Exactly what a reset clears, what it renumbers, and what it does NOT resend
SNAPSHOT
  [ ] The join key, and which stream's sequence it names
  [ ] Direction of the join: which buffered updates are dropped and which applied
  [ ] Snapshot cycle period, loop-order guarantees (or their absence), and fields the snapshot omits
GAP
  [ ] Whether A/B arbitration must precede gap declaration
  [ ] Re-request address, request format, retained depth, truncation rule, rate limit
  [ ] What a gap invalidates: this instrument, or every book on the channel
CONTENT
  [ ] Which message types are book-eligible; which are volume-eligible; the printable flag
  [ ] Every deliberately-constant field, its value, and its effective date
  [ ] Conflation policy: which streams, under what conditions, state- or delta-encoded
TIME
  [ ] Each timestamp's meaning, epoch, timezone, DST rule, and clock discipline
  [ ] Which timestamp is authoritative for staleness
```

A slot you cannot fill is a slot a consumer will fill by guessing, and their guess becomes a position.
