# H-300: one school overage purchase is fulfilled at most once (beta)

Owner approved the fix for beta on 2026-11-09 (relayed by the SM). Branch
task/h300-double-grant off origin/beta a3736b83c. The survey (roads, verdict,
characterization run on staging) is on scratch branch task/h300-survey,
docs/evidence/h300/NOTES.md there (114d67d3f, a71bc5dc3; not pushed).

## The bug, in one paragraph

`StripeWebhookHandler._handle_license_overage_checkout_completed` stops a
second run only when `intent.status == COMPLETED`. A purchase where a
teacher left before payment is saved FAILED with its grants made, so a
second run of the same checkout grants the remaining teachers again and
writes a second BillingTransaction. On beta the road is: the worker commits
and dies before SUCCEEDED, the sweeper marks the started claim FAILED, and
`replay_stripe_events --apply` re-runs it (or a redelivery claims the FAILED
row). The license-inactive branch, re-run, also writes a second
BillingTransaction.

## The fix (declared)

In the handler, right after the intent is locked:

1. Already fulfilled: return when `intent.completed_at is not None`
   (written only on the grant path; the only writer of this model's
   completed_at), or when a CreditLedger row carries
   `metadata.intent_id == str(intent.id)`, scoped to the wallets of the
   intent's teachers (the unscoped JSON lookup is a sequential scan of
   CreditLedger, see `_overage_already_granted`). The existing COMPLETED
   check stays.
2. Already recorded as not granted: return when the intent is FAILED and
   already holds this session's PaymentIntent (the unpaid and
   license-inactive branches write it), so a replay writes no second
   BillingTransaction.

Log lines only (logger.info); no user-facing text changes.

## Red 01 (declared before the run)

billing.tests.test_h300_reprocess, count from build_suite: **4**.

| Test | Predicted on beta today |
|---|---|
| b1 skipped teacher, road B replay | FAIL: `(10000, 10000, 0) != (5000, 5000, 0)`, the double grant |
| b4 control, full purchase, road B replay | PASS: the COMPLETED guard holds |
| c1 license inactive, road B replay | FAIL: `2 != 1` BillingTransactions |
| d1 belt: ledger row alone, completed_at cleared, replay | FAIL: `(10000, 10000) != (5000, 5000)` |

Predicted: **Ran 4, FAILED (failures=3)**. Log: `01_red.log`.

Red 01 result (`01_red.log`): **Ran 4, FAILED (failures=2)**: b1
`(10000, 10000, 0) != (5000, 5000, 0)` and d1 `(10000, 10000) != (5000,
5000)`; b4 and c1 pass. **Prediction wrong**: c1 was predicted to fail
(`2 != 1`) and passed.

Why: `BillingTransactionService.record` (billing/billing_transaction_service.py)
is an idempotent `get_or_create` keyed on the PaymentIntent, so a replay
updates the one row and never writes a second. The survey's claim
"each re-run also writes a second BillingTransaction" was wrong (for the
grant path and the license-inactive path alike). The license-inactive and
unpaid branches therefore duplicate nothing on a replay: the inactive one
re-saves the same intent fields and re-logs its ERROR; the unpaid one writes
no record at all. Decision, reported to the SM: fix step 2 (the "already
recorded as not granted" return) is NOT added, because no test can go red
for it and its plant could not be killed. c1 stays as a regression test
pinning the existing upsert.

## The fix

`_handle_license_overage_checkout_completed`: after the COMPLETED check,
return when `intent.completed_at is not None` or
`_license_overage_already_granted(intent)` (a CreditLedger row with
`metadata.intent_id == str(intent.id)` in a wallet of the intent's
teachers). One logger.info line; no user-facing text.

Green 02 (declared before the run): billing.tests.test_h300_reprocess, 4.
Predicted: **Ran 4, OK**. Log: `02_green.log`.

Plants (one per run, declared before each; labels: the same 4 tests; the
file restored from a saved copy, sha256 checked):

- P1, the completed_at check removed (the belt alone): predicted
  **Ran 4, OK, SURVIVES by design**: in b1 the belt finds the ledger rows;
  d1 already clears completed_at, so it is the belt's own test. Log
  `03_plant_p1.log`.
- P2, the belt removed (completed_at alone): predicted **Ran 4, FAILED
  (failures=1)**: d1 `(10000, 10000) != (5000, 5000)`; b1 passes on
  completed_at. Killed by d1. Log `04_plant_p2.log`.
- P3, both removed: predicted **Ran 4, FAILED (failures=2)**: b1
  `(10000, 10000, 0) != (5000, 5000, 0)` and d1 `(10000, 10000) != (5000,
  5000)`. Killed by b1 with the double-grant fragment. Log `05_plant_p3.log`.

Green 02 result (`02_green.log`): Ran 4, OK. **As predicted.**

Plant results (each restored, sha256 MATCHED 10dc88234f35cfb8):
- P1 (`03_plant_p1.log`): Ran 4, OK. Survives, **as predicted**: the belt alone holds.
- P2 (`04_plant_p2.log`): Ran 4, FAILED (failures=1), d1 `(10000, 10000) != (5000, 5000)`. Killed, **as predicted**.
- P3 (`05_plant_p3.log`): Ran 4, FAILED (failures=2), b1 `(10000, 10000, 0) != (5000, 5000, 0)` and d1. Killed, **as predicted**.

## Near (declared before the runs)

Billing plus the 66-label standing guard list. Seven of the 66 do not exist
on beta (staging-only code: classrooms.tests_e1_grade_guard,
students.tests_student_data_map, ai_processor.tests_metering_chokepoint,
billing.tests.test_wallet_save_guard, test_zero_credit_registry,
test_holds_entry, test_holds_single_call), so beta's list is the 59 that do.
The billing guards among them run inside the `billing` label.

- Near 06, `billing`, --parallel 2: count from build_suite **2378**; no
  skips in billing. Predicted: **Ran 2378, OK**. Log `06_near_billing.log`.
- Near 07, the 50 non-billing guards that exist on beta, SERIAL (the
  redis-isolation fork tests run): count **928**; one decorator skip (ssrf
  peer introspection, CI_REQUIRE_NETWORK). Predicted: **Ran 928, OK
  (skipped=1)**. Log `07_guard_sweep.log`.

    AutoGrader.tests_credscan
    AutoGrader.tests_h271_static_files
    AutoGrader.tests_network_guard
    AutoGrader.tests_student_classmates_guard
    AutoGrader.tests_student_feedback_guard
    AutoGrader.tests_submission_audience_guard
    ai_processor.tests_call_record_static
    ai_processor.tests_h2_grading_benchmark_guard
    ai_processor.tests_ssrf_guard
    audit.tests_history_guard
    audit.tests_route_coverage
    audit.tests_h273_query_filters
    students.tests_reading_label_static
    students.tests_app_boundaries
    students.tests_h279_regrade_pending_approval
    users.tests_roads_that_sign_in_or_activate
    AutoGrader.tests_cache_invalidation_coverage
    AutoGrader.tests_codederror_serialization
    AutoGrader.tests_management_commands_are_commands
    AutoGrader.tests_no_pii_in_logs
    AutoGrader.tests_no_playwright_at_import
    AutoGrader.tests_no_wildcard_invalidation
    AutoGrader.tests_reason_codes
    AutoGrader.tests_redis_test_isolation
    ai_processor.tests_grading_run_pipeline
    ai_processor.tests_h205_image_fallback
    ai_processor.tests_provider_block
    ai_processor.tests_reading_config
    assignments.tests_schema_extension
    classrooms.tests_course_roster_scope_sweep
    classrooms.tests_teacher_access_sweep
    students.tests_grading_label_written
    students.tests_identity_reason_codes
    users.tests_h296_message_rewording
    AutoGrader.tests_h246_sentry_environment
    AutoGrader.tests_log_scrubbing
    AutoGrader.tests_sentry_scrubbing
    AutoGrader.tests_sentry_sends_no_variables
    AutoGrader.tests_h285_djoser_removed
    AutoGrader.tests_pip_audit_gate
    ai_processor.tests_h262_prod_image_dev_tools
    ai_processor.tests_ai_call_trace
    ai_processor.tests_grading_cache_key_v2
    ai_processor.tests_grading_config
    ai_processor.tests_grading_run_checker
    assignments.tests_task_results_carry_no_text
    students.tests_answer_document_before_release
    students.tests_grading_label_fields
    students.tests_reading_label_columns
    students.tests_step_label_words

Any failure is a STOP.
