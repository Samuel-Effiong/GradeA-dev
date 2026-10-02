# Bundle 6: the strict full run

Recorded by Integration & Release (0b), 2026-10-02.

**Scope:** `task/beta-batch-6` at `fd2bcdbf`: beta `74bfc8d3` plus bundle 6's four items: H-94 (`93cb8648`), H-88 with H-93 and H-81 (`ac2ec323`), H-85 (`76cc9b97`) and H-99 (`78a099c4`). The last code merge is `78a099c4`; `fd2bcdbf` adds only the backlog rows in `docs/HARDENING_BACKLOG.md`. One migration against beta: `billing 0073_schoolcreditallocation_grant_anchor_at` (a nullable column). No settings change.

**Command:** ONE strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1` and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `fd2bcdbf` | 2026-10-02 17:08:02–17:18:01 (599 s wall, 555 s tests) | **Ran 5601 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend |

**Tests per app** (counted from the run's per-test lines; they sum to 5601):

| App | Tests | Change from bundle 5's strict run (5521) |
|---|---|---|
| ai_processor | 817 | 0 |
| assignments | 628 | 0 |
| AutoGrader | 469 | +2 (H-94's two tests) |
| billing | 2094 | +62 (H-88, H-93, H-81, H-85) |
| classrooms | 401 | +16 (H-99's module) |
| dashboard | 270 | 0 |
| students | 268 | 0 |
| users | 654 | 0 |

**What this run covers that the items' own regressions did not:** H-99's regression ran classrooms, users, dashboard and AutoGrader (SM ruling), so this is the first run of the whole billing app, students and assignments on the combined bundle. H-85's base update onto H-88 was gated on the 23 billing modules that exercise `billing/license_service.py`, with no whole-app run (SM ruling); the 2094 billing tests here are that run. All green.

**The test fixed by H-94** (`AutoGrader.tests_redis_hygiene`, the cross-worker race that failed once in beta's CI on `74bfc8d3`) ran here under 4 parallel workers and passed. The test class that hit H-99's collision by chance on the epic line (`RemovedTeacherRosterNameMatchTests`) passed; with H-99 in this bundle the collision can no longer attach an account.

**The machine:** shared with another project; its manager held their test runs for this run. Two short serial GAP runs ran beside it (v2's one-minute H-101 probe and d5's H-89 serial steps). **This was the only strict run of bundle 6.** Log: 7.6 MB, sha256 prefix `30f1d9874b779696`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/strict_b6.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `fd2bcdbf`.
