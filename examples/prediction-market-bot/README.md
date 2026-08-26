# A prediction-market bot you can run

Two implementations of the same bot, one fake venue in the same process, one frozen event
log, and a test suite that shows the difference in balances. Nothing here reaches the
network, there is no credential of any kind, and there is no live mode to switch on.

```
python3 examples/prediction-market-bot/demo.py        # watch the two disagree
python3 examples/prediction-market-bot/run_tests.py   # 86 cases, standard library only
```

`pytest examples/prediction-market-bot` runs the same cases where pytest is installed. The
cases are `unittest.TestCase`, so neither path needs a dependency.

## The scenario

`fixtures/script.json` is a frozen list of operator actions against an invented binary market
called `FAKE-BINARY-1`. Buy 100 YES at 0.40, filled 60 as maker and 40 as taker. Rest a bid
on NO and cancel it. Sell 30 YES at 0.70 as taker. The market resolves. `fixtures/session.jsonl`
is the event log the fake venue emits for that script, frozen byte for byte, and
`test_the_frozen_log_is_what_the_venue_produces` fails if the two ever drift apart.

Collateral is `FUSD`. Fees are charged in `FPOINT`, which is a different asset with a
different balance. A resting order holds both: the collateral a fill can spend, and the
worst-case fee a fill can owe. Amounts are integers in micro-units and there is no float in
the example.

The demo runs that log twice: once resolving to YES with a settlement reconnect that
redelivers the resolution, and once resolving half and half with no reconnect.

| | safe | unsafe |
|---|---|---|
| Scenario A, FUSD available | 1051.000000 | 1118.879000 |
| Scenario B, FUSD available | 1016.000000 | 978.879000 |

Neither run raises. The unsafe bot books the payout twice in A and books nothing in B.

## The files

| File | What it is |
|---|---|
| `fake_venue.py` | The stub venue. Idempotent on a client key, answers a lookup by that key, charges maker and taker fees, publishes a provisional determination before a terminal payout vector, and redelivers on reconnect. Signing is HMAC against a literal fake key, because the counterparty is a Python object. |
| `safe_bot.py` | The version that holds collateral and the worst-case fee while an order rests, treats a lost response as UNKNOWN, keeps fee amount, rate and asset apart, and credits a payout once. |
| `unsafe_bot.py` | The counter-example. Five design notes, all false, each refuted by a named test. |
| `fixtures/` | The market metadata, the operator script, and the frozen event log. |
| `tests/` | The cases, including the network guard that denies the process a socket. |
| `demo.py` | Runs both bots and prints the two balance columns above. |

## What the safe version is claimed to get right

Each row names the test that fails if the claim stops holding.

| Claim | Test |
|---|---|
| An accepted order holds its collateral for as long as it rests | `test_a_resting_buy_holds_its_collateral` |
| A cancel releases the hold, a partial fill releases only its slice | `test_a_cancel_releases_the_hold`, `test_a_partial_fill_releases_only_its_slice` |
| A resting order holds the worst-case fee in the asset the fee is charged in | `test_a_resting_order_holds_the_worst_case_fee_in_the_fee_asset`, `test_the_hold_is_taken_at_the_price_that_maximises_the_fee` |
| An order whose worst-case fee cannot be held never reaches the venue | `test_an_order_whose_fee_cannot_be_held_never_reaches_the_venue`, `test_an_order_whose_fee_asset_this_process_does_not_hold_cannot_rest`, `test_a_fill_pays_its_fee_out_of_what_the_order_held` |
| Orders resting at once hold the sum of their worst cases, never the larger | `test_two_resting_orders_reserve_the_sum_of_their_worst_cases`, `test_a_second_order_affordable_alone_is_refused_beside_the_first`, `test_the_fee_holds_add_up_the_same_way` |
| A sell locks shares, not collateral, and a sell from flat is refused | `test_a_sell_locks_shares_and_no_collateral`, `test_the_bot_refuses_to_sell_what_it_does_not_hold` |
| Short exposure is planned as a purchase of the complement | `test_a_short_is_planned_as_a_purchase_of_the_complement` |
| Maker and taker fills are both booked, each at its own rate | `test_a_maker_fill_is_charged_its_own_rate` |
| Fee amount, fee rate and fee asset stay three separate things | `test_the_fee_asset_and_the_collateral_asset_are_separate_balances`, `test_a_fee_in_an_asset_this_process_does_not_hold_is_refused` |
| State rebuilds from the logs deterministically | `test_two_rebuilds_of_one_log_are_byte_identical` |
| A redelivery changes nothing, with or without fresh event identities | `test_a_full_redelivery_changes_nothing`, `test_a_redelivery_under_fresh_identities_changes_nothing` |
| Illegal state and event pairs are refused with an error, never ignored | `test_a_second_acceptance_does_not_re_open_a_working_order`, `test_an_unknown_event_type_is_refused_not_ignored` |
| A terminal state accepts a late fill as a correction and is not re-opened | `test_a_late_fill_on_a_cancelled_order_is_a_correction_and_is_booked` |
| A response that does not prove the outcome leaves one order, never two | `test_a_lost_response_after_acceptance_leaves_exactly_one_order`, `test_a_lost_response_before_acceptance_resends_the_same_key` |
| The payout is credited exactly once, from the authority, as a vector | `test_the_payout_is_credited_exactly_once`, `test_the_credit_comes_from_the_authority_not_from_local_state`, `test_a_split_market_pays_the_vector_not_the_index` |
| A provisional determination moves no balance, and only the terminal payout state releases the value | `test_a_provisional_determination_moves_no_balance`, `test_only_the_terminal_payout_state_releases_the_value` |
| The terminal payout is not re-opened by a later determination | `test_a_determination_after_the_terminal_payout_pays_nothing`, `test_a_determination_is_provisional_and_the_resolution_is_the_payout` |
| Rounding dust is accounted rather than dropped | `test_rounding_leaves_dust_that_is_accounted_for` |
| The reconciliation detects a planted break | `test_a_planted_break_is_detected` |

## What the unsafe version gets wrong

| The design note | The number it produces | Test |
|---|---|---|
| "Collateral is committed when a trade happens, so nothing is held while an order rests" | The full balance reads as spendable while 40 FUSD of it is committed | `test_resting_orders_are_not_reserved` |
| "The fee is not owed until it is charged" | An order rests whose fill owes 1.680000 FPOINT against a 1.000000 FPOINT balance, and no balance shows it | `test_nothing_is_held_for_the_fee_the_fill_will_owe` |
| "A sell is a short, and a short reserves what it sells" | Off by `qty * (payout - 2 * price)`, which changes sign at 0.50 | `test_short_reserve_is_the_wrong_side` |
| "A repeated response is one we already handled" | 140 FUSD credited where 70 was owed | `test_settlement_is_credited_twice` |
| "The fee is one number at one rate" | 2.121000 taken from FUSD instead of 1.113000 from FPOINT, the maker fill charged a taker rate | `test_fee_is_taken_from_the_wrong_asset` |
| "A resolved market has a winner" | Zero credited on a split resolution | `test_split_market_credits_nothing` |

A sixth, on the submission path: a lost response retried under a fresh client key leaves two
live orders that no party can recognise as one intent, in
`test_an_ambiguous_submission_mints_a_second_order`.

## Offline by construction

`tests/netguard.py` patches `socket` before any test module is imported, from three places so
no runner misses it: `tests/__init__.py`, `conftest.py` for pytest, and `run_tests.py` for
unittest. Opening an `AF_INET` or `AF_INET6` socket, calling `socket.create_connection`, or
resolving a name raises `NetworkAccessDenied`. `tests/test_netguard.py` proves each of those,
proves an HTTP client cannot get out, and greps the three modules for an import of `requests`,
`urllib`, `http`, `socket`, `websocket` or `aiohttp`. There is no switch that turns the guard
off, so a live call cannot be reintroduced quietly.

## What this example is not

It is not a model of any named venue, and no number in it is evidence about one. Every
identifier, rate, key and price is invented. The signing key is a literal string in
`fake_venue.py` labelled as fake, and it authenticates nothing.

It is not durable. `SafeBot.orders` stands in for a committed intent table, `seen_events` for
a durable dedupe table, and `watermark` for a conditional `UPDATE ... WHERE v < :v` in the
same transaction as the effect. A dict is not a database and this example does not ship one.
What it does show is the ordering that makes the durable version correct: the intent and its
key exist before the call, and the guard is checked before the effect.

`SafeBot.reconcile` compares this process against the venue position by position, and a test
plants a break to prove it detects one. The example does not schedule it and does not ship an
alert destination. A reconciliation that nothing runs is not a control.

The cross-venue properties the fake venue implements, and the venue-specific detail it
deliberately leaves out, are in
`skills/fin-exchange-integration/references/prediction-market-core.md`.
