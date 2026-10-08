# Batch 11: the two strict full runs, the first red, the second green

Recorded by the Release Engineer (0b), 2026-10-07. 0b started both runs and read their results.

**Why there are two:** rule 15 gives a batch ONE full run. The first, at `079ae209`, was red on one test. It was not run again: its failure was read, the cause was fixed by a tests-only change that was gated, verified and merged, and the second run is the one full run of the tip that goes out. The red run is kept here whole, by the Senior Manager's ruling.

**Scope of the batch:** `task/beta-batch-11`: beta `3457df56` (batch 10) plus H-130 (`7944259e`), H-120 (`be05f953`), H-133 (`baf58b24`), H-130's tests-only follow-up (`a716c862`) and three docs commits (`0f5fb51b`, `079ae209`, `76f713e3`). Nineteen files outside `docs/` differ from beta. Nine are production files: `assignments/serializers.py`, `assignments/tasks.py`, `classrooms/final_grade.py` (new), `classrooms/serializers.py`, `classrooms/signals.py`, `students/exceptions.py`, `students/serializers.py`, `students/services.py`, `students/views.py`. One is tooling: `scripts/check_migration_safety.py`. Nine are test modules. No migration against beta, no model change, no settings change.

**Command, the same for both:** the strict full run under rules 12, 13, 16, 17 and 18, by `strict_b11.sh <tip> <label>` (a copy is beside this file as `strict_b11.sh.txt`; it is batch 10's script with the branch name changed): `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1`, `PYTHONFAULTHANDLER=1`, and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Output went straight to a file, with stdin from `/dev/null`; no pipe. A watchdog at the side would have recorded the process tree and stopped the run after 300 s without a new log line. Whole-repo mypy (passed, both times) and `makemigrations --check` (no changes detected, both times) ran first.

| Run | Tip | Start–end of the suite (WAT) | Result |
|---|---|---|---|
| first (`b11`) | `079ae209` | 2026-10-07 11:28:04–11:35:25 (441 s wall, 411 s tests) | **Ran 5913 tests, FAILED (failures=1, skipped=28)**, exit 1, 1 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |
| second (`b11b`) | `76f713e3` | 2026-10-07 12:17:14–12:24:35 (441 s wall, 403 s tests) | **Ran 5915 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired |

**Written beforehand** (0b's run log): for the first, OK, skipped=28 and more than batch 10's 5812 tests; it came out red, so it differed. For the second: exit 0, OK, skipped=28, Ran 5915 (the first run's 5913, less the one test replaced, plus the three that replace it); it came out so.

## The red run

**The one failure:** `AutoGrader.tests_cache_bespoke_1114.SubmissionDetailFreshnessTests.test_a_teachers_assignment_edit_does_NOT_change_this_payload`. A student reads `/api/v1/submissions/<id>`, the teacher retitles the assignment, the student reads again; the test asserted the two answers equal. They differed in the assignment's title inside the answer document ("Original Title" against "Retitled By Teacher").

**The cause, by reading:** the test's own docstring said the cache key of this answer was scoped to the student alone because the answer document was "a snapshot materialised once", and that the scope must be looked at again "if this is now live-rendered". H-130 (in this batch) made the student's document, until release, a fresh build from the assignment's current title, due date and the student's current name. So the test pinned behaviour H-130 replaced on purpose. It is deterministic: not load, not order.

**Why no earlier gate saw it:** H-130's regression ran classrooms, students and assignments. This module is in the AutoGrader app and is not on the repo-wide guard list. This full run was the first run of the AutoGrader app on H-130's code.

**What was done:** not re-run. The Hardening Engineer read the cache key and what invalidates it (an assignment's save bumps every student who holds an enrolment row in the course; no stale read; one narrow case logged as H-149). The Senior Manager ruled: the live render stands; the old test is replaced by three; tests only. That follow-up is `docs/evidence/h130-cache-test-contract/` (author's chain Ran 487 OK and three mutants, each failing exactly the tests named beforehand; Verifier 2 VERIFIED-WITH-NOTES; regression of AutoGrader, students and assignments Ran 1639 OK, skipped=16). Team rule 20 was written the same day: a change to what a cached answer contains runs the AutoGrader app's cache tests before it is called ready.

**Between the two tips:** `079ae209` → `76f713e3` changes one file outside `docs/`: `AutoGrader/tests_cache_bespoke_1114.py`.

## Both runs, side by side

**Load average (1 minute):** first run 2.88 when the suite started, 6.31 when it ended; second run 2.64 and 4.14.

**Tests per app** (counted from each run's per-test lines; they sum to 5913 and 5915):

| App | First run | Second run | Change of the second from batch 10's strict run (5812) |
|---|---|---|---|
| ai_processor | 823 | 823 | 0 |
| assignments | 663 | 663 | 0 |
| AutoGrader | 607 | 609 | +22 |
| billing | 2109 | 2109 | 0 |
| classrooms | 420 | 420 | +19 |
| dashboard | 270 | 270 | 0 |
| students | 367 | 367 | +62 |
| users | 654 | 654 | 0 |

The changes are counted, not attributed test by test: +103 against batch 10 in all.

**Skipped: 28 in both**, the same as batch 10's run, by the reasons printed in the logs: 12 real AI calls, 9 load tests, 4 live network, 1 `CI_REQUIRE_REDIS`, and 2 tests that cannot fork inside a parallel worker. No test was skipped for want of Chromium (0 lines "Headless Chromium not available" in each log).

**The machine:** shared with another project. For each run its manager agreed a quiet window and 0b sent every session of ours a notice to start no commit hooks, type checks, scans or test runs; no other run of ours went beside either.
- **Disclosed, second run:** the other project's manager asked, about two minutes before the start, to move the window from 12:17 to 12:20 so that one single-process run of theirs could finish by about 12:19. 0b's launcher started the suite at 12:17:14 by itself before 0b had read that message, so that run and this one overlapped for about two minutes. The load at the start was 2.64. Nothing failed.
- **Disclosed, both runs:** 0b sent the end-of-window notices late (the first on time to the other project and about a minute late to our sessions; the second 23 minutes late to all). That delayed others; it does not touch the runs.
- No suspend during either run.

**The logs:** `strict_b11.log.xz` (first run; 8,062,239 bytes unpacked, sha256 `8dc6f1db9a0c60f36518afc789b8375ded4520b4874c73133bc4f451f85fe8da`) and `strict_b11b.log.xz` (second run; 8,065,545 bytes unpacked, sha256 `0ca845c931d0c92c184d63262ac1b930c03ce005ef36aa1160569de62e400717`); unpack with `xz -dc`. Each was compared with its original after packing. Each holds exactly one "Ran" line and its result line; after them come only the five "Destroying test database" lines and, in the first, systemd's line that the run failed. `strict_b11.summary.txt` and `strict_b11b.summary.txt` are the script's own summaries, as written.

**Credential pattern check (0b, on the five files added with this record, values masked):** done with the team's tool of record (the one outside the repository, which opens `.xz`), its scan function called on these files. No address with a password part; no archive it could not read. Two lines of each log match the assignment pattern on the word "secret" (lines 73379 and 73384 of the first, 73550 and 73555 of the second); by their place they are the test's host name in a "blocked unsafe fetch" warning, the same two lines as in batch 8's, 9's and 10's logs; 0b did not print the matched value. That tool skips lines longer than 4000 characters in silence (H-137): neither log has one (counted from the unpacked logs). Its limit: the two patterns only.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
