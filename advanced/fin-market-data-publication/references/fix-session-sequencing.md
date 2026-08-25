# FIX session sequencing and recovery

Protocol behaviour for feeds that ride a FIX or FIXP session rather than a multicast transport. The session
layer, not the application message, owns identity, ordering and gap recovery here, so the sequencing contract
you publish is largely a set of decisions about how you use the session protocol. Read this when the
repository names any of the session-level constructs below, including on the order-entry side, because the
session semantics are shared and getting them wrong on either side loses messages the same way.

## Contents

- The session counter, its scope, and where it resets
- ResendRequest: what a publisher owes, and what a resend must carry
- Gap fill and the reset that is defined as lossy
- Sequence resets at logon, and the identity they implicitly mint
- Duplicate and resend flags, and the timestamp trap they exist to close
- FIXP sessions: the identity that scopes recoverability, and the anti-pattern the specification names
- What to publish when your feed is a FIX session

---

## The session counter

`34-MsgSeqNum` is per session, per message, in the message header. The receiver arithmetic is a single
increment per message, and a gap triggers a ResendRequest rather than a silent re-subscribe. Two properties
distinguish it from a packet-oriented transport, and both change what a publisher has to do.

First, the counter is bidirectional and per session: each side numbers its own outbound stream, and neither
side's numbering tells the other anything about the other's. Second, the session, not the channel, is the unit
of recovery, so everything recoverable is recoverable only within the identity the session currently holds.
That makes the identity, rather than the counter, the thing to reason about when anything goes wrong.

| Scope | Where it lives | Receiver arithmetic | Reset event |
|---|---|---|---|
| per session, per message | message header, tag `34` | `+= 1`; a gap triggers ResendRequest | `ResetSeqNumFlag` on Logon, or SequenceReset |

## ResendRequest

A consumer that sees a gap asks for a range. The publisher owes three things, and each is a slot in the
specification rather than a property of the protocol:

- **A store that can answer.** Retained depth, expressed in messages or in time, published as a number. A
  request for a range you no longer hold gets an explicit answer, never silence.
- **A stated answer for application messages that are no longer meaningful.** A market-data update superseded
  by a later one may legitimately be gap-filled rather than resent, but which types you treat that way is a
  content decision a consumer cannot derive from the protocol.
- **A stated behaviour under a large request.** Whether you answer a wide range in one response or several,
  and whether you rate-limit requests. A limit you enforce and do not publish turns a recoverable gap into a
  silent stall.

## Gap fill, and the reset that is defined as lossy

`SequenceReset` has two modes and they are not interchangeable. With `GapFillFlag` set it is a statement that
the skipped range contained only administrative or superseded messages, and the receiver advances its expected
sequence without loss of application content. Without it, the message is a plain sequence reset, and the FIX
specification is unusually direct about what that means: it "should ONLY be used to recover from a disaster
situation which cannot be recovered via Gap Fill", it "may result in the possibility of lost messages", and it
"should NOT be used as a normal response to a Resend Request."

Treat that as a rule about your own publisher, not as advice to consumers. Answering a resend with a plain
reset is the FIX-session equivalent of minting a new session identity on a multicast feed: it closes the gap
by declaring the content unrecoverable, and every consumer that had a position derived from the skipped range
now has one derived from nothing. It belongs in a runbook for an unrecoverable store, with an alert, never in
the normal resend path.

## Sequence resets at logon

`ResetSeqNumFlag` on Logon restarts both directions at one. It is convenient, and it quietly changes what
recovery means: everything numbered under the previous sequence space is no longer requestable, because the
numbers now refer to different messages. A daily reset at a session boundary is a normal and well-understood
design, and the specification you publish has to state when it happens and what remains recoverable across it.
An unscheduled reset is an incident, and it is worth an explicit operational record for the same reason a
market-data session restart is: it is the moment content stopped being recoverable.

## Duplicate and resend flags

A resent message carries `PossDupFlag(43)` set, and `OrigSendingTime(122)` carrying the sending time of the
original transmission alongside the fresh `52-SendingTime` of the resend. The pair exists because the send
time of a retransmission is fresh while its content is old.

This is the mechanical reason send time is disqualified as a staleness input. A consumer computing age from
`52` sees every retransmitted message report itself as current, so the one class of message most likely to be
stale is the one class that never trips the gate. Where you publish an event time, publish it as a distinct
field with a distinct meaning, and say in the specification that it, not the sending time, is authoritative
for staleness. Where a consumer must compare two messages about the same object, the comparison is on the
event time for the same reason.

`PossResend` covers the different case where the application content may already have been delivered under a
different sequence number, and it is a content-level warning rather than a session-level one. A consumer needs
to be told which of your message types can arrive with it set, because deduplication then has to be keyed on
something in the message body rather than on the sequence number.

## FIXP sessions and the identity that scopes recovery

CME iLink 3 makes the identity point explicitly on the order-entry side, and the same reasoning governs any
FIXP feed. Business messages are recoverable only for the pair of a sequence number and the session UUID, and
the specification names the anti-pattern directly: "Do not Terminate the FIXP session and Re-Negotiate with a
new UUID as a normal response to a Not Applied message."

A new identity permanently strands everything unrecovered under the old one. That is the same failure as a new
multicast session id, and it is worth stating in the same words in your own specification, because the
operational instinct under load is always to restart the session. Restarting is the mechanism of last resort,
and it converts a bounded gap into unbounded loss.

## Market data carried on a FIX session

Where the application messages are market data rather than orders, two seams need stating. The first is that
an application-level counter, if you publish one, is a second counter with a different scope from tag `34`,
and the specification has to say which one gap detection runs on and what the other is not valid for. The
second is that the session's recovery mechanism and the feed's recovery mechanism are different things: a
resend replays the session's byte stream, while a snapshot rebuilds state. A consumer needs to know which one
answers a gap, and whether a snapshot is even available on this transport.

The common defect is a feed that offers both and specifies neither boundary. A consumer then requests a resend
for a gap wide enough that a snapshot would have been faster and cheaper, at the moment your session is
already behind, which is the moment the resend is most expensive to serve.

## What to publish when your feed is a FIX session

- The counter, its scope, and the fact that it is per session and per direction.
- Retention: how far back a resend can reach, expressed as a number, and what happens beyond it.
- Which message types you may gap-fill rather than resend, and which you always resend.
- Whether and when sequence numbers reset, and what remains recoverable across the reset.
- Which of your message types can arrive as a possible duplicate or possible resend, and what a consumer
  should deduplicate on in each case.
- That the event time, not the sending time, is authoritative for staleness, with its epoch, timezone,
  daylight-saving rule and clock discipline.
