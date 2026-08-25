# The ENGINE CONTRACT block: every slot, and the question it is really asking

The default output of this skill is one entry per finding. Because authority is SELF, it also emits the block
below, and the block is not a summary: each slot names a control whose absence is an unreviewable gap, so a
slot with nothing to point at is the finding. Fill only the slots the change touches. A slot it does not touch
is omitted, not left blank, because a blank reads as "checked and absent" when it means "not looked at".

```
ENGINE CONTRACT
- Venue half:   authority SELF · exposure record. <crosses resting orders / assigns priority / mints ExecID>
- Client half:  authority EXTERNAL (<venue>) · exposure <own|customer>, reconciled by <key> | none
- Durable in:   <file:line where the inbound command is durable and ordered before the book is touched>
- One writer:   <the fence, and the resource that rejects a stale epoch> | single process, no failover
- Recovery:     persisted decisions at <f:l> | reducer+config pinned by <version+digest+build> at <f:l>
- Replay test:  <test name that replays the command stream and byte-compares the emitted sequence>
- Publish:      <file:line where the send result is bound and checked>
- Identifiers:  cmd-seq · match · exec · session-seq · feed-seq, five counters at <file:line>
- Rulebook:     <venue-specific answer this change depends on> = <the answer>, pinned by <test name>
- STP:          scope <published scope> · strategy <published strategy>, read from <config source>
- Exposures:    working <f:l> · filled <f:l> · settlement <f:l|none>; transfer atomic | derived at <f:l>
- Auction:      cutoff at <file:line> | bounded batch, max <n> passes falling back to the cutoff at <f:l>
- Emit bound:   <name>=<value> at <file:line>; trip flag reset owner: <component, not the emitter>
- Halt:         <incident gate | market-state halt | quiesce> level <1-6>; cancel gated by <flag> at <f:l>
- Conservation: <Σ allocations == min(aggressing qty, Σ leaves)> at <f:l>; <aggregate> checked at <f:l>,
                saturation emitted at <f:l> | none saturates
```

**The two halves.** A process that both runs a book and trades on somebody else's is two changes wearing one
deployment, and the first two slots force them apart. A merged declaration inherits the weaker obligation of
the client half, which is the half an external statement can correct, and the venue half then ships with the
proof burden of a component that has an oracle when it has none.

**Durability, writer and recovery.** These three answer one question between them: if the process dies now,
what does the persisted record say happened. The durable-in slot wants the line where the command is on disk
before the book moves, which is a different line from the one that opens the transaction. The one-writer slot
wants the resource that rejects a stale epoch, not the lock service that hands out the token, because a token
nothing checks is decoration. The recovery slot has exactly two acceptable answers, and "we replay the
journal" is not one of them until it says which reducer the replay applies.

**Replay test and publish.** A named test that byte-compares an emitted sequence is what makes every other
claim in the block checkable, so this slot is the one to fill first and the one whose absence downgrades the
rest to assertions. The publish slot wants the line where the send result is bound, because the discarded
result is the failure the outbox exists for.

**Identifiers, rulebook and STP.** Five counters at one file:line, a venue-specific answer named with the test
that pins it, and the two published halves of self-match prevention. Each of these is a place where a value
taken from memory produces a book that is consistent with itself and at odds with what you published, so what
the slot is really asking for is where the value was read from.

**Exposures, auction, emit bound and halt.** Four bounds, each of which has a shape that looks correct and
fails: one counter serving two exposure buckets, a cross priced over a set that is still changing, a per-item
limit with no aggregate companion, and a halt that means a different one of its three senses than the code
implements. Naming the file:line is what separates a control from an intention.

**Conservation.** The last slot is the one that catches the arithmetic rather than the plumbing. It wants the
assertion that runs before any execution is emitted, in the build you deploy, and the place a saturation
becomes an event rather than a quietly wrong number on a feed.

Two cases drop back to findings alone: an engine deployed only to a sandbox that mints no identifier an
outside system consumes, and a read-only path that creates no obligation. Both become `exposure: record` the
day participant funds or an outside consumer arrive. The internal invariants still obey *reconciliation*: the
journal, the replay test, the emit bound, the halt gate and the conservation assertions are the
reconciliation, and they ship as a scheduled entrypoint in production, reading independently of the writer,
alerting to a destination that has no default.
