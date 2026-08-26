# The five properties, and one instantiation in Python

The skill states five properties a venue client must prove. They are properties, not test files: implement
them in the repository's own language and framework. This file explains what each one has to establish, what a
test that looks right can fail to establish, and shows one worked instantiation in Python so the shape is
concrete. A Rust or Go version asserts the same five things with a fault-injecting transport double and a
table-driven or `proptest` / `quickcheck` generator over the same production fixture.

## Contents

- Property 1: a lost response creates no duplicate economic effect
- Property 2: only a provable pre-send failure follows the retry path
- The retired test, and why a harness-knowledge assertion is not a requirement
- Property 3: a normalised order satisfies every venue constraint simultaneously
- Property 4: reconnect and backfill neither lose nor double-count a fill
- Property 5: local position and PnL converge to the venue's state
- One instantiation, in Python
- The same properties in a compiled language

## Property 1: a lost response creates no duplicate economic effect

The instruction **is delivered** to the venue and the answer is lost. What the test must establish:

1. the client does not send the instruction again;
2. the client queries the venue about the identity it sent;
3. exactly one instruction exists at the end.

The third assertion is the one that catches real bugs. A client can satisfy the first two and still end up
with two orders, because the recovery path built a fresh identity for the query and then placed against it.

Fault injection: a proxy such as toxiproxy with a `timeout` toxic, or a stub transport that forwards the
request upstream and then returns a 503 to the caller. The distinction that matters is that the request
reached the venue, so the venue's state has changed and the client's knowledge has not.

## Property 2: only a provable pre-send failure follows the retry path

**A retry is permitted only when the production code path itself establishes that the economic instruction
could not have become externally visible.** Those are the failures that occur before transmission begins:

| Failure | Why the code can prove it |
|---|---|
| DNS resolution failure | no connection was opened |
| Connection refused | the TCP handshake was rejected |
| TLS handshake failure | no application bytes were written |
| Local validation or serialization rejection | the request never left the process |

Everything else is UNKNOWN: a timeout, a socket error once transmission may have begun, any 5xx, a 429, and
any rejection the venue does not document as "not enqueued" for that exact code. ccxt's default retry funnel
re-POSTs a create-order under the identical client order ID; set `options['maxRetriesOnFailure']` to 0 and
do not rely on any of its recovery behaviour, including its documented `fetchBalance()` procedure, which
races against fees, funding and other strategies.

So the test injects a failure from the table above and asserts that the retry happens. It asserts the retry
happens **because the failure is one the production code can classify**, not because the harness knows the
request was dropped.

## The retired test, and why a harness-knowledge assertion is not a requirement

An earlier version of this skill shipped a test named `test_timeout_that_never_arrived`, which used a stub
method `drop_before_upstream_then_fail` and asserted `venue.post_count("/order") == 2`, that is, that the
retry does happen.

That assertion is wrong as a requirement. The harness knows the request was dropped before it reached
upstream. The production code sees a 503, or a timeout, and **cannot distinguish that case** from one where
the venue received the order and the response was lost. Encoding the harness's private knowledge as an
assertion on production behaviour requires the client to resubmit in a situation where resubmitting can double
the position. A client that passes that test is less safe than one that fails it.

The mirror case is still worth testing, and Property 2 is the correct form of it: inject a failure the code
can genuinely classify as pre-send, and assert the retry. If a suite has no such case at all, Property 1 can
pass by doing nothing, since a client that never retries anything satisfies it trivially. That is the real
concern the retired test was reaching for.

## Property 3: a normalised order satisfies every venue constraint simultaneously

Generate over the **boundary region of the real constraint set**, for each instruction type, from a fixture
captured from production rather than hand-written. For every generated input the output is either an explicit
skip signal or legal against all constraints at once, and never larger than what was asked for. Parametrising
over order type is what exercises `MARKET_LOT_SIZE` as distinct from `LOT_SIZE`.

## Property 4: reconnect and backfill neither lose nor double-count a fill

Kill the client mid-session with fills arriving, restart it, and assert that each fill appears exactly once in
the persisted set, that running the same recovery twice produces identical state, and that no order is
submitted before the ready gate opens.

## Property 5: local position and PnL converge to the venue's state

Drive a session of fills, funding, and at least one venue-originated event such as a liquidation or a
settlement, then run the scheduled reconciliation and assert convergence per position key, within a tolerance
expressed in the instrument's own tick or lot. Then plant a break and assert the reconciliation **detects**
it, closes the gate, and does not reopen the gate on a timer.

Replay the same session shuffled, duplicated and interrupted by a restart, and assert it reaches the state the
arrival-order run reached. The property is convergence, not commutativity: both runs agree because both sort the
persisted fills into the venue's canonical order before folding. Do not assert that realized PnL is invariant to the
*economic* order, which under FIFO, LIFO or average cost it is not. Arrival order must not change it; economic order
must.

Ship the negative half of the same property. Strip or blank the venue's ordering fields on one fill so the canonical
order cannot be established from the venue's own data, and assert the fold **rejects** rather than falling back to
arrival order, insertion order or a timestamp it invented. A guessed sequence produces a realized number that looks
like every other realized number, so nothing downstream disagrees with it.

## One instantiation, in Python

This is one language's version, not the specification. `bot` is the client under test and `venue` is a
fault-injecting transport double.

```python
# (a) Property 1: the instruction was delivered, the answer was lost
def test_lost_response_creates_no_duplicate(bot, venue):
    # toxiproxy `timeout` toxic, or a stub: request IS delivered upstream, then 503
    venue.deliver_upstream_then_fail("POST /order", status=503)
    bot.place(SYMBOL, "BUY", qty, px)
    assert venue.post_count("/order") == 1                            # MUST NOT resubmit
    assert venue.was_queried_by(client_id=bot.last_client_order_id)   # MUST query by client ID
    assert len(bot.all_orders(SYMBOL)) == 1                           # exactly one order, not two


# (b) Property 2: a failure the code itself can classify as pre-send
@pytest.mark.parametrize("fault", ["dns_failure", "connection_refused", "tls_handshake_failure"])
def test_provable_pre_send_failure_may_retry(bot, venue, fault):
    venue.fail_before_transmission("POST /order", fault=fault)   # raises before any byte is written
    bot.place(SYMBOL, "BUY", qty, px)
    assert venue.post_count("/order") == 2       # the retry is permitted: nothing became visible
    assert bot.last_client_order_id == bot.first_client_order_id   # same identity, not a new one


# (c) Property 3: the filter property test
FILTERS = load_fixture("exchangeInfo.BTCUSDT.json")   # captured from PRODUCTION, not hand-written
@pytest.mark.parametrize("order_type", ["LIMIT", "MARKET"])   # exercises MARKET_LOT_SIZE
@given(price=decimals_near(minPrice, minNotional / minQty, tick_decimals + 3),
       qty=decimals_near(minQty, minNotional / price, step_decimals + 3))
def test_normalize_satisfies_every_filter_simultaneously(price, qty, order_type):
    out = normalize(price, qty, FILTERS, order_type)
    if out is SKIP:                     # an explicit skip signal is a legal outcome
        return
    assert out.qty != 0                 # zero is never a silent result
    assert out.price % tick == 0 and out.qty % step == 0        # Decimal, not float
    assert out.price * out.qty >= min_notional
    assert min_qty <= out.qty <= max_qty
    assert out.qty <= qty               # rounding toward validity never increases size
```

Note the second assertion in (b): the retry reuses the **same identity**. A retry that mints a fresh client
order ID is not a retry of that instruction, it is a second instruction, and it defeats the recovery path even
when the retry itself was permitted.

## The same properties in a compiled language

Nothing above is Python-specific. In Rust, the transport double is a trait object injected into the client and
the generator is `proptest`; in Go, an interface implementation and a table-driven test over the same fixture
file, with `testing/quick` or an explicit boundary table where a shrinking generator is not available. What
must not change across languages is the fixture source, which is production, and the four assertions in (a),
which are what make the test about economic effect rather than about HTTP.
