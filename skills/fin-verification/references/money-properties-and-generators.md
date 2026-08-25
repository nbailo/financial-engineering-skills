# Money properties, generators, and second oracles

Which properties are worth asserting on money code, how to build a generator that reaches the values that break
them, how to prove an amount survives the real driver, and two cheap ways to check money math against something
other than itself. A generator you have not measured does not produce the cases you believe it does.

## Contents

- The property catalogue: one concrete assertion per property
- Assert after every step, not at the end
- Generator design: the boundary families that find real bugs
- Round-trip through the real wire format and the real driver
- Counterexamples belong in the repo; CI is not random
- Mutation testing, scoped to the money-math modules
- Differential testing of two rounding or pricing implementations

## The property catalogue

Five are load-bearing everywhere; three more apply only when the design claims them. Write each in the form
given: a property stated in prose becomes a comment, which is the failure the *a comment is a claim* rule catches.

| Property | Assertion, literally | Applies when | Primary evidence |
|---|---|---|---|
| **Conservation** | `sum(entry.amount for entry in txn) == 0` per transaction **and** `sum(all account balances) == 0` globally, with an out-of-system `world` account originating funds | always, for any double-entry store | Square Books: "all transactions… must balance to 0, so each cent lost is matched with a cent gained"; Uber Money: "The sum of all the entries is zero"; Beancount: "the sum of all the postings of a transaction must equal zero" |
| **Idempotence** | `apply(ev); s1 = snapshot(); apply(ev); assert snapshot() == s1` for every operation carrying an identity: event id, `tradeId`, `pspReference`, idempotency key | every retryable or at-least-once path | Stripe mints the key once per logical request and re-uses it across retries |
| **Reordering invariance** | apply a permutation of the event list, assert byte-identical terminal state (**only for operation sets the design claims are order-independent**) | claimed-commutative sets only | nautilus `test_avg_px_invariant_to_fill_arrival_order` (`crates/model/src/orders/mod.rs:1769`) asserts ascending and descending fill order give byte-identical `avg_px` |
| **Allocation totality** | `sum(allocate(total, weights)) == total` **exactly**, in minor units, over generated totals and degenerate weight vectors (all-zero, single-nonzero, `1/3,1/3,1/3`, one weight `0`, N=1, N=1000) | any split, pro-rata, fee share, tax line | Numscript "allocates remainders from the top of the list down… no invisible fractions or mystery money"; Fowler's Money pattern names "it's easy to lose pennies" |
| **Reservation-implies-postable** | for every reachable state, every committed reservation can still be posted: `credits_posted - debits_posted - debits_pending >= 0` on a `debits_must_not_exceed_credits` account | holds, auths, margin, escrow | TigerBeetle rejects a pending transfer at **reserve** time if posting it could later break the account's balance invariant: "It will not wait to get to posted status to fail" |
| **Non-negativity where configured** | `balance >= 0` only on accounts that declare the constraint; assert the *flag*, never a blanket rule | per-account, from the schema | TigerBeetle `debits_must_not_exceed_credits` / `credits_must_not_exceed_debits`; `debits_exceed_credits` counts `debits_pending` (`src/tigerbeetle.zig:34-37`) |
| **Monotonicity** | the quantity the design declares never decreases actually never decreases: `filled_qty`, a per-user accrual index, a sequence number, a watermark. `assert new >= old` at the write, over generated sequences including duplicates and out-of-order arrivals | declared-monotonic fields | Compound's 2021 COMP over-distribution violated per-user accrual-index monotonicity plus reward-pool conservation |
| **Round-trip serialization** | `parse(serialize(x)) == x` through the **actual** wire codec and the **actual** DB driver, not an in-memory copy | every storage/wire boundary | freqtrade declares every money field `Float()` (`freqtrade/persistence/trade_model.py:1106-1115`) while doing the aggregation in `Decimal` |

**Overfill makes conservation an inequality on the order aggregate.** nautilus writes its invariant as
`filled_qty + voided_qty + leaves_qty >= quantity` (`crates/model/src/orders/mod.rs:1341-1352`), deliberately
`>=`, so a venue overfill does not trip it. Assert the inequality on the order and the *equality* on the ledger.

## Assert after every step, not at the end

An end-of-sequence assertion is a much weaker test: it passes on a system that transiently creates money and
destroys it again two steps later: exactly what a concurrent reader, a crash, or a reconciliation snapshot
observes. In Hypothesis, `@invariant()` runs after **every** rule; in `proptest-state-machine`,
`StateMachineTest::check_invariants` runs after every transition; in jqwik an `Action`'s `check()` runs after
each action. Use those hooks; do not accumulate and assert once.

The step-wise form catches three defects in one pass: an in-memory dedupe set (add a `restart` rule and the
invariant fires on the next step), a watermark held on the live object rather than in storage, and an illegal
transition that a later event happens to repair.

## Generator design: the boundary families that find real bugs

A uniform generator over `Decimal` between 0 and 1000 finds nothing. Enumerate boundary families explicitly and
compose them with `st.one_of` / `fc.oneof` / `Arbitraries.oneOf`, weighted toward the boundaries.

| Family | Emit exactly | The defect it reaches |
|---|---|---|
| **Threshold triple** | `t-1`, `t`, `t+1` in minor units for **every** configured limit: `minNotional`, `minQty`, `maxQty`, `max_position`, `max_notional`, `MAX_NUM_ORDERS`, the aged-break escalation threshold | `>` written where `>=` was meant, at the one value where it costs money |
| **Sub-tick precision** | a price with `tickSize` decimals **+1**, a qty with `stepSize` decimals **+1**, and the same for `MARKET_LOT_SIZE`'s different `stepSize` | rounding into invalidity; rounding *down* to `qty == 0` that the caller treats as a no-op success and marks the intent complete |
| **Filter interaction** | a `(price, qty)` that satisfies `PRICE_FILTER` and *therefore* violates `NOTIONAL` after normalisation | one-way validation: satisfying one filter breaks another, and only the venue tells you |
| **Integer width** | `2**63-1`, `2**63`, `2**64-1`, and (for a `u128` minor-unit ledger) values whose **sum** overflows, not just whose value does | a `saturating_add` accumulator that absorbs the overflow silently. TigerBeetle checks before mutating (`sum_overflows`, `src/state_machine.zig:5144-5149`) and returns `overflows_debits_posted` / `overflows_credits_posted` / `overflows_timeout` (`src/tigerbeetle.zig:307-313`); nautilus saturates (`orders/mod.rs:1270`, `:1366`) |
| **Zero and negative** | amount `0`; qty `0` after normalisation; a negative delta wherever the domain permits one: reversal, refund, clawback, short position, `leaves_qty` after an overfill | `0` treated as "no-op success"; a quantity that goes negative and is then clamped |
| **Currency exponent** | exponent 0 (JPY, KRW, VND, CLP, ISK, XOF), 2, 3 (KWD, BHD, JOD, OMR, TND), 4 (CLF); MGA and MRU are subdivided by **5**, not a power of ten | any `amount_cents = round(amount * 100)` |
| **Float-hostile decimals** | values exact in `Decimal` and not in `float`: `Decimal("1.6666666666666666666666666667")`; nautilus asserts precisely this is **not** equal to `Decimal::from_f64_retain(5.0/3.0)` (`orders/mod.rs:1728`) | a `Float` column, a JSON round-trip, `math.isclose` on money |
| **Stream shape** | the same event id twice; a fill arriving *after* the terminal event; a restart injected mid-sequence; a partial-fill sequence that over-runs the order quantity | in-memory dedupe; the residual/overfill branch that a "fills sum exactly" generator never reaches |

Drive the filter values from a **production** `exchangeInfo` fixture. A testnet fixture gives you the wrong
`tickSize` and the whole property test then proves nothing: Binance SPOT testnet shipped the
`MIN_NOTIONAL` → `NOTIONAL` rename *before* production (ccxt issue #17545).

## Round-trip through the real wire format and the real driver

The assertion is `parse(serialize(x)) == x`, and the load-bearing word is *actual*. A round-trip through an
in-memory dataclass copy proves nothing; the defect lives at the boundary:

```python
@given(amt=amounts_in_minor_units(), ccy=st.sampled_from(["USD", "JPY", "KWD", "CLF", "MGA"]))
def test_amount_survives_the_database(pg_session, amt, ccy):
    m = Money(minor=amt, currency=ccy)
    pg_session.add(Posting(amount=m.minor, currency=m.currency))
    pg_session.commit(); pg_session.expire_all()                  # real re-read through the driver
    row = pg_session.execute(text("SELECT amount, currency FROM postings")).one()
    assert row.amount == amt and type(row.amount) is int          # not 1.0000000000000002
    assert json.loads(json.dumps(m.to_wire()))["minor"] == amt    # and through the wire codec
```

Cover, at minimum: the SQL column type (`NUMERIC(38,0)` or `BIGINT`, never `Float`/`DOUBLE PRECISION`), the ORM
field type, `json.dumps`/`JSON.parse` (JavaScript numbers are IEEE-754 doubles; a minor-unit amount above
`2**53` loses precision silently), protobuf (`double` vs `sint64` vs a decimal message), and CSV/Excel export.
freqtrade is the citable case: `Mapped[float] = mapped_column(Float())` for `amount`, `price`, `average`,
`filled`, `remaining`, `cost`, `funding_fee`, `ft_fee_base`, while `recalc_trade_from_orders`
(`trade_model.py:1266-1335`) aggregates in `Decimal` and casts back to `float` to store. The operative test is
not "is a float used" but **"can the stored value be re-derived exactly from something that is exact?"**

## Counterexamples belong in the repo; CI is not random

Two defaults most teams have backwards:

- **Hypothesis sets `derandomize=True` automatically in CI environments**, and `deadline` is auto-disabled
  there. CI therefore runs the *same* examples forever. Run two modes: the fast derandomized suite in CI, and a
  separate nightly job with a large `max_examples` and an explicit `derandomize=False`.
- **The example database is a local directory** (`DirectoryBasedExampleDatabase` at `.hypothesis/examples`) that
  CI discards, so a counterexample found once is lost unless it is committed. `print_blob` is auto-`True` in CI
  precisely so you can paste the blob: commit every shrunk failure as `@example(...)` (the durable form) or
  `@reproduce_failure(version, blob)` as a temporary pin. fast-check's equivalent is
  `fc.commands([...], {replayPath: "..."})`; proptest writes `proptest-regressions/*.txt`, which **must be
  committed**.

## Mutation testing, scoped to the money-math modules

PIT's framing: line coverage "does not check that your tests are actually able to detect faults." Rounding, fee,
allocation and PnL code is exactly where a test executes every line while asserting nothing about the boundary.

Run it on those modules **only**: `pitest` (Java), `mutmut` (Python), `cargo-mutants` (Rust), `Stryker`
(JS/TS); and require a high score there rather than chasing repository-wide coverage. Scoping is a cost
decision: a whole-repo run is hours of CPU and yields mostly equivalent mutants in glue code. The mutants that
matter for money are the boundary and arithmetic-operator ones: `>` → `>=` at a threshold,
`ROUND_HALF_UP` → `ROUND_HALF_EVEN`, `+` → `-` in a fee sign, a removed `abs()`, a removed remainder
distribution. A surviving `>` → `>=` mutant on a `minNotional` check is a real, shippable bug.

**Evidence status:** no published case study of mutation testing on a financial system exists in the research
behind this file; the recommendation is mechanism-derived from PIT's coverage-vs-fault-detection argument.

## Differential testing of two rounding or pricing implementations

Two implementations of the same economic function, one generated input space, exact comparison. The cheapest
strong oracle available, and it needs no model. **In trading the second implementation is free: the venue
publishes the fee it charged and the executed price and quantity per fill**, so every locally computed fee,
notional and average fill price can be compared to the venue's own number on **every fill, in production**,
catching fee-tier changes, rebates and rounding-mode mismatches that no fixture encodes.

```python
@given(total=st.integers(1, 10**12), weights=st.lists(st.integers(0, 10**6), min_size=1, max_size=64))
def test_allocators_agree_exactly(total, weights):
    assume(sum(weights) > 0)
    legacy = [round(total * w / sum(weights)) for w in weights]      # the incumbent
    new    = allocate_largest_remainder(total, weights)              # the replacement
    assert sum(new) == total                                         # totality, independent of the diff
    assert new == legacy or sum(legacy) != total                     # divergence ONLY where legacy was wrong
    assert max(abs(a - b) for a, b in zip(new, legacy)) <= 1         # and only by a remainder unit
```

Three rules for the comparison itself:

- **Compare in minor units with `==`, never with an epsilon.** A relative float tolerance on money is itself the
  defect: hummingbot decides order terminality with `math.isclose(self.executed_amount_base, self.amount)`
  (`hummingbot/core/data_type/in_flight_order.py:177-183`), which coerces both `Decimal` operands to `float` at a
  relative `1e-9`; an order 0.9999999995 filled is `is_done`, and on a large notional that residue is real money.
- Where a tolerance is genuinely required (a venue's reported number vs yours), key it on the instrument's
  declared precision, not a global epsilon: nautilus pairs `DEFAULT_TOLERANCE = 0.0001` with a *single-unit*
  tolerance derived from the instrument's size precision (`reconciliation/positions.rs:40`, `:423`). A fixed
  epsilon is wrong for both a 0-decimal and an 18-decimal asset.
- **Assert divergence in one direction.** `new == legacy` is the wrong assertion when the point of the rewrite
  is to fix the legacy allocator. State which input classes may differ, and why.
