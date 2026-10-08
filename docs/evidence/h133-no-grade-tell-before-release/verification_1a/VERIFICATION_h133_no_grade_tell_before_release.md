# Verification: H-133: before release a student is told and shown nothing that names grading @ b4a3b70e

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07.
**Branch:** `task/h133-no-grade-tell-before-release` @ **b4a3b70e** (production code tip `8e61018a`; test tip `a9ca796b`), on `task/beta-batch-11` `be05f953` (batch 10 as pushed, with H-130 and H-120). For batch 11. No model change, no migration, no setting.
- The refusal sentences, the codes and the student's list (`846e7a6e`, tests first in `d9226893`).
- The refusal's code on the tracked task row, for a client that polls (`f15c8608`, tests first in `fb1c4576`); from my pre-read.
- `max_points` for a student before release (`8e61018a`, tests first in `a5209874`; two more tests in `a9ca796b`); from my first run.
- One assertion of an H-130 test changed, test only (`2233be8a`), with its author's agreement.
- Three base updates by 0b (`7dcdac89`, `20e3a3a5`, `73fc315f`). Everything else up to the tip is evidence (docs only); `24355264` and `b4a3b70e` are docs only.

The evidence is in `docs/evidence/h133-no-grade-tell-before-release/`.

**I ran at b4a3b70e** (2026-10-07, 11:10:40 to 11:15:51 WAT) in 0b's slot, from my own detached scratch checkout, serial, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; under rule 18 the output went straight to a file with stdin from `/dev/null`. Each run had its own test database, created and destroyed by Django (every log has its "Destroying test database" line; I listed the server's databases after the runs and found none of mine).

Under rule 15 I cite ed's gates and ed's regression (students and assignments, serial, 1030 OK, skipped=14, at `24355264`) and repeat none of them.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I drove: through the real routes, the two real queued tasks polled as a client polls them, and a paper graded by the real grading function. Two things I found were folded in by the SM's rulings before this verdict (the code for a polling student; `max_points`). Nothing is required before the merge. **N1 must be read with the title:** the title's sentence is wider than what was built, and one student answer is known to fall outside it (H-141).

## What changes
- **A student whose upload or edit is refused** because the paper is graded, being graded, or still being processed is told one of two sentences that do not name grading. A teacher is told the reason in words, as before.
- **"Being graded" and "an earlier upload is still being processed"** give a student the same status, the same code and the same sentence.
- **Every such refusal carries a code** (`submission_closed`, `submission_busy`, `submission_attempts_used`): in the 409 answer, in the queued task's result, and on the tracked task row that the task-status route serves to a client that polls.
- **On the student's list of submissions** the grading state reads IDLE until release and DONE after; the three scheduling fields are empty; a student who filters by grading state is refused (403).
- **`max_points`**, for a student before release, is the assignment's total (or nothing), as on a submitted paper, on the list and on the submission page.
- **Not changed, by the founder's choice:** the student's remaining attempts still drop to 0 when a paper is graded, and a change is still refused.

## How this item came to its present shape
- **My pre-read** (by reading, before any run) found that a student who polls a queued upload or edit was served the neutral sentence but no code, because the task-status route serves the tracked row and not the task's result. The SM ruled it in (`f15c8608`).
- **My first run, 2026-10-06 19:13 to 19:14 at `bce9c5d4`, stopped at a red baseline:** `Ran 53 tests`, FAILED (failures=9). I released the slot at once and ran no mutant.
  - **Eight of the nine were my probe's own fault.** The six P2 tests, P3 and P4 polled the task-status route with the tracked row's own id; the route finds a task by its Celery id. They said nothing about the code under test.
  - **One was real.** P1 showed that on a graded, unreleased paper `max_points` differed from the same paper when only submitted. The SM ruled it into this row; `8e61018a` is the change.
  - The probe as it ran then, its log and the note written before it are kept (files below).
- **For the second attempt** I corrected the probe (the tracked rows have a Celery id and are polled by it), and added P5 on the SM's word: the paper graded by the real grading function, every student route compared whole with the paper before grading. A sixth mutant undoes the `max_points` change.
- **The eight corrected tests had never been seen green or red before this run.** The run below is their first evidence, both ways.

## What I checked by running (at b4a3b70e)
The author's tests ask one route at a time and, for the first part, make the graded row by hand. My probes ask every student route at once for one paper as it moves through its states, drive the two real tasks, and grade through the real path.

| Probe | Result |
|---|---|
| **P1: one paper, seven student readings** (the submission page; the list plain, by assignment and by "unreleased"; the assignment page; the assignment list; the dashboard list), each compared whole with the same paper when only submitted. | **Grading scheduled, being graded, grading failed: 0 differences** in all seven. **Graded, unreleased: 4 differences, all `remaining_attempts`** (2 to 0), on the submission page and the three list readings; the assignment page, the assignment list and the dashboard list do not differ at all. Released: the list says DONE with its time and the page differs in 9 places, as it should. |
| **P5: the same comparison after the real grading function** (`grade_engine`; the AI call replaced by a plain grading result, the formatting task and the student-summary task not run). Once with no total on the assignment, once with a total of 7 against the grader's 10. | The stored maximum was 10 both times. **4 differences each time, all `remaining_attempts`.** `max_points` did not differ. |
| **P2: the two real queued tasks, polled by the student** through the task-status route by the Celery id. Upload task: a grade lands during the extraction; a grading claim starts during it; a grade lands after the second check, so that the check under the row lock refuses. Edit task: a grade lands during it; a claim starts during it; the task starts on a paper already graded. | In all six the student is served `status` failed and a `meta` holding the neutral sentence and its code: `submission_closed` with "This submission can no longer be changed." or `submission_busy` with "This submission can't be changed right now. Please try again later." No word of grading. |
| **P3: the teacher's edit task, a grade lands during it; the teacher polls.** | The teacher is served the reason in words ("This assignment has already been graded, so it can no longer be submitted again.") with the code `submission_closed`. |
| **P4: being graded, read from a task, against being processed, refused at once.** | One sentence and one code for both: the busy sentence and `submission_busy`. |

## Each probe was seen red (rule 19)
The failing tests for each mutant were written by name before any run of this version (`h133_expected_kills.txt`, file time 2026-10-06 19:24:26). Each mutant run is my probe module alone, 11 tests, so each failing set is exact.

| Mutant (mine) | Result | Failing tests |
|---|---|---|
| **R1** a student is told the teacher's sentence for a graded paper | `Ran 11 tests in 2.839s`, FAILED (failures=4) | The four P2 tests in which a grade lands or is already there. As written. |
| **R2** a student is told the teacher's sentence for a paper being graded | `Ran 11 tests in 1.994s`, FAILED (failures=3) | The two P2 "grading claim starts" tests and P4. As written. |
| **R3** the teacher is given the student's neutral sentence | `Ran 11 tests in 2.297s`, FAILED (failures=1) | P3. As written. |
| **R4** the student's list shows the real grading state before release | `Ran 11 tests in 3.623s`, FAILED (failures=5) | P1 and the two P5 tests: the three test methods written. **The count is 5, not 3:** P1 failed in three of its states (being graded, grading failed, graded and unreleased), and each is printed as its own failure. My note said "fails exactly three" and did not foresee that; no other test method failed. |
| **R5** the refusal's code is not put on the tracked row | `Ran 11 tests in 3.474s`, FAILED (failures=7) | The six P2 tests and P4. As written. |
| **R6** a student is shown the paper's own stored maximum before release | `Ran 11 tests in 4.290s`, FAILED (failures=3) | P1 and the two P5 tests. As written. |

All eleven probe tests were red under at least one mutant and green on the code as it is.

## Evidence
| Check | Result |
|---|---|
| **Run** @ b4a3b70e: my probes + `students.tests_no_grade_tell_before_release` + `AutoGrader.tests_student_feedback_guard` | **`Ran 63 tests in 10.893s`, OK** (wall 41 s): my 11, the author's module 37 and the guard 15. Load before: 6.87. |
| **R1 to R6** @ b4a3b70e | As in the table above. Load before each: 8.03, 5.97, 7.09, 6.35, 9.01, 8.82. No test of mine has a wall-clock limit. |
| ed's gates (cited) | Reproduce-first on the old production files at `6fc161e2`: red, exactly the 17 tests named beforehand. Modules and guards: 527 OK at `6fc161e2`, 536 OK at `e85e2ae0`, 555 OK at `a9ca796b` (skipped=1 each). Mutants: 25 of 25 on the final code, each with its failing tests named beforehand (10 at `6fc161e2` on files unchanged since, 12 at `e85e2ae0`, and at `a9ca796b` the six on `students/serializers.py` again with three new). The regression: 1022 OK at `bce9c5d4`, then 1030 OK (skipped=14) at `24355264`. 0b's three guards on the merged tree: 56 OK at `73fc315f` and again at `24355264`. |
| The battery is on the final code | `students/serializers.py` last changed at `8e61018a`; its nine mutants ran at `a9ca796b`. `assignments/tasks.py` last changed at `f15c8608`; the twelve on it and on `students/services.py` ran at `e85e2ae0`. `students/services.py`, `students/views.py` and `students/exceptions.py` last changed at `846e7a6e`, before the first battery at `6fc161e2`. I checked the commit order, not the battery. |
| No production change after the author's last runs | `git diff 24355264 b4a3b70e` is two files under `docs/`. `git diff 8e61018a b4a3b70e` outside `docs/` is one test module. |
| Against the base | The branch differs from `be05f953` outside `docs/` in 10 files: 5 production files (`students/exceptions.py`, `students/services.py`, `students/views.py`, `students/serializers.py`, `assignments/tasks.py`) and 5 test modules. |
| Hooks | `pre-commit run --from-ref be05f953 --to-ref b4a3b70e`: exit 0, 18 passed, 0 failed, 7 skipped for want of files (the range; I did not run each commit separately). |
| Rule 14 | The AI call, the formatting task and the task launch are replaced by mocks whose return values are set to plain values; no mock reaches a response, a serializer, a log or the database. |

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and each mutant was applied with `python -B`. `__pycache__` under `AutoGrader/`, `students/`, `assignments/`, `dashboard/` and `users/` was deleted before the baseline, before each mutant and after each restore (the logs show 0 directories each time). Each mutant log has its "applied" line and "mutated sha differs: True"; after each mutant the restored file matched the commit blob's sha256, and no tracked file was changed. On 0b's note the runner stops without a test run if a mutant is not applied; that did not happen.

**The form of my logs.** Each holds exactly one "Ran" line and one OK or FAILED line. After them come the lines written to standard output (Django's own and the lines my probes print), the "Destroying test database" line, and my footer: exit status, wall time, load and the restore checks.

## Notes
**N1 (the title says more than was built; one student answer is known to fall outside it).**
- **The known case, told to me by the SM on 2026-10-07 and read by me at b4a3b70e, not run.** The answer to a student's own upload that is ACCEPTED (201, `upload_answers`) is built with the teacher's detail serializer and no request. That serializer carries `grading_state`, `scheduled_grading_at`, `grading_task_name` and `is_grading_scheduled`. A paper that is not graded and has no live grading claim is open to a new upload while attempts are left; so after a teacher's schedule, or after a grading that failed or was left stale, the student's own upload answer can carry those fields as they are.
- **Does it make an H-133 claim false as worded?** The claims listed under "What changes" are about the refusals and about the student's list; none of them is made false. The evidence names the upload answer as H-141's ground, but only for a graded paper ("it never answers for a graded paper", which is true). **The title's sentence, "a student is told and shown nothing that names grading", is false for this answer**, and items 3 and 4 of the evidence's own list of tells (the grading state as it is; the teacher's schedule) are closed on the list and stay open here.
- **What I ask:** the package and the backlog row say what was closed, in the words of "What changes", and name this answer as open until H-141. Not the word "nothing".
- **What I did not check.** My P1 and P5 read the seven GET answers. They do not read the answer to an accepted upload or an accepted edit, in any state.

**N2 (the one tell left by decision, observed).** `remaining_attempts` goes from 2 to 0 at grading, before release, on the submission page and the list. Founder's choice A; a student who watches that number can tell.

**N3 (`max_points`, the limits).**
- A serializer built without the request gives the old answer. Three places do that, by the author's reading and mine: two teacher-only answers and the upload answer of N1.
- If the teacher changes the assignment's total after grading, the student sees the new total before release, as on a submitted paper. By the rule.
- After release a student reads the grader's maximum, and a teacher reads it at all times; both pinned by the author's tests.
- In P5 the formatting task did not run, so P5 says nothing about a formatted grade. That is H-130's ground and H-127's.

**N4 (not covered by any test, mine or the author's).** A second AI grading of a paper already graded. The frontend: nobody has read it for the old sentences, for RUNNING or FAILED, for the scheduling fields or for the filter, which now answers 403 for a student.

**N5 (for the deployment and the frontend).**
- A student's refusal sentences change. A client should read `code`, not the sentence: in the 409 body beside `error`, or, for a queued upload or edit, inside `meta` in the task-status answer, which that route sends as text.
- List answers cached before the deployment are served for up to 5 minutes. A tracked row refused before the deployment keeps the sentence it was given then.

**N6 (the author's one test never seen red).** `test_a_submitted_paper` pins something that was already true. The author says so and does not claim it as evidence; I agree.

**N7 (the load during my runs).** 6 to 9 by the one-minute figure. Nothing of mine is timed, and every result came out as written.

**N8 (not observed anywhere live).** Everything here is from the code and from test runs on this machine. No stored row, staging or live response was looked at, by the author or by me.

**N9 (rollback).** Code only; no step. After a rollback a student is told the old sentences again, the list shows the real grading state and the schedule, and `max_points` shows the stored maximum. A tracked row refused while this was live keeps its neutral sentence and its code.

**The credential check on my own files** (the widened form, values not printed): this record, the probe, the probe as first run, the mutants, the two notes written before the runs and the eight logs hold no URL with anything in the password position, in plain or percent-decoded form, and no assignment form.

Logs: `runs/h133_b4a3b70e.log`, `runs/h133_mutant_R1_b4a3b70e.log` to `runs/h133_mutant_R6_b4a3b70e.log`, `runs/h133_first_baseline_red_bce9c5d4.log`. Probe: `h133_probe_tests_vf1a_h133_probe.py`; as first run: `h133_probe_v1_as_run_19h13.py`. Mutants: `h133_mutants_R.py`. Written before the runs: `h133_expected_kills.txt` (2026-10-06 19:24:26), `h133_expected_kills_first_attempt.txt` (2026-10-06 18:44:40).
