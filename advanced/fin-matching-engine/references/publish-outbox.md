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
  seq          BIGINT      PRIMARY KEY,           -- the core's sequence number, NOT a bigserial
  instrument   TEXT        NOT NULL,
  payload      BYTEA       NOT NULL,              -- the encoded wire message, byte-exact
  payload_crc  INTEGER     NOT NULL,
  committed_at TIMESTAMPTZ NOT NULL DEFAULT now(),-- observability only; never an ordering key
  published_at TIMESTAMPTZ                        -- NULL = not yet sent
);
CREATE INDEX ON feed_outbox (seq) WHERE published_at IS NULL;
```

- **`seq` is assigned by the core, not by the database.** A `BIGSERIAL`/`SEQUENCE` is not transactional, is not
  derived from the journal, and will not reproduce under replay, so a sequence-generated feed number makes the
  replay byte-comparison fail for a reason that has nothing to do with matching.
- **`payload` is the encoded bytes, not a struct to be re-encoded at send time.** Re-encoding at send time
  re-introduces every determinism hazard of the core on the publish path and makes "byte for byte" unverifiable.
- **The relay is at-least-once and that is the correct design.** The outbox moves the atomicity boundary into
  one transaction; it does not remove duplicates (microservices.io, *Transactional outbox*). A crash between
  `send()` and `UPDATE published_at` republishes. So the dedupe key must be **consumer-visible**: the feed
  sequence number itself. Consumers discard an already-processed sequence, the same rule A/B feed arbitration
  already forces on them.
- **Never renumber on recovery.** A republished execution carries its original `seq`, `ExecID` and match
  number. Corrections travel as a Trade Cancel referencing the original match number, never as a re-issue.
