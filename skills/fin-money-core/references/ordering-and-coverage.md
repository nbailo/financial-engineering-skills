# Ordering and coverage

Two mechanisms by which a system that never crashes and never logs an error still loses value: an event
applied in an order its sender did not intend, and a durable cursor that advances over a range nobody
actually read. Both are write-side rules, and neither is caught by a test that replays events in the order
they were produced. Read this before you write an event handler, a webhook consumer, a backfill loop, or any
code that persists "how far I have got".

## Contents

- [A pushed event is a notification, not a fact](#a-pushed-event-is-a-notification-not-a-fact)
- [Legality: enumerate the pairs, reject the rest](#legality-enumerate-the-pairs-reject-the-rest)
- [Version: the watermark is the write](#version-the-watermark-is-the-write)
- [Deriving a total order when the source publishes none](#deriving-a-total-order-when-the-source-publishes-none)
- [Re-read from the authority before the value moves](#re-read-from-the-authority-before-the-value-moves)
- [A cursor advances only over a proven range](#a-cursor-advances-only-over-a-proven-range)
- [Holes that look like empty results](#holes-that-look-like-empty-results)
- [The filter set is part of the range](#the-filter-set-is-part-of-the-range)
- [The tests this reference owes](#the-tests-this-reference-owes)

---

## A pushed event is a notification, not a fact

Delivery order is a property of the transport, not of the events. A queue that redrives, a webhook endpoint
that retries, a websocket that reconnects and replays, and a REST backfill that runs beside a live stream all
deliver events out of order and more than once, by design. The handler therefore needs two independent
guards, and both are required: **legality**, which decides whether this event may be applied to this state at
all, and **version**, which decides whether this event is newer than what has already been applied.

Neither guard subsumes the other. A legality check alone applies a stale duplicate of a legal event twice. A
version check alone silently accepts a transition the business does not allow, because a higher version
number is not a statement that the transition is meaningful.

## Legality: enumerate the pairs, reject the rest

Enumerate the legal `(state, event)` pairs and reject everything else with an explicit error. Deny by
default: the fall-through arm is `_ => Err(InvalidStateTransition)`, never a bare `return`, never a log line
at debug level. A silently ignored illegal transition is the same defect as an applied one, because the
system now believes a fact the counterparty never sent and no operator will ever see the divergence.

A terminal state accepts exactly the events by which the counterparty corrects a fact you already booked: a
late fill, a fill void, a chargeback reversal. It is never re-opened by a *status* message. "Cancelled, then
a status update saying Accepted" is the transport reordering two messages, not the venue changing its mind.

nautilus_trader is the worked positive. Its order state machine ships `(Canceled, Filled) => Filled`
annotated `// Real world possibility`, and adds a fifteenth status `Voided` for `(Filled, FillVoided)`, so
the two ways a counterparty can correct a booked economic fact each have an explicit arm. Meanwhile
`(Canceled, Accepted)`, `(Filled, Accepted)`, `(Filled, Canceled)` and `(Rejected, *)` stay absorbing by
hitting the deny-by-default arm rather than by being individually listed. That is the shape to copy: the
corrections are enumerated, the status noise is refused by construction.

## Version: the watermark is the write

The watermark is keyed on the entity id, stored independently of the live object, and **the guard is the
write**:

```sql
UPDATE watermarks SET v = :v WHERE id = :id AND v < :v;
-- proceed only on rowcount 1, in the same transaction as the effect
```

A read-then-compare guard is a time-of-check race that two concurrent redeliveries both pass. Two more
shapes fail for a related reason:

- **A guard that reads the live object.** "If a prior record exists and this event is older, return" is
  skipped entirely once a terminal event deleted or archived the record, so a replayed pre-terminal event
  re-inserts a phantom row carrying an old amount.
- **A guard stored inside the row it protects.** An `UPDATE ... SET version = :v` on the same row as the
  balance cannot protect the row's own creation, and it disappears with the row.

Wall-clock arrival is not a version. Last-write-wins is not a policy: it is the absence of one, written down.

## Deriving a total order when the source publishes none

`>=` is only correct on a total order, so establish one before you rely on it.

| What the source publishes | The watermark |
|---|---|
| a monotonic per-entity version or sequence | that value |
| a global log offset or block height plus an index | the pair, compared lexicographically |
| a coarse timestamp only | the pair `(clock, applied_event_ids)`: an event at the same clock is admitted unless its id is already in the persisted set |
| nothing usable | the source is not orderable; re-read from the authority on every event and treat the payload as a trigger only |

The coarse-clock row is not hypothetical. Stripe's `created` is second-granularity, and `refund.created` and
`refund.updated` for one `re_...` routinely share a second. A bare `>=` there discards the `succeeded`
update, and the refund stays `pending` in your database forever while the customer has the money.

## Re-read from the authority before the value moves

After both guards pass, re-read amount, status and attribution from the authority that owns them before any
value-moving decision. Never act on the pushed payload's state. The payload tells you *that* something
changed and *which* entity changed; it does not prove *what the entity is now*, because by the time you
handle it the entity may have moved again, and because a replayed old payload carries an old amount that
still passes a legality check.

The re-read is also what makes the handler idempotent in the useful sense: applying the same notification
twice converges on the authority's current value instead of applying a delta twice.

## A cursor advances only over a proven range

A durable cursor, watermark or high-water mark advances only over a range whose completeness was established,
inside the same conditional and the same transaction that applied that range's effects.

```
claim range -> fetch -> is coverage proven?
   yes -> apply effects and advance the cursor, one transaction
   no  -> leave the cursor; the range stays claimable
branch that skips the work -> skips the advance
```

The failure mode is permanent silent under-crediting. Value vanishes with no exception, no alert and no log
line, because the only record that the range was ever owed to anyone was the cursor itself, and the cursor
now says the range is done. Nothing downstream can reconstruct what was skipped.

## Holes that look like empty results

Four results are holes, and each of them arrives looking like "there was nothing there".

| What came back | Why it is a hole |
|---|---|
| an error or a timeout on the fetch | you covered nothing, and the range is unread |
| a provider range rejection (block range too wide, window too long, "query returned more than N results") | the provider refused the range; it did not report it empty |
| a result count sitting exactly at the documented page cap | the cap truncated the answer; there is always at least one more page to prove otherwise |
| a truncated or cursor-terminated page with a continuation token you did not follow | the remainder is unread |

The one that reaches production most often is a branch that skips the work and commits progress anyway: the
query is guarded by `if (addresses.size > 0)` while `saveCursor()` sits outside the guard. On a fresh deploy
with no registered addresses the cursor sprints to the chain head, and every address registered afterwards
can never see a deposit that landed in a passed block. The deposit is on chain, confirmed, and invisible.

## The filter set is part of the range

A filter set (an address list, a subscription list, a set of account ids) loaded once per outer iteration
while the inner loop advances the cursor makes every entry added mid-loop unreadable for the whole span the
loop covers. Re-read the filter set at the same cadence as the cursor advances, or make the cursor per
filter-set-version so that adding an entry replays the range for that entry.

Same rule, stated generally: anything the query depends on for completeness is part of the coverage claim. If
it can change between the claim and the advance, the claim is not proven.

## The tests this reference owes

1. **Permutation.** For a fixed set of events on one entity, every arrival permutation converges to the same
   final state, and each illegal permutation raises rather than silently returning.
2. **Redelivery.** Applying any event twice, including the terminal one, produces exactly one economic
   effect, asserted on the balance and on the count of postings, not on a return value.
3. **Same-clock pair.** Two events sharing a coarse timestamp both apply, in either order, and neither is
   discarded by the version guard.
4. **The hole.** Inject each of the four hole shapes into the fetch and assert the cursor did not move, then
   re-run and assert the range is covered exactly once.
5. **The empty branch.** With an empty filter set, assert the cursor is unchanged after a full loop
   iteration, which is the regression test for the `saveCursor()` shape above.
