# Batch 33 (H-278 + H-279) NOTES

Base: origin/beta e11e56536. Merges: H-278 task/h278-session-active at af67b4da2
(73482ef40), H-279 task/h279-regrade-pending-approval at 71631829f (66fd9a64d).
Both merged clean.

## Merge note (H-278 x H-273)
H-278 writes `target_type="Session"` (SESSION_ACTIVE_CHANGE). H-273's scan test
`audit.tests_h273_query_filters...test_every_target_type_the_code_writes_has_a_label`
requires every written target_type in `_TARGET_LABEL`.

### Expectation B33-E1 (committed before the run, at 66fd9a64d)
`manage.py test audit.tests_h273_query_filters --settings=settings_worktree`:
`FAILED (failures=1)`, the one failure being
test_every_target_type_the_code_writes_has_a_label, listing exactly ['Session'].

### Expectation B33-E2 (after adding "Session": "Session")
Same module: OK, same test count as E1.

### B33-E1 result at 34cf7af3: MET
`Ran 32`, `FAILED (failures=1)`: test_every_target_type_the_code_writes_has_a_label,
`['Session'] != []`. Log e1_34cf7af3.log.

### B33-E2 result: MET
`"Session": "Session"` added to `_TARGET_LABEL` (audit/serializers.py) as the
H-278 merge note asked. `Ran 32`, `OK`. Log e2.log.

## B33-E3 full run expectation (committed before the run)
Owner go-ahead 2026-11-03: "Yes, and push if green".
Run: GAP-a0-runs/b33/strict_bundle.sh (--parallel 4 -v 2, mypy + makemigrations
pre-checks, watchdog, memory guard) on this commit.
Expected: `Ran 8839 tests`, `OK (skipped=30)`, skip reasons identical to
Batch 32. (8839 = DiscoverRunner.build_suite count at 8a1d4a18; Batch 32 was 8737.)

## B33-E3 result (full run on 5c1e12a5): RED, expectation missed

- Ran 8839 (as predicted), FAILED (failures=2, skipped=30). Memory guard and watchdog did not fire; this is a real red.
- Both failures are in AutoGrader.tests_migration_rollback_defaults (the rollback-default guard): `classrooms.session.is_active` (added by H-278, migration classrooms.0017_session_is_active) is a NOT NULL field with no `db_default`. Code older than the migration would omit the column from INSERT, so a code-only rollback would break every Session insert.
- Not pushed. Fix goes back to H-278: `db_default=False` on the field plus a generated AlterField migration, red test first, then a re-run.
- Process gap: the H-278 near run did not include AutoGrader.tests_migration_rollback_defaults. Any branch that adds a migration must include that module in its near run.
- Files: e3_5c1e12a5_summary.txt, e3_failures.txt.

## B33-E4 expectation (committed before the run): full re-run after the H-278 db_default fix
- Merged H-278 follow-up at 0131db92 (5fc519031): Session.is_active gets db_default=False, generated AlterField classrooms/0018. Worker evidence: F1 red, F2 green (guard Ran 12 OK), near run Ran 1091 OK (skipped=2), mutants M-h278.13 (killed by makemigrations --check) and M-h278.14 (killed by the guard).
- makemigrations --check: "No changes detected". Discovered test count: 8839 (unchanged; no tests added).
- Prediction: `Ran 8839 tests`, `OK (skipped=30)`, with skip reasons identical to Batch 32 / B33-E3. The 2 rollback-guard failures from E3 are gone.
- Owner approval 2026-11-03 ("ye"): re-run and push to origin and backup if green.

## B33-E4 result (full re-run on dc3d4604): RED, expectation missed
- Ran 8839 (as predicted), FAILED (failures=1, skipped=30). Skip reasons are identical to E3. The memory guard and watchdog did not fire. The E3 rollback-guard failures are gone, so the H-278 fix works.
- The one failure is AutoGrader.tests_concurrency_harness.RunConcurrentlyTests.test_a_worker_that_leaves_its_connection_open_fails_the_run: the harness reported "2 database session(s) still open", but the test demands exactly "3".
- Not caused by this batch: B33 does not touch AutoGrader/testing/ or the test module, and the same code passed in E3 and Batch 32. This is its first recorded failure in any archived run log.
- Likely cause (reading only, not proven): `run_concurrently` counts leaks as other_backends() minus a baseline taken before the run. If a backend counted in the baseline closes during the run, 3 real leaks show as 2. The test's exact-count regex is then flaky.
- Not pushed. The rule is: anything other than the expectation = STOP.

## Batch 34 folded in (owner 2026-11-03: "merge batch 34 into 33 and do it as 1 full suite")
| Merge | Ticket | Branch tip |
|---|---|---|
| 3abddf96c | H-280 school detail/list blank name | a5a5067e (near run Ran 698 OK) |
| 44676a73a | H-283 harness leak count by pid set (SM-built) | 343b7482 (near run Ran 353 OK, 5/5, 3/3 mutants) |
| c6ec941d5 | H-281 individual-teacher credit wording | 29f6a8b8 (near run Ran 2468 OK, 19/19 mutants) |
All merges clean. makemigrations --check: "No changes detected". mypy passed. Co-Authored-By lines on the batch: 8 (all from H-278), stripped before push.

## B33-E5 expectation (committed before the run)
- Discovered count 8894 = 8839 + 7 (H-280) + 3 (H-283) + 45 (H-281).
- Prediction: `Ran 8894 tests`, `OK (skipped=30)`, skip reasons identical to E3/E4.
- Owner approval: push to origin and backup if green. Anything else means STOP, no push.
