# Merge-down b12b: the gates and the full runs

Recorded by the Release Engineer (0b), 2026-10-08. 0b ran everything below and read each result. How each conflict was resolved: `RESOLUTIONS.md`.

**What it is:** origin/beta `035e0a07` (batches 8 to 12b as pushed) merged into `phase2/epic-a` `a29d8cb4`. Merge commit `79ab843c`; three test-and-guard-only follow-ups (`0b268c3e`, `5a6133bd`) and docs (`bf7404ad`); no production code differs from the merge commit's resolution.

| Step | Tip | When (WAT, 2026-10-08) | Result |
|---|---|---|---|
| Quick module gate (makemigrations; the four named modules of requirement 2 and the guards of both lines; 316 tests) | `79ab843c` | 12:12:47 to 12:13:36 | **FAILED, failures=2, errors=15**: two cross-line interactions (RESOLUTIONS.md), `md_b12b_modules_79ab843c.log.gz` |
| Quick module gate again | `0b268c3e` | 12:15:55 to 12:16:43 | Ran 316 OK, `md_b12b_modules_0b268c3e.log.gz` |
| ONE FULL RUN (first) | `0b268c3e` | 12:17:52 to 12:26:34 (483 s of tests) | **Ran 7079, FAILED (errors=1, skipped=30)**: `students.tests_formatter_input...test_the_prompt_dispatched_after_a_grading` (RubricMissingError; the same cause as follow-up 1); 0 blocked outbound; `strict_md12b_run1_red.log.xz` (kept as it is) |
| students.tests_formatter_input alone | `bf7404ad` | 12:28:33 to 12:28:53 | Ran 6 OK, `md_b12b_modules_formatter_bf7404ad.log.gz` |
| Quick module gate | `bf7404ad` | 12:28:58 to 12:29:47 | Ran 316 OK |
| **SECOND FULL RUN (the record)** | `bf7404ad` | 12:30:04 to 12:39:56 (556 s of tests; 592 s wall) | **Ran 7079 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired, `strict_md12b2_run2_green.log.xz` |

The Senior Manager ruled (12:3x) that the bundle's record should end on a green full run of the tip that goes out, not on a red one plus an argument, so the second full run was made; the first run's raw log stays as evidence and its cause is above.

**Tests per app** (counted from the run's per-test lines; they sum to 7079; the same in both runs): ai_processor 1031, assignments 698, audit 367, AutoGrader 754, billing 2172, classrooms 467, dashboard 270, students 622, users 698.

**Pre-checks of the script** (`strict_bundle.sh.txt`, the new version that stops on a missing settings file, a shared test-database name, a failing mypy or a dirty makemigrations): mypy Passed and makemigrations "No changes detected" in both runs. The whole script runs inside one inhibit (rule 16 revised).

**Skipped: 30** (batch 12b's full run on beta skipped 28: two more here are tests of the epic line that cannot fork inside a parallel worker or need an unavailable service; the reasons are printed in the log).

**Load average (1 minute):** 1.83 at the first run's start, 3.37 at the second's; 5.2 and 7.1 at their ends (the other projects on the laptop ran beside them: one niced single-process run at a time was their agreement).

**Credential pattern check (0b, `scripts/credscan.py`'s scan function called on each file added with this record, values masked):** the record, the scripts, the summaries and the four packed quick-gate logs: 0 hits. Each of the two packed full-run logs: no address with a password part; 36 literal rows, of which 34 are lines whose name merely contains the letters KEY ("keys", "Sort Key", "audit event metadata dropped" etc., an empty value: the tool counts and does not list them) and 2 are the known warning line from the AI module's test (a 13-character test host name, as in batch 12b's record); 12 variables or masks, 13 code expressions, 5 placeholder words. Not credentials.
