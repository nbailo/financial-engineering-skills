# Fail-closed, halt levels, and where an assertion belongs

What the system stops doing while a break is open, which of the six halt levels a runtime invariant fires, and
when a money path may crash instead.

## Contents

- Fail-closed policy: what stops, what must keep working, who reopens the gate
- Halt levels: naming the blast radius in the code, and the six prohibitions
- Assertion placement: provenance, not exposure, decides between a crash and a typed guard

## Fail-closed policy

The reconciliation result is an input to a risk gate, not just to a dashboard. Name the scope and the level in
the code, at the call site.

| Break state | What stops | What must keep working | Reopened by |
|---|---|---|---|
| Position/balance mismatch on one instrument | new and increasing exposure **on that instrument** | `cancel_all`, `flatten`, `close`, settle, with a test proving they work while the gate is closed | a **successful reconcile**, never a timer, and never the code path that closed the gate |
| Overfill (venue reports more filled than ordered) | trading on that instrument | reads, cancels | successful reconcile after the fill is booked |
| Break on a customer-facing balance | debits from that account | reversals and clawbacks against the frozen account | reviewed repair job |
| Reconciliation job itself failed to run or errored | treat as **unknown**, not as clean | everything else | next successful run |

**An overfill is an unreconciled economic fact: record it as a break and stop trading that instrument. Never
silently drop it and never silently clamp it.** `nautilus_trader` ships the opposite default:
`allow_overfills: bool` with `#[serde(default)]` ⇒ `false` (`execution/src/engine/config.rs:61-65`), and with
`false` the fill report is **discarded entirely** (`return None`, `reconciliation/orders.rs:785-796`) while
the live path `anyhow::bail!`s (`engine/mod.rs:3600-3624`). The venue sent you units; your model now disagrees
with the venue by exactly that amount, traced only by a `WARN`.

Startup is the one place to be strictly harsher: gate `start_trader()` on a successful startup reconciliation
and abort if it fails, rather than starting and reconciling concurrently.

## Halt levels: naming the blast radius in the code, and the six prohibitions

"Halt" names six different actions with wildly different blast radii. Name which one, in the code, at the
call site. This applies to any runtime invariant that fires on a money path, not only to a reconciliation
break.

| # | Halt level | What it does | Obligations |
|---|---|---|---|
| 1 | Reject the operation | this call fails, typed; process fine | untouched |
| 2 | Freeze one aggregate | writes to that account/symbol refused; reads still serve | untouched |
| 3 | Fail-closed (risk-off) | no new or increasing exposure; cancel, close, flatten, settle stay hot | actively managed |
| 4 | Cancel-all plus disconnect the emitter | withdraw resting orders, sever order entry; risk and drop-copy stay up | actively managed |
| 5 | Quiesce | stop accepting *and* producing; drain; deliver or explicitly void everything already produced | drained, then frozen |
| 6 | Process abort | `panic` / `exit` | **abandoned** |

Evaluate the predicates in order; first match wins. The last column names who designs the response;
`fin-verification` proves the response exists, is at the smallest scope that provably contains the breach,
and is reachable by a test.

| Observable predicate | Response | Designed in |
|---|---|---|
| No external effect yet, and the check runs in the same transaction as the write | **Level 1**, typed, terminal for that idempotency key. Never `log.warn` and proceed; never clamp into range | `fin-money-core`, `fin-ledger` |
| Wrong value in your own store; no counterparty acted on it; no open position | **Level 2**, write path only. **No automatic corrective write**; repair is a separate reviewed job | `fin-ledger` |
| An external record disagrees with yours, and money left / a fill happened / a customer saw the balance | Neither halt nor silent reversal: raise a named, aged **break** record and quarantine the disputed amount so no path spends, nets or sweeps it; keep the rest operating and escalate on a clock. A corrective posting waits for an established cause | `fin-ledger` |
| A position, working order or obligation exists whose value moves without you acting | **Level 3.** Record the true value, alert, close the risk gate for that scope. `cancel_all` and `flatten` MUST work while it is closed, with a test proving it. Reopen only on a successful reconcile, never on a timer, never by the code path that closed it | `fin-exchange-integration` |
| Own output exceeded its bound relative to its input (`orders_out` vs `orders_in`, `shares_issued` vs authorised, `payouts` vs instructions) | **Level 4**, automatic. The bound is checked **on the emit path before the send**, not by a monitor, and the flag must not be resettable by the component that tripped it | the venue-side matching and settlement design |
| Recomputable from an append-only log that passes its own checksums | Mark the view stale; return a typed `Stale{as_of}`, never a stale number, never zero; rebuild | `fin-ledger` |
| Every value in the relation was produced by this process, with no network, file, config or clock | **Level 6.** Crash. Provenance is the discriminator: a term that crossed a network, a file, a config or a clock is an operating error and takes a typed fail-closed guard instead | `fin-verification` |

Six prohibitions, each traceable to an incident, and each a discipline failure rather than an ignorance
one:

- **Never abort a process holding unmanaged obligations.** Ariane 501: *"It was the decision to cease the
  processor operation which finally proved fatal."*
- **Never let the failure path create state while the system is live and aberrant**: no retry, no
  resubmit, no hot rollback. Knight ¶27: *"This action worsened the problem."*
- **Never disable the failing check as the mitigation.** NASDAQ 2012 removed the validation code from the
  failover path to get the cross out, and that is what created the error position.
- **Never silently drop the violating event, and never clamp a reported quantity into range.** The units
  were really received. nautilus's `allow_overfills` defaults to `false`, which discards the fill report
  entirely.
- **Never gate the risk-reducing path on the same flag that gates the risk-increasing path.**
- **Never implement a halt by severing the transport.** A halt means the engine is quiesced AND everything
  already produced is delivered or explicitly voided.

Where the invariant can be transiently false by design, give it a self-heal window before escalating. LULD
waits 15 seconds in Limit State before pausing. A check that halts on a momentarily-inconsistent
intermediate state is itself an availability bug.

## Assertion placement

Both answers in the corpus are correct, and the difference is deployment topology, not rigour. TigerBeetle
asserts in production at about 1 per 10.6 lines (487 in 5,166, `TIGER_STYLE.md:104-113`), asserting *"the
positive space that you do expect AND the negative space that you do not expect"*, on six replicas with
state in a replicated WAL: *"assertions downgrade catastrophic correctness bugs into liveness bugs"*, and a
ledger at rest loses nothing. nautilus compiles its three `debug_assert!`s out of release, because a panic
in a process holding open orders leaves exposure unmanaged, and an unattended resting order keeps filling.

Jepsen's counterweight: an assertion on **recoverable** state turns a repairable fault into an outage, which
is what the padding bit-flip did.

The discriminator is provenance, not exposure. The Polymarket overfill assertion looked like a programmer
error and was an operating error: the violating `last_qty=5.012345` against `quantity=5.000000` came from
the venue, so it needed a typed fail-closed guard and a break record, not a panic. Name the provenance of
every term in the asserted relation first; if any of them crossed a network, a file, a config or a clock, it
is not an assertion.
