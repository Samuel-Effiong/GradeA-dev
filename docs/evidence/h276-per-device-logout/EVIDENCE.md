# H-276 evidence: logout signs out this browser/device only

Owner decision 2026-11-02: per-browser logout only. A password change or reset still signs out everywhere.

| Gate | Commit | Result |
|---|---|---|
| Red run on beta f5d2d577 | 1d2f3ba8 | `users.tests_h276_per_device_logout`: Ran 10, FAILED (failures=4, errors=2). Reproduces the owner's report |
| Fix | a8e39de9 | `sid` claim per sign-in + `RevokedSession` table (migration users/0041), checked on authenticate and on refresh |
| Mutation run 1 | 62ee5fd1 | 8/9 killed; M-3 (refresh-endpoint guard) SURVIVED, see mutation_run_62ee5fd1.log |
| M-3 closed by a test, not called equivalent | 768c16ab | RefreshGuardTests added; rerun: M-3 KILLED, restore sha256 OK (mutation_rerun_3.log) |
| Full regression | 01ac7adb | **Ran 8598, OK (skipped=28)**. Expectation said skipped=30, see below |

Label note: mutation_run_62ee5fd1.log prints mutants as "M-H273-n". That was a copy-paste prefix in mutants.py at the time; these are the H-276 mutants M-H276-1..9 (fixed at mutants.py:115 before the rerun).

## Expectation miss: skipped=28, not 30 (explained, not a code defect)

The committed expectation copied skipped=30 from the Batch 31 baseline. That baseline ran under `strict_bundle.sh` with
`--parallel 4`. This run was serial (no `--parallel`).

The Batch 31 log (GAP-a0-runs/b31/strict_b31.log, verbosity 2) lists 30 skips. Two of them are
`AutoGrader.tests_redis_test_isolation` fork tests (`test_a_forked_workers_cache_clear_cannot_reach_a_sibling`,
`test_a_forked_workers_queue_purge_cannot_reach_a_sibling`). They skip only inside a daemonic `--parallel` worker.
In a serial run they execute, and here they passed. So 30 - 2 = 28. The other 28 skips are opt-in env flags
(RUN_REAL_AI 12, RUN_LOAD_TESTS 9, CI_REQUIRE_NETWORK 4, AUDIT_BENCH 2, CI_REQUIRE_REDIS 1). A targeted -v 2 rerun of
the env-dependent modules confirmed those skip deterministically (Ran 101, OK, skipped=4).

The expectation file is left unedited, as committed. The miss is recorded here. The Batch 32 gate runs with
`strict_bundle.sh` (`--parallel 4`, `-v 2`) so its skip count is comparable to the baseline.

## Archived log (too large for the repo)

`~/Documents/Projects/GAP-a0-runs/h276_full_01ac7adb.log` (4,107,359 bytes)
sha256 `16b7b48ca35262239c083bb899456e2349996139ee56d09572abd4d827fa6430`
Result lines: `Ran 8598 tests in 1858.086s` / `OK (skipped=28)`. Venv: AutoGrader_env_h273. Test DB: test_AutoGrader_h276.

## Known, not changed (pre-existing)

Logout still blacklists whatever refresh token is posted, even another user's. Session revocation is guarded
(same-user check, test `test_posting_someone_elses_refresh_never_signs_them_out`), so the victim stays signed in
until their access token expires, but that refresh token is dead. Low risk; offered to the owner as a follow-up.
