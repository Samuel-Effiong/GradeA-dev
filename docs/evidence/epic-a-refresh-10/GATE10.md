# Epic A staging refresh 10: Gate 10 (the bundle 7 merge-down)

Recorded by Integration & Release (0b), 2026-10-05.

**Scope:** phase2/epic-a `2919e5aa`: everything merged since refresh 9 (`db6f5155`). That is the bundle 7 merge-down (`96e8c65c`, merged at `2919e5aa`), which brings beta `63c3da22` into the epic: H-89, H-91, H-97, H-107, H-108, H-109, H-112. H-89's scrubber replaces the epic's own Sentry scrubber (BE-A-04; SM ruling); the epic's list of files excused from the no-names-in-logs check is emptied. No migration. `AutoGrader/settings.py` changes for H-89 only, identical to beta's; no environment setting is added.

**Command:** the strict full run under rules 12, 13, 16, 17 and 18: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null`. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

**Stall watchdog:** a watcher would have recorded the process tree and aborted the run after 300 s without a new log line. It never fired.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `2919e5aa` | 2026-10-05 17:17:32–17:31:11 (819 s wall, 764 s tests) | **Ran 6400 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, no stall |

**Tests per app** (counted from the run's per-test lines; they sum to 6400):

| App | Tests | Change from refresh 9's Gate 10 (6320) |
|---|---|---|
| ai_processor | 830 | 0 |
| assignments | 663 | 0 |
| audit | 367 | 0 |
| AutoGrader | 613 | +77 |
| billing | 2139 | +3 |
| classrooms | 434 | 0 |
| dashboard | 270 | 0 |
| students | 386 | 0 |
| users | 698 | 0 |

The +80 is the net of bundle 7's new tests and the five tests of the epic's old scrubber that left with it. This record does not split them by item.

**Skips:** the 30 are all opt-in or structural: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, 2 that cannot fork inside a parallel worker, 2 audit benchmarks. None was skipped for want of Chromium: the real-browser PDF tests ran.

**Why this run is a hard gate here:** the merge-down's own regression ran AutoGrader and billing (2752 OK at `b4e8da84`). This run is the first full run of the merged tree, and it covers the seven other apps. It is also the epic's first full run with H-107's fixes in; because its output went to a file, it does not test the stream fix against a full pipe.

**The merge and the record:** the merge-down branch was gated at `af350044` (426 OK over changed modules and both sides' guards; 22 of 22 mutants killed) and verified by v2 at `318895d0` (VERIFIED-WITH-NOTES). v2's record was committed byte-identical at `96e8c65c`; v2 and 0b each compared it (identical, sha256 prefix `e8385c07774603ab`). The merge commit `2919e5aa` has the tree of `96e8c65c`.

**The machine, and why the load is recorded:** shared with another project. At 17:01 to 17:10 the same day a next-bundle run on the beta line failed one wall-clock assertion in a real-browser test while the load average was 22 (four whole-tree scans by 0b and the other project's checks beside it). For this run the other project's manager held their heavy runs, 0b ran nothing beside it, and the start waited for the load to fall. Load average (1, 5, 15 minutes): 2.75 / 10.20 / 12.79 at 17:16:46 before the pre-checks; 2.27 / 9.03 / 12.27 at the tests' start; 5.93 / 6.90 / 8.79 at the end. No other GAP test run was active. Log: 8.4 MB, sha256 prefix `4b999d171ae85b2c`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/gate10_epic_r10.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `2919e5aa`.
