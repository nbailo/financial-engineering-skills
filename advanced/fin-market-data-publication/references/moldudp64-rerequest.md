# The MoldUDP64 re-request server, and the truncation rule that decides whether recovery ends

> **Provenance**
> provider: Nasdaq
> surface: the MoldUDP64 transport as a publisher operates it: the unicast re-request path, how a retransmission is
> delivered, and what happens when a response does not fit in a datagram
> version: MoldUDP64 Protocol Specification V 1.00
> verified_at: 2026-08-25
> sources: https://www.nasdaqtrader.com/content/technicalsupport/specifications/dataproducts/moldudp64.pdf
> pinned: the specification was downloaded on 2026-08-25 and read as extracted text, not as a summary.
> verified: the two sentences quoted below were read in that document on 2026-08-25: the one describing a
> retransmitted packet arriving on the multicast processing socket, and the one stating that only the
> messages that completely fit are returned.
> unverified: nothing quoted here. The rate-limit obligation and the store assertions stated below are this
> repository's advice, not the specification's words.
> revalidate_when: MoldUDP64 publishes a version above V 1.00, or the request format or truncation rule
> changes.

One request does not necessarily close one gap, and a retransmission does not arrive on a channel of its
own. Both are publisher obligations rather than consumer details, because a consumer cannot discover either
one from the bytes. Read this when a recovery loop asks for a range, or when a re-request server decides how
much of a range to answer.

## Retransmission

The re-request server answers a unicast request naming a session, a first sequence number and a count. Two
properties of the path change how every consumer must be written, and both are yours to document.

**Retransmissions arrive on the live socket.** The server answers with a normal downstream packet sent unicast
to the requester: "This allows downstream MoldUDP64 users to read the retransmitted Downstream Packet in their
multicast processing socket if the request was made from that socket (in other words, the client need only
have one socket open to listen to the multicast and to process retransmissions, even though the
retransmissions are not multicast)." A consumer therefore sees already-requested ranges interleaved with live
data on one socket. Say so in the specification, or every consumer's first recovery attempt reorders their
book.

**One request does not necessarily close a gap.** Where the response would not fit in a datagram, "only the
number of messages that completely fit will be returned. Additional retransmission requests must be made for
the subsequent messages if they are still desired." A recovery loop that assumes one request per gap stalls
forever at the truncation boundary. Publish the truncation rule, and publish any request rate limit you
enforce: a limit that exists and is undocumented turns a recoverable gap into a silent stall, and the consumer
has no way to tell the two apart.

Both properties are also assertions to run on your own side. The store is keyed on (session, sequence) with a
published depth, and a request for a range you no longer retain gets an explicit answer, never silence.
