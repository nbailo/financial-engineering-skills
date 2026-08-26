# Working order, filled position and settlement: three buckets, and the transfer

The word exposure names three different quantities, and a fill transfers between the first two rather
than incrementing both. A gate keeping one counter is wrong in one direction or the other.

## Three exposures, three named buckets

Specialises *hard limits*. No counter serves two of the three, and which direction a single-counter gate is
wrong in depends on which of the three the author had in mind. Name each bucket at its definition and name
the bucket every limit is written against; a counter that serves two buckets is the defect, not the
optimisation.

| Exposure | What it counts | What a fill does to it | What else moves it |
|---|---|---|---|
| **Working order** | `Σ leaves` over live orders, each at its own limit price; a market order under the market-order rule below | **decrements** it by the filled quantity | increments at accept; decrements on an acknowledged cancel, a reject, an expiry, and on a self-match-prevention decrement |
| **Filled position** | the net position and its notional | **increments** it by the same filled quantity, signed | a bust or a correction, retroactively; nothing else |
| **Settlement** | delivery and payment obligations already created, until clearing or settlement finality | creates one on each side | settlement, novation to a clearing house, or an explicit void |

**A resting limit order bounds its own exposure. A market order does not, so it gets a rule of its own.**
`leaves × limit` is the most a limit order can cost whoever carries it, whatever the book does next, so it is
the number the gate uses. A market order carries no such bound: valuing it at the touch, at the last trade or
at the mid values it at a book that is about to change, and it reads smallest exactly when the book is
thinnest. Decide the treatment before a market order is accepted, and write which one the code implements:
value it at the far edge of the band that would still let it execute, value it at a notional the sender
supplied and reject it when none is supplied, or do not accept market orders on that instrument. Whichever
you pick, it is a stated rule with a test, not a default inherited from whatever price the code had to hand.

Four consequences, and they are the whole of it.

- **A fill is a transfer, not two independent events.** It reduces working-order exposure by exactly the
  quantity it adds to filled-position exposure. A design that moves one and then the other has a window in
  which the same quantity counts twice, and a crash inside that window makes the double permanent.
- **Where the atomic unit ends.** One commit owns the order-state change and the **immutable execution
  obligation** it created. A position store, a credit gate or a ledger is inside that commit only where it
  literally shares the transaction. Where it does not, the execution record is the authority and the
  position is **derived** from it, by a consumer keyed on the execution id so that applying the same
  execution twice is a no-op. What is never correct is a second writer incrementing a position on the emit
  path while the execution record is the authority for the same quantity.
- **Do not net the first two into one counter.** Incrementing a position without decrementing `leaves`
  double-counts the same quantity; decrementing `leaves` without booking the position under-counts it. A
  credit or capital check is written against working **plus** filled, and it says so at its definition.
- **Settlement exposure outlives the other two.** A round trip that ends flat is zero position and two live
  delivery obligations. A gate reading net position alone reports no exposure at exactly the moment two
  deliveries are outstanding, which is the moment a counterparty failure costs the most.

One further number is often kept beside those three buckets: cumulative notional entered over a session,
used as a throughput or credit-consumption cap. It is monotone by construction and it is not exposure. Gating it as if it were
produces a limit that can only ever tighten, and an engine that refuses orders on a flat book.
