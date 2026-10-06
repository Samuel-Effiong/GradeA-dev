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
  key. The codes are attributes of the four exception classes, which keep their names and their
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
  The key is added beside `error` (HTTP 409 as before) and to the queued task's result beside
  `message`.
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

**Step 1** (the new module, the guard, ten related modules among them the two older ones above,
the 22 guard modules): exit 0, OK. I have not counted the tests.

**Mutants:** 17, each with the tests it must fail named in `mutate.py` before any run: six on who
is told which sentence, three on the codes, six on the list, two on the filters. I expect 17
KILLED.

**Regression** (students, assignments, serial): OK.

## Not done

Nothing run. No database, staging or live service contacted. The frontend not read.
