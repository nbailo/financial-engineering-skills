# Fault injection, replay, and deterministic simulation

How to make a money path fail at the exact instant that costs money — between an external effect and the local
commit — and how to replay real traffic afterwards so the fix stays fixed. Covers proxy-level toxics, kill
harnesses at each atomic-phase boundary, recorded-fixture capture and the testnet fidelity gaps that make a
green suite meaningless, shadow diffing for rewrites, and the deterministic-simulation stack for systems with no
external oracle, including what a simulator systematically under-tests.

## Contents

- Ordering the campaign — which faults to inject first, and why
- Toxiproxy: the toxic table, stream direction, and the API calls
- The delivered-then-failed request, and its mirror — the canonical test, in full
- Atomic-phase boundaries, the kill harness, and write-ahead coverage
- Duplication and reordering harnesses for event consumers
- Clock-skew injection for anything that expires or orders by timestamp
- Recorded fixtures: `record_mode="none"`, matcher scope, and the required cassette set
- Testnet and sandbox fidelity gaps, and dry-run optimism
- Deterministic replay from a journal, and the same-seed byte-compare meta-test
- Shadow and dark-launch diffing for a money-path rewrite
- Deterministic simulation testing: what it buys, what it costs, the one principled trigger, protocol-aware DST
- Fuzzer blind spots, and why DST does not subsume an external adversarial pass

## Ordering the campaign

Do not start with disk corruption. Antithesis, testing four Raft implementations, reported that "network
partitions and turbulence were sufficient to surface examples of divergence (i.e. no node kill/restart required,
or disk corruption, or other faults)" — three HashiCorp Raft bugs in an hour. FDB's most productive nemesis is
not a symmetric partition either: *swizzle-clogging* clogs nodes' connections sequentially then "unclog[s] them
in a random order" — the asymmetric heal finds the bug.

| # | Fault class | Mechanism for a money path | Cost to build | What it finds |
|---|---|---|---|---|
| 1 | Delay / reorder / duplicate | replay the recorded event stream shuffled, with `injectDuplicates`, with a restart mid-stream | hours | in-memory dedupe sets, watermarks keyed on the live object, non-commutative `position +=` |
| 2 | Partition with **asymmetric** heal | Toxiproxy `timeout` on one stream direction only; unblock in a different order than you blocked | hours | the delivered-then-failed request; split-brain between your view and the venue's |
| 3 | Process kill at a chosen boundary | `SIGKILL` at four labelled points (below) | a day | write-ahead fields nothing reads back; foreign effect before local commit |
| 4 | Clock skew | freeze/step the injected clock; skew the venue's clock relative to yours | hours | expiry, `recvWindow`, signature tolerance, LWW on a balance |
| 5 | Storage corruption | bit flips, torn writes, sector loss | weeks | only if you wrote the storage engine — and even there the fault *model* is the limit; see the last section |

## Toxiproxy: toxics, stream direction, and the API

Toxiproxy (Shopify) is a TCP proxy whose faults are set at runtime over an HTTP API, so a test body drives them.

| Toxic | Attributes | Money-path use |
|---|---|---|
| `timeout` | `timeout` (ms) | **the delivered-then-failed request.** "Stops all data from getting through, and closes the connection after timeout. If timeout is 0, the connection won't close, and data will be delayed until the toxic is removed" |
| `latency` | `latency`, `jitter` | push the client past its own `recvWindow` / signature tolerance; make a race window wide enough to hit reliably |
| `reset_peer` | `timeout` | RST instead of FIN — the client sees `ECONNRESET`, which many HTTP stacks classify differently from a timeout |
| `down` | — | proxy refuses connections: the **definite** failure (nothing was delivered). Use it for the mirror case |
| `limit_data` | `bytes` | close after N bytes: a **mid-stream disconnect** inside a fill message |

**Stream direction is the whole trick.** Each toxic applies to `upstream` (client → server) or `downstream`
(server → client), independently. A `timeout` on `upstream` blocks the request from reaching the venue — a test
of nothing. On **`downstream`** it lets your bytes through, then stops the response and closes the connection:
the venue has your order and you do not know it.

```bash
curl -sX POST 127.0.0.1:8474/proxies -d '{"name":"venue","listen":"127.0.0.1:9443",
  "upstream":"stub-venue.test:8443","enabled":true}'
curl -sX POST 127.0.0.1:8474/proxies/venue/toxics -d '{"name":"swallow_response",
  "type":"timeout","stream":"downstream","toxicity":1.0,"attributes":{"timeout":2000}}'
curl -sX DELETE 127.0.0.1:8474/proxies/venue/toxics/swallow_response
curl -sX POST 127.0.0.1:8474/reset          # clear every toxic on every proxy
```

Two operational notes. **TLS**: `127.0.0.1:9443` breaks SNI and certificate verification against the real
hostname — terminate TLS at a local stub venue behind the proxy, which also gives you the assertable request
log. **Client timeouts**: one shorter than the toxic's yields a client-side abort rather than a server close,
and `requests.Timeout` and `ECONNRESET` often land in different `except` arms.

## The delivered-then-failed request, and its mirror

**A test that stubs the call to raise before delivery tests nothing.** The failure mode is the request that
*arrived*. Binance says so in writing: "It is important to **NOT** treat this as a failure operation; the
execution status is **UNKNOWN** and could have been a success" (rest-api general information); it has three
unknown-status signals — `-1006` and `-1007` both carry "Execution status unknown" (`errors.md`) — plus a socket
timeout with no code at all. Build the fake venue so it records the request *before* deciding how to fail.

```python
class StubVenue:                       # records the order FIRST, then decides how to fail
    def __init__(self): self.orders, self.request_log = {}, []

    async def post_order(self, body, mode="ok"):
        self.request_log.append(body)
        coid = body["newClientOrderId"]
        self.orders.setdefault(coid, {"clientOrderId": coid, "orderId": len(self.orders) + 1,
                                      "status": "NEW", "executedQty": "0"})
        if mode == "delivered_then_timeout":
            await asyncio.sleep(30); raise ConnectionResetError   # > the client timeout
        if mode == "delivered_then_503":
            return Response(503, b'{"code":-1007,"msg":"Timeout waiting for response from backend '
                                 b'server. Send status unknown; execution status unknown."}')
        return Response(200, json.dumps(self.orders[coid]).encode())

@pytest.mark.parametrize("mode", ["delivered_then_timeout", "delivered_then_503"])
async def test_timeout_that_already_filled(bot, venue, mode):
    venue.mode = mode
    await bot.submit_entry(symbol="BTCUSDT", side="BUY", qty=Decimal("0.01"))

    posts = [r for r in venue.request_log if r["path"] == "/api/v3/order"]
    assert len(posts) == 1, f"resubmitted: {posts}"         # 1. no resubmission
    sent_coid = posts[0]["newClientOrderId"]
    queries = [r for r in venue.request_log if r["method"] == "GET"]
    assert any(q.get("origClientOrderId") == sent_coid     # 2. queried by the minted id
               for q in queries), f"never queried by minted id; issued {queries}"
    assert len(venue.orders) == 1                          # 3. exactly one effect
    assert bot.open_order_count() == 1
    assert bot.position() == venue.filled_qty(sent_coid)
```

Assertion 2 is the one that fails on real code. CCXT's documented recovery for a timed-out `createOrder` calls
`fetchOrders()`/`fetchOpenOrders()`/`fetchClosedOrders()` and, if the order is not `'open'`, `fetchBalance()`
"to check if the balance has changed" — which any other strategy, fee or funding payment invalidates. And its
single REST funnel retries on `e instanceof OperationFailed` (`ts/src/base/Exchange.ts:6435`) with no
HTTP-method discrimination, where `RequestTimeout extends NetworkError extends OperationFailed`
(`ts/src/base/errors.ts:219`, `:177`, `:171`) — so a `POST` create-order is re-sent on the same terms as a `GET`
ticker, with the *identical* `newClientOrderId` (`ts/src/binance.ts:6969`), unique on Binance only among
**open** orders.

Two documented venue behaviours the test must pin down. **`-2013 NO_SUCH_ORDER` is not proof the order was not
created**: Binance documents three data sources — Matching Engine, Memory, Database — with different staleness
and "if it cannot find the value it's looking for it will check the next one", so a query issued right after
placement can miss an order that exists; hummingbot's `_lost_order_count_limit` defaults to `3`
(`client_order_tracker.py:45`) and a lost order **stays fillable** (`:107-109`). And **a 200 means accepted, not
executed**: OKX, "Successful response only means the request has been accepted by the exchange".

**The mirror case.** Run the same test with the effect genuinely absent and assert the retry **does** fire —
without it, a bot that never retries anything passes the ambiguity test and quietly stops trading on every blip.

| Case | Injection | Venue state after | Required behaviour |
|---|---|---|---|
| Ambiguous | `timeout` toxic, `stream: downstream` (or 5XX after recording) | order exists | query by minted id; **no** resubmit; converge to one order |
| Definite non-delivery | Toxiproxy `down`, or connection refused before any byte left | no order | resubmit **with the same minted id**, exactly once |
| Definite business reject | venue returns `-1013 Filter failure: LOT_SIZE` / `-2010` | no order | terminal failure for that intent; no resubmit; no "unknown" state |

The third row is the one people collapse into the second. Jepsen on TigerBeetle's client: it "do[es] not surface
networking failures and instead will continuously retry a request until receiving a reply," which "unnecessarily
convert[s] definite errors into indefinite ones" — `ECONNREFUSED` proves the operation did not execute and the
retry loop destroys that proof. The asymmetry runs the other way too: a `429` on an order endpoint is **not**
documented by any venue in this research as proof of non-creation, so treat it as unknown. Hummingbot holds both
errors in one codebase: its create path funnels a socket timeout into the same terminal `FAILED` state as a
`min_order_size` rejection via an unqualified `except Exception` (`exchange_py_base.py:466`), while its cancel
path changes no state on `asyncio.TimeoutError` (`:539-546`).

## Atomic-phase boundaries, the kill harness, and write-ahead coverage

An **atomic phase** is the set of local mutations between two foreign mutations (brandur.org/idempotency-keys):
"Even foreign calls within your own infrastructure count! It's tempting to treat emitting records to Kafka as
part of atomic operations… They're not." Four injection points, not one.

| # | Kill point | The defect it exposes | Assert after restart |
|---|---|---|---|
| B0 | after the intent COMMIT, before the call | none — this is the safe boundary. It exists to prove the intent row is durable and carries the minted id | recovery finds a `PENDING` intent, queries the venue by its minted id, resolves to placed-or-not-placed; exactly one effect |
| B1 | after the call, before the outcome write | the foreign effect exists and nothing local records it | exactly one venue order; exactly one local record; position matches |
| B2 | after the outcome write, before the publish | downstream consumers never see the fill; ledger and risk diverge from execution | the publish is re-driven from local state; consumers dedupe on the venue's own id; no second effect |
| B3 | **inside the recovery pass itself** | recovery is not re-entrant: it advances a cursor before applying, or applies twice | a second recovery run is a no-op; final state identical to a single clean run |

B3 is routinely omitted and is where the second-order bugs live — the classic indexer shape is
`saveCursor(tx, {lastBlock: toBlock})` running unconditionally outside the guard that gated the query, so an
empty address table lets the cursor sprint past blocks nobody scanned.

```python
def crash_point(name: str) -> None:            # named, compiled in, env-gated. No sleeps.
    if os.environ.get("CRASH_AT") == name:
        os.kill(os.getpid(), signal.SIGKILL)

async def submit_entry(self, intent):
    coid = mint_client_order_id(intent.id)     # intent-instance identity
    await self.db.execute("INSERT INTO order_intent (intent_id, client_order_id, symbol, side, qty,"
        " phase) VALUES (%s,%s,%s,%s,%s,'PENDING') ON CONFLICT (intent_id) DO NOTHING",
        (intent.id, coid, intent.symbol, intent.side, intent.qty))
    await self.db.commit();                                        crash_point("B0")
    resp = await self.venue.post_order(client_order_id=coid, **intent.payload())
    crash_point("B1");  await self.record_outcome(coid, resp)      # phase -> 'PLACED'
    crash_point("B2");  await self.bus.publish(OrderPlaced(coid, resp["orderId"]))

@pytest.mark.parametrize("point", ["B0", "B1", "B2", "B3"])
def test_kill_at_phase_boundary(point, stub_venue, pg):
    p = subprocess.Popen([sys.executable, "-m", "bot"], env={**os.environ, "CRASH_AT": point})
    p.wait(timeout=30)
    assert p.returncode == -signal.SIGKILL     # it really died there, not cleanly
    recover = [sys.executable, "-m", "bot", "--recover-once"]
    subprocess.run(recover, check=True, timeout=30)
    assert len(stub_venue.orders) == 1
    assert pg.scalar("SELECT count(*) FROM order_intent WHERE phase='PLACED'") == 1
    assert pg.scalar("SELECT count(*) FROM fills") == stub_venue.fill_count()
    subprocess.run(recover, check=True, timeout=30)
    assert len(stub_venue.orders) == 1         # B3: recovery is idempotent
```

Use `os.kill(os.getpid(), SIGKILL)`, never `sys.exit()` or an exception — those run `finally` blocks and flush
buffers, the machinery a real kill denies you. Assert `returncode == -signal.SIGKILL` so a crash point that
stopped firing turns the test red rather than green.

**Every write-ahead field must be read back:** persisting the write-ahead client order id is the easy half,
and the half that gets skipped is actually consuming it on the recovery path. In the failing shape
`phase=BUY_PLACED` is journalled before the POST but `buy_order_id` only after, so `resume()` calls
`get_order(None)` and raises `ValueError` on exactly the crash the journal exists for. Make the coverage
mechanical: enumerate every column written between the intent INSERT and the call site; run the B1 recovery
against a database where **every other column is NULL or poisoned**, and if it still succeeds it is not using
the field; assert the field appears in the recovery query's *parameters*, not merely that recovery returned.

The same audit applies to dedupe state. An in-memory `set` for `_seen_trade_ids` is the default reach and does
not survive the process, so the standard restart recovery — a REST backfill of recent fills — re-applies every
already-counted fill. **Dedupe state must be persisted in the same transaction as the state it protects**; the
B2 kill plus a backfill proves it, and Helland's generalisation is that dedup state must travel with the entity
when it is repartitioned or it silently resets. Inverted form: committing an *unresolvable* event id to
`processed_stripe_events` means the processor never redelivers and the miss is permanent — mark an event
processed only when it was applied, and dead-letter the rest with an alert.

## Duplication and reordering harnesses

Reconnect replay and at-least-once delivery are the normal case. Build the harness as transforms over one
recorded stream, asserting conservation **after every step, not only at the end**:

```python
@given(seed=st.integers())
def test_terminal_state_invariant(seed):
    rng, baseline = random.Random(seed), None
    for name, events in [("recorded",   list(SESSION)),
                         ("duplicated", [e for e in SESSION for _ in range(1 + (rng.random() < .3))]),
                         ("swapped",    swap_one_adjacent_pair(SESSION, rng)),
                         ("restarted",  SESSION)]:      # driver kills the consumer mid-stream
        c = Consumer()
        for i, e in enumerate(events):
            c.apply(e)                                  # asserted after EVERY step, and recomputed
            head = events[: i + 1]                      # independently of c's own accumulators
            assert c.position == sum_signed_fills(head), f"{name}@{i}"
            assert c.cash == -sum_notional(head) - sum_fees(head)
        baseline = baseline or c.snapshot()
        assert c.snapshot() == baseline, f"{name} diverged"
```

Then assert the **generator produces the interesting cases** — jqwik's `injectDuplicates()` and
`Statistics.coverage`, or FDB's `TEST(cond)` macros whose cross-run hit counts reveal whether a scenario is
generated at all. Two questions: does the generator ever emit a duplicate *at a terminal state*, and does it
ever restart *between* the effect and the outcome write? The first is the ghost-order resurrection — `if
existing is not None and ts < existing.update_time: return` skips the guard entirely once a terminal event has
popped the order, so a replayed pre-snapshot `PARTIALLY_FILLED` re-inserts a phantom open order — a common
defect in hand-rolled order-state trackers. The order-invariance form ships upstream: nautilus's
`test_avg_px_invariant_to_fill_arrival_order` asserts ascending and descending fill arrival produce
byte-identical `avg_px` (`crates/model/src/orders/mod.rs:1769`).

## Clock-skew injection

Test skew for anything that expires, ages out, or orders by timestamp; Jepsen's TigerBeetle nemesis used skews
from milliseconds to hundreds of seconds. Inject the clock as a parameter — never `time.time()` in the core.

| Surface | Documented behaviour | Skew test |
|---|---|---|
| Binance `recvWindow` | default 5000 ms, max 60000; the entry check is `timestamp < serverTime + 1s && serverTime - timestamp <= recvWindow`, and the matching engine re-checks at its own boundary — so `-1021` under a *synced* clock is a latency signal, not a clock signal | skew your clock **fast** by >1 s and assert the client detects `-1021` and re-syncs rather than widening `recvWindow`; assert repeated `-1021` under a synced clock raises a latency alarm |
| Stripe webhook signature | timestamp with a default 5-minute tolerance; "Don't use a tolerance value of `0`. Using a tolerance value of `0` disables the recency check entirely" | skew ±tolerance±1s and assert accept/reject on both sides of the edge |
| Quote / order TTL, lease expiry | compares a remote timestamp against local `now()` | skew both directions: a live quote read as expired loses revenue; an expired quote read as live is arbitraged |

A large `recvWindow` is not a fix, only a wider window in which a stale order still reaches the book. There is
no skew test for last-write-wins on a money row: assert the code contains none.

## Recorded fixtures

**Record from production endpoints and replay with `record_mode="none"`.** VCR.py's `none` mode "replay[s]
previously recorded interactions" and causes "an error to be raised for any new requests" (vcrpy usage docs). It
is the only mode that cannot turn a CI run into a live API call: `once` records when the cassette is missing,
`new_episodes` records any unrecorded request and silently absorbs a contract change, `all` re-records. Capture
by hand; pin CI to `none`.

Set `match_on` explicitly. The money is in the request body, and a matcher list that omits the body replays one
recorded response for two different order payloads — a bot that sends the wrong `quantity` passes.

```python
vcr = VCR(record_mode="none", cassette_library_dir="tests/cassettes",
          match_on=["method", "scheme", "host", "port", "path", "query", "body"],
          filter_headers=["X-MBX-APIKEY", "Authorization"],
          filter_query_parameters=["signature", "timestamp"])
```

Filtering `timestamp` and `signature` out of the match is mandatory for a signed API — otherwise no replayed
request matches — and out of the *stored* cassette, which is a file in your repo.

**The required cassette set.** Happy-path fills test nothing about the branches that lose money:

| Cassette | Must contain |
|---|---|
| `reject` | a real business rejection code and message — `-1013 Filter failure: LOT_SIZE`, `-2010`, `-4164` |
| `partial_fill_then_cancel` | two partials, then a cancel ack, with `leaves_qty` on each |
| `overfill_residual` | cumulative filled quantity **exceeding** the order quantity — the case nautilus discards by default (`allow_overfills` is `#[serde(default)]` ⇒ `false`, `crates/execution/src/engine/config.rs:61-65`), returning `None` from the fill report path (`reconciliation/orders.rs:785-796`) |
| `cancel_race` | `-2011 CANCEL_REJECTED` because the order filled between your decision and your cancel — expected in normal operation — and HTTP 409 with `-2021` (cancel failed, new order succeeded, or the reverse) or `-2022` (both failed) |
| `rate_limited` | a `429` **followed by** a `418`, with `Retry-After`; bans scale "from 2 minutes to 3 days" and are keyed on **IP**, not API key |
| `unknown_status` | `-1006`, `-1007`, and a bare HTTP 503 with no body |
| `mid_stream_disconnect` | a WebSocket cut inside a fill message — pair with the Toxiproxy `limit_data` toxic |

## Testnet and sandbox fidelity gaps

Testnet proves protocol conformance — serialization, auth, endpoint shape — and nothing else. Model it as *a
different venue that speaks the same protocol*, not a lower-fidelity production.

| Gap | Evidence | What it invalidates |
|---|---|---|
| Order books independent and unsynchronised with production | Binance testnet overview (doc-derived wiki) | any claim about queue position, spread, or realistic fills |
| Balances, orders and history wiped by periodic resets; only `/api` served, `/sapi` unavailable | same | any test whose setup assumes prior state; anything touching transfers, sub-accounts or account config |
| Filters and rate limits "are active but may be configured with different thresholds than production" | same | **the filter property test** — a testnet `exchangeInfo` fixture gives the wrong `tickSize` and the property test then proves nothing |
| Breaking API changes can land on testnet **first** | ccxt#17545: SPOT testnet implemented the `MIN_NOTIONAL` → `NOTIONAL` rename before production | both false passes and false failures |
| No real partial fills, no adverse selection, different latency | mechanism | any PnL, slippage or fill-ratio claim |

Drive filter values from a **production** `exchangeInfo` fixture captured as a cassette, and fail closed on any
unrecognised filter type. Gaps for non-Binance sandboxes were not researched — do not assume they are smaller.

A dry-run engine is optimistic by construction. freqtrade's documented assumptions are the list: market orders
"fill based on orderbook volume the moment the order is placed, with a maximum slippage of 5%"; limit orders
fill "once the price reaches the defined level"; limit orders crossing by more than 1% are converted to market
and filled immediately; and with `stoploss_on_exchange` "the stop_loss price is assumed to be filled" — the
worst, because a stop assumed filled at the stop price is the one thing that never happens in the move that
triggers it. Dry-run is a plumbing test. Never gate on paper PnL; if you print it, print the assumptions too.

## Deterministic replay from a journal

LMAX's business logic processor is single-threaded, in-memory and event-sourced: "the current state of the
Business Logic Processor is entirely derivable by processing the input events", and a production bug is
diagnosed by copying "the sequence of events to their development environment and replay[ing] them there"
(martinfowler.com/articles/lmax.html). The precondition is a list of bans inside the core — no wall-clock reads,
no RNG, no I/O, no map-iteration-order dependence — and external interaction split into an output event plus a
later input event. Two tests make the claim real:

```python
def test_replay_reconstructs_live_state(journal, live_engine):
    replayed = Engine.from_journal(journal.read_all())
    assert replayed.serialize() == live_engine.serialize()   # byte-for-byte, not "approximately"

def test_same_seed_is_byte_identical(tmp_path):              # meta-test: prove determinism
    a, b = (run_sim(seed=0xC0FFEE, trace=tmp_path / n) for n in ("a.trace", "b.trace"))
    assert a.read_bytes() == b.read_bytes()
```

The second is not ceremony. S2, retrofitting deterministic simulation onto async Rust, needed exactly this
byte-compare of TRACE logs across two runs of one seed to locate its determinism leaks: HTTP timestamp headers
inserted by dependencies, Rust's DOS-hardened randomized `HashMap` iteration order, and dependency-internal
threads and clocks (s2.dev/blog/dst). Any one makes a "deterministic" replay emit false divergences until
somebody switches the detector off.

**Replaying a production event stream against a new build** is the strongest form: capture a window of real
input events, run the candidate offline over them, diff its output events against the incumbent's recorded ones
— no stubs, no sandbox, because the core makes no external calls.

## Shadow and dark-launch diffing

For a money-path rewrite at T2 and above, production traffic plus the incumbent is the cheapest strong oracle
available.

- **Run the new implementation on the same inputs with its effects disabled** — not "a feature flag at 1%". If
  it cannot run with effects disabled, that is a design finding about the rewrite, not a reason to skip the
  diff.
- **Compare economic outputs, not internal state:** amount and currency in minor units, account or instrument,
  side/sign, fee, and the identity that would have been minted. Diffing intermediate fields trains people to
  ignore the diff.
- **Tolerance is zero, or one minor unit with a named reason** — where rounding differs, fix the rounding.
  Classify every divergence before cutting over: new-path bug, incumbent bug (common and valuable), or
  documented behaviour change.
- **Cut over on a stated volume of clean diff, not elapsed time** — "N million events with zero unexplained
  divergences, including one of each cassette shape above" is a different claim from "two weeks green". Keep the
  diff running after cutover with the old path in shadow; it becomes the rollback trigger.

Uber's dual-write migration used the same idea as its safety net: an `EntityChangeLog` consumer detects
**version gaps** and back-fills. The tolerance and volume thresholds above are mechanism-derived.

## Deterministic simulation testing

**The principled trigger is Axis B: no external oracle exists to reconcile against.** A bot reconciles against
the exchange, a payments integrator against the processor. A matching engine, custodian or system-of-record
ledger cannot — it *is* the oracle — so a bug is undetectable after the fact and the proof burden moves before
deployment, into a simulator. Complexity, team size and importance are not triggers.

**What it buys** is reproducibility first, fault coverage second. FoundationDB found deterministic reproduction
so much more productive than production debugging that when a bug escaped, the team improved the simulator until
it reproduced there and only then debugged (foundationdb.org/files/fdb-paper.pdf). TigerBeetle measured "3.3
seconds of VOPR simulation gives you 39 minutes of real-world testing time" — about an hour of simulation per
month of real time; FDB reports roughly 10:1. **What it costs** is closing the system to nondeterminism:

| Cost | FoundationDB | madsim / turmoil (Rust) |
|---|---|---|
| Concurrency, time, entropy | all code deterministic, multithreaded concurrency avoided, "one database node is deployed per core"; time and randomness from the Flow runtime | single-threaded executor scheduling every spawned task, plus libc overrides for `clock_gettime`, `getrandom`, `getentropy`, `CCRandomGenerateBytes` |
| Dependencies | "unable to test third-party libraries or dependencies, or even first-party code not implemented in Flow. As a consequence, we have largely avoided taking dependencies on external systems"; and "Simulation is not able to reliably detect performance issues" | every external environment must be simulated; one missed source of entropy destroys determinism |

**Three techniques people skip, and they are the ones that make it work.** `buggification` injects
unusual-but-legal behaviour at named points *inside production code* — a legal error, a delay, an unusual tuning
parameter — biasing execution toward the dangerous path. FDB **randomises tuning parameters** so "specific
performance tuning values do not accidentally become necessary for correctness". And `TEST(cond)` coverage
macros, whose hit counts across runs say whether a scenario is generated at all.

**Antithesis** collapses part of that cost by supplying the deterministic environment — "The Antithesis
environment is fully deterministic. This makes every bug we find perfectly reproducible" — which is why *buying*
DST is recommended rather than wasteful at T2 while building it is not. The Raft result calibrates expectations:
one minimal property ("all replicas apply the same sequence of commands in the same order", via a hash-chained
state machine), partitions alone, three bugs in an hour. Vendor source; note the incentive.

**Protocol-aware DST** is the T3-and-you-wrote-consensus-or-storage extension: plain DST checks *system-level*
invariants, and the marginal value is in *per-replica internal* ones — cross-replica commit checksum equality,
LSM metadata checksum equality across levels, byte-for-byte superblock/grid/client-reply equality — plus deep
liveness properties such as "replicas should never wind up in a state where they need to coordinate" when logs
are uncorrupted (tigerbeetle.com/blog/2026-08-20-protocol-aware-dst/).

## Fuzzer blind spots, and why DST does not subsume an adversarial pass

sled's simulation guide claims the approach yields systems "Jepsen will not find bugs in". TigerBeetle ran the
VOPR "24/7 on 1024 cores" and Jepsen still found two safety bugs plus seven crashes. Both misses generalise:

- **Generator blind spot.** Both merge-capable fuzzers generated queries sharing a common prefix in their target
  fields, so matching objects were always *consecutive in each index* and the zig-zag merge join's **probe**
  branch was never exercised; multi-predicate queries silently returned truncated suffixes, 1 result where 9
  were expected (#2544). "The VOPR's seemingly sophisticated approach to query generation created a blind spot
  that hid a real bug." The trading analogue: a generator whose fills always sum to the order quantity never
  executes the over-fill and residual-dust branches.
- **Fault-model blind spot.** The VOPR corrupted whole sectors — always caught by checksums, always repaired.
  Jepsen flipped single bits in unused padding, which passed the checksums and tripped a defensive assertion,
  panicking a replica that could have repaired itself (#2681a/b).
- **The model was the finding.** Jepsen's checker was a ~1,600-line single-threaded Clojure reference model of
  accounts, transfers and error codes, written from the documentation by someone who did not share the
  implementers' assumptions — the one property an internal simulator structurally cannot have.

**A simulator injects the faults its author imagined and generates the cases its generator produces.** At T3
both DST and an external adversarial pass are required; the second's value is that someone else wrote it.
