# H-283: concurrency harness leak count drifts

Owner approved 2026-11-03 (fix the flaky test, folded into Batch 33). Built by the SM.

## Symptom
In the B33 re-run (beta-batch-33 dc3d4604, evidence c4a70096d),
`AutoGrader.tests_concurrency_harness.RunConcurrentlyTests.test_a_worker_that_leaves_its_connection_open_fails_the_run`
failed with "2 database session(s) still open", but the test expects exactly 3. It passed in B33-E3 and Batch 32,
and this is its first failure in any archived log.

## Cause (hypothesis, proven by H1 below)
`run_concurrently` takes `other_backends()` (a COUNT of other sessions on the test database) before the run and
subtracts it afterwards. If a session counted in the baseline ends during the run, every leak is under-counted by
one. Example: an autovacuum worker on the test database, which pg_stat_activity lists under that database. That
explains 2 instead of 3, and a single real leak could be cancelled out to 0 and pass silently.

## Fix
Compare SETS of backend pids instead of counts. Leaked = the other sessions present after the run whose pid was not
present before. A baseline session that ends can no longer offset a leak, and one that stays open is still not counted.

No autovacuum filter by backend_type: no test could trigger one reliably, so the filter would be unguarded, and
the set comparison already covers a baseline session ending.

## Expectations (committed before any run)
- H1, red at the red-test commit: `python manage.py test AutoGrader.tests_concurrency_harness --settings=settings_worktree --noinput`
  gives Ran 10 with FAILED (failures=2): R1 (leaks under-counted: "2" where "3" is expected) and R2 (a single leak
  cancelled out: AssertionError not raised). R3 (a baseline session that stays open is not a leak) passes; it's a
  control. The 7 existing tests pass.
- H2, green at the fix commit: the same command gives Ran 10, OK.
- H3, stability: the module run 5 times in a row at the fix gives Ran 10, OK each time.
- H4, planted bugs (one per guard, restored by sha256, run serially):
  - M1: leak count back to count subtraction. Killed by R1 and R2.
  - M2: worker `connection.close()` removed. Killed by test_a_clean_run_leaves_no_extra_backends (harness raises).
  - M3: leaked = every current other session, not only those new since the baseline. Killed by R3.
- H5, near run: every module that imports AutoGrader.testing.concurrency (13 modules), --parallel 4. Ran N, OK, where N is the
  discovered count, recorded before the run.
- mypy passes. makemigrations is clean, and there's no migration.

## Results
- H1 (c059e5e4): Ran 10, FAILED (failures=2), exactly the tests predicted. R1 reproduced the B33 message verbatim ("2 database session(s) still open after all 3 workers"). R2: "AssertionError not raised", so a single real leak passed silently on the old code. MET.
- Addition not in the expectations, disclosed: test_a_clean_run_leaves_no_extra_backends also compared counts (`other_backends() == before`), which is the same drift. It now asserts no new pids. `other_backends()` had no other users and is removed.
- H2 (fix commit): Ran 10, OK. MET.
- H3: 5/5 runs Ran 10, OK (h3_5x.txt). MET.
- H4: M1 killed by R1 and R2. M2 killed by test_a_clean_run_leaves_no_extra_backends and also by R3 (R3 was not predicted; it's a superset). M3 killed by R3. All restored by sha256 bbe5ec25 (h4_mutants.txt). MET.
- mypy passed; makemigrations "No changes detected".
- H5 expectation, recorded before the run: the labels in h5_labels.txt (13 modules), --parallel 4, give Ran 353, OK.
- H5: Ran 353, OK, rc=0, --parallel 4 (h5_near_run.log.gz, sha256 4d0d4a4159bb75f4). MET. All expectations met; H2 has one disclosed addition (the clean-run test now compares pids).
