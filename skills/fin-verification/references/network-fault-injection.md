# Network fault injection: toxics, the ambiguous request, and clock skew

How to make a money path fail at the instant that costs money: the request the counterparty received and then
failed to acknowledge. Which faults to inject first, the toxics that produce them, and skew for anything that
expires or orders by timestamp.

## Contents

- Ordering the campaign: which faults to inject first, and why
- Toxiproxy: the toxic table, stream direction, and the API calls
- The delivered-then-failed request, and its mirror: the canonical test, in full
- Clock-skew injection for anything that expires, ages out, or orders by timestamp

## Ordering the campaign

Do not start with disk corruption. Antithesis, testing four Raft implementations, reported that "network
partitions and turbulence were sufficient to surface examples of divergence (i.e. no node kill/restart required,
or disk corruption, or other faults)": three HashiCorp Raft bugs in an hour. FDB's most productive nemesis is
not a symmetric partition either: *swizzle-clogging* clogs nodes' connections sequentially then "unclog[s] them
in a random order"; the asymmetric heal finds the bug.

| # | Fault class | Mechanism for a money path | Cost to build | What it finds |
|---|---|---|---|---|
| 1 | Delay / reorder / duplicate | replay the recorded event stream shuffled, with `injectDuplicates`, with a restart mid-stream | hours | in-memory dedupe sets, watermarks keyed on the live object, non-commutative `position +=` |
| 2 | Partition with **asymmetric** heal | Toxiproxy `timeout` on one stream direction only; unblock in a different order than you blocked | hours | the delivered-then-failed request; split-brain between your view and the venue's |
| 3 | Process kill at a chosen boundary | `SIGKILL` at four labelled points (`crash-boundary-tests.md`) | a day | write-ahead fields nothing reads back; foreign effect before local commit |
| 4 | Clock skew | freeze/step the injected clock; skew the venue's clock relative to yours | hours | expiry, `recvWindow`, signature tolerance, LWW on a balance |
| 5 | Storage corruption | bit flips, torn writes, sector loss | weeks | only if you wrote the storage engine, and even there the fault *model* is the limit; see `deterministic-simulation.md` |

## Toxiproxy: toxics, stream direction, and the API

Toxiproxy (Shopify) is a TCP proxy whose faults are set at runtime over an HTTP API, so a test body drives them.

| Toxic | Attributes | Money-path use |
|---|---|---|
| `timeout` | `timeout` (ms) | **the delivered-then-failed request.** "Stops all data from getting through, and closes the connection after timeout. If timeout is 0, the connection won't close, and data will be delayed until the toxic is removed" |
| `latency` | `latency`, `jitter` | push the client past its own `recvWindow` / signature tolerance; make a race window wide enough to hit reliably |
| `reset_peer` | `timeout` | RST instead of FIN: the client sees `ECONNRESET`, which many HTTP stacks classify differently from a timeout |
| `down` | (none) | proxy refuses connections: the **definite** failure (nothing was delivered). Use it for the mirror case |
| `limit_data` | `bytes` | close after N bytes: a **mid-stream disconnect** inside a fill message |

**Stream direction is the whole trick.** Each toxic applies to `upstream` (client → server) or `downstream`
(server → client), independently. A `timeout` on `upstream` blocks the request from reaching the venue: a test
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
hostname; terminate TLS at a local stub venue behind the proxy, which also gives you the assertable request
log. **Client timeouts**: one shorter than the toxic's yields a client-side abort rather than a server close,
and `requests.Timeout` and `ECONNRESET` often land in different `except` arms.

## The delivered-then-failed request, and its mirror

**A test that stubs the call to raise before delivery tests nothing.** The failure mode is the request that
*arrived*. Binance says so in writing: "It is important to **NOT** treat this as a failure operation; the
execution status is **UNKNOWN** and could have been a success" (rest-api general information); it has three
unknown-status signals: `-1006` and `-1007` both carry "Execution status unknown" (`errors.md`), plus a socket
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
"to check if the balance has changed", which any other strategy, fee or funding payment invalidates. And its
single REST funnel retries on `e instanceof OperationFailed` (`ts/src/base/Exchange.ts:6435`) with no
HTTP-method discrimination, where `RequestTimeout extends NetworkError extends OperationFailed`
(`ts/src/base/errors.ts:219`, `:177`, `:171`), so a `POST` create-order is re-sent on the same terms as a `GET`
ticker, with the *identical* `newClientOrderId` (`ts/src/binance.ts:6969`), unique on Binance only among
**open** orders.

Two documented venue behaviours the test must pin down. **`-2013 NO_SUCH_ORDER` is not proof the order was not
created**: Binance documents three data sources (Matching Engine, Memory, Database) with different staleness
and "if it cannot find the value it's looking for it will check the next one", so a query issued right after
placement can miss an order that exists; hummingbot's `_lost_order_count_limit` defaults to `3`
(`client_order_tracker.py:45`) and a lost order **stays fillable** (`:107-109`). And **a 200 means accepted, not
executed**: OKX, "Successful response only means the request has been accepted by the exchange".

**The mirror case.** Run the same test with the effect genuinely absent and assert the retry **does** fire;
without it, a bot that never retries anything passes the ambiguity test and quietly stops trading on every blip.

| Case | Injection | Venue state after | Required behaviour |
|---|---|---|---|
| Ambiguous | `timeout` toxic, `stream: downstream` (or 5XX after recording) | order exists | query by minted id; **no** resubmit; converge to one order |
| Definite non-delivery | Toxiproxy `down`, or connection refused before any byte left | no order | resubmit **with the same minted id**, exactly once |
| Definite business reject | venue returns `-1013 Filter failure: LOT_SIZE` / `-2010` | no order | terminal failure for that intent; no resubmit; no "unknown" state |

The third row is the one people collapse into the second. Jepsen on TigerBeetle's client: it "do[es] not surface
networking failures and instead will continuously retry a request until receiving a reply," which "unnecessarily
convert[s] definite errors into indefinite ones"; `ECONNREFUSED` proves the operation did not execute and the
retry loop destroys that proof. The asymmetry runs the other way too: a `429` on an order endpoint is **not**
documented by any venue in this research as proof of non-creation, so treat it as unknown. Hummingbot holds both
errors in one codebase: its create path funnels a socket timeout into the same terminal `FAILED` state as a
`min_order_size` rejection via an unqualified `except Exception` (`exchange_py_base.py:466`), while its cancel
path changes no state on `asyncio.TimeoutError` (`:539-546`).

## Clock-skew injection

Test skew for anything that expires, ages out, or orders by timestamp; Jepsen's TigerBeetle nemesis used skews
from milliseconds to hundreds of seconds. Inject the clock as a parameter, never `time.time()` in the core.

| Surface | Documented behaviour | Skew test |
|---|---|---|
| Binance `recvWindow` | default 5000 ms, max 60000; the entry check is `timestamp < serverTime + 1s && serverTime - timestamp <= recvWindow`, and the matching engine re-checks at its own boundary, so `-1021` under a *synced* clock is a latency signal, not a clock signal | skew your clock **fast** by >1 s and assert the client detects `-1021` and re-syncs rather than widening `recvWindow`; assert repeated `-1021` under a synced clock raises a latency alarm |
| Stripe webhook signature | timestamp with a default 5-minute tolerance; "Don't use a tolerance value of `0`. Using a tolerance value of `0` disables the recency check entirely" | skew ±tolerance±1s and assert accept/reject on both sides of the edge |
| Quote / order TTL, lease expiry | compares a remote timestamp against local `now()` | skew both directions: a live quote read as expired loses revenue; an expired quote read as live is arbitraged |

A large `recvWindow` is not a fix, only a wider window in which a stale order still reaches the book. There is
no skew test for last-write-wins on a money row: assert the code contains none.
