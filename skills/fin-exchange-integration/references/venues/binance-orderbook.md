# Binance order book: the two join algorithms

> **Provenance**
> provider: Binance · surface: the two local order book join procedures, spot `@depth` with `GET /api/v3/depth` and USDⓈ-M with the `pu` continuity field
> version: as stated in this file's own header, the spot documentation repository at "Last Updated: 2026-07-27" and the derivatives documentation read 2026-08-24. Neither dating was re-checked here.
> verified_at: 2026-08-26, for the two join procedures only
> sources: https://github.com/binance/binance-spot-api-docs · https://developers.binance.com/docs/binance-spot-api-docs · https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
> verified: the spot join procedure, read verbatim from binance/binance-spot-api-docs at commit `976cc580553890e92031b77306147c0ed1de5a46`, section "How to manage a local order book correctly". The step-5 acceptance condition is *"discard any event where `u` is <= `lastUpdateId` of the snapshot. The first buffered event should now have `lastUpdateId` within its `[U;u]` range"*. The string `lastUpdateId+1` does not appear in that repository, but it IS the live condition in binance/binance-toolbox-python at commit `51547845a9e3725b98e5a1bc55d4895c69ca0ca2`. Both are Binance-published and neither is marked deprecated, so this file documents the contradiction rather than calling either one superseded. The USDⓈ-M procedure and its `pu` continuity rule were read from the derivatives documentation on 2026-08-26 and match what is written here.
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
5. Discard every buffered event with `u <= lastUpdateId`. Accept the first remaining event when
   **`U <= lastUpdateId + 1`**, which is the no-gap condition. See the note below: Binance publishes
   two different predicates for this step and they disagree at exactly one value.
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

### Two published predicates for step 5, and which value separates them

Binance publishes both of these, and they are not the same test. Neither is marked deprecated on
either source, so neither is called superseded here.

| Source | Step-5 condition |
| --- | --- |
| Spot prose, `binance/binance-spot-api-docs` at `976cc580553890e92031b77306147c0ed1de5a46` | *"discard any event where `u` is <= `lastUpdateId` of the snapshot. The first buffered event should now have `lastUpdateId` within its `[U;u]` range"* |
| `binance/binance-toolbox-python` at `51547845a9e3725b98e5a1bc55d4895c69ca0ca2`, `manage_local_order_book.py` | `if json_data['U'] <= last_update_id + 1 <= json_data['u']:` |

The discard in step 5 already removes every event with `u <= lastUpdateId`, so `u >= lastUpdateId + 1`
holds for everything that survives it and the `u` half of both conditions is satisfied by
construction. What is left is the `U` half, and there the two differ by exactly one value:

| First surviving event | Prose: `U <= L` | Toolbox: `U <= L + 1` | What it is |
| --- | --- | --- | --- |
| `U <= L`, `u > L` | accept | accept | the event straddles the snapshot; overlap is fine |
| `U == L + 1` | **reject** | **accept** | the next event exactly, no overlap and no hole |
| `U > L + 1` | reject | reject | a real gap; sequences between `L` and `U` were never delivered |
| `u <= L` | discarded at step 5 | discarded at step 5 | stale; entirely inside the snapshot |

`L` is the snapshot's `lastUpdateId`. The row that matters is the middle one. `U == L + 1` is a
perfectly contiguous stream, and rejecting it sends a correct client back to step 3 to re-snapshot,
possibly forever on a quiet book. **Use `U <= L + 1`**: the property the join actually needs is that
no update between the snapshot and the first applied event went missing, and that is what this tests.

Neither source is safe to assume is the current one. Re-read both before trusting either, and note
that the toolbox is executable and the prose is not, so the toolbox is the easier of the two to check.

## Order book: the Futures algorithm

USDⓈ-M "How to manage a local order book correctly". **The acceptance rule and the continuity rule are both
different from spot.** Using the spot procedure here is the single most-copied incorrect snippet in this
ecosystem.

1. Open a stream to `wss://fstream.binance.com/public/stream?streams=<symbol>@depth`, the URL given in
   the USDⓈ-M page cited in the provenance block (read 2026-08-27).
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
