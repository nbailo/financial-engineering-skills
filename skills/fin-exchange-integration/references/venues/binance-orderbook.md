# Binance order book: the two join algorithms

> **Provenance**
> provider: Binance · surface: the two local order book join procedures, spot `@depth` with `GET /api/v3/depth` and USDⓈ-M with the `pu` continuity field
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: not established
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: none in this pass. No sentence below was re-read against a source for the 2026-08-25 review pass.
> unverified: all of it, including the dating in the header above. This file predates the provenance requirement and was not re-checked in the 2026-08-25 review pass, so its claims carry the confidence of their original sourcing and no more, with no date you can check. The step numbering below is a restatement of the venue's own procedure and a restatement can drift from its source silently, which is exactly the risk a missing date leaves open. The URLs above are where a recheck starts; each of them resolved on 2026-08-25, and nothing in any of them was read against a claim in this file.
> revalidate_when: either venue edits its "How to manage a local order book correctly" procedure; the futures diff stream stops carrying `pu`, or spot starts carrying it; the documented depth snapshot limit changes.

The two different order-book join algorithms, written out as steps. Spot and Futures are not the same, and
using the spot procedure on futures is the single most-copied incorrect snippet in this ecosystem.
Facts are dated to the docs revision read (spot repo HEAD "Last Updated: 2026-07-27", derivatives 2026-08-24),
so re-verify before keying production behaviour on one.

## Contents

- [Order book: the Spot algorithm](#order-book-the-spot-algorithm)
- [Order book: the Futures algorithm](#order-book-the-futures-algorithm): the `pu` continuity check

## Order book: the Spot algorithm

`web-socket-streams.md`, "How to manage a local order book correctly". Do not paraphrase; these are the steps.

1. Open a stream to `wss://stream.binance.com:9443/ws/<symbol>@depth`.
2. Buffer the events you receive. Note the `U` of the **first** event received.
3. `GET /api/v3/depth?symbol=<SYMBOL>&limit=5000`.
4. If the snapshot's `lastUpdateId` is **strictly less than** the `U` from step 2, the snapshot is too old:
   **go back to step 3**.
5. Discard every buffered event with `u <= lastUpdateId`. The first remaining event must satisfy
   `U <= lastUpdateId+1` and `u >= lastUpdateId+1`, i.e. `lastUpdateId` falls inside `[U-1, u]`.
6. Set your local book to the snapshot. Set `localId = lastUpdateId`.
7. Apply the update procedure to the buffered events, then to live events:
   - if `u < localId` → **ignore** the event (it predates the snapshot);
   - if `U > localId + 1` → **you missed events. Discard the book and restart from step 1.**
   - otherwise `U` of each event equals `u + 1` of the previous;
   - per level: absent locally ⇒ insert; `qty == 0` ⇒ delete; else **set** (the payload is the absolute
     quantity at that price, never a delta);
   - `localId = u`.

Spot depth events carry **no `pu` field**. A snapshot is depth-limited (5000 per side), so levels outside it
are *unknown*, not empty: *"you won't learn the quantities for the levels outside of the initial snapshot
unless they change."* Sizing a large order against `sum(local_book)` over-states available liquidity.

## Order book: the Futures algorithm

USDⓈ-M "How to manage a local order book correctly". **The acceptance rule and the continuity rule are both
different from spot.** Using the spot procedure here is the single most-copied incorrect snippet in this
ecosystem.

1. Open a stream to `wss://fstream.binance.com/stream?streams=<symbol>@depth`.
2. Buffer the events you receive.
3. `GET /fapi/v1/depth?symbol=<SYMBOL>&limit=<venue max>`.
4. Drop any buffered event with `u < lastUpdateId`.
5. The **first event you process** must satisfy `U <= lastUpdateId` **AND** `u >= lastUpdateId`. (Spot's rule
   is `lastUpdateId` inside `[U-1, u]` after discarding `u <= lastUpdateId`, not the same predicate.) If no
   buffered event satisfies it, go back to step 3.
6. Set the book to the snapshot.
7. For **every subsequent event**: `pu` must equal the **previous event's `u`**. On mismatch, the stream has a
   hole: **discard the book and re-initialise from step 3.**
8. Level semantics are identical to spot: `qty == 0` ⇒ delete, otherwise set the absolute quantity. *"Receiving
   an event that removes a price level that is not in your local order book can happen and is normal"*; do not
   warn-spam on it.

Why `pu` is not optional: server-side coalescing can emit a frame whose `[U, u]` range still looks adjacent to
your `localId` while an intermediate frame was dropped, and `pu` is the only field that catches it. The book
then carries a phantom level forever (the delete was in the lost frame) and the strategy quotes into a
spread that does not exist, posting inside the real book and adversely selected on every print. On any gap on
either venue: **discard and re-snapshot. Never patch, never interpolate, never "fill in the missing levels."**
Suppress quoting on that instrument between the gap and the completed resync.
