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

Near results (under flock):
- Near 06 (`06_near_billing.log.gz`, --parallel 2, 207 s): **Ran 2378, OK**. **As predicted.**
- Near 07 (`07_guard_sweep.log.gz`, serial, 113 s): **Ran 928, OK (skipped=1)**. **As predicted.**

Ready for the SM to push to beta. No push from Lane 2.

# Part 2: staging (SM go 2026-11-09)

Branch task/h300-staging off origin/staging 4aff59a43. `git merge --no-ff
origin/beta` (e4a893197, Part 1 as pushed): no conflicts, merge
687d6a70c84b65386068545790876012e29dfba0. The merged handler holds both the
beta guard and B1a's departed-share refund. `makemigrations --check
--dry-run`: No changes detected.

## Road A (staging only, B1a code)

`_refund_departed_share_on_commit` registers `transaction.on_commit` without
robust=True and calls `refund_departed_teacher_share.delay` and
`send_email_task.delay` directly. If either raises after commit, the
handler raises, and `_run_handler_inline` marks the event FAILED with the
grants made. The fix (declared): register with `robust=True` and queue
both through `AutoGrader.dispatch.safe_delay`, as the receipt hook does; a
refund that could not be queued (safe_delay returns None) is logged with
the module's existing "MANUAL RECONCILIATION NEEDED" helper. Log lines only;
no user-facing text.

New tests, billing/tests/test_h300_refund_hook.py (from the survey's a1,
b2, b3; rewritten to assert what the fixed hook does):

- a1 (real commits): the refund's `.delay` raises OperationalError → the
  event is SUCCEEDED, a and b hold 5000 each, c 0, `.delay` called once, no
  Refund.create, and a MANUAL RECONCILIATION line naming the intent.
- a2 (real commits): the email's `.delay` raises OperationalError → the
  event is SUCCEEDED and the refund was still queued once.
- a3 (real commits): the refund's `.delay` raises RuntimeError (not a
  broker error; safe_delay lets it through) → robust=True contains it: the
  event is SUCCEEDED, the grants are made once.
- b2, b3 (road B, switch on): a replay grants once and refunds once. They
  already hold through the merged beta guard (regression on staging).

Red 08 (declared before the run): test_h300_refund_hook (5) plus beta's
test_h300_reprocess (4), as merged. Count from build_suite: **9**.
Predicted: **Ran 9, FAILED (failures=3)**: a1, a2, a3, each at the first
assertion, `'FAILED' != 'SUCCEEDED'`. b2, b3 and beta's four pass. Log
`08_red_road_a.log`.

Red 08 result (`08_red_road_a.log`): **Ran 9, FAILED (failures=3)**, a1, a2, a3, each `'FAILED' != StripeEventStatus.SUCCEEDED`; b2, b3 and beta's four pass. **As predicted.**

## Road A fix

`_refund_departed_share_on_commit`: `transaction.on_commit(_after_commit,
robust=True)`; both tasks through `safe_delay`; when the refund cannot be
queued (safe_delay returns None), `departed_share_refund._manual(...)` logs
"MANUAL RECONCILIATION NEEDED: ..." with the intent id and the cents owed.

Green 09 (declared before the run): test_h300_refund_hook (5),
test_h300_reprocess (4), and test_departed_teacher_refund (20; its tests
patch the refund task's `.delay`, which safe_delay still calls). Count from
build_suite: **29**. Predicted: **Ran 29, OK**. Log `09_green_road_a.log`.

Plants (one per run, declared before each; labels: green 09's 29; file
restored from a saved copy, sha256 checked):

- Q1, the refund back to a plain `.delay` (robust and the email's
  safe_delay stay): predicted **Ran 29, FAILED (failures=1)**: a1, no
  "MANUAL RECONCILIATION" line (robust=True swallows the OperationalError,
  so the event is SUCCEEDED, but nobody is told the refund was not
  queued). Killed by a1. Log `10_plant_q1.log`.
- Q2, the email back to a plain `.delay` (robust stays): predicted
  **Ran 29, OK, SURVIVES by design**: robust=True contains the email's
  error after the refund has been queued, so a2 still sees SUCCEEDED and
  one refund. safe_delay on the email is defence in depth, not separately
  observable. Log `11_plant_q2.log`.
- Q3, robust=True removed (both safe_delays stay): predicted **Ran 29,
  FAILED (failures=1)**: a3 `'FAILED' != StripeEventStatus.SUCCEEDED` (the
  RuntimeError is not a broker error, so it reaches the handler). Killed by
  a3. Log `12_plant_q3.log`.

Green 09 result (`09_green_road_a.log`): Ran 29, OK. **As predicted.**

Plant runner note: the first attempt at Q1-Q3 stopped in the runner script
itself (a SyntaxError in its plant table, a quoting slip); no plant was
applied, no test ran, and `billing/stripe_service.py` was untouched. The
runner was fixed and the three plants ran as declared.

Plant results (each restored, sha256 MATCHED 24e6932dfdf62331):
- Q1 (`10_plant_q1.log`): Ran 29, FAILED (failures=1), a1, no MANUAL RECONCILIATION line in the logs. Killed, **as predicted**.
- Q2 (`11_plant_q2.log`): Ran 29, OK. Survives by design, **as predicted**.
- Q3 (`12_plant_q3.log`): Ran 29, FAILED (failures=1), a3 `'FAILED' != StripeEventStatus.SUCCEEDED`. Killed, **as predicted**.

## Staging near (declared before the runs)

- Near 13, `billing`, --parallel 2: count from build_suite **2619**; 4
  decorator skips (the opt-in Gate 6 measurements: test_holds_gate6_latency,
  _m2, _m3, _profile); no runtime skips. Predicted: **Ran 2619, OK
  (skipped=4)**. Log `13_near_billing.log`.
- Near 14, the 66-label standing guard list, SERIAL: count **1146**; 1
  skip (ssrf peer introspection, CI_REQUIRE_NETWORK). Predicted: **Ran
  1146, OK (skipped=1)**. Log `14_guard_sweep.log`.

Any failure is a STOP.

Near results (under flock):
- Near 13 (`13_near_billing.log.gz`, --parallel 2, 273 s): **Ran 2619, OK (skipped=4)**. **As predicted.**
- Near 14 (`14_guard_sweep.log.gz`, serial, 186 s): **Ran 1146, OK (skipped=1)**. **As predicted.**

Ready for the SM to push to staging. No push from Lane 2.
