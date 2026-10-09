# Epic A staging refresh 7: Gate 10 (the bundle 5 merge-down)

Recorded by Integration & Release (0b), 2026-10-02.

**Scope:** phase2/epic-a `3fff1382`: the bundle 5 merge-down (beta `74bfc8d3`, merged from `task/epic-a-merge-down-b5` `b7b82632`). Bundle 5 is H-78, H-65, H-73, H-76, H-66, H-71, H-55, H-69, H-60/H-57, H-82, H1, H-80/H-86 and H2. No migrations and no model changes against staging refresh 6; one new setting, `ENABLE_GRADING_BENCHMARK` (default off).

**Command:** the strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1` and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `3fff1382` | 2026-10-02 14:58:37–15:06:20 (463 s wall, 434 s tests) | **Ran 6161 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend |

**Tests per app** (counted from the run's per-test lines; they sum to 6161):

| App | Tests |
|---|---|
| ai_processor | 830 |
| assignments | 663 |
| audit | 342 |
| AutoGrader | 533 |
| billing | 2048 |
| classrooms | 415 |
| dashboard | 270 |
| students | 362 |
| users | 698 |

**Why this run matters for the merge-down:** the merge-down's own seven-app regression (at `c32de6aa`) ran 5135 tests with one failure, `audit.tests_volume_report.MeasuredTests.test_every_windowed_count_has_an_index_path`, a planner choice on a near-empty table. By the SM's ruling that run was not repeated; the test was made deterministic, test-only (`21645f8c`, H-95). This run is the first full run of the merged tree, and it is green, including that module and both sides' guards. It also covers assignments and students, which the seven-app run left out.

**Two tests known to depend on timing** ran and passed here: the audit index-path test (fixed, above) and `AutoGrader.tests_redis_hygiene`'s lookalike-prefix test, which races under parallel workers (H-94; it failed once in beta's CI on `74bfc8d3` and passed on the re-run).

**The machine:** shared with another project today. Its manager held their test runs for this run, so the load average at the start was under 4. Log: 8.5 MB, sha256 prefix `b026f0231b86f55c`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/gate10_epic_r7.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `3fff1382`.
