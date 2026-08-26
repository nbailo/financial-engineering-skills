# The feed specification, and the contract block that reports it

The document a consumer reads before writing a line of code against your feed. A slot you leave unfilled
is one they fill by guessing, and the guess becomes a position. Load this file only for an explicit
design, review or ship-readiness task. Below is the checklist, then it answered at `file:line`.

## The checklist

```
SEQUENCING
  [ ] Which counter gap-detection runs on (per-message / per-packet / per-instrument), and its arithmetic
  [ ] Whether packets carry multiple messages, and how the count is encoded
  [ ] Every additional counter published, its scope, and what it is NOT valid for
CONTROL
  [ ] Heartbeat interval, what a heartbeat carries, and whether it consumes a sequence number
  [ ] What a heartbeat covers: the channel, or the state of any one instrument
  [ ] End-of-session semantics and how long recovery requests remain serviceable
RESET
  [ ] The reset message and its trigger
  [ ] Exactly what a reset clears, what it renumbers, and what it does NOT resend
  [ ] How ONE instrument is withdrawn and restored, and what that event invalidates
SNAPSHOT
  [ ] The join key, and which stream's sequence it names
  [ ] Direction of the join: which buffered updates are dropped and which applied
  [ ] Snapshot cycle period, loop-order guarantees (or their absence), and fields the snapshot omits
GAP
  [ ] Whether A/B arbitration must precede gap declaration
  [ ] Recovery address, request format, retained depth, truncation rule, rate limit
  [ ] What a gap invalidates: this instrument, or every book on the channel
CONTENT
  [ ] Which message types are book-eligible; which are volume-eligible; the whole volume-bearing set
  [ ] Which rulebook decides eligibility, and which figures are computed from which set
  [ ] Every deliberately-constant field, its value, and its effective date
  [ ] Conflation policy: which streams, state or delta encoded, the interval, the bound, the chain link
  [ ] Which raw updates a conflated message covers: a raw range, or the stream's own counter
  [ ] The slow-consumer policy per stream, and the recovery load it implies
TIME
  [ ] Each timestamp's meaning, epoch, timezone, DST rule, and clock discipline
  [ ] Which measurement each supports: event age, send latency, receive latency
  [ ] Which timestamp is authoritative for staleness
PUBLISHER
  [ ] Where the sequence number is assigned, and that fan-out happens once, after that point
  [ ] Whether two paths carry one sequence space, and where byte identity per sequence is produced
  [ ] Which assertions run on the emit path, in which session states, and what a breach freezes
  [ ] Which sinks the fan-out feeds, and where the per-message hand-off records are retained
```

## The FEED CONTRACT block

Emitted on a design, review or ship decision. Fill only the slots the change touches; one it touches and
cannot fill is the finding, reported as `UNRESOLVED:`. A slot it does not touch is omitted.

```
FEED CONTRACT
- Identity:    <scopes the sequence space> · new one minted by <event> at <file:line>
- Sequence:    <counter consumers gap-detect on> · <arithmetic> at <file:line> · others NOT valid for <use>
- Control:     heartbeat <interval> carrying <what>, covering <scope> · end of session drains <window>
- Reset:       <message> · clears <list> · renumbers <list> · NOT resent <list> · one instrument <event>
- Snapshot:    as-of names <stream>.<counter>, stamped with the copy at <file:line> · cycle <n> · omits <f>
- Recovery:    <mechanism> ends on <condition> · retained depth <n> · truncation and rate limit in <doc>
- Arbitration: <A/B or none> · byte identity at <file:line> · gap declared after arbitration
- Conflation:  <none | state-encoded on <streams>> · interval <n> · bound <n> GLOBAL · raw range <field>
               OR own counter <field> · chain link <field> · trades excluded
- Filters:     book-eligible <set> · volume-eligible <set> by <rulebook> · fee tier by <schedule> · index by
               <methodology, not yours> · constant <field>=<value> since <date>, test <name>
- Time:        event time <field, epoch, timezone, DST>, authoritative for staleness · clock <source>
- Emit checks: <assertion> valid in <session states> at <file:line> · on breach <freeze scope> and
               <invalidation event> · withheld or marked <field> · saturation at <f:l>
- Fan-out:     single point at <file:line> · sinks <list> · hand-off records retained at <location>
```
