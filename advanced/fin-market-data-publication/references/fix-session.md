# FIX session sequencing: the counter, the resend, and the reset defined as lossy

> **Provenance**
> provider: FIX Trading Community for the session layer, read through the OnixS FIX 4.4 dictionary
> surface: `MsgSeqNum`, and Sequence Reset in gap fill and in reset mode
> version: FIX 4.4 session layer as rendered by the OnixS dictionary on 2026-08-25. No FIX errata level is
> recorded, because the dictionary pages read here do not state one.
> verified_at: 2026-08-25
> sources: https://www.onixs.biz/fix-dictionary/4.4/msgType_4_4.html
> · https://www.fixtrading.org/standards/
> verified: the two Sequence Reset sentences quoted below were read on the OnixS page listed above on
> 2026-08-25.
> unverified: the OnixS dictionary is a widely used secondary rendering of FIX 4.4, not the FIX Trading
> Community's own document, so what is established is that the dictionary carries these words, not that they
> are word-for-word the committee's; fixtrading.org was not opened for these sentences. The publisher
> obligations listed under each quotation are this repository's advice about seams the protocol leaves open.
> revalidate_when: your counterparty moves above FIX 4.4; a venue you publish to disables Resend Request; the
> specification changes what a Sequence Reset in reset mode may do, or what a gap fill must carry.

Sequencing for a feed that rides a FIX session rather than a multicast transport. The session layer, not the
application message, owns identity, ordering and gap recovery here, so what you publish is largely a set of
decisions about how you use the session protocol. Read this when the repository names any construct below,
including on the order-entry side, because the session semantics are shared.

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
| per session, per message | message header, tag `34` | one increment per message; a gap triggers ResendRequest | `ResetSeqNumFlag` on Logon, or SequenceReset |

## Gap fill, and the reset that is defined as lossy

`SequenceReset` has two modes and they are not interchangeable. With `GapFillFlag` set it is a statement that
the skipped range contained only administrative or superseded messages, and the receiver advances its expected
sequence without loss of application content. Without it, the message is a plain sequence reset, and the
dictionary read here is unusually direct about what that means: "Sequence Reset - Reset should ONLY be used to
recover from a disaster situation which cannot be recovered via the use of Sequence Reset - Gap Fill. Note
that the use of Sequence Reset - Reset may result in the possibility of lost messages." And, separately:
"Sequence Reset - Reset should NOT be used as a normal response to a Resend Request (use Sequence Reset - Gap
Fill mode)."

Treat that as a rule about your own publisher, not as advice to consumers. Answering a resend with a plain
reset is the FIX-session equivalent of minting a new session identity on a multicast feed: it closes the gap
by declaring the content unrecoverable, and every consumer that had a position derived from the skipped range
now has one derived from nothing. It belongs in a runbook for an unrecoverable store, with an alert, never in
the normal resend path.
