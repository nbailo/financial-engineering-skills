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
| **Working order** | `Σ leaves` valued by **that product's own rule** (see below); a market order under the market-order rule below | **decrements** it by the filled quantity | increments at accept; decrements on an acknowledged cancel, a reject, an expiry, and on a self-match-prevention decrement |
| **Filled position** | the net position and its notional | **increments** by the same filled QUANTITY; the amount of risk it adds is not the amount the working bucket shed | a bust or a correction, retroactively; nothing else |
| **Settlement** | delivery and payment obligations already created, until clearing or settlement finality | creates one on each side | settlement, novation to a clearing house, or an explicit void |

**A fill moves quantity between buckets. It does not move an equal amount of risk.**
The working bucket sheds what an unfilled order might have cost; the position bucket takes on what a held
position does cost, and those are different quantities under almost any product. A long option's working
exposure is bounded by the premium while the resulting position carries the payoff; a leveraged position
consumes margin the working order did not; fees crystallise at the fill and belong to neither bucket before
it. Report the two separately and never net them into a single number.

**`leaves x limit` is one product's rule, not the rule.** It is right for a cash-settled linear instrument
bought outright, where the limit price is the worst case. It is wrong for a short option, where the loss is
not bounded by the premium; wrong under leverage and portfolio margin, where the binding number is what the
margin regime demands rather than notional; wrong for inverse and quanto contracts, whose notional is not
linear in price; and wrong wherever the rulebook defines the measure itself. Derive the working contribution
per product, side, fee schedule, margin regime and rulebook, state which of those inputs your number depends
on, and assert the derivation is the one the rulebook names.

**Where that rule applies, a resting limit order bounds its own contribution. A market order does not, so it
gets a rule of its own.** For an outright, cash-funded, linear buy whose rulebook names notional as the
measure, `leaves x limit` is the worst case and is the number the gate uses. Read that as the scoped case it
is, not as a property of limit orders: change the product, the side, the margin regime or the rulebook and the
bound changes with it. A market order carries no price bound at all under any of them: valuing it at the touch, at the last trade or
at the mid values it at a book that is about to change, and it reads smallest exactly when the book is
thinnest. Decide the treatment before a market order is accepted, and write which one the code implements:
value it at the far edge of the band that would still let it execute, value it at a notional the sender
supplied and reject it when none is supplied, or do not accept market orders on that instrument. Whichever
you pick, it is a stated rule with a test, not a default inherited from whatever price the code had to hand.

Four consequences, and they are the whole of it.

- **A fill moves QUANTITY from working to filled in one step, not two.** It reduces working leaves by
  exactly the quantity it adds to the filled position. A design that moves one and then the other has a
  window in which the same quantity counts twice, and a crash inside that window makes the double permanent.
  What moves atomically is the quantity; the RISK the two states carry is not the same amount and is not
  conserved across the fill, so nothing here licenses netting one against the other.
- **A credit gate does not add raw working and filled quantities.** They are quantities in different states,
  and summing them is only meaningful once each has been converted into a common measure the product defines:
  a margin requirement, a risk number, or a liability under the rulebook. Convert first, then aggregate, and
  say in the code which measure the sum is in. Adding leaves to position because both are in lots gives a
  number in no unit at all, and it is wrong in the direction that lets trading continue.
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
