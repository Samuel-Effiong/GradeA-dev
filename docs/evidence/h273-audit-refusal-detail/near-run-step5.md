# H-273 step 5: near run (7-app set)

## Commit

2f2dc117 (tip, code same as cba462cf)

## Command

```
python manage.py test audit users classrooms billing students assignments AutoGrader \
  --settings=AutoGrader.settings --verbosity=2 --noinput
```

## Venv

~/Documents/Virtualenvs/AutoGrader_env_h273 (freeze SHA-256: 52da179f)

## Expectation

- Collected: 6690 (matches baseline)
- OK, zero failures, zero errors
- Mismatch = STOP

## Result

```
Ran 6690 tests in 1666.588s

OK (skipped=20)
```

**PASS** — matches expectation. Zero failures, zero errors.

## Skipped tests (20)

All opt-in gated; none H-273 related.

### Benchmarks (2)
1. `audit.tests.test_bench_volume.BusySchoolDay.test_print_one_busy_day` — AUDIT_BENCH=1
2. `audit.tests.test_bench_history.HistoryCostBenchmark.test_print_the_cost` — AUDIT_BENCH=1

### Real AI calls (5)
3. `students.tests_async_edit_path.RealProviderEditTest.test_a_real_edit_extracts_the_answer_and_charges_once` — RUN_REAL_AI=1
4. `assignments.tests_real_extraction.RealAssignmentExtractionTest.test_a_real_extraction_returns_a_usable_assignment` — RUN_REAL_AI=1
5. `assignments.tests_real_upload_billing.RealProviderBatchUploadBillingTest.test_a_real_partly_failed_batch_is_charged_once_and_replays_free` — RUN_REAL_AI=1
6. PDF branch of prepare_ai_content — RUN_REAL_AI=1
7. Real output editor conversion — RUN_REAL_AI=1

### Load tests (8)
8. 24 concurrent uncached downloads through real Chromium — RUN_LOAD_TESTS=1
9. Pagination stability (entire list walk) — RUN_LOAD_TESTS=1
10. Direct paginator check — RUN_LOAD_TESTS=1
11. Invalidation-scope fix under traffic — RUN_LOAD_TESTS=1
12. N+1 guard (page of 25) — RUN_LOAD_TESTS=1
13. Publish stampede (30 students) — RUN_LOAD_TESTS=1
14. Shedding contract end to end — RUN_LOAD_TESTS=1
15. Thirty seconds of everything at once — RUN_LOAD_TESTS=1

### Scale test (1)
16. `assignments.tests_course_ownership_scale.SixThousandStudentSchoolTest.test_six_thousand_student_school` — RUN_LOAD_TESTS=1

### Network / external (3)
17. `users.tests_google_auth.LiveContractSkipGuardTests.test_network_is_required_when_ci_says_so` — CI_REQUIRE_NETWORK
18. Google auth raise_for_status — CI_REQUIRE_NETWORK
19. Google auth ValueError — CI_REQUIRE_NETWORK

### Redis (1)
20. Redis load tests — CI_REQUIRE_REDIS

## Log archive

~/Documents/Projects/GAP-a0-runs/h273/near_run_step5_7app.log
