# H-127 and H-128: what a student is sent of a saved grading result

Author: ed (Security Engineer), 2026-10-06. Branch `task/h127-student-feedback-whitelist`, off
`task/beta-batch-9` cc9682b8. Beta line. H-127 MEDIUM, H-128 LOW (SM rulings 2026-10-06 12:26 and
12:29 WAT). Verifier: 1a.

**State of this file: written at 2026-10-06 12:53 WAT, BEFORE ANY RUN.** Nothing below has been run. The
sections "Written before the runs" say what I expect; the results are added after 0b's grants, in
a later commit, and where a result differs from what is written here I say so and leave this text.

## The defect (H-127)

`StudentSubmission.feedback` holds the grading result as the grader produced it. Much of it is
for the teacher: the second grader's marks and reasons (`second_opinion`), which model graded,
the review flags, the rationale for the level chosen, the evidence quotes, the advice to the
teacher. The student's own submission page has shown a whitelisted projection of it since the
second-opinion work (`students/tests_student_feedback_scoping.py`). Three other student routes
did not. All read by me in the code at cc9682b8; none observed in a live response.

| Route a student can call | What it returned | When |
|---|---|---|
| Assignment detail, `performance_summary` (`assignments/serializers.py`) | the saved feedback, whole (`feedback`, else `ai_feedback`) | once the grade is released |
| Student dashboard, assignment list, `feedback` (`dashboard/views.py`) | the saved feedback, whole | once the grade is released |
| The student's list of their submissions (`students/serializers.py`, list serializer) | `needs_review`, `review_reasons` (both AI graders' marks for each disputed question), `review_severity`, `review_tier`, `grading_confidence`, `graded_at` | **released or not** |

On the same list a student could also filter by `needs_review` or `review_tier` and order by
`review_severity`, and learn from which rows came back that the graders disagreed.

Own submissions only; no other student's data. The feedback formatter (an AI call whose wording
the student reads) was sent the whole saved result at three places, second opinion included.

## The defect (H-128)

When a second opinion fails, `result["second_opinion"]["error"]` was `str(e)`, saved with the
result. That text could be: the teacher's credit balance ("Task requires ~N credits, but you only
have M credits"); the reason the teacher's plan was refused; or the AI provider's whole error body.
The last I read in the installed provider library (openai 1.107.1,
`_base_client._make_status_error_from_response`): the message is
`"Error code: <status> - <the response body>"`. With H-127's routes a student could read it.

## What changed

| Commit | What |
|---|---|
| 90911a82 | Tests first: assignment detail and student dashboard must return the student projection |
| 9792251b | The whitelist moves to `students/feedback_projection.py` (`student_safe_feedback`); both routes use it |
| 224a24e7 | Tests first: the student's list must not carry the review-queue fields |
| 91960a9f | The list serializer replaces them for a student caller; the raw-text edit passes the request |
| cf84de72 | Tests first: a student cannot filter or order by the review queue |
| ac8cf2c0 | The three are answered 403 for a student |
| 25941016 | Tests first: the formatter must not be sent the second-opinion block (with the function, unused) |
| 3a3901d4 | The three formatter prompts use `grading_result_for_formatter` |
| 9ea19e6e | H-128 tests first: a failed second opinion saves a code |
| 53182e4d | H-128: six codes; the text goes to the log |
| 14fbae09 | Repository-wide guard `AutoGrader/tests_student_feedback_guard.py` |

No model change, no migration, no setting. Production files: `assignments/serializers.py`,
`dashboard/views.py`, `students/serializers.py`, `students/views.py`, `students/services.py`,
`students/second_opinion_serializers.py` (docstring), `ai_processor/services.py`, and the new
`students/feedback_projection.py`.

Behaviour changes, each stated because someone may meet it:
- **The whitelist itself is unchanged**: the same keys as before, now in one place.
- **A saved value that is not a dictionary is shown as nothing.** The old function returned it as
  stored. A grading result is always a dictionary, so I expect no row to be affected; not checked
  against any database.
- **For a student, the submission list** sends `needs_review` false and `review_reasons`,
  `review_severity`, `review_tier`, `grading_confidence` null, always; `graded_at` only for a
  released grade. A teacher's list is unchanged.
- **For a student, `?needs_review=`, `?review_tier=` and ordering by `review_severity` answer
  403.** Other filters and orderings are unchanged.
- **`second_opinion.error`** is one of `out_of_credits`, `access_refused`, `provider_error`,
  `evidence_rejected`, `incomplete_response`, `other`. The out-of-credits warning in the log now
  carries the refusal's text (it used to be saved and not logged).
- **The grading batch's final failure is raised `from` its last error**, so the kind can be read.
  Its message is unchanged.

**At deployment, cached responses outlive the change for a few minutes.** The student dashboard's
assignment list is cached per student for 15 minutes (`dashboard/views.py`, `cache.set(cache_key,
data, 60 * 15)`), and the submission list per caller and query for `CACHE_TTL` (5 minutes by
default, `users/mixins.py`). A response built before the deploy can be served until it expires,
unless the deploy clears the cache. Read in the code; lifetimes on the live service not checked.

**Old rows are not rewritten.** A row saved before H-128 keeps whatever error text it holds, and
every row keeps its second-opinion block. For students, the projection is what keeps both away.
A teacher still sees the old text on old rows.

**Left as it is, by the SM's ruling (12:3x):** the student list still returns `grading_state`, and
`?grading_state=` and `?is_published=` still work for a student. Whether a student may know that
an unreleased grade exists is with the founder's representative; if the answer is a reduced state
it is a follow-up row.

**Not fixed here, reported to the SM for its own row:** the three formatter prompts carry the
student's full name to the AI provider.

## For the frontend

Students keep exactly the shape their own submission page already receives.
- `performance_summary` (assignment detail) and the dashboard list's `feedback`:
  - `grading_summary`: `total_score`, `max_total_points`, `percentage` only;
  - `question_evaluations`: per question only `question_number`, `question_text`,
    `question_type`, `max_points`, `student_answer`, `score_awarded`, `level_achieved`,
    `strengths`, `weaknesses`, `improvement_suggestions`, `feedback_for_student`;
  - `overall_performance_analysis`, whole; `recommendations.for_student` only.
  - Gone for students: `second_opinion`, `grading_model`, `grading_confidence`, `graded_by`,
    `flag_for_review`, `evaluation_rationale`, `evidence_quotes`, `model_answer`,
    `snapped_from`, the recommendations for the teacher, and any other top-level key.
- The dashboard's `feedback` is declared as text (`dashboard/serializers.py`), so it has always
  carried the Python text form of the dictionary, not JSON. H-127 changes its content, not its type.
- The student's submission list: the five review fields are false or null; `graded_at` is null
  until release.
- A student page that sent `?needs_review=`, `?review_tier=` or `?ordering=review_severity`
  would now get 403.
- **Inferred, not checked (I cannot read the frontend):** no student page uses a removed key or
  sends one of the refused queries.
- A teacher's `second_opinion.error` is now a code. A screen that printed the old text prints the
  code.

## What production shows students until the next promotion

The founder's representative decided on 2026-10-06 that main gets no hot fix; the exposure waits
for the next promotion of beta to main, so H-127 must be in beta before it. Read by me in
origin/main 9c21bee8, not observed on the live service. Own submissions only:
1. the student dashboard's assignment list shows the score, the percentage and the whole saved
   grading result as soon as a paper is graded, **released or not**;
2. the assignment detail shows the whole saved result once released;
3. the submission list shows, released or not, that a grade exists and when, how confident the
   grader was, whether the two graders disagreed, and both graders' marks for each disputed
   question.
"Whole saved result" includes the second grader's marks and reasons, the model names, review flags,
notes for the teacher and a failed second opinion's error text. Beta before H-127 differs from main
in point 1 only: beta already hides the dashboard's score and feedback until release.

## The guard and its limits

`AutoGrader/tests_student_feedback_guard.py`, three rules over every production `.py` file:
every read of `.feedback` / `.ai_feedback` is inside one of the two projection functions or in a
function named with who it serves (eight today); every serializer whose `Meta.fields` names one
of those columns or a review-queue field is named (four), and the two a student receives are
checked for the code that hides them; every review-queue filter and ordering is one a student is
refused. Its limits, also in its docstring: it does not show which caller reaches a teacher-shaped
serializer (the route tests do, for today's routes); it does not see a column read through the ORM
by name or through a name built at run time.

Outside the test runner, as a plain Python call of the scanner's functions on this tree (not a
test run): 245 production files, no unnamed read, no unnamed field list, every named one present.

## Written before the runs

**Step 0, reproduce-first** (the new test modules and the scoping module, on the seven changed
production files as at cc9682b8, with `students/feedback_projection.py` present and unused). I
expect exit non-zero and these 19 red, no others:
- `students.tests_student_feedback_routes`, 6: the two assignment-detail tests on a released
  grade, the dashboard test on a released grade, the two student list tests, the filter test.
- `students.tests_formatter_input`, 3: the three site tests.
- `ai_processor.tests_second_opinion_error_code`, 6: all.
- `AutoGrader.tests_student_feedback_guard`, 4: rule 1, rule 2 (student detail), rule 2 (list),
  rule 3.
The scoping module and every other test in those four pass. The guard's rule 1 should name three
reads there: the two routes, and the old `_student_safe_feedback` call, which is a method of
another name.

**Step 1** (4 new modules, 11 related, 22 guard modules, at the tip): exit 0, OK. I have not
counted the tests.

**Mutants:** 25, each with the tests it must fail named in `mutate.py` before any run. I expect
25 KILLED. The route mutants A1 to A3 are each expected to be caught twice, by a route test and by
the guard's rule 1. One doubt written down now: E3 and `test_evidence_rejected` rest on the batch's
last error being the evidence refusal on its final try; if the final try relaxes the evidence
check, that test is red at the tip and I correct the test or the claim, not the count.

**Regression** (assignments, dashboard, students, ai_processor, serial): OK.

## Not done

Nothing run. No database, staging or live service contacted. The frontend not read. Whether any
stored row holds a non-dictionary feedback: not checked.
