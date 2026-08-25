# Reconnect, resync and backfill

Your absence is the venue's problem to notice; your return is yours. A process that dies cannot cancel
anything, so the only switch that fires because you went away is the one the venue holds. Coming back is the
harder half: re-subscribing restores the connection, not the range of events that happened while you were
gone. This file carries the session-recovery mechanics: the dead-man switch, the local invalidation path that
is a different failure, the ready gate, materialising the missed range, cursor durability, and the pagination
rule that turns a truncated page into a detected hole rather than an apparent end.

## Contents

- The dead-man switch you do not hold: venue-native cancel-on-disconnect and its timeout
- Local invalidation is a different failure: `cancel_all()` inside the function that invalidates state
- The ready gate: `mark_ready()` after `on_resync`, and order submission blocked until it returns
- Computing the missed range and materialising it as synthetic events
- Cursor durability: `last_trade_id` and `last_update_id` outlive the process
- Pagination: a page at the documented cap is a hole, not the end
- Session credentials that expire while you are connected: `listenKey` and friends
- Test recipe

## The dead-man switch you do not hold

Arm the venue's own cancel-on-disconnect at session start, with a timeout **shorter than your reconnect
backoff**. If the timeout is longer than the backoff, the switch never fires during the outage it exists for,
because you reconnect first and the venue never observes a gap long enough to act on.

| Venue | Mechanism |
|---|---|
| Binance | cancel-on-disconnect on the supported order-entry sessions |
| Bybit | cancel-on-disconnect |
| OKX | cancel-on-disconnect |
| Deribit | `set_heartbeat`, which is an order-cancellation mechanism, not a liveness ping |
| FIX | `CancelOnDisconnect` on the session |

Deribit's heartbeat deserves its own line: it is documented as an order-cancellation mechanism, so failing to
respond to it cancels resting orders. That is the desired behaviour when you are dead and the wrong behaviour
when you are merely slow, and the timeout has to be chosen with both in mind.

## Local invalidation is a different failure

There is a second, unrelated failure: you are still connected, and your own local state went bad. The venue's
switch cannot help, because from the venue's side nothing is wrong.

The response is a local `cancel_all()`, and it belongs **inside the function that invalidates state**, not
beside it in the caller:

```
invalidate_state():
    result = cancel_all(scope)
    check(result)            # an unchecked return value is not a cancel
    mark_unsynced(scope)
```

An unchecked return is the common defect: `cancel_all` returns a per-order result set, some of which can fail,
and discarding it leaves orders resting that the code believes are gone. Under *implemented, not described*, a
defined-but-uncalled `on_stale` handler, a `cancel_all` behind a config flag defaulting off, and a comment
deferring the decision to "whoever owns risk" are each the defect itself rather than a plan for it.

## The ready gate

Reconnection has an ordering requirement that is the whole failure when it is wrong:

```
1. reconnect
2. subscribe, and confirm the subscription
3. snapshot the authority
4. on_resync: diff the snapshot against persisted state, emit the missed events, persist the cursor
5. mark_ready()          <- only now
6. order submission is unblocked
```

`mark_ready()` is called **after** `on_resync` completes, never before, and the send path is blocked until it
returns. Calling it at step 2, when the socket is up, is the natural mistake: the connection is healthy, the
strategy resumes, and it makes decisions from a position that is missing every fill received during the
outage.

## Computing the missed range and materialising it

Re-subscribing restores the stream from now. It does not deliver what happened while you were gone. That range
has to be computed and **materialised as events**, not skipped:

- Diff the venue's snapshot against your persisted state, and emit a **synthetic missed-fill event** for each
  difference. Recovering net position alone leaves realized PnL permanently short, because realized PnL is a
  function of the individual fills, not of the net.
- Synthesised ids must be **deterministic over venue-supplied fields including a venue timestamp**, so the
  same inference made again after a restart dedupes against itself instead of double-booking.
- A sequence gap in the stream is discarded and re-snapshotted, never patched, and the re-snapshot uses your
  venue's own snapshot/incremental join algorithm: Binance Spot and Binance USDⓈ-M Futures do not share one,
  and the wrong algorithm is the most-copied incorrect snippet in the ecosystem.

## Cursor durability

`last_trade_id` and `last_update_id` are persisted **to disk or to Redis**, not held in memory. Held in
memory, every cold start begins from a null cursor, and the usual shape of the code is a `continue` that skips
the reconciliation entirely: no error, no log line, and a permanently under-credited position.

The cursor advances **only inside the same conditional and the same transaction that covered the range**. A
branch that skips the work skips the advance. An error, a provider range rejection, a result count at the
documented cap, or a truncated page is a hole rather than an empty result.

## Pagination

The backfill loops until the venue returns **fewer rows than the page size**. One unpaginated call silently
truncates the gap, and the truncation looks identical to a complete answer:

```
rows = []
while True:
    page = fetch(cursor, limit=PAGE)
    rows += page
    if len(page) < PAGE:       # the ONLY safe terminator
        break
    cursor = advance(page)
```

`len(page) == PAGE` is the cap condition, and it means there is more, always, even when there is not: one
extra request is the cost of never mistaking a cap for an end. Venue-specific retention bounds decide how
far back the backfill can reach at all, so read your venue's documented recovery-endpoint window before
assuming an outage longer than it is recoverable from the venue rather than from your own store.

## Session credentials that expire while you are connected

Binance's `listenKey` has a **60-minute lifetime** and must be renewed while the user-data stream is open. A
lapsed key does not close the socket loudly; the stream stops delivering, and the bot holds a blind position
against a book it believes is current. Treat the renewal as part of the session state machine, and treat a
failed renewal as a resync trigger rather than a retryable background error.

The general form: any credential, token or subscription with a lifetime shorter than your intended session is
a scheduled outage. Handle it on your own clock, before it fires.

## Test recipe

Kill the process mid-session with resting orders and an in-flight instruction, restart it, and assert four
things: the venue-side switch fired or the resting orders are accounted for; no order is submitted before
`mark_ready()` returns; the fills that occurred during the outage appear individually in the persisted set,
not merely as a net position; and running the same recovery twice produces byte-identical state, because the
synthesised ids dedupe against themselves.
