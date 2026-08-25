# Model-based testing against a naive reference model

Driving an implementation and a naive reference model from one generated command sequence, in the four
framework families a money codebase uses. The model is worthless if it shares the implementation's
misunderstanding, and the generator binds before the framework does.

## Contents

- Hypothesis `RuleBasedStateMachine`: a complete ledger model
- fast-check, proptest-state-machine, jqwik
- Writing the naive reference model
- Make the workload traceable, or the checker cannot attribute state
- Generator-coverage assertions

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
