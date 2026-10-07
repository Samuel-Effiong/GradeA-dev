# H-141: a student's own answer upload is answered with the student's serializer

Author: Security Engineer (ed). Branch `task/h141-student-upload-answer`. Beta line, batch 12,
after H-147. Verifier: Verifier 1. Severity LOW-MEDIUM (Senior Manager, 2026-10-07).

**Everything down to the heading "Results" was written on 2026-10-07 at 11:24 WAT, before any
run.** Nothing in it was observed in a run. What I read in the code is marked **[R]**; what I
checked by calling a function directly, with no test run, is marked **[C]**.

## What was wrong

The student's own upload route (`students/views.py`, `upload_answers`, students only) answered
201 with the teacher's `StudentSubmissionDetailSerializer`, built without the request, so none of
that serializer's student branches could run. **[R]**

My proposal of 2026-10-06 called this safe for the moment, because an upload is refused once the
paper is graded. That holds for the score and the feedback. It does not hold for two things,
which I found on 2026-10-07 while reading for this row (correction appended to
`~/Documents/Projects/GAP-planning/H-141-staff-shaped-answers-proposal.md`):

1. **A scheduled grading run.** A teacher can schedule a grading run for one paper
   (`schedule_grade_async`), which writes the time and the task's name on the row. The paper is
   not graded, so a student with attempts left (three are allowed) may upload again, and the
   answer carried `scheduled_grading_at`, `grading_task_name` and `is_grading_scheduled: true`.
   **[R]**
2. **The state of grading.** After a grading run that failed, the row reads `FAILED` and stays
   open; on a claim older than the staleness window it reads `RUNNING` and no longer blocks an
   upload. The answer carried the state as stored. **[R]**

Both are things H-133 hides on the student's list. H-133's listed claims stay true; Verifier 1's
note N1 on H-133 names this gap. Not observed on a live or staging service; `main` not read.

## What changes

`students/serializers.py`:

- A new `StudentUploadAnswerSerializer`. The same thirty keys as the teacher's serializer, in the
  same order **[C]**. Fourteen are the student's own facts and keep their values: `id`,
  `assignment`, `student`, the three names, `email`, `submission_status`, `remaining_attempts`,
  `max_points`, `is_published`, `submission_date`, `raw_input`, `answers`. Sixteen are staff keys
  and are constants, declared with a small field class `SentAs(value)` that does not read the
  row's column: `score` null, `score_percentage` null, `was_regraded` false, `regraded_at` null,
  `grade_status` "NOT GRADED", `formatted_grade` null, `grading_state` "IDLE", `needs_review`
  false, `review_reasons` / `review_severity` / `review_tier` null, `second_opinion` null,
  `question_breakdown` [], `scheduled_grading_at` / `grading_task_name` null,
  `is_grading_scheduled` false.
- `max_points` is the assignment's total (H-133's rule for a paper before release); `raw_input`
  comes from `answer_document_for_student` (H-130's function).
- Every serializer of a submission declares `audience`: "staff", "student" or "both". The list
  serializer is "both" and names the methods that change its answer for a student.

`students/views.py`: `upload_answers` builds the new serializer, with the context. The API
description of the 201 answer names it.

Because the answer does not read the grade's columns, it no longer depends on the refusal of an
upload on a graded paper, nor on the request being passed.

## What a page will see differently (for the frontend note)

**One value: the upload answer's `score` is null until release (was 0.0 or "0.00").** The
teacher's serializer sent the column's default, a zero: the number 0.0 on a first upload and the
text "0.00" on a later one **[R]**. The student's other routes send null there before release.
Ruled by the Senior Manager on 2026-10-07; the Release Engineer carries the line into batch 12's
package under frontend changes. In fact the upload answer never carries a score at all, since an
upload on a released paper is refused.

And, only in the two cases this row closes: the three schedule keys are null / null / false and
`grading_state` is "IDLE", where they showed a teacher's schedule or FAILED / RUNNING.

Every other key and value of an ordinary upload's answer is unchanged **[R]**: for a paper nobody
has graded or scheduled, the constants are the values the row has.

## The guards

- New: `AutoGrader/tests_submission_audience_guard.py`. Rule 1: every serializer of
  `StudentSubmission` in `students/serializers.py` declares its audience. Rule 2: every action of
  the submissions view is named with who can call it (checked against the view's
  `get_permissions`) and which submission serializers its source builds. Rule 3: an action a
  student can call builds a "staff" serializer only in the `else` of a test for a student caller,
  and gives every serializer a `context=`. Rule 4: `get_serializer` gives a student no "staff"
  serializer on list and retrieve. Its own docstring lists what it does not show (other views;
  hand-built dictionaries; a serializer built in a helper; that the context holds the request).
  I checked its table against the code by calling its helper functions directly: on b4a3b70e
  every action matched but `upload_answers`; with the fix all match **[C]**.
- H-127's guard (`AutoGrader/tests_student_feedback_guard.py`): the new serializer lists five
  guarded columns as keys, so it is named in `SERIALIZERS`, and a new test holds that each of
  them, and the state and schedule keys, is a `SentAs` constant with the agreed value.
- H-130's census (`students/tests_answer_document_before_release.py`, d5's file): the new
  serializer carries `raw_input`, so it is named in `READERS_OF_THE_DOCUMENT` as a student's; the
  teacher's serializer's entry no longer says the upload route answers with it; one docstring is
  brought up to date. No assertion of that file is changed.

## What it does NOT claim

- Other views. The guard covers the submissions view only. Assignments and classrooms have their
  own rows (H-147 for the course answer).
- The queued upload and the queued edit: they answer with task ids, not a submission **[R]**.
- The edit route (`partial_update`): it answers with the list serializer, with the request; H-127
  and H-133 cover what that hides. Unchanged here.
- `remaining_attempts` still drops to 0 at grading (the founder's choice A, H-133). In the upload
  answer that can only be seen if the refusal is taken out, as one test does.
- The teacher's serializer keeps its old student branches. They are not removed here: the
  teacher's routes do not need them, but removing them is a change to a file many tests read, and
  not this row's.
- Nothing was observed on a live or staging service. The frontend was not read.

## Tests

`students/tests_student_upload_answer.py`, 15 tests, through the real route; only the file
reading and the AI call are replaced.

- A first upload: the thirty keys; the own facts; the document equals the student's page; every
  staff key the constant; the assignment's total.
- A later upload on an ungraded paper: after the teacher's real scheduling route; after the real
  function that marks a failed run; with a stale claim; the whole answer compared with a first
  upload's (only the upload's own facts may differ); every staff key the constant, `score` null.
- With the refusal taken out and the upload engine handing the route a paper graded by the real
  grade save (with a missing answer, a second opinion, a formatted grade, a regrade and a
  schedule on the row): every staff key the constant; the assignment's total, not the grader's;
  the document a submitted paper has; and the whole answer before and after grading differs in
  `remaining_attempts` only.
- The teacher's page of a graded paper: the same thirty keys, real values.

Committed first, tests only, at f32b316f (14 of these 15 and the audience guard). Added with the
fix: the later-upload constants test (the Senior Manager's condition for `score`), and the test
in H-127's guard.

## Written before the runs

Gate script: `~/Documents/Projects/GAP-ed-scripts/run_h141_gate.sh` (sha256 starts
6567ca5f2dae458b when this was written), step 1.

**0. Reproduce-first** (the new module and the two guards, on the production files as at the
base): Ran 48, FAILED, **18 red** (failures or errors), and exactly these:

- `students.tests_student_upload_answer` (10 of 15): `test_every_staff_field_is_the_constant`;
  `test_a_scheduled_grading_run_is_not_shown`; `test_a_failed_grading_run_is_not_shown`;
  `test_a_grading_claim_too_old_to_block_the_upload_is_not_shown`;
  `test_it_answers_as_a_first_upload_does_but_for_the_uploads_own_facts`;
  `test_every_staff_field_is_the_constant_on_a_later_upload_too`; and the four of
  `OnlyTheAnswerProtectsTest`. Green there: the keys, the own facts, the document and the page,
  the assignment's total on a first upload, the teacher's page.
- `AutoGrader.tests_submission_audience_guard` (6 of 17): rule 1 "says who it is for" and "for
  whom this guard was told"; rule 2 "builds the serializers named and no other"; both of rule 3;
  rule 4. Green there: the six scanner self-tests, the census, rule 2's other three, and rule 1's
  "for both names where a student is answered" (which checks nothing until a serializer says
  "both").
- `AutoGrader.tests_student_feedback_guard` (2 of 16): the new
  `test_rule_2_the_upload_answer_sends_every_guarded_key_as_a_constant` (it cannot import
  `SentAs`), and `test_the_scan_covered_the_repository` (a serializer named in its table does not
  exist yet).

**1a.** `makemigrations --check`: no changes.

**1. Modules and guards at the tip**: OK. No count written: the related modules' sizes are not
known to me by reading.

**2. Mutants**: 32, each KILLED with at least the tests `mutate.py` names for it
(`python docs/evidence/h141-student-upload-answer/mutate.py --check` passes: anchors unique, all
parse). In groups: the route back to the teacher's serializer, and the route without a context;
each of the sixteen staff keys given back the row's value or a wrong constant, one at a time;
the maximum and the document read from the row; a key dropped; four on the audience
declarations; two on the view's permissions; a new unclassified action; PUT routed; the
teacher's maximum.

**3. Regression** (students, assignments, serial; own grant): OK.

Rule 19, as it should stand after these runs: of the 15 route tests, 10 red in step 0; the other
five each under a mutant (keys and own facts: K1; the document and the page: O3; the first
upload's total: O4; the teacher's page: T1). Of the audience guard's 17: six red in step 0; rule
1's "both" test under A3 and A4, rule 2's "named" under N1, "PUT" under H1, "who can call" under
P1 and P2. **Never seen red, and not claimed as evidence:** the six scanner self-tests and the
census test of the audience guard. H-127's new guard test: red in step 0 and under nine mutants.

## Not done

Nothing run. No frontend read. `main` not read for the same route.

## Results

(none yet)
