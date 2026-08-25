# MoldUDP64: the header, the two control packets, and session identity

> **Provenance**
> provider: Nasdaq
> surface: the MoldUDP64 transport as a publisher operates it: the downstream header, the two control packets that
> carry sequence instead of payload, and what a session identifier scopes
> version: MoldUDP64 Protocol Specification V 1.00
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf
> pinned: the specification was downloaded on 2026-08-25 and read as extracted text, not as a summary.
> verified: read in that document on 2026-08-25 and quoted below: the header and its three fields, the
> implicit numbering sentence, the two magic message counts stated in one sentence, the heartbeat sentence
> and the end-of-session sentence.
> unverified: nothing quoted here. The session-identity rules under those sentences are this repository's
> reading of what the quoted contract costs a publisher, not Nasdaq's words.
> revalidate_when: MoldUDP64 publishes a version above V 1.00, or the header, the magic counts or the session
> identifier change meaning.

A transport that numbers messages rather than packets, and the receiver arithmetic that follows from it.
Read this when the repository names MoldUDP64 or SoupBinTCP, or rotates a session identifier. The re-request
path is in this skill's retransmission reference.

## The MoldUDP64 header

MoldUDP64 carries a 20-byte header on every downstream packet: a 10-byte `Session` identifier, an 8-byte
`SequenceNumber` and a 2-byte `MessageCount`. The sequence number is that of the **first** message in the
packet: "If there is more than one message contained in a packet, any messages following the first message are
implicitly numbered sequentially."

The receiver arithmetic is therefore `expected += message_count`, not `expected += 1`. That is the most
consequential fact about the transport, and it is a publisher obligation rather than a consumer detail. A
consumer incrementing once per datagram is correct while every packet carries one message and drifts the
instant you batch, which is what you do under load. Batching is published behaviour, not an internal
optimisation, and any specification on top of MoldUDP64 has to say the sequence is per message.

| Scope | What the header carries | Receiver arithmetic | Reset event |
|---|---|---|---|
| Per message, per session | `Session[10]`, `SequenceNumber[8]`, `MessageCount[2]`; the sequence is that of the first message, the rest implicit | `expected += message_count` | a new 10-byte session id |

## Heartbeat and end of session

`MessageCount` has two magic values, and the specification states both in one sentence: "Note that a Message
Count of zero denotes a heartbeat and that a Message Count of 0xFFFF(hex, or 65535 in decimal) denotes end of
session." Both packets carry the next expected sequence number and no payload.

| `MessageCount` | Meaning | Publisher duty |
|---|---|---|
| `0` | Heartbeat | "Heartbeats are sent periodically by the server so receivers can sense packet loss even during times of low traffic. Typically, these packets are transmitted once per second and contain the next expected Sequence Number." |
| `0xFFFF` | End of Session | Keep emitting while the session drains: "While the End of Session messages persist, re-requests may be made on the current session. This is the last chance to ensure that all messages have been received." |

The heartbeat is the only thing distinguishing "nothing is trading" from "your multicast group stopped
delivering", and a consumer with no heartbeat always guesses the first. Publish the interval, because every
consumer's liveness timeout derives from it: tighten it later without a version bump and you have changed the
false-positive rate of every consumer at once.

## Session identity

An end-of-session packet is a commitment that the sequence is closed. Do not emit it and then send further
messages under the same session id; mint a new one. The session id scopes retransmission, since a Request
Packet names a session, a first sequence number and a count, so reusing an id across a logical break strands
the ranges a consumer is still requesting, and minting a new one leaves everything unrecovered addressable
only under the old id. That is why a session restart is the recovery mechanism of last resort rather than a
routine response to trouble.
