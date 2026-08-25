# The proof set for a small live bot

Live, own capital, one venue, a few hundred lines. Five tests and one scheduled comparison carry the risk,
and a bot with them is in far better shape than one with 95% line coverage of its indicator math. Two of
them ship as executable code in `fin-exchange-integration`.

1. **`test_timeout_that_already_filled`**: the ambiguity worth testing is the request that arrived, applied
   to order submission. A Toxiproxy `timeout` toxic on the downstream, or a stub that delivers then hangs.
   Assert: no resubmit; the bot calls its query-by-`clientOrderId` path; exactly one order exists. Run the
   mirror case, `test_provable_presend_failure_retries`, where the failure is provably pre-send (`down`
   toxic: connection refused), and assert the documented retry path fires.
2. **`test_normalize_satisfies_every_filter_simultaneously`**: a normalised instruction is legal against
   every venue constraint at once, or is an explicit skip, and is never larger than what was asked for.
   Proven as a property test over generated (price, size) in the boundary region of the real constraint set,
   one run per instruction type. Assert all constraints in one pass, that normalisation never returns zero
   without an explicit skip signal, and that rounding moves toward validity and never increases size beyond
   available balance. Drive the constraint values from a **production** fixture: a testnet one carries
   different filter thresholds and received the `MIN_NOTIONAL` to `NOTIONAL` rename **before** production
   (ccxt #17545). On one venue that reads: `price % tickSize == 0`, `qty % stepSize == 0`,
   `price*qty >= minNotional`, `minQty <= qty <= maxQty`, a LIMIT/MARKET parameterisation to exercise
   `MARKET_LOT_SIZE`, and filter values loaded from `exchangeInfo`.
3. **`test_fill_stream_replayed_shuffled_and_duplicated`**: every arrival order produces the same state, on
   one recorded session: place, partial, partial, amend/cancel, reject, disconnect, reconnect-with-replay.
   Assert `position == sum(signed filled qty)` and `cash == -sum(price*qty) - sum(fees)` computed
   **independently of the bot's own accumulators**, and that its fee equals the venue's reported fee per
   fill. Then feed the same stream with duplicates and one swapped pair and assert identical terminal state.
4. **`test_kill_after_send_places_no_second_order`**: one crash boundary, not three. `SIGKILL` between
   "request sent" and "order persisted locally"; restart; assert startup reconciliation converges to exactly
   one order and the correct position. If the bot has no startup reconciliation, this test is what forces
   you to write one.
5. **`test_limits_hold_and_ambiguity_halts`**: a property test over generated sequences of fills, rejects
   and reconnects. Assert that at no point does position exceed `max_position`, notional exceed
   `max_notional`, or orders-per-minute exceed the cap; and that an ambiguous reconciliation result puts the
   bot in a state that places no new orders **while cancels still work**.

**Plus the daily comparison**, which is not optional: one scheduled entrypoint asserting
`sum(signed fills) == venue position` and `local free balance == venue balance` per asset, joined on the
venue's `tradeId`, alerting to a config key with no default. It owes its own detect-test.

Deliberately not asked for at this size, and say so when asked why: model-based testing, deterministic
simulation, race detectors, mutation testing, Jepsen, loom. None of their triggers is present. The bot's
dominant risk is a duplicate order after a timeout, not consensus divergence, and a reference model is a
second implementation to maintain.

**The trap that is not in any table.** Hypothesis sets `derandomize=True` automatically in CI and its
example database is a local `.hypothesis/examples` directory CI discards. A team expecting fresh random
examples per run gets the same ones forever, losing every counterexample. Commit each as `@example(...)`.
