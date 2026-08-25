# FIX resend flags, logon resets, and the FIXP identity that scopes what is recoverable

> **Provenance**
> provider: FIX Trading Community for the flags and the logon reset, read through the OnixS FIX 4.4
> dictionary; CME Group for the FIXP identity rules quoted at the end
> surface: what a publisher owes a Resend Request, then `ResetSeqNumFlag`, `PossDupFlag`, `OrigSendingTime`,
> `PossResend`, and FIXP session identity as a publisher meets it
> version: FIX 4.4 session layer as rendered by the OnixS dictionary on 2026-08-25; CME iLink Binary Order
> Entry Session Layer page, version 1, last modified 2025-04-22.
> verified_at: 2026-08-25
> sources: https://www.onixs.biz/fix-dictionary/4.4/tagNum_141.html
> · https://www.onixs.biz/fix-dictionary/4.4/tagNum_43.html
> · https://www.onixs.biz/fix-dictionary/4.4/tagNum_97.html
> · https://www.onixs.biz/fix-dictionary/4.4/tagNum_122.html
> · https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/714145834
> pinned: the CME page was read through the wiki's content API on 2026-08-25, which returned its version and
> last-modified timestamp in the same response.
> verified: the definitions of tags 141, 43, 97 and 122 were read on the OnixS pages listed above on
> 2026-08-25; the three CME sentences at the end, on the iLink page the same day.
> unverified: the OnixS dictionary is a secondary rendering of FIX 4.4, so what is established is that it
> carries these words. Nothing in the market-data section below is a quotation: it is this repository's
> advice about a seam the protocol does not settle.
> revalidate_when: your counterparty moves to FIXP with a different session identity; the cited CME page
> moves past version 1; the dictionary changes what a resent message must carry.

Why a retransmitted message reports itself as current, what a logon-time reset does to everything numbered
before it, and what a new session identity costs the messages left unrecovered under the old one. Read this
when a resend path sets or reads a duplicate flag, when deduplication is keyed on a sequence number, or when
the operational instinct under load is to reset the numbering or renegotiate the session.

## Contents

- ResendRequest: what a publisher owes, and what a resend must carry
- Sequence resets at logon, and the identity they implicitly mint
- Duplicate and resend flags, and the timestamp trap they exist to close
- FIXP sessions: the identity that scopes recoverability, and the anti-pattern CME names
- Market data carried on a FIX session: two seams the protocol does not settle
- What to publish when your feed is a FIX session

---

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

## Sequence resets at logon

`141-ResetSeqNumFlag`, carried on Logon, "Indicates that the both sides of the FIX session should reset
sequence numbers." It is convenient, and it quietly changes what recovery means: everything numbered under the
previous sequence space is no longer requestable under the new one, because the numbers now refer to different
messages. A daily reset at a session boundary is a normal and well-understood design, and the specification
you publish has to state when it happens and what remains recoverable across it. An unscheduled reset is an
incident, and it is worth an explicit operational record for the same reason a market-data session restart is:
it is the moment content stopped being recoverable through the ordinary path.

## Duplicate and resend flags

A resent message carries `43-PossDupFlag`, which "Indicates possible retransmission of message with this
sequence number", alongside `122-OrigSendingTime`, the "Original time of message transmission (always
expressed in UTC (Universal Time Coordinated, also known as "GMT") when transmitting orders as the result of a
resend request." The pair exists because the send time of a retransmission is fresh while its content is old,
and tag 52 carries the fresh one.

This is the mechanical reason send time is disqualified as a staleness input. A consumer computing age from
tag 52 sees every retransmitted message report itself as current, so the one class of message most likely to
be stale is the one class that never trips the gate. Where you publish an event time, publish it as a distinct
field with a distinct meaning, and say in the specification that it, not the sending time, is authoritative
for staleness. Where a consumer must compare two messages about the same object, the comparison is on the
event time for the same reason.

`97-PossResend` covers the different case: it "Indicates that message may contain information that has been
sent under another sequence number." That is a content-level warning rather than a session-level one, and a
consumer needs to be told which of your message types can arrive with it set, because deduplication then has
to be keyed on something in the message body rather than on the sequence number.

## FIXP sessions and the identity that scopes recovery

CME iLink 3 makes the identity point explicitly on the order-entry side, and the same reasoning governs any
FIXP feed. On the iLink Binary Order Entry session layer page: "Do not Terminate the FIXP session and
Re-Negotiate with a new UUID as a normal response to a Not Applied message." The page gives the reason in the
next two sentences, and both are worth reading beside the FIX Sequence Reset language above, because they are
the same rule stated for a different protocol: "Re-Negotiate with a new UUID should be used only to recover
from a disaster situation that cannot be recovered via the use of Sequence message." And: "Re-Negotiating with
a new UUID will mean recovering messages sent by the exchange in the previous FIXP session with the previous
UUID."

The last sentence is the precise cost, and it is worth stating precisely rather than dramatically. A new
identity does not destroy what came before; it moves it out of the identity you are now on, so anything still
unrecovered is reachable only by going back to the old UUID while the new session restarts numbering at one.
The operational instinct under load is always to restart, and restarting converts a bounded gap into a
recovery problem in a session nobody is watching any more. Say so in your own specification, in whatever terms
your protocol uses.

## Market data carried on a FIX session

Where the application messages are market data rather than orders, two seams need stating, and neither is
settled by the protocol. The first is that an application-level counter, if you publish one, is a second
counter with a different scope from tag `34`, and the specification has to say which one gap detection runs on
and what the other is not valid for. The second is that the session's recovery mechanism and the feed's
recovery mechanism are different things: a resend replays the session's byte stream, while a snapshot rebuilds
state. A consumer needs to know which one answers a gap, and whether a snapshot is even available on this
transport.

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
