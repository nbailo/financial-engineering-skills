# Recorded fixtures, testnet fidelity, and dry-run optimism

The fixtures a money-path test replays against, and the ways they stop being evidence: a record mode that lets
CI call the live API, a matcher that ignores the request body, and a sandbox that is a different venue speaking
the same protocol. The required cassette set is the list of branches that lose money rather than the happy
path, and a dry-run engine is optimistic by construction: every documented assumption biases its PnL upward.

## Recorded fixtures

**Record from production endpoints and replay with `record_mode="none"`.** VCR.py's `none` mode "replay[s]
previously recorded interactions" and causes "an error to be raised for any new requests" (vcrpy usage docs). It
is the only mode that cannot turn a CI run into a live API call: `once` records when the cassette is missing,
`new_episodes` records any unrecorded request and silently absorbs a contract change, `all` re-records. Capture
by hand; pin CI to `none`.

Set `match_on` explicitly. The money is in the request body, and a matcher list that omits the body replays one
recorded response for two different order payloads: a bot that sends the wrong `quantity` passes.

```python
vcr = VCR(record_mode="none", cassette_library_dir="tests/cassettes",
          match_on=["method", "scheme", "host", "port", "path", "query", "body"],
          filter_headers=["X-MBX-APIKEY", "Authorization"],
          filter_query_parameters=["signature", "timestamp"])
```

Filtering `timestamp` and `signature` out of the match is mandatory for a signed API (otherwise no replayed
request matches) and out of the *stored* cassette, which is a file in your repo.

**The required cassette set** is reject, partial-fill-then-cancel, over-fill/residual, 429-then-418, and
mid-stream disconnect. Happy-path fills test nothing about the branches that lose money:

| Cassette | Must contain |
|---|---|
| `reject` | a real business rejection code and message: `-1013 Filter failure: LOT_SIZE`, `-2010`, `-4164` |
| `partial_fill_then_cancel` | two partials, then a cancel ack, with `leaves_qty` on each |
| `overfill_residual` | cumulative filled quantity **exceeding** the order quantity: the case nautilus discards by default (`allow_overfills` is `#[serde(default)]` ⇒ `false`, `crates/execution/src/engine/config.rs:61-65`), returning `None` from the fill report path (`reconciliation/orders.rs:785-796`) |
| `cancel_race` | `-2011 CANCEL_REJECTED` because the order filled between your decision and your cancel (expected in normal operation) and HTTP 409 with `-2021` (cancel failed, new order succeeded, or the reverse) or `-2022` (both failed) |
| `rate_limited` | a `429` **followed by** a `418`, with `Retry-After`; bans scale "from 2 minutes to 3 days" and are keyed on **IP**, not API key |
| `unknown_status` | `-1006`, `-1007`, and a bare HTTP 503 with no body |
| `mid_stream_disconnect` | a WebSocket cut inside a fill message; pair with the Toxiproxy `limit_data` toxic |

## Testnet and sandbox fidelity gaps

Testnet proves protocol conformance (serialization, auth, endpoint shape) and nothing else. Model it as *a
different venue that speaks the same protocol*, not a lower-fidelity production.

| Gap | Evidence | What it invalidates |
|---|---|---|
| Order books independent and unsynchronised with production | Binance testnet overview (doc-derived wiki) | any claim about queue position, spread, or realistic fills |
| Balances, orders and history wiped by periodic resets; only `/api` served, `/sapi` unavailable | same | any test whose setup assumes prior state; anything touching transfers, sub-accounts or account config |
| Filters and rate limits "are active but may be configured with different thresholds than production" | same | **the filter property test**: a testnet `exchangeInfo` fixture gives the wrong `tickSize` and the property test then proves nothing |
| Breaking API changes can land on testnet **first** | ccxt#17545: SPOT testnet implemented the `MIN_NOTIONAL` → `NOTIONAL` rename before production | both false passes and false failures |
| No real partial fills, no adverse selection, different latency | mechanism | any PnL, slippage or fill-ratio claim |

Drive filter values from a **production** `exchangeInfo` fixture captured as a cassette, and fail closed on any
unrecognised filter type. Gaps for non-Binance sandboxes were not researched; do not assume they are smaller.

A dry-run engine is optimistic by construction. freqtrade's documented assumptions are the list: market orders
"fill based on orderbook volume the moment the order is placed, with a maximum slippage of 5%"; limit orders
fill "once the price reaches the defined level"; limit orders crossing by more than 1% are converted to market
and filled immediately; and with `stoploss_on_exchange` "the stop_loss price is assumed to be filled": the
worst, because a stop assumed filled at the stop price is the one thing that never happens in the move that
triggers it. Dry-run is a plumbing test. Never gate on paper PnL; if you print it, print the assumptions too.
