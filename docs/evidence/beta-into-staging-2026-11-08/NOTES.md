# Beta into staging, 2026-11-08 (Lane 2, SM brief)

Standing rule (owner, 2026-11-08): staging always holds every bug fix on
beta plus the Phase 2 work; beta takes bug fixes only; staging is never
merged into beta.

## The merge

- `git log origin/staging..origin/beta` (after fetch): one commit,
  a3736b83c H-298: deleting a teacher whose courses hold assignments no
  longer fails.
- Branch task/beta-into-staging-2026-11-08 off origin/staging
  240567975a53d0f8ba4992829466d440ef2bd4cf (contains c25519939).
- `git merge --no-ff origin/beta`: no conflicts. Merge commit
  756b518bb63e2f9d285e6431b975402700c7e1c8 (parents 240567975, a3736b83c).
- Files from beta: assignments/signals.py, classrooms/signals.py,
  assignments/tests_h298_teacher_delete.py, docs/evidence/h298/*.
  `related_or_none` (used by both signal changes) exists on staging in
  AutoGrader/cache_generation.py:210.
- `makemigrations --check --dry-run`: No changes detected.

## Near run 01 (declared before the run)

Labels (23): the 20 guards outside classrooms and users, plus classrooms,
assignments, users (these hold classrooms.tests_e1_grade_guard,
users.tests_roads_that_sign_in_or_activate and
assignments.tests_h298_teacher_delete, so they are not listed twice).
List: near_t1_labels below.

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
    billing.tests.test_license_admin_user_guard
    billing.tests.test_qa_console_guardrail
    billing.tests.test_qa_time_travel_guardrail
    billing.tests.test_renewal_guards
    students.tests_reading_label_static
    students.tests_app_boundaries
    students.tests_h279_regrade_pending_approval
    students.tests_student_data_map
    classrooms
    assignments
    users

Count from build_suite: 2996 (classrooms 813, assignments 824, users 982,
the 20 guards 377 = 395 - 8 - 10; H-298 tests 2, inside assignments).

Predicted: **Ran 2996, OK (skipped=18)**. Skips: 14 by decorator (13 in
assignments: course_ownership_scale 1, real_extraction 3, tests_load 8,
real_upload_billing 1; ai_processor ssrf peer introspection 1) and 4 at run
time in users (LiveGoogleEndpointContractTests 2, CI_REQUIRE_NETWORK guard
1, CI_REQUIRE_REDIS guard 1; Redis answers PONG locally, so the other
Redis tests run). Any failure or error is a STOP.

Near run 01 result (`01_near.log.gz`, under flock, `--parallel 2`, 290 s):
**Ran 2996, FAILED (failures=1, skipped=18)**. **Prediction wrong**: the
count and the skips matched; the failure was not predicted.

The failure: `classrooms.tests_teacher_access_sweep.TeacherAccessSweepTests.
test_no_unlisted_direct_owner_scoping` (H-38 guard: no direct owner scoping
outside its ALLOWED list) flags two lines:

    students/data_erase.py   Course.objects.filter(teacher=caller).values_list("id", flat=True)
    students/data_export.py  Course.objects.filter(teacher=caller).values_list("id", flat=True)

Not from this merge: `git diff origin/staging HEAD` on the guard and both
files is empty, so staging 240567975 carries this red. The lines are Lane 2's
NFR-CMP-04 teacher scope (e813dc61c); its near runs did not include
classrooms, and this guard is not in the 22-guard list. It is a real H-38
issue, not only a listing one: a teacher removed from the school keeps
export/erase reach over students of their old courses. STOP: no fix made;
reported to the SM.

## SM ruling and the H-38 fix (2026-11-08)

SM: fix the H-38 red on this branch, one push with the merge. Done on
NFR-CMP-04's side (its NOTES, "Second post-push miss"): tests first
9c3d705fac5ffc11f75134084477eb9b95529111 (red 48 Ran 2 failures=2), fix
5d82e4b07901bbe0c7aba01d3c2f75aca46c570e (green 49 Ran 11 OK; plant H38
killed). No allow-list change.

## Near run 02 (declared before the run)

Labels: near 01's 23 plus students.tests_data_export and
students.tests_data_erase (25). Count from build_suite: **3088** (2996 + 46
+ 46). The two added modules have no skips.
Predicted: **Ran 3088, OK (skipped=18)**, the same 18 as run 01. Any
failure is a STOP. Log: `02_near.log`.

Near run 02 result (`02_near.log.gz`, under flock, `--parallel 2`, 312 s): **Ran 3088, OK (skipped=18)**. **As predicted.** Ready for the SM to push; no push from Lane 2.

## Guard sweep 03 on 9131ceb7a (SM, 2026-11-09; declared before the run)

SM: the standing guard list is now 22 + A (21) + B (21) = 64 labels. The
widened grep (inspect.getsource, subprocess grep / git grep, open() or
Path.open on .py files, importlib.resources) found 19 more test modules.
17 are not guards: they open PDFs or images (fitz.open, Image.open), run
renderer subprocesses, or are docs/evidence mutant scripts. Two are added
to B:

- billing.tests.test_live_qa_clock: inspect.getsource(clock.run_local_clock_tasks) must name four tasks
- billing.tests.test_price_drift_reconciliation: inspect.getsource(reconcile_subscription_renewals) must filter on billing_cycle_end__lte and never read stripe_price_id

Labels (66):

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
    billing.tests.test_license_admin_user_guard
    billing.tests.test_qa_console_guardrail
    billing.tests.test_qa_time_travel_guardrail
    billing.tests.test_renewal_guards
    classrooms.tests_e1_grade_guard
    students.tests_reading_label_static
    students.tests_app_boundaries
    students.tests_h279_regrade_pending_approval
    students.tests_student_data_map
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
    ai_processor.tests_metering_chokepoint
    ai_processor.tests_provider_block
    ai_processor.tests_reading_config
    assignments.tests_schema_extension
    billing.tests.test_wallet_save_guard
    billing.tests.test_zero_credit_registry
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
    billing.tests.test_allocation_anchor
    billing.tests.test_h28_stripe_budget
    billing.tests.test_holds_entry
    billing.tests.test_holds_single_call
    billing.tests.test_logs_carry_no_email
    students.tests_answer_document_before_release
    students.tests_grading_label_fields
    students.tests_reading_label_columns
    students.tests_step_label_words
    billing.tests.test_live_qa_clock
    billing.tests.test_price_drift_reconciliation

Count from build_suite: **1146** (the 64: 1106; the 2 added: 28 + 12).
Run WITHOUT --parallel: under --parallel, the two fork tests in
AutoGrader.tests_redis_test_isolation skip themselves (daemonic worker);
run serially, they execute. Skips: 1 by decorator (ssrf peer introspection,
CI_REQUIRE_NETWORK not set); no other skipTest applies.
Predicted: **Ran 1146, OK (skipped=1)**. Any failure is a STOP. Log:
`03_guard_sweep.log`.

Guard sweep 03 result (`03_guard_sweep.log`, under flock, serial, 211 s, on 9131ceb7a): **Ran 1146, OK (skipped=1)**. **As predicted.** Standing guard list: 66 labels (22 + A 21 + B 23).
