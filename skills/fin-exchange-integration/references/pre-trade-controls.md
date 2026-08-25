# Pre-trade controls: venue constraints, your own limits, and the gate

Two independent bodies can refuse an instruction before it reaches the market: the venue, through the
constraint set its validator publishes, and you, through your own risk limits. Both belong inside the function
that sends. This file carries the constraint mechanics, the decimal traps that make a "correct" normaliser
wrong, the regulatory form of the control, and the risk gate that closes when something is unresolved and the
narrow set of calls that must keep working while it is closed.

## Contents

- Fetching the constraint set, and the filters that look like each other but are not
- The simultaneous check: four predicates, all re-evaluated after rounding
- Decimal traps: float floor-division loses a whole step, and scientific notation reaches the wire
- Rounding toward legality, and the quantity that rounds to zero
- Your own limits: the three names to grep for, and where they must be called
- The deviation band, derived per session state
- SEC Rule 15c3-5(c)(1): why the control lives on the send path and counts orders entered
- The risk gate: what closes it, what stays callable, what reopens it
- Never abort a process holding unmanaged obligations: Ariane 501 and Knight ¶42

## Fetching the constraint set

Fetch the instrument's constraints from the venue at startup, refresh them on a schedule, and commit a fixture
captured from production so the tests exercise the real numbers rather than hand-written ones. On Binance the
endpoint is `exchangeInfo`; every venue publishes an equivalent.

Two pairs of filters are routinely conflated, and both conflations are silent:

| Pair | The difference |
|---|---|
| `LOT_SIZE` vs `MARKET_LOT_SIZE` | separate filters. `MARKET_LOT_SIZE` applies **only** to MARKET orders, and a normaliser that reads `LOT_SIZE` for every order type passes its own tests and is rejected in production on the market path |
| `NOTIONAL` vs `MIN_NOTIONAL` | different filter **types**, of which a symbol exposes one. Code that reads only `MIN_NOTIONAL` sees no minimum at all on a symbol carrying `NOTIONAL`, and sends orders below the floor |

The parametrised test that catches the first one is `@pytest.mark.parametrize("order_type", ["LIMIT",
"MARKET"])` over the same normaliser, using a fixture such as `exchangeInfo.BTCUSDT.json` captured from
production.

## The simultaneous check

Satisfying one constraint can break another. Round toward legality first, then re-evaluate the whole set
together, for this instruction type:

```
price % tick     == 0
qty   % step     == 0
price * qty      >= minNotional
minQty <= qty    <= maxQty
```

Rounding a price down to the tick grid can drop `price * qty` below `minNotional`; raising `qty` to clear
`minNotional` can push it past `maxQty`. Only the joint re-check finds that, and only if it runs after every
adjustment rather than before.

## Decimal traps

Do the arithmetic in an exact decimal type, and serialize it as a decimal string.

- `int(0.29 / 0.01) == 28` in binary floating point. A float floor-division against a tick or step size
  silently loses a whole increment, and the loss is one-directional.
- `str(1e-05) == '1e-05'`. A quantity formatted through a float reaches the wire in scientific notation, which
  is not a legal number for most venue validators, and the rejection names a character-set error rather than
  the real cause.

Both faults survive every unit test written with round numbers, which is why the fixture must come from
production.

## Rounding toward legality, and the zero case

Rounding toward validity **never increases size**. A price is snapped away from the aggressive side, a
quantity is snapped down to the step grid, and the post-rounding quantity is re-compared against `minQty` and
`minNotional`.

A quantity that rounds to zero is an **explicit skip signal**, returned and handled by the caller. It is never
a silent no-op and never a zero-quantity order: the strategy that believed it hedged, and did not, is the
economic failure, and a `0` returned from a normaliser is indistinguishable from a legitimate result at the
call site.

## Your own limits, on the send path

Grep the repository for `max_order_notional`, `max_position` and `max_orders_per_second`, then check that each
is actually **called from the function that sends**, evaluated against live position rather than a cached
metric or a monitoring gauge. A limit defined in a config file and read by a dashboard is a detector. A limit
evaluated in a sibling module is a control only for the send paths that happen to route through it, and the
path that does not is the one that trades.

Measure exposure from instructions **entered**, not executions received. A limit that counts fills lets an
unbounded number of unfilled orders rest in the market, and an order resting in the market is exposure.

## The deviation band, derived per session state

Every marketable instruction is bounded against a reference price for **its own instrument**, and the bound is
derived the same way on every session state: continuous, pre-market and post-market, auction or pre-open, and
halted. A band derived from a value the instrument does not participate in admits everything: Goldman's
pre-market check allowed any price above $0.01 and below 1.5 times the highest closing price of *any* listed
option, so a $1 order in any name passed it. The full derivation, the two SEC orders behind it, the per-state
reference table and the rule that a missing reference is a rejection rather than a default are in
[execution-algorithms.md](execution-algorithms.md) under "Collaring a marketable child, derived per session
state". Read that section before writing a band, whether or not the code has a parent/child algorithm.

## SEC Rule 15c3-5(c)(1)

The regulatory form of this rule states where the control lives and what it counts. 17 CFR 240.15c3-5(c)(1)
requires that the control be *"applied on an automated, pre-trade basis, before orders are routed"* and that
it be assessed
*"on the basis of exposure from orders entered … rather than relying on a post-execution, after-the-fact determination"*.

Read as an engineering specification rather than a compliance obligation, it says exactly the two things this
file says: the check runs on the send path, synchronously, before the write; and the quantity it accumulates
is orders entered, not executions received. It applies to your bot whether or not you are a broker-dealer,
because the failure it was written against is the failure your bot has.

## The risk gate

An unresolved instruction, a reconciliation break, an overfill or a book that lost sync all close the same
gate, scoped to the instrument.

| While the gate is closed | Status |
|---|---|
| `submit_order`, and any size-increasing amend | **refused** |
| `cancel`, `cancel_all(scope)`, `flatten(scope)` | callable |
| position, PnL, margin and liquidation-distance reads | callable |

Ship a test that proves the second and third rows, because the natural implementation of a gate is a check at
the top of a shared client method, and that shape disables the risk-reducing calls at exactly the moment they
are needed.

The gate reopens **only on a successful comparison against the venue**, never on a timer and never from the
code path that closed it. A gate that reopens on a timer is a delay, not a control.

## Never abort a process holding unmanaged obligations

The instinct when state looks wrong is to stop the process. That is the failure, not the fix, whenever the
process is the only thing managing an open position.

- **Ariane 501**, from the board of inquiry: *"It was the decision to cease the processor operation which
  finally proved fatal"*. An exception handler shut down a unit that was still the authority for the vehicle's
  attitude.
- **Knight Capital**, SEC order ¶42: the correct action was to disconnect the **emitter**, the component
  sending orders, while continuing to manage the position the emitter had already created. Knight's responders
  did the reverse for 45 minutes.

So: refuse new instructions, keep the position-managing paths alive, and make the shutdown of the emitter a
separate, testable action from the shutdown of the process.
