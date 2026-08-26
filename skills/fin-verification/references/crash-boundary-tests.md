# Crash boundaries: the kill harness and write-ahead coverage

Kill the process at each boundary between local and foreign mutations, restart, run recovery, and assert
exactly one external effect and exactly one local record.

## Atomic-phase boundaries, the kill harness, and write-ahead coverage

An **atomic phase** is the set of local mutations between two foreign mutations (brandur.org/idempotency-keys):
"Even foreign calls within your own infrastructure count! It's tempting to treat emitting records to Kafka as
part of atomic operations… They're not." Four injection points, not one.

| # | Kill point | The defect it exposes | Assert after restart |
|---|---|---|---|
| B0 | after the intent COMMIT, before the call | none: this is the safe boundary. It exists to prove the intent row is durable and carries the minted id | recovery finds a `PENDING` intent, queries the venue by its minted id, resolves to placed-or-not-placed; exactly one effect |
| B1 | after the call, before the outcome write | the foreign effect exists and nothing local records it | exactly one venue order; exactly one local record; position matches |
| B2 | after the outcome write, before the publish | downstream consumers never see the fill; ledger and risk diverge from execution | the publish is re-driven from local state; consumers dedupe on the venue's own id; no second effect |
| B3 | **inside the recovery pass itself** | recovery is not re-entrant: it advances a cursor before applying, or applies twice | a second recovery run is a no-op; final state identical to a single clean run |

B3 is routinely omitted and is where the second-order bugs live; the classic indexer shape is
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

Use `os.kill(os.getpid(), SIGKILL)`, never `sys.exit()` or an exception; those run `finally` blocks and flush
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
not survive the process, so the standard restart recovery (a REST backfill of recent fills) re-applies every
already-counted fill. **Dedupe state must be persisted in the same transaction as the state it protects**; the
B2 kill plus a backfill proves it, and Helland's generalisation is that dedup state must travel with the entity
when it is repartitioned or it silently resets. Inverted form: committing an *unresolvable* event id to
`processed_stripe_events` means the processor never redelivers and the miss is permanent; mark an event
processed only when it was applied, and dead-letter the rest with an alert.
