# Epic A staging refresh 9: Gate 10 (the bundle 6 merge-down, H-101)

Recorded by Integration & Release (0b), 2026-10-05.

**Scope:** phase2/epic-a `d4b8ed8a`: everything merged since refresh 8 (`4e4326c3`). That is H-101 (`7b4a6eaf`: an intent already escalated is not escalated again) and the bundle 6 merge-down (`b5a63778`, merged at `d4b8ed8a`), which brings beta `141c8031` into the epic: H-99, H-85, H-88 with H-93 and H-81, H-94. On the epic, H-85's refusal lands as the coded entry `TEACHER_CANNOT_JOIN_YET` and the code `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` is retired (the founder's choice, 2026-10-05). One migration (`billing 0073`, one nullable column). No change to `AutoGrader/settings.py` against refresh 8.

**Command:** the strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

**Stall watchdog (new in this run, SM's interim rule of 2026-10-02):** a watcher would have recorded the process tree and aborted the run after 300 s without a new log line. It never fired.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `d4b8ed8a` | 2026-10-05 11:41:48–11:56:53 (905 s wall, 854 s tests) | **Ran 6320 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, no stall |

**Tests per app** (counted from the run's per-test lines; they sum to 6320):

| App | Tests | Change from refresh 8's Gate 10 |
|---|---|---|
| ai_processor | 830 | 0 |
| assignments | 663 | 0 |
| audit | 367 | 0 |
| AutoGrader | 536 | +3 |
| billing | 2136 | +65 |
| classrooms | 434 | +19 |
| dashboard | 270 | 0 |
| students | 386 | 0 |
| users | 698 | 0 |

The +87 are bundle 6's tests, the merge-down's epic-side tests and H-101's. This record does not split them by item.

**Why this run is a hard gate here:** the merge-down's own regression ran six apps (billing, classrooms, users, AutoGrader, dashboard, audit: 4441 OK at `d22dff5a`). This run is the first full run of the merged tree, and it covers assignments, students and ai_processor, which that regression left out.

**The merge and the record:** the merge-down branch was gated at `54cc7c1b` (956 OK, 19/19 mutants) and verified by v2 at `bcdd4177` (VERIFIED-WITH-NOTES). After a reboot of the machine on 2026-10-05 at 10:47, v2's finished record was committed byte-identical at `b5a63778` by a session acting for ed on the SM's order; 0b compared it with v2's file before the merge, and v2 repeated the comparison afterwards (identical, sha256 prefix `5bfba88d3b545c2d`). The merge commit `d4b8ed8a` has the tree of `b5a63778`.

**The machine:** shared with another project, whose manager cleared this run; no other GAP test run was active. The run took longer than the last three (8 to 11 minutes); the load was 3 to 4 at the start with several sessions working. Log: 8.4 MB, sha256 prefix `f6d5c257a57c8bfe`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/gate10_epic_r9.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `d4b8ed8a`.
