# The publish check, and the outbox that makes the send at-least-once

A send whose result is discarded is the one failure you needed. Binding it is half the answer; the other half
is that the channel is not the durability mechanism, so what the publisher reads has to be committed state
with a dedupe key the consumer can see. The schema below is an illustration of four properties, not a
prescription.

## The publish check and the outbox

`let _ = tx.send(ev);` discards exactly the failure you need. In Rust, `tokio::sync::mpsc::Sender::send`
returns `Result<(), SendError<T>>`; the error case is *receiver dropped*, i.e. the market-data publisher
thread died and every subsequent execution is being silently swallowed. `try_send` additionally returns
`TrySendError::Full(T)`, i.e. the consumer is slower than the engine and you are about to drop depth updates
under exactly the load where they matter. Go's `ch <- ev` has the mirror shape: a send on a closed channel
panics, and a send on a full unbuffered channel blocks the matching thread. **Neither "it cannot fail" nor "it
is in-process" is true.** Bind the result; on `Err`, halt that transformation at the smallest scope, freezing that
aggregate or failing closed, rather than publishing an incomplete history.

The channel is not the durability mechanism, though. Something committed alongside the state change is, and a
table in the same database is the cheapest version of that. Read the schema below as an illustration of the four
properties beneath it, not as a prescription:

```sql
CREATE TABLE feed_outbox (
  stream       TEXT        NOT NULL,              -- stream/session identity these bytes go out on
  wire_seq     BIGINT      NOT NULL,              -- assigned by the PUBLISHER, persisted before send
  event_seq    BIGINT      NOT NULL,              -- the engine event this was built from: the source
  instrument   TEXT        NOT NULL,
  payload      BYTEA       NOT NULL,              -- the encoded wire message, byte-exact, as sent
  payload_crc  INTEGER     NOT NULL,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),-- observability only; never an ordering key
  published_at TIMESTAMPTZ,                       -- NULL = not KNOWN to have been sent
  PRIMARY KEY (stream, wire_seq),
  UNIQUE (stream, event_seq)                      -- one wire message per event per stream
);
CREATE INDEX ON feed_outbox (stream, wire_seq) WHERE published_at IS NULL;
```

- **`event_seq` is the engine-event sequence, assigned by the core, not by the database.** It records the
  order the core made its decisions and is reproduced by replay from the journal, and it is carried here as
  the SOURCE RECORD this wire message was built from. A `BIGSERIAL`/`SEQUENCE` is not transactional, is not
  derived from the journal, and will not reproduce under replay, so a sequence-generated number makes the
  replay byte-comparison fail for a reason that has nothing to do with matching.
- **`wire_seq` is a different number, assigned by the publisher, and it is PERSISTED BEFORE THE SEND.** The
  engine-event sequence orders decisions; the wire sequence numbers what went out on one stream, and it
  belongs at the publish boundary. Keeping them separate is what lets a feed be re-cut, a second stream be
  added, or a message be withheld from one sink without renumbering the engine's own history. The publisher
  assigns it, encodes the bytes, and commits the row; only then does it send. **Nothing derives a wire
  sequence during recovery.** A number recomputed from outbox order after a crash can differ from the one
  that already went out, if any row was added, withheld or re-cut in between, and then the same content is
  live on the stream under two different sequences.
- **The four things committed before the first byte are the stream identity, the wire sequence, the encoded
  bytes and the engine-event identity.** Together they are what a retry needs: it re-sends THAT stream, THAT
  sequence, THOSE bytes, and can say which engine decision they came from.
- **`payload` is the encoded bytes, not a struct to be re-encoded at send time.** Re-encoding at send time
  re-introduces every determinism hazard of the core on the publish path and makes "byte for byte" unverifiable.
- **The relay is at-least-once and that is the correct design.** The outbox moves the atomicity boundary into
  one transaction; it does not remove duplicates (microservices.io, *Transactional outbox*). A crash between
  `send()` and `UPDATE published_at` republishes, and the retry re-reads the row and retransmits the SAME
  `wire_seq` with byte-identical `payload` on the SAME `stream`. It does not re-encode, re-number, or decide
  afresh which stream the message belongs to; all three were settled before the first send and are on disk.
  So the dedupe key must be **consumer-visible**: the wire sequence the publisher stamped on that stream. Consumers discard an already-processed sequence, the same
  rule A/B feed arbitration already forces on them. The engine-event sequence is not that key, because a
  consumer never sees it.
- **Never renumber on recovery.** A republished execution goes out on its original `stream`, under its
  original `wire_seq`, with the same bytes, and carries its original `ExecID` and match id. Corrections travel as a Trade Cancel referencing the original match number, never as a re-issue.
