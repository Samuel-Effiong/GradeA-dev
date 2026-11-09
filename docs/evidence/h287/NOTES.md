# H-287: migration-safety acknowledgement on classrooms/0017

**Owner-approved for beta, on the condition of NO change to app behavior or data: comment-only.** Brief from the SM (grade-automator-plus-08), 2026-11-09.

Branch `task/h287-0017-marker`, worktree Grade-Automator-Plus-h287, off origin/beta **50183be4963bea01bd2b2dbc32916d751efd2e7c** (fetched first). origin/main = 5ae96393060a7a51d0caaafdf1bf6ca04411f011. Test DB test_h287_0017_marker (new; no clones exist).

## The change

classrooms/migrations/0017_session_is_active.py adds two partial UniqueConstraints in one step (`one_active_session_per_school`, `one_active_session_per_teacher`). scripts/check_migration_safety.py flags each AddConstraint (H-217). The change adds `# expand-contract-step: contract` as line 1, with a reason comment of 4 lines or fewer directly under it. No other line of the file changes, and no other file changes except this NOTES file.

## Declared before running anything

Counts come from build_suite().countTestCases().

- **D1 diff:** `git diff origin/beta -- classrooms/` shows only added lines, each a `#` comment, all at the top of 0017. 0 removed lines.
- **D2 sqlmigrate:** `python manage.py sqlmigrate classrooms 0017` before and after the edit is byte-identical (same sha256). Comments are not operations.
- **D3 makemigrations --check:** "No changes detected" (exit 0), before and after.
- **D4 safety check:** `scripts/check_migration_safety.py --base origin/main`, run with the env block of .github/workflows/migration-safety.yml.
  - Before: exit 1, with a FAIL line for classrooms/migrations/0017_session_is_active.py (the two AddConstraint findings) and no FAIL for any other file.
  - After: exit 0, with `ACK  classrooms/migrations/0017_session_is_active.py` and no FAIL line.
  - Any other files' OK/ACK lines are unchanged.
- **N1 near apps**, under flock: `classrooms users AutoGrader.tests_migration_rollback_defaults`, --parallel 2. 1703 tests (1691 + 12). Predict **Ran 1703, OK (skipped=3)**: 2 live Google contract opt-ins plus CI_REQUIRE_REDIS, all in users.
- **N2 guard sweep**, serial, under flock: guards67.txt (66 labels from task/epic-b1b plus `AutoGrader.tests_live_server_backlog_guard`). 60 of the 67 exist on beta; 7 are staging/B1b-only (classrooms.tests_e1_grade_guard, students.tests_student_data_map, ai_processor.tests_metering_chokepoint, billing.tests.test_wallet_save_guard, test_zero_credit_registry, test_holds_entry, test_holds_single_call). 4 of the 60 are inside classrooms/users and covered by N1, so N2 runs the other 56. 1023 tests. Predict **Ran 1023, OK (skipped=1)** (ssrf, CI_REQUIRE_NETWORK).
- **T trailers:** 0 Co-Authored-By in origin/beta..HEAD.
- No plant: comment-only. D2 and D4 are the before/after evidence.

N2 labels:

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
    students.tests_grading_label_written
    students.tests_identity_reason_codes
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
    billing.tests.test_logs_carry_no_email
    students.tests_answer_document_before_release
    students.tests_grading_label_fields
    students.tests_reading_label_columns
    students.tests_step_label_words
    billing.tests.test_live_qa_clock
    billing.tests.test_price_drift_reconciliation
    AutoGrader.tests_live_server_backlog_guard
