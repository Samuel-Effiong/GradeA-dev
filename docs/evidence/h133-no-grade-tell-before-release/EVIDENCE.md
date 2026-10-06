# H-133: before release, a student is told and shown nothing that names grading

Author: ed (Security Engineer), 2026-10-06. Branch `task/h133-no-grade-tell-before-release`,
stacked on H-127 (`task/h127-student-feedback-whitelist` d027ac91), because it extends the list
serializer and the filter refusal H-127 adds. Beta line. Verifier: 1a. Design accepted by the SM
(2026-10-06 about 15:15), from the founder's representative's decision of the same day.

**State of this file: written at 2026-10-06 16:33 WAT, BEFORE ANY RUN.** Nothing below has been run. No gate
runs until H-127 is in batch 10 and 0b has moved this branch onto it (SM ruling). The results are
added later, against what is written here.

## The rule and the decision

"A student should not know a grade exists before the teacher releases it" (founder's
representative, 2026-10-06). For the refusal of a change the choice was **neutral wording**: a
graded paper stays closed to resubmission and to edits, there is no resubmission until release,
and the student is not told why in words that name grading.

## What a student could tell before this change (read in the code, not observed live)

1. An upload or an edit of a graded paper was refused with "This assignment has already been
   graded, so it can no longer be submitted again.", released or not.
2. While a grading run held the paper: "This submission is being graded right now ...".
3. The student's list of their submissions showed `grading_state` as it is (IDLE, RUNNING, DONE,
   FAILED) and could be filtered by it.
4. The same list showed the teacher's scheduling of a grading run: `scheduled_grading_at`,
   `grading_task_name`, `is_grading_scheduled`.

## What changes

- **Two sentences for a student, chosen where the refusal is raised** (`students/services.py`,
  `_check_submission_open`, the one check behind every path, the one under the row lock
  included; `ensure_no_active_extraction`):
  - graded, released or not: **"This submission can no longer be changed."**
  - being graded, or an earlier upload still being processed: **"This submission can't be changed
    right now. Please try again later."** One sentence for both, so a student cannot tell them
    apart.
  - A teacher (a proxy upload, an edit) is told what they were told before, word for word.
  - The attempt-limit sentence is unchanged for everyone.
  Who is told is a required argument of the check (`told_to_student`), with no default: every
  caller decides. Because the sentence is chosen at the raise, it is the same wherever it lands:
  the 409 answer, the queued task's result, the tracked task row a student can poll, a batch
  session's entry.
- **A stable `code` beside every such refusal** (there was none; only the sentence):
  `submission_closed` (graded), `submission_busy` (being graded, and an earlier upload still
  being processed), `submission_attempts_used` (the limit). The 409 body is
  `{"error": "<sentence>", "code": "<code>"}`; the queued task's result gains the same `code`
  key. **Added after the first gate (Verifier 1's pre-read; SM ruling about 17:55):** the code is
  also put on the tracked task row (`meta["code"]`), because a client that POLLS a queued task is
  served that row by the task-status route and never the task's return value; as first built, a
  polling student read the neutral sentence and no code. The codes are attributes of the four exception classes, which keep their names and their
  place in every handler. The names say what the caller can do, never why.
- **The student's list** (`StudentSubmissionListSerializer`, a student caller only):
  `grading_state` is `DONE` once the grade is released and `IDLE` until then, never `RUNNING` or
  `FAILED`; `scheduled_grading_at` and `grading_task_name` are null and `is_grading_scheduled`
  false. A teacher's list is unchanged.
- **`?grading_state=` answers 403 for a student**, by H-127's refusal (one more name on its list).
  **`?is_published=` is left as it is** (SM): it is false both before grading and before release,
  so it tells nothing, and "show my released grades" is a fair query.
- **The guard** (`AutoGrader/tests_student_feedback_guard.py`) learns the names: the filter list,
  and the declaration of what replaces the state and the schedule fields.

Production files: `students/exceptions.py`, `students/services.py`, `students/views.py`,
`students/serializers.py`, `assignments/tasks.py`. No model change, no migration, no setting.

## What it does NOT hide

- **`remaining_attempts` still drops to 0 when a paper is graded**, released or not. It is the one
  tell that remains by decision (the founder's choice A; SM: it does not go back). A test compares
  a graded, unreleased paper's whole list row with a submitted paper's and asserts this number is
  the ONLY difference. For a paper that is scheduled, being graded, or whose grading failed, the
  row is identical with no exception.
- **A change is still refused** once a paper is graded; the student is told it is closed, not why.
- **The answer document and the course final grade** are d5's row H-130, not this one.
- **Cached list responses** outlive the change for up to 5 minutes (`CACHE_TTL`).
- **The tracked row of an old refusal** keeps the sentence it was given when it was refused.
- **The teacher's own routes** still say "graded" and "being graded": a teacher knows.

## For the frontend

- A student's refusal sentences change (the two above). Do not match on sentences: every such
  refusal now carries `code`: `submission_closed`, `submission_busy`, `submission_attempts_used`.
  **Where each client finds it:** on an immediate refusal, in the 409 body beside `error`; on a
  queued upload or edit that is refused, in the task-status answer, inside `meta` (which that
  route sends as text: it holds `'code': '<code>'` and `'error': '<sentence>'`).
- On the student's submission list: `grading_state` is `IDLE` until the grade is released, then
  `DONE`; the three scheduling fields are null, null and false.
- `?grading_state=` answers 403 for a student.
- **Inferred, not checked (I cannot read the frontend):** no student page depends on `RUNNING`,
  `FAILED`, the scheduling fields, the old sentences or that filter.

## Tests changed, and why

Twelve assertions in two older test modules expected the old words for a STUDENT caller
(`students/tests_post_grading_submission_lock.py`, `students/tests_async_edit_path.py`); they now
expect the neutral sentences. The assertions for a teacher caller in the same modules are
untouched and still expect "already been graded" and "being graded". H-127's guard rule 3 is
widened by one name.

H-127's mutation runner (`docs/evidence/h127-student-feedback-whitelist/mutate.py`) is the record
of its battery as it ran. Two of its mutants (C1, C2) change the line that lists the teacher-only
filters, which this branch extends, so that runner's `--check` no longer passes on this tree. It
is not edited: its results are for the tree it ran on. This branch's runner has its own two
mutants on that line (F1, F2).

## The base, and one line of an H-130 test (added 2026-10-06 about 17:40, still before any run of mine)

- 0b moved the branch onto `task/beta-batch-11` twice: 7dcdac89 (onto 3457df56, batch 10 as
  pushed, which holds H-127 and H-128) and 20e3a3a5 (onto 7944259e, after d5's H-130 was merged).
  No conflict either time. H-130 edits three of the files this branch edits
  (`students/serializers.py`, `students/services.py`, `students/views.py`); by my reading and
  0b's the hunks do not meet: H-130 changes the answer document near the top of `services.py` and
  the student's detail serializer, this branch the closing check further down and the list
  serializer. H-130 calls none of the three functions whose signatures this branch changes.
- **By the SM's ruling this branch is gated once, on the tree that holds H-130.**
- **One assertion of an H-130 test is changed here, test only (2233be8a), with d5's agreement and
  the SM's, both 2026-10-06.** `students/tests_answer_document_before_release.py`,
  `TheUploadRoutesOwnAnswer.test_an_upload_on_a_graded_unreleased_row_is_refused_with_no_document`
  pinned the refusal's body as exactly `["error"]`; this branch adds `"code"`. I found it by
  reading before the two were on one tree. It now asserts what the test is for: no answer document
  and no grade field in the refusal (`raw_input`, `score`, `score_percentage`, `feedback`,
  `formatted_grade`, `graded_at` named absent), and no key outside `{"error", "code"}`. The 409
  line and the stored-document line around it are untouched. v2, H-130's verifier, was told.
- **0b's cross-side run, not mine** (its own grant; its expectation written at 17:19 before the
  run, `crossside_expected_written_by_0b.txt`): on 20e3a3a5, before that test change, H-124's
  guard, H-127's guard as this branch extends it, and d5's module: Ran 62 tests, FAILED
  (failures=1), exactly that one test, with `['error', 'code'] != ['error']`. Both guards pass and
  no other test of d5's module fails. Log, byte-identical to 0b's file:
  `crossside_guards_and_h130_tests_on_h133_20e3a3a5_run_by_0b.log`.
- The gate's list of related modules now includes d5's two H-130 test modules.

## Written before the runs

**Step 0, reproduce-first** (`students.tests_no_grade_tell_before_release` and the guard, on the
five production files as at the base). I expect exit non-zero and these 17 red, no others:
- `StudentToldNothingOfAGradeTest`, all 5;
- `StudentCannotTellBusyKindsApartTest`, all 3;
- `TheOtherRefusalsTest`, all 3 (each needs the `code`);
- `StudentListShowsNoGradingStateTest`, 3 of 8: scheduled/being graded/failed look like submitted;
  graded but unreleased; a student cannot filter on the grading state;
- `TheRefusalCodesAreAClosedListTest`, 1;
- the guard, 2: rule 3, and the H-133 declaration test.

**Step 1** (the new module, the guard, twelve related modules, among them the two older ones
above and d5's two H-130 modules, and the 22 guard modules): exit 0, OK. I have not counted the
tests. In particular the H-130 test changed in 2233be8a passes.

**Mutants:** 17, each with the tests it must fail named in `mutate.py` before any run: six on who
is told which sentence, three on the codes, six on the list, two on the filters. I expect 17
KILLED.

**Regression** (students, assignments, serial): OK.

## Not done

Nothing run. No database, staging or live service contacted. The frontend not read.

## Results

Everything above this heading was written before the runs and is left as written, except the two
places marked "added after the first gate".

### The gate, at 6fc161e2 (0b's GRANT, 2026-10-06 17:49:09 WAT)

One run of `run_h133_gate.sh 6fc161e2 1 7944259e` (script sha256 a599fabeb3b21dfb), 17:49:26 to
17:57:42, script exit 0, serial, 6G scope, rules 12, 13, 16, 17 and 18. Not stopped, not repeated.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0, the new module and the guard on the five production files as at 7944259e | red, 17 named tests, no others | exit 1: Ran 35 tests in 3.458s, FAILED (failures=19, errors=3). **Exactly the 17 named tests.** 22 result lines, because two of the 17 have sub-cases (four and three). | `prefix_base_production_failing_6fc161e2.txt.gz` |
| makemigrations --check | no changes | no changes | `makemigrations_check_6fc161e2.txt` |
| 1, the new module, the guard, 12 related modules, 22 guard modules, at the tip | exit 0, OK | exit 0: **Ran 527 tests in 176.055s, OK (skipped=1)** | `modules_and_guards_6fc161e2.txt.gz` |
| 2, mutants | 17 KILLED | **17 of 17 KILLED**; SURVIVED 0, KILLED_NOT_AS_EXPECTED 0, BROKEN 0 | `mutation_log_6fc161e2.txt`, `mutation_results_6fc161e2.json`, `mutant_logs_6fc161e2/` |

- The one skip is the opt-in real, billed AI call in `students.tests_async_edit_path`
  (`RUN_REAL_AI`). The H-130 test changed in 2233be8a is among the passing.
- Every mutant's inner run shows its own "Ran 66 tests" line, exit 1, every named test among the
  failures, and 0 `__pycache__` directories left. Files restored after step 0 and after every
  mutant; the mutants' database dropped.
- Load average: 4.50 4.69 5.82 at the start; step 1 from 5.01 to 7.05; 13.84 9.15 7.25 at the end
  of the battery (another project's work beside it). Nothing in this gate has a wall-clock limit.
- The logs are as the runs wrote them, gzipped. Before gzip: step 1's log 259,383 bytes, 2,687
  lines, sha256 starts 5d7d39d75c58b274, "Ran" at line 2684, "OK" at 2686; step 0's log 55,435
  bytes, 676 lines, sha256 starts c701dc63c43062b3, "Ran" at line 671, "FAILED" at 673.

### After the gate: one delta, from Verifier 1's pre-read of 6fc161e2

Verifier 1 read the code before running anything and found that the stable code did not reach a
student who polls (see "What changes"). The SM ruled a fold. Tests first, then the code:
- nine tests, `StudentPollsARefusedTaskTest`: the real upload task and the real edit task run for
  a student, and the task-status route read as that student. Refused before the extraction; a
  grade, and a grading claim, landing during the extraction (for the upload both the check after
  the AI call and, with the earlier checks taken out, the one under the row lock; for the edit the
  check under the lock); the paper being graded when the edit runs. Each expects the neutral
  sentence, the code on the tracked row and in the polled answer, and no form of the word "grade"
  anywhere in the answer. These are also the paths Verifier 1 named as read by no test of mine.
- the code: `assignments/tasks.py`, the two refusal handlers put the code into the tracked row's
  meta. No other production file changes.
- five more mutants (P1 to P5): the code left off the tracked row in each task; and, for each
  check that decides whose sentence a task carries (the upload's checks before the lock, its
  check under the lock, the edit's check under the lock), the teacher's sentence given to a
  student.

So the gate's results stand for everything the delta does not touch, and these are owed on the
final tip and NOT yet run as I write this: the modules and guards once more; the mutants on the
two production files the delta's tests and code bear on, `assignments/tasks.py` (C2, P1, P2) and
`students/services.py` (S1 to S6, P3 to P5), twelve in all; then the regression. The other ten
mutants (C1, C3, L1 to L6, F1, F2) are on `students/views.py`, `students/exceptions.py` and
`students/serializers.py`, which the delta does not change; the new test module only gained
tests.

**Written before the second run:** the 1b step: exit 0, OK, 536 tests (the 527 and the nine new),
skipped=1. The twelve mutants: 12 KILLED, each with every test named for it.

### The second run, at e85e2ae0 (0b's GRANT, 2026-10-06 18:17 WAT)

One run of the gate's 1b mode with twelve named mutants (script sha256 a599fabeb3b21dfb, runner
sha256 b1e0e2b0158dc16b), 18:17:21 to 18:30:03, script exit 0, serial, 6G scope. Not stopped, not
repeated. e85e2ae0 is f15c8608 (the code tip) and docs.

| Part | Written before | Found | Log |
|---|---|---|---|
| The new module, the guard, 12 related modules, 22 guard modules, at the tip | exit 0, OK, 536 tests, skipped=1 | exit 0: **Ran 536 tests in 380.814s, OK (skipped=1)** | `modules_and_guards_e85e2ae0.txt.gz` |
| The twelve mutants on `assignments/tasks.py` and `students/services.py` (C2, P1 to P5, S1 to S6) | 12 KILLED | **12 of 12 KILLED**; SURVIVED 0, KILLED_NOT_AS_EXPECTED 0, BROKEN 0 | `mutation_log_e85e2ae0.txt`, `mutation_results_e85e2ae0.json`, `mutant_logs_e85e2ae0/` |

- The nine polling tests ran here for the first time, and passed. That each fails without the
  code on the tracked row is shown by P1 (the five upload tests) and P2 (the four edit tests); that
  a teacher's sentence cannot reach a student's task is shown, check by check, by P3, P4 and P5.
  They were not run against the code as it stood before f15c8608 (no reproduce-first step for the
  delta, rule 15); P1 and P2 undo exactly that commit's two changes.
- Each inner run shows its own "Ran 75 tests" line, exit 1, every named test among the failures,
  0 `__pycache__` directories left.
- **Where each mutant's result is, on the final code:** these twelve at e85e2ae0; the other ten
  (C1, C3, L1 to L6, F1, F2) at 6fc161e2, on three production files unchanged since. 22 mutants,
  22 KILLED, none counted twice (S1 to S6 and C2 ran at both tips and were KILLED at both).
- Load average: 12.48 13.29 9.64 at the start, 16.67 at the end of the first part, 10.20 13.75
  12.54 at the end: another project's full run beside it. Nothing in this run has a wall-clock
  limit.
- The log before gzip: 273,261 bytes, 2,844 lines, sha256 starts 590f4f32088f7a73, "Ran" at line
  2841, "OK" at 2843.

### Still owed as this is committed

0b's base update onto batch 11's current tip with its cross-side guards, then the one owning-app
regression (students, assignments) on that tip, on a quiet machine. Its result is added in a later
docs commit.

### The base update onto batch 11 at be05f953, and 0b's guards

0b merged batch 11's then tip be05f953 into this branch at 16d552c4: 73fc315f (18:31, no conflict).
Outside `docs/` the merge brings two files, `scripts/check_migration_safety.py` and
`AutoGrader/tests_migration_safety_check.py`; it touches no file of this branch. **Run by 0b, not
by me** (its own grant; its expectation written at 18:31:23, before the run,
`crossside_h133_be05f953_expected_written_by_0b.txt`): on 73fc315f the three guards
`AutoGrader.tests_no_playwright_at_import`, `AutoGrader.tests_student_feedback_guard` and
`AutoGrader.tests_migration_safety_check`: Ran 56 tests, OK, exit 0, 18:41. Log, byte-identical to
0b's file: `crossside_guards_on_h133_73fc315f_run_by_0b.log` (two of Django's setup lines print
after the result line there, because the two streams reach the file late; no test line follows
the result). The modules-and-guards step and the mutants are not repeated on the merged tip
(rule 15): the merge changes none of this branch's files.

### The regression, at bce9c5d4 (0b's GRANT, 2026-10-06 18:50 WAT)

bce9c5d4 is 73fc315f and one docs commit. One run of `run_h133_gate.sh bce9c5d4 3 be05f953`
(script sha256 a599fabeb3b21dfb): students and assignments, serial, under the full-suite lock, 12G
scope, on a quiet machine (the other project's work paused for it), 18:49:58 to 18:52:55. Not
stopped, not repeated.

| Written before | Found | Log |
|---|---|---|
| OK | exit 0: **Ran 1022 tests in 162.884s, OK (skipped=14)** | `regression_bce9c5d4.txt.gz` |

- The 14 skips are all opt-in by their own messages: 9 load tests (`RUN_LOAD_TESTS`) and 5 real,
  billed AI calls (`RUN_REAL_AI`). None for want of Chromium; the real-browser tests ran. No FAIL
  or ERROR line.
- Load average: 1.18 6.80 10.04 at the start, 1.82 4.68 8.66 at the end.
- The log before gzip: 1,330,816 bytes, 15,178 lines, sha256 starts fed7bcad2d31de27. "Ran" is
  line 15171 and "OK" line 15173. After them: the runner's "Destroying test database" line and two
  "[scale] ..." lines that two tests print to standard output, which reaches the file at exit.
- A caller of the closing check that I had missed would have shown here as a TypeError (the check
  has no default for who is told). None did.

### Summary of the runs

| What | Tip | Result |
|---|---|---|
| Reproduce-first on the old production files | 6fc161e2 | red, exactly the 17 named tests |
| Modules and guards | 6fc161e2, then e85e2ae0 | Ran 527, OK (skipped=1); Ran 536, OK (skipped=1) |
| Mutants | 10 at 6fc161e2, 12 at e85e2ae0 | 22 of 22 KILLED on the final code |
| 0b's cross-side run before the H-130 test line was changed | 20e3a3a5 | Ran 62, failures=1: the one predicted test |
| 0b's three guards on the merged tree | 73fc315f | Ran 56, OK |
| Regression, students and assignments | bce9c5d4 | Ran 1022, OK (skipped=14) |

Nothing differed from what was written before each run.

**Rule 19 (a test is evidence only if it has been seen red), counted from the run records, not
from memory:** the new module has 29 tests. 15 were red in the reproduce-first step. 28 have been
seen red in some run: those 15, and 13 more under at least one mutant (the nine polling tests
under P1 or P2 among them). **One has never been seen red: `test_a_submitted_paper`.** It pins
that an ungraded, unscheduled paper's row shows IDLE and empty scheduling fields, which was already
true before this change; no mutant of mine breaks it, and it is not claimed as evidence of the
change.

Nothing was observed on a live or staging service, and the frontend was not read.

## A second delta, from Verifier 1's baseline run at bce9c5d4: max_points (written 2026-10-06 19:22 WAT, before any run of it)

Everything above this heading is left as it was. The summary above and its "22 of 22" describe
the code at f15c8608; this section adds to it.

**What Verifier 1 found.** On a graded, unreleased paper a second thing differs from the same
paper when only submitted, besides `remaining_attempts`: `max_points`, on the student's list and
on the submission page. The three submission serializers showed the row's own stored maximum when
it had one and the assignment's total otherwise; the stored maximum is written by the grade save
(`_populate_and_save_grade`, from the grader's own total; also by the teacher's manual grade). So
wherever the assignment has no total, or a different one, a number appeared or changed at
grading, before release. The Senior Manager ruled it into this row.

**Why my whole-row test did not show it.** `test_graded_but_unreleased_looks_like_submitted_but_for_the_attempts`
makes its graded row by hand and does not set `max_points`. The real save always does.

**The change.** One function, `max_points_shown(submission, request)` in `students/serializers.py`,
which the three serializers now call: for a student, until release, the assignment's total (or
nothing), exactly what a submitted paper shows; otherwise as before. A teacher's answers, and a
student's after release, are unchanged. No other file is changed.

**The tests (a5209874, their own commit, before the change).** A new class,
`GradedTheWayTheGraderSavesItTest`, grades the paper with the real save, with a question the
grader found no answer to (so the review fields are written too), and compares the whole answer
with the same paper when only submitted, on the list and on the submission page, with the
assignment's total absent and with it 20 against the grader's 10. `remaining_attempts` must be the
only difference. Two more tests pin what must not change: the student reads the grader's maximum
once released; the teacher reads it before release.

The student routes that carry `max_points`, by reading: the list and the submission page (both
tested); the answers to an upload and to an edit, which are refused for a graded paper and so
never answer for one; the teacher's table of submissions on an assignment
(`AssignmentDetailSerializer`), which a student is not given (they get
`AssignmentDetailStudentSerializer`). The Hardening Engineer adds, from its own reading, that the
stored answer document never printed the maximum.

**My other fixtures, checked for the same fault.** The real save writes fifteen columns
(`GRADING_RESULT_FIELDS`): two AI timestamps, score, ai_score, max_points, score_percentage,
feedback, grading_confidence, graded_at, grading_state, the four review fields, raw_input.
  * The list tests' hand-made graded row sets graded_at, score, score_percentage, grading_state,
    needs_review, review_tier and the schedule. It lacks max_points (the fault), feedback,
    grading_confidence, ai_score, the two AI timestamps, review_reasons, review_severity and a
    rewritten raw_input.
  * The refusal and polling tests use `_submission(graded=True)` of the lock tests, which sets
    graded_at, score, score_percentage, max_points, feedback, grading_state. Those tests assert
    the refusal, not a row, so what the row lacks does not weaken them.
  * Of the columns the list fixture lacks, the list answer carries only grading_confidence,
    review_reasons and review_severity (replaced for a student by H-127) and max_points. The new
    tests cover all of them at once, because the row is graded by the real save and the whole
    answer is compared. I found no second column of this kind by reading; the new tests are what
    would show one.
  * Not covered by any test here: a paper graded by the teacher's manual grade (it writes
    max_points too, through the same column, so the same function hides it), and a re-grade.

**Three mutants** (the ruling asked for one, M1; M2 and M3 are there so that the two "must not
change" tests are seen red as rule 19 asks; they are the Senior Manager's to strike):
  * M1 the student is shown the grader's maximum before release: fails the four whole-answer tests.
  * M2 the student is shown the assignment's total after release too: fails
    `test_once_released_the_student_reads_the_graders_maximum`.
  * M3 the teacher is shown the assignment's total before release: fails
    `test_the_teacher_reads_the_graders_maximum_before_release`.

**Expected, written before the run.**
  * The modules and guards of the earlier runs, with `students.tests_grading_hardening` added
    (it holds the older tests of this fallback): OK. The H-133 module now has 35 tests.
  * No reproduce-first step for this delta: M1 undoes exactly the change and must fail the four.
  * Mutants re-run on the final file (rule 17 addendum): the six on `students/serializers.py`
    (L1 to L6) and M1 to M3, nine in all, each KILLED with its named tests. L1, L3, L4 and L5 will
    fail some of the four new whole-answer tests as well as their named ones; that is expected.
  * The mutants on files this delta does not touch (S, C, P, F: sixteen) are not re-run.
