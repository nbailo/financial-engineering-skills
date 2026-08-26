# Binance order book: the two join algorithms

> **Provenance**
> provider: Binance · surface: the two local order book join procedures, spot `@depth` with `GET /api/v3/depth` and USDⓈ-M with the `pu` continuity field
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: 2026-08-26, for the two join procedures only
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: the spot join procedure, read verbatim from binance/binance-spot-api-docs at commit `976cc580553890e92031b77306147c0ed1de5a46`, section "How to manage a local order book correctly". The step-5 acceptance condition is *"discard any event where `u` is <= `lastUpdateId` of the snapshot. The first buffered event should now have `lastUpdateId` within its `[U;u]` range"*. The string `lastUpdateId+1` does not appear anywhere in that repository, so the `+1` form this file previously carried came from a superseded revision and was corrected. The USDⓈ-M procedure and its `pu` continuity rule were read from the derivatives documentation on 2026-08-26 and match what is written here.
> unverified: everything outside the two join procedures, including the snapshot depth limits, the rate-limit weights and the dating in the header above. Those were not re-read. The drift corrected here is the reason to distrust the rest: a restatement of a venue procedure can go stale silently while still reading as though someone checked it.
> revalidate_when: either venue edits its "How to manage a local order book correctly" procedure; the futures diff stream stops carrying `pu`, or spot starts carrying it; the documented depth snapshot limit changes.

The two different order-book join algorithms, written out as steps. Spot and Futures are not the same.

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
5. Discard every buffered event with `u <= lastUpdateId`. The first remaining event must then have
   `lastUpdateId` **within its `[U;u]` range**. Binance's wording, read at the pinned commit below:
   *"In the buffered events, discard any event where `u` is <= `lastUpdateId` of the snapshot. The
   first buffered event should now have `lastUpdateId` within its `[U;u]` range."* An older revision
   of this page stated the condition as `U <= lastUpdateId+1 AND u >= lastUpdateId+1`; that form is
   no longer in the documentation, and it is not the same predicate.
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
5. The **first event you process** must satisfy `U <= lastUpdateId` **AND** `u >= lastUpdateId`. Futures
   drops `u < lastUpdateId` where spot drops `u <= lastUpdateId`, so the surviving first event differs by
   one event at the boundary. If no buffered event satisfies it, go back to step 3.
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
