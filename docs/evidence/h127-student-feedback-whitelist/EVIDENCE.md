# H-127 and H-128: what a student is sent of a saved grading result

Author: ed (Security Engineer), 2026-10-06. Branch `task/h127-student-feedback-whitelist`, off
`task/beta-batch-9` cc9682b8. Beta line. H-127 MEDIUM, H-128 LOW (SM rulings 2026-10-06 12:26 and
12:29 WAT). Verifier: 1a.

**State of this file: written BEFORE ANY RUN (first at 12:5x, revised at 2026-10-06 13:10 WAT after Verifier 1's
pre-read and the SM's rulings of about 13:03 to 13:10).** Nothing below has been run. The sections
"Written before the runs" say what I expect; the results are added after 0b's grants, in a later
commit, and where a result differs from what is written here I say so and leave this text.
Base since 12:55: `task/beta-batch-9` 9fb6d4fe (0b's merge 8a33c4c5).

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

**A fourth channel, found by Verifier 1's pre-read (read by 1a and by me, not observed):** the
student's own submission page returned `formatted_grade` as stored once the grade was released.
That column is the formatter's output, and the formatter's prompt
(`ai_processor/GRADE_FORMATTER_2.txt`) asks for `final_recommendations.for_teacher` ("written in
third person for the teacher"), `follow_up_actions`, and says "for_teacher must surface ALL flags
... Do not suppress any flag." So what the whitelist hid in the feedback was restated there.

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
| 8a33c4c5 | 0b's base update onto `task/beta-batch-9` 9fb6d4fe |
| 22c3967e, 8a76e046 | Tests first: a student is sent a projection of `formatted_grade`; then the SM's rulings as tests |
| 93db0af5 | `student_safe_formatted_grade`; the student's serializer uses it; a dead method removed |
| a7216ae3 | The guard covers `formatted_grade`; a called method is not a read; its limits named |
| bbd2b53d | Evidence and runner before any run. **The first gate ran on this commit.** |
| 5ca8f909 | Tests first: a value nested under an allowed name must not reach a student; a spaced ordering case |
| 595e323e | Under an allowed name only plain values pass (`_copy_plain`), in both projections |

No model change, no migration, no setting. Production files: `assignments/serializers.py`,
`dashboard/views.py`, `students/serializers.py`, `students/views.py`, `students/services.py`,
`students/second_opinion_serializers.py` (docstring), `ai_processor/services.py`, and the new
`students/feedback_projection.py`.

Behaviour changes, each stated because someone may meet it:
- **The whitelist itself is unchanged**: the same keys as before, now in one place.
- **A saved value that is not a dictionary is shown as nothing.** The old function returned it as
  stored. A grading result is always a dictionary, so I expect no row to be affected; not checked
  against any database.
- **For a student, `formatted_grade`** is the stored text read back as a Python literal
  (`ast.literal_eval`: read, never run), reduced to the student's sections, and returned in the same
  Python text form the page has always received (SM ruling: no change of type in a privacy fix).
  Kept: `overall_performance_summary` (`score_statement`, `performance_narrative`,
  `grade_tier_context`), `strengths`, `areas_for_improvement`, `question_by_question_breakdown`
  (per question `question_number`, `question_text`, `max_score`, `score_awarded`, `narrative`,
  `feedback_for_student`, `strengths`, `weaknesses`), `final_recommendations.for_student`.
  Dropped: `for_teacher`, `follow_up_actions`, any other key. **Shown as nothing:** text that is not
  a Python literal (plain words; JSON holding `null` or `true`), a literal that is not a dictionary,
  text over 500,000 characters, an empty value, any failure to read.
  **`narrative` is kept per question by choice** (mine; the SM agreed and asked Verifier 1 to weigh
  it): the prompt defines it as a third-person description of what the answer covered, and it is the
  body of what the page shows per question, so dropping it would empty the page. The formatter
  writes it with the teacher-directed input still in front of it, so it is the most likely place
  for a review flag to be restated: the limit named below.
- **Under an allowed name only plain values pass, in both projections** (added after the first
  gate, from Verifier 1's read of bbd2b53d; SM ruling about 14:15): text, a number, true or false,
  nothing, or a list of those. A dictionary, or a list holding a dictionary or a list, leaves that
  name out. The values come from an AI's reply; a dictionary nested where a sentence was asked for
  would otherwise be copied with every key in it. A result of the expected shape is unchanged. A
  stored result that does hold such a value loses that entry for students; no stored row was read,
  so whether one exists is not known. **One stated exception:** `overall_performance_analysis` in
  the feedback is still copied whole. It is nested by design, students are shown it today, and its
  real keys are not known without reading real rows, which was not done (SM ruling). A test pins
  that this is a decision.
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

**What the formatted grade can still say, a limit this change does not remove.** The projection
drops sections by name. It cannot judge a sentence. A sentence written TO the student (in
`performance_narrative`, `feedback_for_student`, `narrative`) may itself restate a review flag,
because the formatter is still sent the flags, the rationale, the evidence quotes and the advice to
the teacher; and a row formatted before H-127 may restate the second opinion, because the formatter
was sent it then. What the formatter is sent, and its prompt, are a product question the SM is
logging for the founder; by the SM's ruling they are not changed here beyond the second-opinion
block.

**The rule "a student must not be able to tell that a grade exists before the teacher releases it"
(founder's representative, 2026-10-06 about 13:08) is NOT met by H-127. It is the next row.** By
reading, a student can still tell in these ways:
1. the upload or edit refusal says "This assignment has already been graded" once a paper is
   graded (`students/services.py`, `_check_submission_open`), released or not;
2. `remaining_attempts` drops to 0 at grading (`remaining_student_attempts`);
3. `grading_state` on the student's list, and `?grading_state=` and `?is_published=` as filters;
4. `scheduled_grading_at`, `grading_task_name` and `is_grading_scheduled` on the student's list;
5. the answer document (`raw_input`) is rebuilt at grading with "Graded At" and "Score" in its
   header and returned as stored (found by d5; it is d5's, in H-130, with the course final grade,
   which counts unreleased grades);
6. the dashboard's `submission_status`: clean on beta (graded-but-unreleased shows SUBMITTED), not
   on main;
7. no email is sent to the student at grading time by what I read (the "graded" email is sent from
   publish); in-app notifications, messages a student can poll, the PDF download and the dashboard
   overview counts were not read for this rule.
Points 1 and 2 need the founder's answer; 3 and 4 ride the new row.

**Left as it is, by the SM's ruling (12:3x):** the student list still returns `grading_state`, and
`?grading_state=` and `?is_published=` still work for a student. Whether a student may know that
an unreleased grade exists is with the founder's representative; if the answer is a reduced state
it is a follow-up row.

**Not changed here, closed by the founder's decision (H-129, 2026-10-06):** several prompts carry
students' names to the AI provider (the three formatter prompts among them). No de-identification;
the provider's "do not collect" rule on every call is accepted as sufficient.

**Logged by the SM as a LOW row, not fixed here:** `formatted_grade` (and the dashboard's `feedback`
field) hold a dictionary saved through `str()`, Python text, not JSON.

**From Verifier 1's pre-read, stated, not changed:** the student's answer upload answers with the
teacher's detail serializer and no request. It is safe only because an upload is refused once the
row is graded, under the row lock, and nothing clears `graded_at`; changing that response's shape
is a frontend change I did not make in a privacy fix.

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
- `formatted_grade` on the student's submission page: the same Python-form text as before, with
  `final_recommendations` holding `for_student` only and no other section than the five named
  above. **It is now `null` when the stored text cannot be read as a dictionary** (plain words, an
  empty string, JSON holding `null`); it used to be returned as stored. The frontend must show a
  missing formatted grade gracefully for a released grade.
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
function named with who it serves (nine today), `.formatted_grade` included; every serializer
whose `Meta.fields` names one of those columns or a review-queue field is named (five), and the two
a student receives are checked for the code that hides them; every review-queue filter and ordering is one a student is
refused. Its limits, also in its docstring: it does not show which caller reaches a teacher-shaped
serializer (the route tests do, for today's routes); it does not see a column read through the ORM
by name or through a name built at run time. From Verifier 1's pre-read, also: a review-queue
column read as an attribute into a hand-built dictionary; the list serializer built without the
request (its replacement then replaces nothing); a function merely NAMED like a projection; email
templates handed the whole submission object. And it does not cover `raw_input` at all (d5's
H-130).

Outside the test runner, as a plain Python call of the scanner's functions on this tree (not a
test run): 246 production files, no unnamed read, no unnamed field list, every named one present.

## Written before the runs

**Step 0, reproduce-first** (the new test modules and the scoping module, on the seven changed
production files as at the base 9fb6d4fe, with `students/feedback_projection.py` present and
unused). I expect exit non-zero and these 28 red, no others:
- `students.tests_student_feedback_routes`, 15: the two assignment-detail tests on a released
  grade, the dashboard test on a released grade, the two student list tests, the filter test; and
  9 of the 12 `StudentFormattedGradeTest` tests (all but the unreleased one, the one with no
  formatted grade yet, and the teacher's).
- `students.tests_formatter_input`, 3: the three site tests.
- `ai_processor.tests_second_opinion_error_code`, 6: all.
- `AutoGrader.tests_student_feedback_guard`, 4: rule 1, rule 2 (student detail, both columns),
  rule 2 (list), rule 3. Its eight scanner tests pass.
The scoping module and every other test in those four pass. The guard's rule 1 should name three
reads there: the two routes, and the old `_student_safe_feedback` call, which is a method of
another name.

**Step 1** (4 new modules, 11 related, 22 guard modules, at the tip): exit 0, OK. I have not
counted the tests.

**Mutants:** 32 (25, and seven for `formatted_grade`: the page back to the stored column, the
allow-list widened to `for_teacher`, unreadable text shown as stored, a non-dictionary literal shown
as stored, no size limit, unknown sections copied, an unreleased one shown), each with the tests it
must fail named in `mutate.py` before any run. I expect 32 KILLED. The route mutants A1 to A3 are each expected to be caught twice, by a route test and by
the guard's rule 1. One doubt written down now: E3 and `test_evidence_rejected` rest on the batch's
last error being the evidence refusal on its final try; if the final try relaxes the evidence
check, that test is red at the tip and I correct the test or the claim, not the count.

**Regression** (assignments, dashboard, students, ai_processor, serial): OK.

## Not done

Nothing run. No database, staging or live service contacted. The frontend not read. Whether any
stored row holds a non-dictionary feedback: not checked.

## Results

Everything above this heading was written before the runs and is left as written, except where a
line says "added after the first gate".

### The first gate, at bbd2b53d (0b's GRANT, 2026-10-06 14:11:06 WAT)

One run of `run_h127_gate.sh bbd2b53d 1 9fb6d4fe` (script sha256 82c0de0d96b34311), 14:11:25 to
14:40:19, script exit 0, serial, 6G scope, rules 12, 13, 16, 17 and 18. Not stopped, not repeated.
Vezi's browser suite ran beside it; nothing in this gate has a wall-clock limit.

| Part | Written before | Found | Log |
|---|---|---|---|
| 0, the new modules and the scoping module on the production files as at 9fb6d4fe | red, 28 named tests, no others | exit 1: Ran 57 tests in 8.716s, FAILED (failures=31, errors=2). **Exactly the 28 named tests**: 15 in the routes module, 3 in the formatter module, 6 in the error-code module, 4 in the guard. 33 result lines, because one of the 28, the filter test, has six sub-cases. The scoping module passed. | `prefix_base_production_failing_bbd2b53d.txt.gz` |
| makemigrations --check | no changes | no changes | `makemigrations_check_bbd2b53d.txt` |
| 1, 4 new modules, 11 related, 22 guard modules, at the tip | exit 0, OK | exit 0: **Ran 469 tests in 366.123s, OK** | `modules_and_guards_bbd2b53d.txt.gz` |
| 2, mutants | 32 KILLED | **32 of 32 KILLED**; SURVIVED 0, KILLED_NOT_AS_EXPECTED 0, BROKEN 0 | `mutation_log_bbd2b53d.txt`, `mutation_results_bbd2b53d.json`, `mutant_logs_bbd2b53d/` |

- **The doubt written beforehand is settled:** `test_evidence_rejected` passed at the tip and mutant
  E3 failed it with the two others, as named. The batch's last error is the evidence refusal.
- Every mutant's inner run shows its own "Ran 70 tests" line, exit 1, every named test among the
  failures, and 0 `__pycache__` directories left. The route mutants A1, A2, A3 and F1 were each
  caught by a route test AND by the guard's rule 1, as written.
- The files were restored after step 0 and after every mutant ("source restored", "source clean
  after mutants"); the mutants' database was dropped.
- Load average: 5.28 4.15 4.08 at the start; step 1 started at 6.98 and ended at 18.94 12.24 7.75;
  6.35 12.24 14.01 at the end of the battery.
- The logs are as the runs wrote them, gzipped because they are large (step 0's holds the
  600,000-character test value) or have trailing whitespace. Before gzip: step 1's log 208,333
  bytes, 2,338 lines, sha256 starts ccbeeb5ae0c7f414, "Ran" at line 2335, "OK" at 2337, then the
  runner's "Destroying test database" line; step 0's log 688,775 bytes, 765 lines, sha256 starts
  9c02626539600954, "Ran" at line 760, "FAILED" at 762. Its last line, "systemd-run failed with
  exit status 1.", is the scope reporting the test command's own non-zero exit.

### After the first gate

Two commits changed code after that run (5ca8f909 tests, 595e323e the nested-value rule), both in
or for `students/feedback_projection.py` and the routes test module. So bbd2b53d's results stand
for everything those two commits do not touch, and the following are owed on the final tip and are
NOT yet run as I write this: the modules and guards once more (the gate's 1b mode), and the nine
mutants on `students/feedback_projection.py` (A4, D4, F2 to F6, and two new ones, G1 and G2, for
the nested-value rule), under the rule 17 addendum. The other 25 mutants are on files the two
commits do not change; the routes test module gained one test class and one sub-case and lost
nothing, so their kills are not stale. Then the four-app regression.

Disclosed: those two commits ran their pre-commit hooks at about 14:41 to 14:43, inside a quiet
window 0b had just declared for batch 9's full run; the notice reached me after them. Told to 0b
at once.

**Written before the second run:** the 1b step at the final tip: exit 0, OK, 473 tests (the 469
and the four new ones). The nine mutants: 9 KILLED, each with every test named for it in
`mutate.py`; G1 and G2 by the two nested-value route tests.
