# Bundle 5: the strict full run

Recorded by Integration & Release (0b), 2026-10-02.

**Scope:** `task/beta-batch-5` at `058a9507`: beta `67a06817` plus bundle 5's 13 items (H-78, H-65, H-73, H-76, H-66, H-71, H-55, H-69, H-60/H-57, H-82, H1, H-80/H-86, H2). The last code merge is `5d977d0c` (H2); `058a9507` adds only the backlog rows in `docs/HARDENING_BACKLOG.md`. No migrations and no model changes against beta.

**Command:** ONE strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1` and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `058a9507` | 12:13:14–12:57:22 (2648 s wall, 1003 s tests) | **Ran 5521 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound |

**Tests per app** (counted from the run's per-test lines; they sum to 5521):

| App | Tests |
|---|---|
| ai_processor | 817 |
| AutoGrader | 467 |
| assignments | 628 |
| billing | 2032 |
| classrooms | 385 |
| dashboard | 270 |
| students | 268 |
| users | 654 |

**H2's condition 4:** ai_processor (817) and AutoGrader (467) ran with no failure and no error. H2's own ai_processor + AutoGrader regression was red at an earlier tip (`68f6464f`, 7 errors in `CommandHistoryIntegrationTest`) and was not repeated, by the SM's ruling; this run is the green regression of those two apps on H2's final code.

**The suspends:** the wall clock includes two laptop suspends, in `journalctl`: 12:21:06–12:26:25 and 12:26:53–12:40:22, about 19 minutes together. The lid was closed each time. Rule 16's inhibitor (`idle:sleep`) was held throughout but does not cover a lid close. The run was frozen and resumed, not hung, and finished inside its 3600 s limit. The rest of the gap between wall and test time is load: other sessions' targeted runs until 12:46, and another project's test processes on the same machine.

**This was the only strict run of bundle 5.** Log: 7.4 MB, sha256 prefix `b271e42895550093`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/strict_b5.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `058a9507`.
