# Epic A staging refresh 11: Gate 10 (release 1 of BE-I-04, slices A and B)

Recorded by Integration & Release (0b), 2026-10-07.

**Scope:** phase2/epic-a `82c3108d`: everything merged since refresh 10 (`2919e5aa`, pushed to staging as `33538426`). That is BE-I-04 slice A (the six label columns on a submission, migration `students/0031`, the settings version, the release identifier; merged at `cfe55a0f`), BE-I-04 slice B (the saved-answer match, key v2; merged at `82c3108d`) and H-122 (two documents; merged at `9a581258`). One migration. One optional environment setting, `GRADING_RELEASE_ID`.

**Command:** the strict full run under rules 12, 13, 16, 17 and 18: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null`. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first. The script is here as `gate10_release1.sh.txt` (sha256 prefix `7f1e523f6ab959f6`); it refuses any tip but the one named and any tree that is not clean.

**Stall watchdog:** a watcher would have recorded the process tree and aborted the run after 300 s without a new log line. It never fired.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `82c3108d` | 2026-10-07 10:46:07–10:53:23 (436 s wall, 404 s tests) | **Ran 6498 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, no stall |

**Written beforehand** (run log of 0b, 10:45:41): exit 0, OK, skipped=30, no failure or error, 6496 tests or a few more. 6496 was the count of slice B's own full run at `6ca94c09`; the two tests added after it are in `ai_processor/tests_grading_cache_key_v2_checker.py`. No difference from what was written.

**Tests per app** (counted from the run's per-test lines; they sum to 6498):

| App | Tests | Change from refresh 10's Gate 10 (6400) |
|---|---|---|
| ai_processor | 910 | +80 |
| assignments | 663 | 0 |
| audit | 367 | 0 |
| AutoGrader | 613 | 0 |
| billing | 2139 | 0 |
| classrooms | 434 | 0 |
| dashboard | 270 | 0 |
| students | 404 | +18 |
| users | 698 | 0 |

The +98 is slices A and B. This record does not split them by slice.

**Skips:** the 30 are all opt-in or structural, read from the log's own skip lines: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, 2 that cannot fork inside a parallel worker, 2 audit benchmarks. The same 30 as at refresh 10. None was skipped for want of Chromium: the real-browser PDF tests ran.

**Why this run is a hard gate here:** it is the only full run of the exact merged tip. Slice A had its own full run at `58326e45` (6442 OK) and slice B at `6ca94c09` (6496 OK), each on its task branch before its last small changes; this run is the tree that goes to staging.

**The machine:** shared with another project. Its manager stopped its test runs and headless browsers from 10:42 to 11:02 for this run and the three-minute targeted run before it (H-133's regression on the beta line, 10:42:20 to 10:45:15); every GAP session was told to keep still from 10:38. Load average (1, 5, 15 minutes): 2.93 / 3.81 / 3.26 at the script's start; 4.00 / 4.44 / 3.78 at the end. No other GAP test run was active.

**The log:** committed whole as `gate10_release1_r1_82c3108d.log.xz`. Unpacked it is 8,805,985 bytes with sha256 `e77bb2c2ca77f326286bda7924b1686625c6670590142edfa02dc62699e0e6c2`; the unpacked bytes were compared with the original before this commit. `gate10_release1_r1_82c3108d.summary.txt` is the script's own summary, unchanged.

**After the run tip:** this record and its three files are the only change, so 0 non-docs files differ from `82c3108d`.
