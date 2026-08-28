# One invoice, end to end

One customer pays one invoice through a payment processor, the money is recorded in a double-entry ledger, a settlement report
arrives, and reconciliation checks the two agree. Two implementations of it, one fake processor, frozen fixtures.

```
python3 examples/payment-ledger-reconciliation/demo.py        # watch the two disagree
python3 examples/payment-ledger-reconciliation/run_tests.py   # 142 cases, standard library
```

`run_tests.py` is the standard-library path and needs nothing installed. The cases are also `unittest.TestCase`, so
`pytest examples/payment-ledger-reconciliation` runs them **if pytest is already installed**. Neither path adds a dependency,
and both install the network guard first.

## The run

Both paths meet the same three events: an ambiguous charge timeout, an injected failure between the charge and the local write,
and one settlement delivered twice under a fresh `delivery_id` and a fresh `event_id`. The delivery ids differ and the event ids
differ, so what the two deliveries share is the stable effect identity the processor owns. The demo delivers them one after the
other; two workers delivering at once is a separate test, not something the demo shows. The report's fee disagrees with the
processor's by 25 minor units on purpose, and nothing announces itself: the only exception raised anywhere is the injected one.

| | safe | unsafe |
|---|---|---|
| charges at the processor | 1 | 3 |
| the customer was charged | 125.00 | 375.00 |
| `assets:cash:bank` | 121.25 | 121.00 |
| `assets:receivable:processor` | 0.00 | -125.00 |
| `expense:suspense` | 0.00 | 125.00 |
| open breaks / at stake | 1 / USD 0.25 | 1 / USD 125.00 |

## One append, one identity

`Ledger.commit_once` is the only way an entry is appended, and the effect id and its economic fingerprint are fields on the entry:
one append records the money and the identity that caused it, so an entry without its dedupe identity is not a state this code can
reach; the same fingerprint returns the existing entry and a changed one is refused
(`test_every_entry_carries_the_effect_identity_that_caused_it`, `test_an_entry_with_no_effect_identity_cannot_be_committed`,
`test_the_dedupe_lookup_is_the_entries_themselves` (which asserts the ledger keeps no second table for an identity to go missing from),
`test_the_same_identity_and_fingerprint_returns_the_entry_and_moves_nothing`,
`test_the_same_identity_with_a_changed_fingerprint_is_refused`). That is co-location, not durability: these are dicts and a list,
and the entry cannot diverge from its identity because they are one object; production needs both in one database transaction, with
a unique constraint on the effect id. The intent transition is the one write still outside that append, and it is redoable - a
failure injected after the entry is visible leaves exactly one entry, one identity and a PENDING intent the next retry finishes
without posting again (`test_a_retry_finishes_the_missing_transition_and_posts_nothing_again`,
`test_recovery_finishes_the_missing_transition_as_well`).

## What the safe version is claimed to get right

| Claim | Test |
|---|---|
| An amount is an exact positive integer in minor units and a currency is canonical, checked before an intent exists and again at the processor boundary; a float, a bool, zero or a negative has zero external effects | `test_an_amount_that_is_not_an_exact_positive_integer_has_zero_external_effects`, `test_a_currency_that_is_not_a_canonical_code_has_zero_external_effects`, `test_the_same_rule_is_enforced_at_the_processor_boundary` |
| The identity is committed before the call that uses it | `test_the_identity_is_committed_before_the_send` |
| Every operation is pinned to one authority - provider, account, region - and a retry through another provider, account or region is refused before anything is sent or asked, as is a settlement for an intent pinned elsewhere | `test_the_intent_persists_the_scope_it_was_pinned_to`, `test_a_captured_intent_cannot_be_retried_through_another_account`, `test_a_pending_intent_cannot_be_retried_through_another_region`, `test_a_worker_bound_elsewhere_recovers_nothing`, `test_the_processor_refuses_a_request_addressed_to_another_scope`, `test_the_authority_of_a_bound_worker_cannot_be_swapped_underneath_it`, `test_a_settlement_for_an_intent_pinned_elsewhere_posts_nothing` |
| An existing pending intent is asked about at its pinned authority before anything is resent, and both shapes of an ambiguous timeout are handled: after the processor accepted, by asking; before it accepted, by replaying under the same key | `test_a_direct_retry_of_a_pending_intent_asks_before_it_sends_anything`, `test_a_timeout_after_the_processor_accepted_is_resolved_by_the_lookup`, `test_a_timeout_before_the_processor_accepted_is_replayed_under_the_same_key` |
| Every retry is built from the stored intent, and a replay that changes a bound field is refused whether the intent is pending or captured | `test_every_retry_is_built_from_the_stored_intent_and_not_from_the_caller`, `test_a_replay_that_changes_the_amount_is_refused_in_the_captured_path`, `test_a_replay_that_changes_the_amount_is_refused_in_the_pending_path`, `test_a_replay_that_changes_a_bound_field_is_refused` |
| Every answer is checked before it is believed - its shape first, so a processor answer that is not a mapping and an identifier that names nothing refuse instead of raising in the caller: a capture must carry the persisted key, invoice, amount, currency and scope, a settlement must name itself, its batch and the exact charge asked about, and an answer about some other charge posts nothing | `test_a_lookup_answering_with_another_operations_capture_posts_nothing`, `test_a_capture_answer_missing_the_persisted_key_posts_nothing`, `test_a_settlement_naming_another_charge_posts_nothing`, `test_a_settlement_whose_amounts_do_not_add_up_posts_nothing`, `test_an_answer_about_some_other_charge_posts_nothing`, `test_no_processor_answer_shape_posts_or_escapes_into_the_caller` |
| An injected failure after the effect leaves a pending intent and no postings, and a new worker over the same in-memory store completes it with no second economic effect; a fault before the append, a refused posting set or an intent this store does not hold leaves no identity behind; one effect id carrying different fields is refused | `test_an_injected_failure_after_the_charge_leaves_a_pending_intent_and_no_postings`, `test_a_new_worker_over_the_same_store_completes_it_with_no_second_charge`, `test_a_fault_before_the_append_leaves_the_event_retryable`, `test_a_refused_posting_set_leaves_no_identity_behind`, `test_an_effect_whose_intent_update_names_an_unknown_key_writes_nothing`, `test_one_effect_id_carrying_different_economic_fields_is_refused` |
| Postings come from the processor, never from the notification body, and settlement, charge and batch are structured fields rather than memo text | `test_a_tampered_payload_posts_the_processors_numbers_and_not_its_own`, `test_the_settlement_charge_and_batch_are_fields_and_not_memo_text`, `test_an_event_of_an_unexpected_type_never_posts`, `test_an_event_that_disagrees_about_the_currency_never_posts`, `test_an_event_for_a_charge_the_processor_does_not_know_never_posts` |
| Balances name a currency; USD is never summed with EUR, and a break spanning two currencies keeps both amounts separate instead of adding them | `test_usd_and_eur_are_never_summed_into_one_balance`, `test_balances_are_keyed_by_account_and_currency`, `test_a_mixed_usd_and_eur_break_keeps_both_amounts_separate`, `test_one_settlement_held_in_two_currencies_is_never_summed_into_one_figure` |
| A correct amount attributed to the wrong charge or the wrong batch is a break, and its amounts are not compared to an identity that does not match | `test_the_right_amount_on_the_wrong_charge_is_a_break`, `test_the_right_amount_in_the_wrong_batch_is_a_break`, `test_the_matching_attribution_is_compared_normally` |
| The report is input and not truth: a `lines` that is not a list, a missing or non-integer total, a line that is not a record, one with no identity and an amount that is not an integer are each reported and excluded from comparison | `test_lines_that_are_not_a_list_leave_nothing_to_compare`, `test_a_report_with_no_declared_total_at_all_is_a_break`, `test_a_declared_total_that_is_not_an_integer_is_a_break`, `test_a_line_that_is_not_a_record_at_all_is_a_break`, `test_a_malformed_line_beside_a_good_one_is_excluded_and_the_good_one_compared`, `test_an_amount_that_is_not_an_integer_is_a_break_and_is_never_compared` |
| Reconciliation compares the full union of both sides' identities, checks the report's own arithmetic, and does not report one exposure twice | `test_a_settlement_only_the_report_claims_is_a_report_only_break`, `test_a_settlement_only_the_ledger_holds_is_a_ledger_only_break`, `test_a_settlement_the_ledger_recorded_twice_is_a_duplicate_entry_break`, `test_one_duplicated_line_is_one_exposure_whatever_the_total_claims`, `test_a_line_whose_net_and_fee_do_not_make_its_gross_is_a_break`, `test_a_declared_total_the_lines_do_not_add_up_to_is_a_break`, `test_one_declared_total_cannot_answer_for_two_currencies`, `test_the_planted_break_is_one_compound_row_with_one_amount_at_stake` |
| A correction closes one real open break: it is keyed by break identity, links to the break and to the entry it corrects, replays idempotently, and refuses a fabricated or already-closed one - while a later, genuinely different break on the same settlement is its own identity | `test_reconciliation_records_the_break_it_found`, `test_a_break_closes_only_by_an_approved_correction_that_adds_an_entry`, `test_the_correction_effect_is_keyed_by_the_break_and_not_the_settlement`, `test_the_correction_entry_links_to_the_break_and_to_the_entry_it_corrects`, `test_replaying_an_approved_correction_moves_nothing_a_second_time`, `test_a_fabricated_break_has_nothing_to_close`, `test_a_break_that_is_already_closed_is_refused_a_second_correction`, `test_a_later_genuinely_different_break_is_its_own_identity_and_can_be_closed` |
| A worker keeps **no authoritative payment or dedupe state** - the intents, the effect identities and the open breaks all live in the shared `Store` and its ledger - and each concurrency case builds two separate workers rather than calling one bound method twice, asserting that exactly **one** got inside the critical section against **two** in the unsafe pair, so the count is the shared lock working and not a harness that failed to run two threads | `test_two_separate_workers_paying_one_intent_at_once_charge_once`, `test_two_separate_workers_recovering_at_once_post_exactly_one_set`, `test_two_separate_workers_delivering_at_once_credit_exactly_once` |
| The whole run is identical five times over | `test_five_consecutive_runs_are_identical`, `test_the_balances_are_what_the_demo_prints_and_the_trial_balance_is_zero` |

**Ambiguity.** No universal "never send again" rule, and no claim of one. Query first, then follow the provider's contract:
this fake records a charge before it can lose an answer, so a lookup miss means the request never arrived and replaying the exact
stored request under the same key is safe. Another provider needs a different step two. **Exposure is per currency**:
`exposure(breaks)` returns a figure for each currency and never one integer. A report line's currency is checked non-blank but
not canonical, because reconciliation has to be total and cannot refuse its input, so exposure keys are the counterparty's own
labels: never summed together, but not guaranteed canonical either. **No queue**: an event for a charge this store has not
captured, or one the processor has not settled, is refused and returned to the caller to alert on, not parked - a real integration
needs a durable queue, and this one has none.

## What the unsafe version gets wrong

| The design note | The number it produces | Test |
|---|---|---|
| "A timeout means it did not go through, so send it again under a fresh key" | the customer is charged twice for one invoice | `test_a_timeout_resend_charges_the_customer_twice` |
| "Nothing durable is needed before the call" | the charge exists and nothing local knows it | `test_an_injected_failure_after_the_charge_leaves_nothing_to_recover` |
| "Every delivery has an id, so dedupe on the delivery id" | 242.50 credited where 121.25 was owed | `test_a_redelivery_is_credited_twice` |
| "The dedupe identity belongs to the worker" | two workers share nothing and both credit | `test_two_separate_workers_share_no_dedupe_state_and_both_credit` |
| "The webhook body says what the money was" | a tampered payload posts 9999.99 | `test_the_notification_body_is_trusted_and_posted_verbatim` |
| "A break is a bookkeeping gap: post it to suspense" | 125.00 hidden, and cash and fees then match the report | `test_the_break_is_plugged_into_suspense_and_the_amounts_then_agree` |

## Which skill owns which property

- `fin-money-core`: minor units, rounding direction, one identity per economic decision, both shapes of an ambiguous outcome, durable dedupe, the shared-state guard.
- `fin-payments`: the idempotency key, the authority scope, the processor as authority over a notification, redelivery, the settlement report.
- `fin-ledger`: balanced immutable posting sets, currency as a dimension of a balance, a correction as a new entry, suspense as the anti-pattern.
- `fin-verification`: the planted break, injected-failure recovery, replay and two-worker tests, the offline guard, five identical runs.

## Offline by construction

`tests/netguard.py` patches the network primitives before any test module is imported, from three places so no runner misses it:
`tests/__init__.py`, `conftest.py` and `run_tests.py`. Opening an internet socket, connecting or resolving a name raises
`NetworkAccessDenied`, and no switch turns it back on. `tests/test_netguard.py` proves each of those, then parses **every Python
file in the example tree** with `ast` and rejects networking and process imports, `eval`/`exec`/`compile`/`__import__`, shell and
popen calls, importlib-based dynamic imports, aliased imports such as `import socket as s`, and reaching the builtins table by
string (`test_every_escape_route_is_rejected`, `test_the_ordinary_modules_this_example_uses_are_accepted`,
`test_nothing_outside_the_guard_reaches_for_a_shell_a_socket_or_eval`). The guard has to name a socket, so its path - and only its
path - is allowlisted (`test_the_allowlist_is_one_path_and_that_path_still_needs_it`). It is a review that runs on every commit, not
an OS sandbox: it cannot stop code that is already running.

## What this is not

Not a model of any named processor: every identifier and rate is invented. There is no external or live processor; the counterparty
is an object in this process. Not durable: the store's tables are dicts, where production needs a unique constraint on the effect id
and the entry written in the same transaction. The reverse gap - an entry that survives while its identity is lost - cannot
happen here because the two are one append; a system that keeps them apart can hit it, and reconciliation catches it only where the
duplicated entry reaches the ledger and the report disagrees, so it is not universally detected. This reconciliation is not
scheduled and has no alert destination, and a control nobody runs is not a control.
