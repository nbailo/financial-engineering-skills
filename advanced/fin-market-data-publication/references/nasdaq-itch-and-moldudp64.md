# Nasdaq ITCH, OUCH and MoldUDP64

Protocol behaviour for the Nasdaq family: the MoldUDP64 transport that numbers messages rather than packets,
the two control packets that carry sequence and no payload, the retransmission path and its truncation rule,
and the ITCH content rules a publisher has to state in writing. Read this when the repository names any of
these protocols or reimplements their shapes. The generic properties they illustrate are in the skill body;
this file is the mechanics.

## Contents

- The MoldUDP64 header, implicit message numbering, and the receiver arithmetic it forces
- Heartbeat and end of session: the two message counts that carry sequence instead of payload
- Session identity, and what a new session id costs
- Retransmission: the re-request server, delivery on the live socket, and truncation
- ITCH content: which messages are book events, which are volume, and the printable flag
- Deliberately-constant ITCH fields, their values and their effective dates
- Timestamps: nanoseconds since midnight, and the epoch question the specification does not settle
- OUCH: why an order-entry protocol appears in a market-data file at all

---

## The MoldUDP64 header

MoldUDP64 carries a 20-byte header on every downstream packet: a 10-byte `Session` identifier, an 8-byte
`SequenceNumber`, and a 2-byte `MessageCount`. The sequence number is that of the **first** message in the
packet. Every subsequent message in the same packet is numbered implicitly.

The receiver arithmetic is therefore `expected += message_count`, not `expected += 1`. This is the single most
consequential fact about the transport, and it is a publisher obligation rather than a consumer detail. A
consumer that increments once per datagram is correct while every packet carries one message and drifts the
instant you batch, which is exactly what you do under load. Batching is a published behaviour, not an internal
optimisation, and any specification you write on top of MoldUDP64 has to say that the sequence is per message.

| Scope | What the header carries | Receiver arithmetic | Reset event |
|---|---|---|---|
| Per message, per session | `Session[10]`, `SequenceNumber[8]`, `MessageCount[2]`; the sequence is that of the first message, the rest implicit | `expected += message_count` | a new 10-byte session id |

## Heartbeat and end of session

`MessageCount` has two magic values. Both carry the **next expected** sequence number and no payload:

| `MessageCount` | Meaning | Publisher duty |
|---|---|---|
| `0` | Heartbeat | Emit through quiet periods at a stated interval, carrying next-expected, so loss is detectable when nothing is trading |
| `0xFFFF` | End of Session | Keep emitting while the session drains: "While the End of Session messages persist, re-requests may be made on the current session. This is the last chance to ensure that all messages have been received." |

The heartbeat is the only thing that distinguishes "nothing is trading" from "your multicast group stopped
delivering", and a consumer with no heartbeat always guesses the first. Publish the interval in the
specification, because every consumer's liveness timeout derives from it: tighten the interval later without a
version bump and you have changed the false-positive rate of every consumer at once.

## Session identity

An end-of-session packet is a commitment that the sequence is closed. Do not emit it and then send further
messages under the same session id; mint a new one. The session id scopes retransmission, so reusing it across
a logical break strands the ranges a consumer is still requesting, and minting a new one strands everything
unrecovered under the old one. That is why a session restart is the recovery mechanism of last resort rather
than a routine response to trouble.

## Retransmission

The re-request server answers a unicast request naming a session, a first sequence number and a count. Two
properties of the path change how every consumer must be written, and both are yours to document.

**Retransmissions arrive on the live socket.** The server answers with a normal downstream packet sent unicast
to the requester: "This allows downstream MoldUDP64 users to read the retransmitted Downstream Packet in their
multicast processing socket… even though the retransmissions are not multicast." A consumer therefore sees
already-requested ranges interleaved with live data on one socket. Say so in the specification, or every
consumer's first recovery attempt reorders their book.

**One request does not necessarily close a gap.** "If the total size of the requested messages exceeds the
maximum payload size of one UDP packet, only the number of messages that completely fit will be returned." A
recovery loop that assumes one request per gap stalls forever at the truncation boundary. Publish the
truncation rule, and publish any request rate limit you enforce: a limit that exists and is undocumented turns
a recoverable gap into a silent stall, and the consumer has no way to tell the two apart.

Both properties are also assertions to run on your own side. The store is keyed on (session, sequence) with a
published depth, and a request for a range you no longer retain gets an explicit answer, never silence.

## ITCH content: two filters on one feed

A single ITCH stream carries messages that belong to different downstream computations, and the two sets are
not the same.

| Downstream use | Include | Exclude | Why |
|---|---|---|---|
| Book construction | Add, Executed, Executed With Price, Cancel, Delete, Replace | **Trade messages** | Trades on ITCH are prints, not book events; including them double-counts depth |
| Published volume | Printable executions | **Executions flagged non-printable** | A non-printable execution is followed by a later bulk print; counting both double-counts volume |

Publish which filter applies to which use. The printable flag is the mechanism, and a consumer who does not
know it exists will build a volume figure that is roughly twice the truth on the instruments where it matters.

## Deliberately-constant fields

Nasdaq TotalView-ITCH 5.0 carries two fields that are constant by design, and states both in the
specification, including the date each became constant:

| Field | Constant value | Effective | What a consumer wrongly infers |
|---|---|---|---|
| Trade (non-cross) `Order Reference Number` | `0` | 2010-12-06 | that the print can be linked to a resting order |
| Trade (non-cross) `Buy/Sell Indicator` | `"B"` | 2014-07-14 | aggressor side: the field is `"B"` regardless of the resting side |

The maintenance hazard is one-directional and permanent. Once a constant has shipped, consumers have written
branches whose other arm is dead code, and transaction-cost reports built on the constant with no error
surfaced anywhere. You cannot un-constant the field on the same feed version: the day it carries real data,
every consumer that special-cased it silently produces different output, and nothing in the message says the
semantics changed.

Two rules follow. State the constant and its effective date in the specification, which Nasdaq does. And
populate it from a named encoder constant with a test asserting constancy, rather than from a live value that
happens to be constant today. A field that is constant by accident becomes variable by accident.

## Timestamps

ITCH timestamps are defined as nanoseconds since midnight. In the specification text read for this suite,
neither ITCH nor OUCH defines which midnight, in which timezone, or how a daylight-saving transition is
handled. That is recorded here as an **unverified gap in the reading**, not as a claim about Nasdaq's actual
behaviour: check the current specification before relying on either answer.

The obligation it illustrates is unconditional for your own feed. Write the epoch, the timezone and the
daylight-saving rule into the specification, and state what your clock is disciplined to. A consumer computing
staleness is subtracting your timestamp from their clock, so an unstated discipline gives the result an
unbounded constant error, and a consumer forced to infer the epoch will infer it wrong twice a year.

## OUCH

OUCH is Nasdaq's order-entry protocol, not a market-data feed, and it appears here for two reasons. The first
is the shared timestamp convention above, which is a property of the family rather than of one protocol. The
second is the seam: a venue that speaks OUCH inbound and ITCH outbound has one event, the execution, that must
appear on both, and the quantity conventions differ between the order-entry and market-data views. Where a
change spans that seam, the order-entry half belongs to the matching-engine skill and only the published half
is governed here. Do not derive a market-data rule from an order-entry message layout, and do not assume a
quantity field means the same thing on both sides without checking the specification for each.
