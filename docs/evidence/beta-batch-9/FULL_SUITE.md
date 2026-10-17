# Batch 9: the strict full run

Recorded by Integration & Release (0b), 2026-10-06. 0b started this run and watched it.

**Scope:** `task/beta-batch-9` at `dea58a6c`: beta `74065867` plus batch 9's three items, H-110 (`cc9682b8`), H-123 (`9fb6d4fe`) and H-124 (`52d9628c`), and the batch's docs commit `dea58a6c` (backlog rows and two dated notes). The run is on the tip AFTER the docs commit. Five files outside `docs/` differ from beta: `assignments/pdf_renderer.py` (H-110, the one production file), `assignments/tests_pdf_renderer_driver_stderr.py` and `assignments/tests_pdf_renderer_driver_stderr_reader.py` (H-110), `assignments/tests_pdf_renderer.py` (H-123), `AutoGrader/tests_no_playwright_at_import.py` (H-124). No migration against beta, no settings change.

**Command:** ONE strict full run under rules 12, 13, 16, 17 and 18, by `strict_b9.sh dea58a6c b9` (a copy is beside this file as `strict_b9.sh.txt`; it is bundle 7's script with the branch name changed and a load reading added at the end): `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null`; no pipe. A watchdog at the side would have recorded the process tree and stopped the run after 300 s without a new log line. Whole-repo mypy (passed) and `makemigrations --check` (no changes detected) ran first.

| Tip | Start–end of the suite (WAT) | Result |
|---|---|---|
| `dea58a6c` | 2026-10-06 14:44:18–14:56:17 (719 s wall, 669 s tests) | **Ran 5755 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Load average (1 minute):** 3.70 at 14:41:23 when the script was started; 3.23 at 14:44:18 when the suite started; 6.91 at 14:56:17 when it ended.

**Tests per app** (counted from the run's per-test lines; they sum to 5755):

| App | Tests | Change from batch 8's strict run (5709) |
|---|---|---|
| ai_processor | 817 | 0 |
| assignments | 663 | +35 |
| AutoGrader | 573 | +11 |
| billing | 2109 | 0 |
| classrooms | 401 | 0 |
| dashboard | 270 | 0 |
| students | 268 | 0 |
| users | 654 | 0 |

The changes are counted, not attributed test by test: the batch's new tests are in assignments (H-110, H-123) and AutoGrader (H-124).

**Skipped: 28**, by the reasons printed in the log: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, and 2 tests that cannot fork inside a parallel worker. Batch 8's run skipped 26: it was serial, so those two ran there. No test was skipped for want of Chromium (0 lines "Headless Chromium not available").

**The renderer's stall test (H-123) in this run:** "slowest healthy render alone: 1.73 s; hung render's timeout: 6.93 s; limit for healthy renders beside it: 5.55 s". It passed.

**The machine:** shared with another project. Its manager was told before the start and at the end, and paused every seat of its own from the suite's start to its end; one 35-second single-file test run of theirs, started by hand, ended at 14:41:47, before the suite started. 0b granted no other run of ours during it and had sent every session of ours a notice to start no commit hooks, type checks, scans or test runs. Two commits of the Security Engineer's ran their hooks at about 14:41:30 to 14:43, before that notice reached it: inside this script's opening mypy step and before the suite started at 14:44:18. **This was the only strict run of batch 9.**

**The log:** `strict_b9.log.xz` beside this file is the raw log, compressed (7,595,904 bytes unpacked, sha256 `55482919d031e7e31a817e328959e692a26621d9001a7309b0c870aaeddc9302`; unpack with `xz -dc`). It holds exactly one "Ran" line and its result line; after them come only the five "Destroying test database" lines. `strict_b9.summary.txt` is the script's own summary, as written.

**Credential pattern check (0b, on the three files added with this record, the log unpacked, values masked):** no address with a password part. Two lines of the log match the assignment pattern on the word "secret" (lines 72508 and 72513); the matched text is a test's host name in a "blocked unsafe fetch" warning, the same two lines as in batch 8's logs. Its limit: the two patterns only.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
