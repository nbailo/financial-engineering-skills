# Nasdaq ITCH, OUCH, MoldUDP64 and print eligibility

> **Provenance**
> provider: Nasdaq, plus FINRA and the CFTC for the eligibility rules around the feed
> surface: message content, volume eligibility, and a transport that numbers messages rather than packets
> version: TotalView-ITCH 5.0 · MoldUDP64 V1.00 · UTP Data Feed Services Specification 4.1, July 2026
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/NQTVITCHspecification.pdf
> · https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf
> · https://www.utpplan.com/DOC/UtpBinaryOutputSpec.pdf
> · https://www.finra.org/rules-guidance/rulebooks/finra-rules/5210
> · https://www.cftc.gov/PressRoom/PressReleases/8369-21
> verified: every sentence in quotation marks below was read in the document cited beside it on 2026-08-25,
> including the MoldUDP64 header and its two magic message counts, the ITCH message-type letters and the
> `Printable` field, the two deliberately-constant fields and their effective dates, the UTP Sale Condition
> Matrix rows quoted, and FINRA Rule 5210 Supplementary Material .02.
> unverified: which midnight an ITCH timestamp counts from (absent from the specification itself, see below);
> the OUCH quantity conventions, which were not re-read in this pass; whether a bona fide self-trade is
> reported to a tape and counted in volume, which is a trade-reporting rule that was not read here; the CFTC
> matter is cited from the
> commission's press release rather than from the order, so only the quoted sentence and the headline figures
> are established; no index or benchmark methodology was read at all.
> revalidate_when: TotalView-ITCH publishes a version above 5.0; the Sale Condition Matrix resolves either of
> the two rows it currently marks TBD (`E` Placeholder, `8` Placeholder For 611 Exempt) or adds a modifier;
> FINRA amends Rule 5210 Supplementary Material .02.

Protocol behaviour for the Nasdaq family: the MoldUDP64 transport that numbers messages rather than packets,
the two control packets that carry sequence and no payload, the retransmission path and its truncation rule,
and the ITCH content rules a publisher has to state in writing. It closes with the question ITCH does not
answer on its own, which is which prints count, and the three separate documents that answer it. Read this
when the repository names any of these protocols or reimplements their shapes. The generic properties they
illustrate are in the skill body; this file is the mechanics.

## Contents

- The MoldUDP64 header, implicit message numbering, and the receiver arithmetic it forces
- Heartbeat and end of session: the two message counts that carry sequence instead of payload
- Session identity, and what a new session id costs
- Retransmission: the re-request server, delivery on the live socket, and truncation
- ITCH content: the whole volume-bearing message set, and why the printable flag is not the filter
- Which prints count: your specification, the tape rulebook and the counterparty rulebook
- Deliberately-constant ITCH fields, their values and their effective dates
- Timestamps: nanoseconds since midnight, and the epoch the specification does not settle
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

Both shapes above are MoldUDP64's and neither is a property of feeds in general. A payload-free packet
carrying the next expected number is available precisely because this transport numbers messages and can send
a packet containing none; a session protocol that numbers every message it sends gives its heartbeat a
sequence number of its own, and a WebSocket or HTTP feed may carry a liveness frame that sits outside the
sequence space entirely. The receiver arithmetic differs in all three. Copy the obligation, which is that
quiet is distinguishable from dead and the interval is published. Check that your transport can carry the
encoding before copying that.

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

## ITCH content: the whole volume-bearing set

A single ITCH stream carries messages belonging to different downstream computations, and the two sets
overlap in only two message types. The volume set is larger than the executions that carry a `Printable`
field, so a filter written around that field alone drops most of the day.

| Message | Type | Book event | Volume |
|---|---|---|---|
| Add Order, with and without MPID attribution | `A`, `F` | yes | no |
| Order Executed | `E` | yes | yes |
| Order Executed With Price | `C` | yes | only when `Printable` is `"Y"` |
| Order Cancel, Delete, Replace | `X`, `D`, `U` | yes | no |
| Trade, non-cross | `P` | no | yes |
| Cross Trade | `Q` | no | yes, the bulk cross volume |
| Broken Trade | `B` | no | removes a quantity already printed |

The specification states the join itself: "By combining the executions from both types of Order Executed
Messages and the Trade Message, it is possible to build a complete view of all non-cross executions that
happen on Nasdaq. Cross execution information is available in one bulk print per symbol via the Cross Trade
Message."

Four consequences a publisher owns.

- **The printable flag is on one message type, not on the feed.** Only Order Executed With Price carries
  `Printable`, described as indicating "if the execution should be reflected on time and sales displays and
  volume calculations". The Order Executed field table in the specification read here carries no such field.
  A volume filter keyed on `Printable` alone therefore discards every execution against a displayed order,
  silently, and the size of what it discards is the size of the displayed book's trading.
- **Non-printable means covered later, not cancelled.** "If the execution is marked as non-printed, it means
  that the shares will be included into a later bulk print (e.g., in the case of cross executions)", and the
  instruction that follows is to ignore those messages "to prevent double counting". The bulk print and the
  executions it covers are the same shares. Summing both is the classic doubling, and it is invisible in a
  quiet test capture because it needs a cross to appear at all.
- **One message, two opposite answers.** Trade messages "should be included in Nasdaq time-and-sales displays
  as well as volume and other market statistics", while "since Trade Messages do not affect the book, however,
  they may be ignored by firms just looking to build and track the Nasdaq execution system display". That is
  the two-filter rule stated by the venue in one sentence, and it is the sentence your own specification has
  to contain an equivalent of.
- **Volume is not monotonic.** A Broken Trade message names the `Match Number` of "a previously transmitted
  Order Executed Message, Order Executed With Price Message, or Trade Message", and the break is final: "once
  a trade is broken, it cannot be reinstated". Anything downstream that only ever adds is wrong from the first
  break of the day. The cross print carries the same asymmetry from the other end, since it "may show the
  shares as zero" where order interest was insufficient to cross.

## Which prints count: three documents, not one

Publishing a print, counting it in an official volume figure, and admitting it to a downstream benchmark are
three decisions taken in three places. A publisher that treats them as one number has already chosen an answer
without recording which one.

**Your own specification** decides what leaves the process and which of those messages you count. On ITCH that
is the message set above plus the `Printable` flag, and nothing else.

**The tape or plan rulebook** decides what the official consolidated record counts, and it does not answer
with one bit. The UTP Data Feed Services Specification carries a Sale Condition Matrix whose columns are
separate answers about the same print: consolidated Update High/Low, consolidated Update Last, market-centre
Update High/Low, market-centre Update Last, and Update Volume. Three rows are enough to show the shape.

| Modifier | Condition | Consolidated high/low, last | Market centre high/low, last | Volume |
|---|---|---|---|---|
| `W` | Average Price Trade | No, No | No, No | Yes |
| `I` | Odd Lot Trade | No, No | No, No | Yes |
| `M` | Market Center Official Close | No, No | Yes, Yes | No |

So "did this trade count" has five answers, not one, and two of these rows count toward volume while moving no
price at all. A publisher reporting a single volume figure is reporting one column of that matrix and owes the
consumer a statement of which.

**The counterparty rulebook** decides whether a match between accounts under one beneficial owner is a
legitimate trade, and the answer is not universally no. FINRA Rule 5210 Supplementary Material .02 states that
"transactions in a security resulting from the unintentional interaction of orders originating from the same
firm that involve no change in the beneficial ownership of the security, ("self-trades") generally are bona
fide transactions for purposes of Rule 5210", and that "transactions resulting from orders that originate
from unrelated algorithms or separate and distinct trading strategies within the same firm would generally be
considered bona fide self-trades". The rule that decides whether such a trade is fictitious answers "generally
not", which is already enough to defeat a universal exclusion. Whether a bona fide self-trade is then reported
and counted is a trade-reporting question in the same rulebook family, and it has to be read rather than
assumed; it was not read in this pass. What is sanctioned is the other case: the CFTC's 19 March 2021 order against Coinbase, 6.5M USD, for false, misleading or inaccurate reporting
and wash trading, where the commission's release records that transactional information of this type "is used
by market participants for price discovery related to trading or owning digital assets, and potentially
resulted in a perceived volume and level of liquidity of digital assets, including Bitcoin, that was false,
misleading, or inaccurate."

Do not implement "self-matches are excluded from volume" as a universal, because it is not one. Name the rule
your venue is subject to, apply it in exactly one place, and publish which set each figure was computed from.
The fourth document, a third-party index or settlement methodology, is one you do not write at all; where your
figure feeds it, your filter errors leave with it.

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

ITCH timestamps are defined as nanoseconds since midnight. The Data Types section of the specification read on
2026-08-25 says exactly "Timestamps are represented as nanoseconds since midnight", and each message's field
table repeats that phrase and adds nothing. Which midnight, in which timezone, and what happens across a
daylight-saving transition are stated nowhere in that document. That is a verified absence in this document
and **not** a claim that the behaviour is undefined: the answer exists outside the specification, and a
consumer who infers it from the data will infer it wrong twice a year.

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
