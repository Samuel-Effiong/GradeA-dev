# Batch 10: the strict full run

Recorded by Integration & Release (0b), 2026-10-06. 0b started this run and read its result.

**Scope:** `task/beta-batch-10` at `fd28682c`: beta `8bbf44f9` (batch 9) plus batch 10's one item, H-127 with H-128 (`88c88611`), and the batch's docs commit `fd28682c` (backlog rows and statuses; a dated correction to batch 9's Gate 1 record). The run is on the tip AFTER the docs commit. Twelve files outside `docs/` differ from beta. Eight are production files: `assignments/serializers.py`, `dashboard/views.py`, `students/serializers.py`, `students/views.py`, `students/services.py`, `students/second_opinion_serializers.py`, `ai_processor/services.py`, and the new `students/feedback_projection.py`. Four are test modules: `AutoGrader/tests_student_feedback_guard.py`, `ai_processor/tests_second_opinion_error_code.py`, `students/tests_formatter_input.py`, `students/tests_student_feedback_routes.py`. No migration against beta, no model change, no settings change.

**Command:** ONE strict full run under rules 12, 13, 16, 17 and 18, by `strict_b10.sh fd28682c b10` (a copy is beside this file as `strict_b10.sh.txt`; it is batch 9's script with the branch name changed): `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null`; no pipe. A watchdog at the side would have recorded the process tree and stopped the run after 300 s without a new log line. Whole-repo mypy (passed) and `makemigrations --check` (no changes detected) ran first.

| Tip | Start–end of the suite (WAT) | Result |
|---|---|---|
| `fd28682c` | 2026-10-06 16:31:54–16:39:06 (432 s wall, 396 s tests) | **Ran 5812 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Load average (1 minute):** 1.02 at 16:30:34 when the script was started; 2.94 at 16:31:54 when the suite started; 4.57 at 16:39:06 when it ended.

**Tests per app** (counted from the run's per-test lines; they sum to 5812):

| App | Tests | Change from batch 9's strict run (5755) |
|---|---|---|
| ai_processor | 823 | +6 |
| assignments | 663 | 0 |
| AutoGrader | 587 | +14 |
| billing | 2109 | 0 |
| classrooms | 401 | 0 |
| dashboard | 270 | 0 |
| students | 305 | +37 |
| users | 654 | 0 |

The changes are counted, not attributed test by test: the batch's 57 new tests are in H-127's four test modules.

**Skipped: 28**, the same as batch 9's run, by the reasons printed in the log: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, and 2 tests that cannot fork inside a parallel worker. No test was skipped for want of Chromium (0 lines "Headless Chromium not available").

**The machine:** shared with another project, which had stopped its work since 15:06 and started nothing during the run; its manager was told the run's end. 0b granted no other run of ours during it and had sent every session of ours a notice to start no commit hooks, type checks, scans or test runs. The laptop had been suspended from 16:08:10 to 16:27:53, before the run; there was no suspend during it. **This was the only strict run of batch 10.**

**The log:** `strict_b10.log.xz` beside this file is the raw log, compressed (8,032,907 bytes unpacked, sha256 `674586d28004c8ca4113106ed2569cf59cadaa9555bad0d7fe1a0a35f1fb5bfb`; unpack with `xz -dc`). It holds exactly one "Ran" line and its result line; after them come only the five "Destroying test database" lines. `strict_b10.summary.txt` is the script's own summary, as written.

**Credential pattern check (0b, on the three files added with this record, values masked):** done with the team's tool, which opens `.xz` since 2026-10-06 (H-136). No address with a password part; no archive it could not read. Two lines of the log match the assignment pattern on the word "secret" (lines 72793 and 72798); the matched text is a test's host name in a "blocked unsafe fetch" warning, the same two lines as in batch 8's and batch 9's logs. The tool skips lines longer than 4000 characters in silence (H-137): this log has none (counted from the unpacked log). Its limit: the two patterns only.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
