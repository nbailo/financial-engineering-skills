# Property-based and model-based testing for money

Which properties are worth asserting on money code, how to build a generator that reaches the values that break
them, and how to drive an implementation and a naive reference model from one command sequence. Covers the four
framework families a money codebase uses, differential testing against a second implementation, the narrow band
where an interleaving explorer applies, and (per technique) what each one structurally cannot find. Two claims
organise it: only a handful of properties are load-bearing for money, and a generator you have not measured does
not produce the cases you believe it does.

## Contents

- [The property catalogue](#the-property-catalogue): one concrete assertion per property
- [Assert after every step, not at the end](#assert-after-every-step-not-at-the-end)
- [Generator design: the boundary families that find real bugs](#generator-design-the-boundary-families-that-find-real-bugs)
- [Round-trip through the real wire format and the real driver](#round-trip-through-the-real-wire-format-and-the-real-driver)
- [Hypothesis `RuleBasedStateMachine`: a complete ledger model](#hypothesis-rulebasedstatemachine-a-complete-ledger-model)
- [fast-check, proptest-state-machine, jqwik](#fast-check-proptest-state-machine-jqwik)
- [Writing the naive reference model](#writing-the-naive-reference-model)
- [Make the workload traceable, or the checker cannot attribute state](#make-the-workload-traceable-or-the-checker-cannot-attribute-state)
- [Generator-coverage assertions](#generator-coverage-assertions)
- [Counterexamples belong in the repo; CI is not random](#counterexamples-belong-in-the-repo-ci-is-not-random)
- [Mutation testing, scoped to the money-math modules](#mutation-testing-scoped-to-the-money-math-modules)
- [Differential testing of two rounding or pricing implementations](#differential-testing-of-two-rounding-or-pricing-implementations)
- [Concurrency: the barrier double-spend, and the narrow band for loom/jcstress](#concurrency-the-barrier-double-spend-and-the-narrow-band-for-loomjcstress)
- [Cost and blind spot, per technique](#cost-and-blind-spot-per-technique)

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

## Hypothesis `RuleBasedStateMachine`: a complete ledger model

The class of bug `@given` cannot reach needs an interleaved sequence; MacIver's canonical example took 13 steps
to unbalance a heap. Ledger and order-lifecycle bugs have that shape: open → fund → reserve → post → void →
restart → replay.

```python
from hypothesis import strategies as st, settings
from hypothesis.stateful import (
    Bundle, RuleBasedStateMachine, initialize, rule, invariant, consumes,
)

MINOR = st.integers(min_value=1, max_value=2**64)          # minor units only; never a float, never a Decimal

class LedgerMachine(RuleBasedStateMachine):
    accounts = Bundle("accounts")
    pending  = Bundle("pending")

    @initialize()
    def boot(self):
        self.db      = Ledger(dsn=freshly_migrated_dsn())   # the implementation under test
        self.model   = {}                # account_id -> (posted_debits, posted_credits, pending_debits)
        self.applied = set()             # transfer ids the MODEL believes are committed
        self.constrained = {}            # account_id -> debits_must_not_exceed_credits
        self.world   = self.db.open_account(currency="USD", constrained=False)   # Formance-style
        self.model[self.world], self.constrained[self.world] = [0, 0, 0], False

    @rule(target=accounts, constrained=st.booleans())
    def open_account(self, constrained):
        aid = self.db.open_account(currency="USD", constrained=constrained)
        self.model[aid] = [0, 0, 0]
        self.constrained[aid] = constrained
        return aid

    @rule(dst=accounts, amount=MINOR, tid=st.uuids())
    def fund(self, dst, amount, tid):
        self.db.transfer(tid, src=self.world, dst=dst, amount=amount)
        self.model[self.world][0] += amount
        self.model[dst][1] += amount
        self.applied.add(tid)

    @rule(target=pending, src=accounts, dst=accounts, amount=MINOR, tid=st.uuids())
    def reserve(self, src, dst, amount, tid):
        available = self.model[src][1] - self.model[src][0] - self.model[src][2]
        expect_ok = not self.constrained[src] or amount <= available
        result = self.db.reserve(tid, src=src, dst=dst, amount=amount)
        assert (result.ok, result.code) == (expect_ok, None if expect_ok else "exceeds_credits")
        if not expect_ok: return None
        self.model[src][2] += amount
        return (tid, src, dst, amount)

    @rule(p=consumes(pending))
    def post(self, p):
        if p is None: return                     # a rejected reserve still lands in the bundle
        tid, src, dst, amount = p
        assert self.db.post_pending(tid).ok      # reservation-implies-postable, exercised not just asserted
        self.model[src][2] -= amount
        self.model[src][0] += amount
        self.model[dst][1] += amount

    @rule()
    def replay_a_committed_transfer(self):
        """Idempotence: re-submitting a committed id must not move money a second time."""
        if not self.applied: return
        before = self.db.all_balances()
        self.db.transfer(sorted(self.applied)[0], src=self.world, dst=self.world, amount=1)
        assert self.db.all_balances() == before      # same id, different body: still a no-op

    @rule()
    def restart(self):
        """Drops every in-process cache. The invariants below then run against storage alone."""
        self.db = Ledger(dsn=self.db.dsn)

    @invariant()
    def conservation(self):
        bal = self.db.all_balances()                     # credits - debits, per account, from storage
        assert sum(bal.values()) == 0

    @invariant()
    def model_agrees_exactly(self):
        for aid, (dr, cr, _) in self.model.items():
            assert self.db.balance(aid) == cr - dr       # exact integer equality, no tolerance

    @invariant()
    def every_reservation_is_postable(self):
        for aid, (dr, cr, pending_dr) in self.model.items():
            if self.constrained[aid]: assert cr - dr - pending_dr >= 0

TestLedger = LedgerMachine.TestCase
TestLedger.settings = settings(max_examples=300, stateful_step_count=60, deadline=None)
```

Three Hypothesis facts that change how this is written:

- **`@precondition` and `@invariant` cannot access bundles**, which is why the model lives in `self.model` /
  `self.applied` (plain attributes) rather than in the bundles: invariants must be able to read it.
- **Rules cannot take pytest fixtures or `@parametrize` arguments.** Build the resource in `@initialize()`.
- **Failures shrink to a minimal operation sequence**, the reason to prefer this over a hand-written 13-step
  test: the printed counterexample is a runnable script.

For an order book, the same shape with `Bundle("orders")`: rules `submit`, `partial_fill`, `amend`, `cancel`,
`venue_reject`, `late_fill_after_terminal`, `disconnect_then_replay`; invariants `sum(signed fills) == position`,
`cash == -sum(price*qty) - sum(fees)` computed independently of the implementation's accumulators, and
`filled + voided + leaves >= quantity`. Include `(Canceled, Filled) → Filled`: it is legal and real (nautilus
`crates/model/src/orders/mod.rs:250`, commented *"Real world possibility"*), and a model that refuses it reports
a false positive on correct code.

## fast-check, proptest-state-machine, jqwik

| Framework | Command shape | Runner | Notes that matter |
|---|---|---|---|
| **fast-check** (TS/JS) | `ICommand<Model, Real>` with `check(model)`, `run(model, real)`, `toString()` | `fc.modelRun`, `fc.asyncModelRun`, **`fc.scheduledModelRun`** | `scheduledModelRun` exists specifically to explore promise-resolution orderings, the closest thing to an interleaving explorer for async money code. `fc.commands([...], {replayPath})` replays a specific failing path |
| **proptest-state-machine** (Rust) | `ReferenceStateMachine { State, Transition, init_state, transitions, apply, preconditions }` + `StateMachineTest { SystemUnderTest, Reference, init_test, apply, check_invariants, teardown }` | `prop_state_machine!` | The two-trait split *forces* the reference model into a separate type; `check_invariants` runs per transition |
| **jqwik** (Java) | `Action.JustMutate` / `Action.Transformer` with `when()` preconditions and `check()` | `ActionSequenceArbitrary` | Ships the two things nobody else does: **`injectDuplicates()`** on an arbitrary (at-least-once delivery, for free) and `Statistics.coverage` for asserting the generator's own distribution |
| **Hypothesis** (Python) | `@rule` / `@initialize` / `@precondition` / `@invariant`, `Bundle`, `consumes()` | `Machine.TestCase` | See the caveats above |

```typescript
class PostPending implements fc.AsyncCommand<LedgerModel, Ledger> {
  constructor(readonly tid: string) {}
  check = (m: LedgerModel) => m.pending.has(this.tid);               // precondition, model-only
  async run(m: LedgerModel, r: Ledger) {
    const { src, dst, amount } = m.pending.get(this.tid)!;
    expect((await r.postPending(this.tid)).ok).toBe(true);           // reservation-implies-postable
    m.pending.delete(this.tid); m.debit(src, amount); m.credit(dst, amount);
    expect(await r.balance(src)).toBe(m.balance(src));               // exact, integer minor units
  }
  toString = () => `postPending(${this.tid})`;
}
```

## Writing the naive reference model

The model is worthless if it shares the implementation's misunderstanding. Three rules, each with a named
failure behind it:

1. **Derive it from the specification or the venue's documentation, never from the implementation.** Jepsen's
   TigerBeetle checker (a ~1,600-line single-threaded Clojure model stepping through timestamp-sorted
   operations and verifying results **down to specific error codes**) was written by an outsider from the docs,
   and it caught the query-engine safety bug that 1024 cores of the project's own simulator missed.
2. **Keep it in a separate module, importing nothing from the implementation but its public types.** fast-check
   states the anti-pattern outright: otherwise you are "testing code against itself."
3. **Prefer naive and obviously correct over efficient.** A `dict` of integers; an O(n²) fold over the full
   event log, recomputed from scratch every step. Not a compromise: nautilus's `avg_px` is a fold over the fill
   log (`orders/mod.rs:600-613`) and freqtrade's `recalc_trade_from_orders` walks `self.orders` from scratch on
   every call: two projects, no shared code, same conclusion that a weighted-average cost is a fold and not a
   running accumulator. The naive model *is* the correct algorithm.

**Assert error codes and identity fields, not just amounts.** TigerBeetle's Java client returned duplicate
execution timestamps because a mutable singleton (`Batch.EMPTY`) was reused across responses; a model checking
only balances passes it. Model the full result: `(ok, code, timestamp, id)`.

## Make the workload traceable, or the checker cannot attribute state

Elle's formal result: dependencies are inferable only when versions are **recoverable** ("every version we
observe can be mapped to a specific write in some observed transaction") and objects **traceable** ("every
version `xᵢ` has exactly one trace"). Blind writes to a register *"destroy history"*; a counter's increment
history is non-recoverable because "we can't tell which increment produced a particular version."

Consequence for money: **a generated workload that sets balances is nearly uncheckable; one that appends
uniquely-identified transfers is fully checkable.** Generate `transfer(id, src, dst, amount)`, never
`set_balance(account, value)` (even in the setup rules). Accounts are counters; transfers are the appendable,
traceable thing, which is why Jepsen built a bespoke semantic model for TigerBeetle rather than using an
off-the-shelf register checker.

## Generator-coverage assertions

The binding constraint on every generated-input technique is whether the generator reaches the interesting
branch. TigerBeetle ran the VOPR on 1024 cores 24/7 and still shipped a query bug: both merge-capable fuzzers
generated queries sharing a common prefix, so matching objects were always **consecutive in each index** and the
zig-zag merge join's **probe** path was never taken: "a blind spot that hid a real bug."

The money-code analogue is precise: a generator whose fills always sum exactly to the order quantity never
executes the over-fill or residual-dust branch: the one nautilus disables by default (`allow_overfills` is
`#[serde(default)]` on a `bool`, so `false`, and the fill report is then discarded entirely,
`crates/execution/src/reconciliation/orders.rs:785-796`).

Name the scenarios and assert their frequency:

```java
Statistics.label("fill shape").collect(shapeOf(seq));
Statistics.label("fill shape").coverage(c -> {
    c.check("exact").percentage(p -> p > 5);
    c.check("partial-then-cancel").percentage(p -> p > 5);
    c.check("overfill").percentage(p -> p > 1);      // the branch that loses money
    c.check("duplicate-at-terminal").percentage(p -> p > 1);
});
```

FoundationDB's equivalent is the conditional coverage macro `TEST(cond)`, whose hit counts **across runs** say
whether a scenario is generated at all. Hypothesis has `event()` and `target()` but nothing equivalent to FDB's
cross-run analysis; `event()` plus a CI check on the printed statistics is the available approximation. This is
the largest tooling gap in the area, and exactly where the TigerBeetle bug lived.

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

## Concurrency: the barrier double-spend, and the narrow band for loom/jcstress

**A lost update across two transactions is not a data race, and no race detector will ever find it.** Go's
detector "only finds races that happen at runtime, so it can't find races in code paths that are not executed";
loom cannot see any type that is not a loom replacement type; jcstress is probabilistic. None of them observes
two database connections. The canonical double-spend is a transaction-isolation defect: `SELECT balance` →
check in application code → `UPDATE balance = computed`.

PostgreSQL's documented boundary is the decisive detail. Under READ COMMITTED a single
`UPDATE accounts SET balance = balance - :amt WHERE id = :id AND balance >= :amt` is safe (the row is re-fetched
and the `WHERE` re-evaluated against the updated version) while the SELECT-then-compute-then-UPDATE form is not.
Under REPEATABLE READ / SERIALIZABLE, "applications using this level must be prepared to retry transactions due
to serialization failures" (SQLSTATE `40001`), and an untested retry path is where a "safe" isolation level
becomes a dropped payment.

Write the reproduction as a **two-connection barrier test**, never as a loop of threads hoping to hit the window:

```python
def test_concurrent_debit_cannot_overdraw(pg_dsn):
    barrier = threading.Barrier(2)
    results = []
    def attempt():
        with psycopg.connect(pg_dsn) as c:
            c.execute("BEGIN ISOLATION LEVEL READ COMMITTED")
            c.execute("SELECT balance FROM accounts WHERE id = %s", (ACC,))   # both read 100
            barrier.wait()                                                     # both are now inside
            try:
                results.append(debit(c, ACC, 100))                             # the code under test
                c.commit()
            except SerializationFailure:                                       # 40001: a legal outcome
                c.rollback(); results.append("retry")
    ts = [threading.Thread(target=attempt) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert balance(ACC) == 0                                       # exactly one debit landed
    assert sorted(results) in ([False, True], ["retry", True])     # the loser failed, typed
```

The barrier makes it deterministic, and therefore a regression test rather than a flake. Assert the final
balance **and** that the loser got a typed failure: a silent overwrite and a rejected second debit both leave
exactly one transaction reporting success.

**loom and jcstress apply to one thing: hand-written lock-free data structures.** loom exhaustively permutes
concurrent executions under the C11 memory model with state reduction (based on CDSChecker); jcstress
(`@JCStressTest`, `@State`, `@Actor`, `@Result`, `@Outcome`) is probabilistic and "requires substantial time to
catch all the cases." Almost no financial application code contains a lock-free structure; if your concurrency
is a database transaction or a mutex, both report nothing, and loom will not even see the code.
`fc.scheduledModelRun` is the closer analogue for async application code; it explores promise-resolution
orderings, which is where a JS money path's interleaving bugs live.

## Cost and blind spot, per technique

| Technique | Adoption cost | Catches | Structurally cannot find |
|---|---|---|---|
| `@given` property test on a pure function | hours; no infrastructure | rounding into invalidity, `qty == 0`, filter interaction, exponent assumptions | anything requiring a sequence or shared state |
| Round-trip through the real driver/codec | hours; needs a real DB in the test | `Float` columns, JSON precision loss above `2**53`, protobuf `double` | a value that never crosses the boundary in test |
| `RuleBasedStateMachine` + naive model | 1–3 days per component; the model is the cost | interleaved lifecycle bugs, in-memory dedupe (with a `restart` rule), unpostable reservations, error-code divergence | bugs the model shares with the implementation; anything outside the generated command alphabet |
| Generator-coverage assertions | hours once the machine exists | the blind spot that makes every other generated test a false negative | nothing, but it only reports what you thought to name |
| Mutation testing, money-math modules only | ~1 day to scope; minutes-to-hours per CI run | tests that execute money math without asserting its boundary | anything outside the mutated modules; equivalent mutants waste the reviewer's time |
| Differential vs a second implementation | hours if the incumbent still runs | rounding-mode drift, fee-tier changes, allocation remainder handling | a defect both implementations share |
| Differential vs the venue's reported fee/price | hours; runs in production, free | fee-tier and rebate changes no fixture encodes | anything the venue does not report |
| Two-connection barrier test | hours; one per contended row-shape | lost update, missing `40001` retry, a `FOR UPDATE` that was never taken | interleavings you did not name; anything not on that row |
| loom / jcstress | days, and only if the target exists | memory-model bugs in hand-written lock-free structures | database transactions, mutexes, anything not using loom's replacement types |
| Race detector in CI (`-race`, TSan) | ~1 hour; 5–10× memory, 2–20× time | in-process data races on executed paths | **double-spend**: a cross-transaction lost update is not a data race |
| Line coverage | free | untested files | whether a single executed line asserts anything |
