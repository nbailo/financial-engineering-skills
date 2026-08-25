---
name: fin-matching-engine
description: >-
  BETA, opt-in. Financial correctness for code that owns an order book and mints the executions others
  book: durable ordered input, deterministic replay, order-state transitions, allocation conservation and
  residue, priority and iceberg refresh, auctions, self-match prevention, checked aggregates, fan-out
  bounds, halt and resume, single-writer recovery. Use when you are the venue; to call one, use
  fin-exchange-integration.
license: MIT
---

# You own the book

**BETA.** Opt-in, not installed with the six skills under `skills/`, and the least exercised material here.
Every venue-specific answer in the references is an example of a published rule, never the answer for yours.

This code **is** the record: it crosses resting orders, mints the execution, assigns priority and sequence.
Nothing outside can tell you that you are wrong, so the proof burden moves before deployment, into replay,
determinism and conservation. The usual pair is `authority: SELF · exposure: record`, and one question
governs every change: if this process dies now, does the persisted record reproduce the identical emitted
sequence, byte for byte, under the logic that produced it?

## When to use

Your process assigns order-book state no counterparty can independently verify: it matches an aggressing
order against resting orders, allocates a fill across them, decides who holds queue position, computes an
auction price, or mints an identifier other systems consume because you minted it. Suggested, never defined,
by `order_book`, `price_level`, `leaves_qty` as structures **this repo owns**; by `match`, `uncross`,
`allocate`, `pro_rata`, `iceberg`, `imbalance`; by a gate that **rejects** somebody else's order; by `ExecID`
or a sequence number **you mint**.

## When not to

- Calls `create_order`, `ccxt`, a venue SDK or a FIX client session, or reconciles against somebody else's
  fills: the client side, and `fin-exchange-integration` owns it.
- Publishes the feed rather than computing the book, so packet sequencing, snapshot joins, gap detection,
  A/B arbitration, conflation: `fin-market-data-publication`.
- Also writes authoritative balances or postings: `fin-ledger`. Amount arithmetic, rounding, operation
  identity or retry classification alone: `fin-money-core`.
- Reads recorded payloads and assigns nothing: a backtest, a volume query that republishes nothing.

Both a venue and a client of another venue is two changes: split it and report `authority` and `exposure` per
half, because a merged declaration inherits the weaker obligation of the client half.

## Workflow

1. Split into the venue half and the client half; report `authority` and `exposure` for each.
2. Name the authoritative state you assign and the durable inputs it must be reproducible from.
3. Make the input durable and ordered before that state changes, under exactly one writer.
4. Establish determinism, mint every identifier inside the core, and decide whether recovery loads persisted
   decisions or replays a pinned reducer under the configuration then in force.
5. Enumerate the legal transitions, refuse the rest, name the rulebook behind each venue-specific answer.
6. Bound every emission with a counter keyed to the inbound unit, checked before the send.
7. Separate the exposure buckets, decide halt scope and the fate of in-flight executions, prove replay.

## Invariants

One line each. Every one is worked through, evidenced, and tied to the fin-money-core rule it specialises, in
the reference its trigger row names.

1. Durable ordered command first; state change and the obligations it creates commit as one step; the
   publisher reads committed state; one writer extends the record.
2. Recovery reproduces what this engine **decided**: persisted immutable decisions, or replay under a
   journaled reducer identity and the configuration then in force.
3. Determinism is demonstrated by a seeded replay that byte-compares the emitted sequence, never asserted.
4. Five identifiers, five counters, none derived from another: command, match, execution, session, feed.
5. Only enumerated `(state, event)` pairs are legal, on state re-read from the committed store, and every
   other pair is refused with a typed error the engine counts.
6. Five answers are your rulebook's alone: execution price, the priority-destroying edit set, iceberg refresh
   eligibility, the auction tie-break, self-match scope and strategy.
7. One band derivation, called from every session state, against **that instrument's own** reference price;
   a missing price rejects and a sentinel is never multiplied.
8. Working-order, filled-position and settlement exposure are three named buckets, and a fill **transfers**
   between the first two rather than incrementing both.
9. Allocation is unfinished until the residue has an owner, and `Σ allocations == min(aggressing qty, Σ
   resting qty)` is asserted before an execution is emitted.
10. An aggregate you publish is checked in the build you ship, and a saturation is itself emitted.
11. A fan-out bound sits on the emit path, keyed to the inbound unit, with an aggregate companion for every
    per-item limit.
12. An auction prices a finite input set: a cutoff, or a bounded batch whose fallback is the cutoff.
13. Halt names an incident gate, a published market state or quiescence, and the code says which. Severing
    the transport is none of them.

## References

A literal below appears in the code, the repo or the task text → **read that file and apply it. Do not
summarise it.** One hop: each answers its own question and points at no other.

- [pro-rata-residue.md](references/pro-rata-residue.md): pro_rata, allocate, residue, floor division
- [leftover-pass.md](references/leftover-pass.md): leftover pass, one extra lot, assign_residue, largest remainder
- [allocation-pipeline.md](references/allocation-pipeline.md): PIPELINE, algo ==, algorithm steps, configured per product
- [allocator-tests.md](references/allocator-tests.md): @given, hypothesis, fuzz, generator coverage
- [rulebook-answers.md](references/rulebook-answers.md): which answer is ours, filed rule text, no neutral default
- [execution-price.md](references/execution-price.md): which price prints, trade_price, improvement, midpoint
- [priority-preservation.md](references/priority-preservation.md): time_priority, priority_seq, amend, Replace, Modify
- [iceberg-refresh.md](references/iceberg-refresh.md): iceberg, display_qty, reserve order, refreshed slice
- [quantity-conventions.md](references/quantity-conventions.md): leaves_qty, LeavesQty, CumQty, intended total
- [self-trade-prevention.md](references/self-trade-prevention.md): stp, self_trade, aiq, decrement both, cancel oldest
- [prevented-matches.md](references/prevented-matches.md): preventedQuantity, counterfactual, excluded from volume
- [auction-uncross.md](references/auction-uncross.md): uncross, opening_price, imbalance, indicative, LULD collar
- [cross-input-cutoff.md](references/cross-input-cutoff.md): recalculation loop, pending, drain, close_order_ports
- [facebook-cross.md](references/facebook-cross.md): a cross that never printed, 34-69655, confirmations withheld
- [order-state-transitions.md](references/order-state-transitions.md): (state, event), state_machine, terminal order, silently ignored
- [cancel-and-replace-races.md](references/cancel-and-replace-races.md): CancelAck, ack then fill, Replaced, UserRefNum
- [journal-inputs.md](references/journal-inputs.md): what gets journaled, TimeTick, injected seed, admin command
- [write-ordering.md](references/write-ordering.md): wal, journal.append, fsync, torn append, one commit
- [publish-outbox.md](references/publish-outbox.md): outbox, tx.send, try_send, published_at, relay
- [deterministic-core-hazards.md](references/deterministic-core-hazards.md): Instant::now, thread_rng, HashMap iteration, f64 price
- [five-minted-identifiers.md](references/five-minted-identifiers.md): ExecID, match_number, next_exec, feed sequence
- [replay-harness.md](references/replay-harness.md): golden events, byte-compare, seed=, buggify, simulation
- [snapshots-crash-points.md](references/snapshots-crash-points.md): snapshot, truncation, kill -9, book_digest
- [failover-fencing.md](references/failover-fencing.md): failover, epoch, fencing, standby, two writers
- [reducer-epochs.md](references/reducer-epochs.md): reducer, matcher_version, config_digest, migration
- [assertion-policy.md](references/assertion-policy.md): debug_assert, overflow-checks, saturating_add, total_qty depth
- [recovery-runbook.md](references/recovery-runbook.md): reproduce an incident, git_sha, build identity, offline tool
- [pre-trade-rejection.md](references/pre-trade-rejection.md): pre_trade, risk_limit, credit_check, post-execution screen
- [exposure-buckets.md](references/exposure-buckets.md): working versus filled, net_position, notional cap, settlement leg
- [price-bands.md](references/price-bands.md): price_band, reference_price, fat finger, prior close, sentinel
- [fanout-bounds.md](references/fanout-bounds.md): fan_out, one input many outputs, basket notional
- [separate-gates.md](references/separate-gates.md): kill_switch, circuit_breaker, override, reset authority
- [halt-levels.md](references/halt-levels.md): halt, quiesce, market state, fail_closed, cancel-all
- [resumption.md](references/resumption.md): resume, reopen, undelivered fills, what the resume can trust
- [busts-and-corrections.md](references/busts-and-corrections.md): bust, trade_break, an execution row deleted, clearly erroneous
- [engine-contract.md](references/engine-contract.md): a review or a ship decision, VERDICT, which slots to fill

## Output

Open with one line, `authority: SELF · exposure: record` being the usual pair, then one entry per finding:

```
FINDING   <the wrong economic outcome, concretely>
WHY       <the mechanism that produces it>
EVIDENCE  <file:line>
FIX       <the change that closes it>
TEST      <the property to assert>
```

Add `VERDICT   SHIP | NO-SHIP: <the unresolved control>` as a last line only for a review or a ship decision.
No findings is a sentence or two saying so and why the change is safe. A claimed control points at executable
code and a named test; an absent one is `UNRESOLVED: <control> (<why>)`.

Because authority is SELF, **also emit the ENGINE CONTRACT block by default**, filling only the slots this
change touches: venue half, client half, durable in, one writer, recovery, replay test, publish, identifiers,
rulebook, STP, exposures, auction, emit bound, halt, conservation. Every slot wants a `file:line` or a test
name, and the templates are in the engine-contract reference.
