# Order-book synchronisation

The snapshot/incremental join, per venue, written out as an algorithm you can implement without inference.
Binance Spot and Binance Futures use different algorithms and the wrong one is the single most-copied incorrect
snippet in the ecosystem. The two durable rules (use your venue's exact algorithm and nothing else, and on any
sequence gap discard the book and re-snapshot rather than patch) hold regardless of what the per-venue detail
says. **The venue-specific facts here are dated (read 2026-08-24) and volatile: re-verify against the venue's
current documentation before relying on any of them.** Reading a price *out* of a synced book, the depth
horizon, the absent-versus-empty distinction and the `max_age` freshness gate are the sibling file
`prices-and-staleness.md`.

## Contents

- The two durable rules, and why "patch the gap" fails silently rather than loudly
- Applying a delta: absolute quantities, `qty == 0` deletes, and the delete-miss that is normal
- Binance Spot: buffer, snapshot, drop, first-event condition, `U`/`u` continuity
- Binance USDⓈ-M Futures: the `pu` field and how the continuity condition differs from Spot
- The two Binance algorithms side by side: the four lines that differ
- OKX: `seqId`/`prevSeqId` validation, and the checksum that is always 0 from 2026-06-23
- Bybit: `u == 1` means a service restart; level-1 re-sends a snapshot with the same `u`
- Kraken: CRC32 over exactly the top 10 levels, on string forms with the decimal point and leading zeros
  stripped; a float-yielding JSON parser destroys it
- Coinbase, Deribit and Hyperliquid book channels and their sequence fields
- Gap detection that keeps working: integrity checks that fail loudly when their field disappears
- Reconnect storms: the connection-rate limiter, jittered backoff, and re-subscribing before the first order
- Test recipes: replaying a captured stream with an injected gap

## The two durable rules

**Rule 1: implement your venue's exact algorithm, per venue and per product.** No cross-venue abstraction is
correct for all of them. Binance Spot and USDⓈ-M Futures have no in-band snapshot at all (you re-fetch REST);
Kraken and Coinbase deliver one in-band; Bybit delivers one in-band and signals a service restart in the same
field it uses for updates. A shared `on_orderbook_message` handler across venues is an anti-pattern; it is the
shape that produces the Spot/Futures cross-copy.

**Rule 2: on any gap, discard the book and re-snapshot. Never patch, interpolate, or "resync the missing
levels".** A gap is any of: a sequence discontinuity, a checksum mismatch, a `pu` / `prevSeqId` /
`prev_change_id` mismatch, or a per-product `sequence_num` that skipped.

Patching is not merely suboptimal; its failure is silent and directional. Depth deltas carry absolute
quantities, so the update that *removes* a level is a message like any other. Lose it and the level stays in
your book forever, at a price inside the real spread. Every subsequent message is well-formed, every remaining
integrity field passes, the book renders normally, and you are quoting inside a spread that does not exist,
adversely selected on every print. Nothing throws. P&L is the only alarm.

**Rule 2's usually-missing third clause: suppress quoting and order submission on that instrument between the
gap and the completed re-snapshot.** Trading on the last known book because "the resync is only 200ms" is
trading on a book you have already proven wrong.

## Applying a delta

```python
# CORRECT: absolute quantity semantics
for price, qty in event["b"]:          # bids; "a" for asks
    if Decimal(qty) == 0:
        book.bids.pop(Decimal(price), None)   # a delete for a level you don't hold is NORMAL
    else:
        book.bids[Decimal(price)] = Decimal(qty)   # SET, not +=
```

Binance states both halves outright: *"The data in each event is the absolute quantity for a price level"* and
*"receiving an event that removes a price level that is not in your local order book can happen and is
normal"* ([Spot: How to manage a local order book correctly](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams)).
`level.qty += delta` is wrong on every venue in this file. A `WARN` on a delete-miss is noise that trains you
to ignore the log where the real gap will eventually appear. Parse prices and quantities with a decimal or
string decoder: on Kraken that is load-bearing for the checksum, everywhere else for the price you send back.

## Binance Spot

Source: [web-socket-streams.md, "How to manage a local order book correctly"](https://developers.binance.com/docs/binance-spot-api-docs/web-socket-streams).
Fields: `U` = first update ID in event, `u` = final update ID in event. Spot has **no `pu`**.

1. Open `wss://stream.binance.com:9443/ws/<symbol>@depth`.
2. Buffer the events. **Note the `U` of the first event you received.**
3. `GET /api/v3/depth?symbol=…&limit=5000`.
4. If the snapshot's `lastUpdateId` **is less than the `U` from step 2**, the snapshot is too old: **go back
   to step 3**. (The branch almost always dropped when the algorithm is retyped from memory.)
5. Discard buffered events with `u <= lastUpdateId`. **The first remaining event must have `lastUpdateId`
   within `[U, u]`.** If it does not, start over from step 1.
6. Set the local book to the snapshot; `local_id = lastUpdateId`.
7. Apply buffered events, then live events, with this update procedure:
   - if `u < local_id` → ignore the event (already incorporated);
   - if `U > local_id + 1` → **you missed events: discard the book and restart from step 1**;
   - in normal operation the next event's `U == local_id + 1`;
   - apply levels with absolute-quantity semantics;
   - `local_id = u`.

```python
def apply_spot(evt, book):
    if evt["u"] < book.local_id:
        return                                   # stale, already applied
    if evt["U"] > book.local_id + 1:
        raise SequenceGap(book.local_id, evt["U"])   # -> discard book, restart at step 1
    apply_levels(book, evt["b"], evt["a"])
    book.local_id = evt["u"]
```

## Binance USDⓈ-M Futures

Source: [USDⓈ-M Futures, "How to manage a local order book correctly"](https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly).
Same `U`/`u`, **plus `pu` = the `u` of the previous event on the stream**.

Two changes, both of which matter:

- **The first-event acceptance condition is different.** The first processed event must satisfy
  `U <= lastUpdateId AND u >= lastUpdateId`: the snapshot ID must fall *inside* the event's range.
- **Every subsequent event must satisfy `pu == previous event's u`, otherwise initialize from step 3.**

```python
def apply_futures(evt, book):
    if evt["pu"] != book.local_id:
        raise SequenceGap(book.local_id, evt["pu"])   # -> re-snapshot from step 3
    apply_levels(book, evt["b"], evt["a"])
    book.local_id = evt["u"]
```

`pu` is not redundant with `U`/`u`. Server-side coalescing can produce a frame whose `[U, u]` looks adjacent to
what you last applied while an intermediate frame was dropped; the ranges are contiguous and the
`U > local_id + 1` test passes. **`pu` is the only field that catches that case.**

## The two Binance algorithms side by side

| | Spot | USDⓈ-M Futures |
|---|---|---|
| Stream | `<symbol>@depth` | `<symbol>@depth` (futures endpoint) |
| Snapshot | `GET /api/v3/depth?limit=5000` | `GET /fapi/v1/depth` |
| `pu` field present | **no** | **yes** |
| Snapshot-too-old test | `lastUpdateId < U` of first buffered event → refetch | documented on the futures page; do not assume identical |
| First-event acceptance | drop `u <= lastUpdateId`; first remaining event must have `lastUpdateId ∈ [U, u]` | `U <= lastUpdateId AND u >= lastUpdateId` |
| Steady-state continuity | `U == local_id + 1` (gap iff `U > local_id + 1`) | **`pu == local_id`** |
| Action on gap | discard book, restart from step 1 | discard book, re-initialize from step 3 |

Mixing them up, both directions: **futures algorithm on Spot** can seat the book on a snapshot older than the
first buffered event, because you never ran the "snapshot is too old, refetch" branch. **Spot algorithm on
Futures** never reads `pu`, so a coalescing-boundary drop is invisible and the book carries a phantom level
indefinitely.

## OKX

**Do not validate `checksum`.** OKX's [checksum deprecation
notice](https://www.okx.com/en-sg/help/okx-order-book-channels-checksum-field-deprecation) states that on the
`books`, `books-l2-tbt` and `books50-l2-tbt` channels *"the checksum field will remain present in push messages
but will always return 0 and must no longer be used for data integrity validation"* (demo environment
2026-06-02, production **2026-06-23**) and directs integrators to *"migrate from checksum to
seqId/prevSeqId"*. `books5` and `bbo-tbt` are unaffected; `books-rpi` never carried one.

Both obvious guard shapes fail, in opposite ways, on the day the field goes to zero:

| Guard as written | Behaviour after 2026-06-23 |
|---|---|
| `if computed != msg["checksum"]: resync()` | resyncs on **every frame**: a permanent resnapshot loop |
| `if msg.get("checksum") and computed != msg["checksum"]: resync()` | **silently stops validating**; no error, no log line |
| `if msg["checksum"] == 0: raise ChecksumFieldRetired(...)` | fails loudly, once, when the contract changed |

The third shape is the rule this venue teaches: **an integrity check must fail loudly when the field it depends
on disappears.** A truthiness guard around a validator is a validator with a scheduled self-destruct.

**Sequence linkage.** Validate `prevSeqId` against the `seqId` you last applied. The operator implementation to
copy is NautilusTrader's OKX adapter: on a `prevSeqId` mismatch, **drop the frame and suppress further updates
until a fresh snapshot arrives with `prevSeqId: -1`**; do not attempt to patch
([nautilustrader.io OKX integration](https://nautilustrader.io/docs/latest/integrations/okx/)).

**Unverified:** whether `seqId` can legitimately decrease, and the full semantics of the `prevSeqId == -1`
sentinel, are *not* established by a primary source here: the OKX WS order-book channel page is a JS-rendered
SPA that could not be fetched. The NautilusTrader behaviour above is operator evidence, not vendor
documentation. Re-read that page before encoding this branch.

## Bybit v5

Source: [Bybit v5 public orderbook WS](https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook).
Messages carry `type: "snapshot" | "delta"`, an update id `u`, and a `seq`.

- **`u == 1` means the service restarted.** It is not update number one in a monotonic run. The message is a
  fresh snapshot and you must **overwrite your local book with it**, not diff against it.
- **For level-1 data, a snapshot is re-emitted with the *same* `u`** if nothing changed for 3 seconds. A
  duplicate `u` is therefore normal traffic, not a replay to be discarded.
- **`seq` is a cross-depth-level comparator**, not the continuity field: *"for the smaller `seq`, the data is
  generated earlier"*. Use it to order messages that arrived from different depth subscriptions; do not build
  gap detection on it.

So `assert u > last_u` (the guard you would write for any other venue) is wrong here in both directions at
once: it **false-positives** on the level-1 3-second re-snapshot and **misses** the restart, the one event that
actually invalidates your book.

```python
def on_bybit(msg, book):
    if msg["type"] == "snapshot" or msg["data"]["u"] == 1:
        book.replace(msg["data"])            # restart or in-band snapshot: overwrite
        book.local_u = msg["data"]["u"]
        return
    u = msg["data"]["u"]
    if u == book.local_u:                    # level-1 idle re-send: no-op, not a gap
        return
    if u != book.local_u + 1:
        raise SequenceGap(book.local_u, u)   # -> discard and resubscribe
    apply_levels(book, msg["data"]["b"], msg["data"]["a"])
    book.local_u = u
```

## Kraken WS v2

Sources: [book channel](https://docs.kraken.com/api/docs/websocket-v2/book) and the [spot WS v2 book checksum
guide](https://docs.kraken.com/api/docs/guides/spot-ws-book-v2/).

**The checksum covers exactly the top 10 levels per side, regardless of the depth you subscribed to**
(10/25/100/500/1000). Subscribe at depth 100 and checksum your whole book and you will mismatch on every single
message. *"Updates should always be processed in sequence."*

The algorithm operates on the **string forms**, not the numbers: take the top 10 asks in **ascending** price
order then the top 10 bids in **descending** price order; for each level take `price` then `qty` as strings and
**remove the decimal point and strip leading zeros**; concatenate asks-then-bids into one string; CRC32 over
the ASCII bytes, cast to **unsigned 32-bit**, compared against the message's `checksum`.

```python
import zlib
from decimal import Decimal

def _tok(s: str) -> str:                      # s arrives as a STRING from the wire
    return s.replace(".", "").lstrip("0")

def kraken_checksum(asks, bids) -> int:
    # asks/bids: lists of (price_str, qty_str) already in the venue's own string form
    parts = []
    for p, q in sorted(asks, key=lambda l: Decimal(l[0]))[:10]:
        parts.append(_tok(p)); parts.append(_tok(q))
    for p, q in sorted(bids, key=lambda l: Decimal(l[0]), reverse=True)[:10]:
        parts.append(_tok(p)); parts.append(_tok(q))
    return zlib.crc32("".join(parts).encode()) & 0xFFFFFFFF
```

The guide says it explicitly: *"Parse `price` and `qty` fields using a decimal or string decoder to preserve
full precision."* A JSON parser that yields `float` destroys the checksum before your code runs, and does so
non-deterministically: most levels round-trip, one in a few thousand does not, and you get a resync loop that
looks like a network problem. In Python: `json.loads(raw, parse_float=Decimal)`; in JS you need a
string-preserving parser, because `JSON.parse` has already lost the digits by the time you see them. Keep the
original wire strings on the level: `str(Decimal("0.0100"))` and the venue's `"0.01000"` tokenize differently.

## Coinbase, Deribit, Hyperliquid

| Venue | Channel | Continuity field | Documented semantics |
|---|---|---|---|
| Coinbase Advanced Trade | `level2` | `sequence_num`, **per product** | Increments by exactly 1. A gap ⇒ messages were dropped. A value lower than the previous ⇒ out of order, may be ignored. The [WS overview](https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/websocket/websocket-overview) states outright: *"even though a WebSocket connection is over TCP, the WebSocket servers receive market data in a manner that can result in dropped messages."* |
| Deribit | `book.{instrument}.{interval}` | `change_id` / `prev_change_id` | The initial `snapshot` message has **no** `prev_change_id`. On every subsequent message `prev_change_id` must equal the last `change_id` you applied; a mismatch means messages were missed ([book subscription docs](https://docs.deribit.com/subscriptions/orderbook/bookinstrument_nameinterval.md)). |
| Hyperliquid | `l2Book` | **unverified** | This research base establishes Hyperliquid's exchange endpoint, nonce window and tick/lot rules but does **not** establish a documented sequence or continuity field on `l2Book`. Read the Hyperliquid WS docs before writing the gap branch; do not assume a `seq`. |

Coinbase's sentence generalises: **TCP does not give you a gapless feed.** The delivery guarantee ends at the
venue's own fan-out, and every venue here ships a continuity field precisely because messages are lost above
the transport.

## Gap detection that keeps working

Three properties, all violated by ordinary-looking code:

1. **The validator must fail loudly when its input field disappears**: the OKX zero-checksum case above. Any
   guard of the form `if <integrity_field>: validate()` degrades to a no-op on a contract change and emits
   nothing. Assert the field's presence and shape separately from validating its value.
2. **The validator must not stop after the first mismatch.** A checksum path that disables itself after N
   consecutive failures (a common "stop the log spam" patch) converts a permanent corruption into a silent one.
   Rate-limit the *log line*, never the *check*, and expose a `book_integrity_failures_total` counter labelled
   by instrument.
3. **Sequence and event time are high-water marks, and every regression is logged.** NautilusTrader applies
   out-of-order deltas so a venue that replays still converges, but never lets `sequence` or `ts_last` regress.
   It carves out L1 books driven by quotes/trades, where a stale `QuoteTick`/`TradeTick` is skipped outright:
   two answers inside one system, justified by book type. Pick yours deliberately and write down which.

## Reconnect storms

The failure sequence: a venue blip disconnects every symbol at once, each task reconnects immediately, the
connection-rate limiter bans the IP, every retry then fails at the transport layer, and the ban extends. Your
book is unsynced for minutes, not the 200ms your code assumed, and if you did not gate order submission on
sync state, you are trading the whole time.

Binance Spot's published limits: **300 connections per 5 minutes per IP**, 1024 streams per connection,
**5 inbound messages/second** per connection (PING, PONG and JSON control messages all count), 24-hour
connection lifetime, 20-second server ping requiring a pong within a minute. Subscribe messages are inbound
messages: a re-subscribe burst across 200 symbols on one connection trips the 5/s cap before the connection
cap. What to build:

- **One global connection-attempt budget per IP**, shared across every symbol task, sized under the venue's cap,
  not a per-task budget, which multiplies by the task count exactly when they all fail together.
- **Jittered exponential backoff**; un-jittered backoff re-synchronises the herd on every subsequent attempt.
- **Batch re-subscription under the inbound message cap**, with the batches themselves rate-limited.
- **Re-subscribe and confirm the subscription before the first order.** A program that places before the socket
  is up misses the first events for that order; one that awaits a watcher which only resolves on an update
  never places at all (ccxt#8245 documents both halves).
- **Plan the 24-hour disconnect**: it is scheduled, so reconnect on your own clock, new connection synced
  before the old one drops.
- **Recognise the venue's escalation codes and stop.** Bybit returns HTTP 403 "access too frequent" and
  documents terminating all sessions and waiting **≥10 minutes**; Deribit's `too_many_requests` (10028)
  **terminates the session**, and with cancel-on-disconnect armed that closure cancels every open order.

## Test recipes

**(1) Replay with an injected gap.** Capture a real stream to a file (raw frames, unparsed; you need the wire
strings). Replay it through your handler, dropping frame *k* in the middle. Assert:

```python
def test_dropped_frame_discards_book(recorded_frames, handler):
    frames = recorded_frames[:K] + recorded_frames[K+1:]     # one frame removed
    for f in frames:
        handler.on_message(f)
    assert handler.book.state is BookState.UNSYNCED     # not SYNCED with a plausible-looking book
    assert handler.resnapshot_calls == 1                # discarded, not patched
    assert handler.book_id_after_gap is None            # local_id was NOT advanced past the gap
    assert not handler.order_gate.is_open("BTCUSDT")    # quoting suppressed until resync completes
```

The assertion that catches the real bug is the last two: plenty of implementations log the gap, increment a
counter, and then apply the frame anyway.

**(2) The venue-mismatch test.** Feed a recorded **futures** stream through the handler with `pu` checking
disabled and assert the resulting book diverges from one built with `pu` checking on. If the two agree, your
capture contains no coalescing boundary; capture a longer one, across a volatile minute. This is the test that
would have caught the most-copied incorrect snippet.

**(3) Kraken checksum against a captured frame.** Assert `kraken_checksum(...) == frame["checksum"]` on a real
recorded message, then re-run the assertion with the frame parsed by a float-yielding parser and assert it
**fails**. The second half is the test: it pins the parser configuration, which is what silently regresses when
someone swaps the JSON library.
