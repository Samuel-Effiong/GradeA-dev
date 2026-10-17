# H-240: hardest and easiest questions on the teacher's assignment analytics

Endpoint: `GET teacher-admin/dashboard/assignments/<assignment_id>`
(`dashboard/views.py:TeacherAdminDashboardView.assignments`). User-directed,
built by the Senior Manager at the user's request ("I want you to be the one
that implement it"). Branch `task/h240-question-difficulty`, code at
`05eb4753` + follow-up `ccced3c3`, on beta `980e8b1f`.

## The problem

The "Hardest Questions" / "Easiest Questions" cards showed "No data
available" for every assignment. The endpoint returned nothing for them: the
view had a `# Not implemented` note where they belonged, and the response
serializer had no such fields. A helper (`DashboardService.analyze_question_difficulty`)
existed and was unit-tested but had never been called by any code.

Before wiring it in, its behaviour on real grading data was checked, and
six gaps were found that would make the cards misleading:

1. An answer the AI could not read or find (`answer_status` ILLEGIBLE /
   NOT_FOUND_IN_DOCUMENT) is scored 0. Counted, a scan failure looks like
   the class found the question hard.
2. A teacher's override (`update-grade`) rewrites only the total; the
   per-question marks stay the AI's, so they no longer describe the grade.
3. No minimum sample: with two papers the "hardest" question is noise.
4. With three or fewer questions, or ties, one question could be both
   hardest and easiest; with all questions equal it still named a hardest.
5. Only a number and a fraction came back: no question text, and no way to
   tell "got it wrong" from "left it blank".
6. Question numbers were matched by `str()`, not the grader's own
   `_question_number_key`, and ties sorted by number as text ("10" < "2").

The discrimination check (do strong students also miss it?) was explicitly
deferred by the user.

## What changed

- **`dashboard/question_difficulty.py` (new).** `question_difficulty(assignment, submissions)`.
  Keeps the standard measure (each question's average share of its own
  marks, clamped to [0, 1]), and adds:
  - unreadable answers left out of the average, counted as `not_read`;
  - a submission left out if its per-question marks do not add up to its
    recorded score (±0.01). The grader's total is always exactly the sum
    of its per-question marks (`AIProcessor._finalize_grading_result`), so
    a mismatch can only mean the grade was changed afterwards. This catches
    an override however it was made, and never drops an untouched AI grade;
  - blank answers kept in the average (skipping is part of difficulty) and
    counted as `not_attempted` (`answer_status == BLANK`, or
    `level_achieved == "not_attempted"` on results from before
    `answer_status` existed);
  - only questions on the assignment now; a question repeated within one
    submission counted once; question numbers matched with
    `AIProcessor._question_number_key`;
  - a question ranked only once 5 students are counted on it; nothing named
    if fewer than two questions qualify (`not_enough_data`) or all averages
    are equal (`no_difference`);
  - at most 2 per list and at most half the ranked questions; "hardest"
    only below the best average, and no question tied with a named-hardest
    one can be named easiest; ties follow the paper's order;
  - each entry: `question_number`, `question_text` (the assignment's own
    text, HTML stripped and entities decoded), `question_type`, `points`,
    `average_percentage`, `students_counted`, `not_attempted`, `not_read`.
- **`dashboard/views.py`.** The view passes the same graded submissions it
  already uses for `average_grade` (non-withdrawn enrolment and graded, per
  H-238), read once into a list, and returns `hardest_questions`,
  `easiest_questions` and `question_difficulty` (`status`,
  `students_analysed`, `excluded_manual_grade_change`, `minimum_students`).
  Cache key versioned `:qd1`, so a response cached before deploy (without
  the new fields) is not served afterwards.
- **`dashboard/serializers.py`.** `QuestionDifficultySerializer`,
  `QuestionDifficultySummarySerializer`, three new fields on
  `TeacherAssignmentAnalyticsSerializer`.
- **Removed:** the never-called `DashboardService` class (its only method
  was the old helper) and `dashboard/tests_question_difficulty.py`.
- **Guards updated:** `AutoGrader/tests_student_feedback_guard.py` names the
  new raw `feedback` reader (`dashboard/question_difficulty.py:_evaluations`,
  teacher dashboard, returns numbers and the assignment's own text, no
  feedback) in place of the removed one;
  `AutoGrader/tests_cache_invalidation_coverage.py` registers the `:qd1` key.

## Gate 1: tests and regression

- `dashboard/tests_h240_question_difficulty.py`: **37 tests, OK**.
  - **Rules layer (30 tests):** each rule pinned on hand-built submissions.
  - **Endpoint layer (7 tests):** feedback produced the way production
    produces it, via the grader's own `_finalize_grading_result` and
    `_stamp_answer_provenance`, saved with the persist step's fields. One
    test makes a real teacher override through the `update-grade` route.
    Covered: served lists and text, withdrawn students excluded, ungraded
    excluded, a lost answer reported not counted, a blank answer counted,
    the override taking the paper out, and the fields always present.
- **Full regression on `05eb4753`** (`dashboard students classrooms AutoGrader assignments`):
  **3258 tests, OK (skipped=16)**, 742s, no failures.
- **On `ccced3c3`** (the follow-up touches only `dashboard/question_difficulty.py`
  and its test file): `dashboard AutoGrader` **1104 tests, OK (skipped=2)**.
  This includes the feedback guard and the cache-coverage test.
- `pre-commit` on every changed file: black, mypy, flake8, isort, bandit,
  detect-secrets, PII-logging guard all passed.
- `manage.py spectacular`: Warnings 68 (53 unique) / Errors 33 (8 unique).
  These are the same counts recorded as the pre-existing baseline in H-237's
  evidence, and no message refers to the new code.

## Gate 2: mutation

Driver applies one textual substitution (anchor verified unique), runs
`dashboard.tests_h240_question_difficulty`, and restores the file from
`git show <commit>:<path>`. It then checks the file's sha256 against the
committed blob before the next mutant. The chain ran in a separate detached
worktree, so mutants could not reach the regression running in the task
worktree, on its own test database (`test_h240_qd_mutants`).

**Run 1, on `05eb4753`: 18/20 killed.** The two survivors:

- **M18 survived:** `students_analysed` also counted a paper that
  contributed no mark. This was a missing test, so
  `test_a_paper_with_nothing_readable_is_not_counted_as_analysed` was
  added.
- **M20 survived:** an equivalent mutant. "Easiest must beat the lowest
  average" can never decide anything, because the lowest average is always
  among the hardest, and the tied-average rule already excludes it. The dead
  condition was removed in `ccced3c3`, and
  `test_a_question_tied_for_worst_is_not_easiest` pins the behaviour it
  stood for.

**Run 2, on `ccced3c3`: every live mutant killed.** M20's code no longer
exists. M7's anchor was reflowed by black, so it was re-run as M7b on the
new line.

| Mutant | Rule broken | Killed by |
|---|---|---|
| M1 | unreadable answers counted | 5 tests incl. `test_an_answer_the_ai_could_not_find_is_not_counted_as_hard`, endpoint `test_a_lost_answer_is_reported_not_counted` |
| M2 | hand-changed grades kept | `test_a_submission_whose_marks_no_longer_match_its_score_is_left_out`, endpoint `test_a_teachers_override_takes_that_paper_out` |
| M3 | no rounding tolerance | `test_a_rounding_difference_is_not_a_grade_change` |
| M4 | no minimum students | 4 tests incl. `test_below_the_minimum_nothing_is_ranked` |
| M5 | all-equal still ranked | `test_all_questions_equal_names_none` |
| M6 | list not capped at half the questions | `test_three_questions_leave_the_middle_one_out`, +1 |
| M7b | tied-with-hardest may be easiest | `test_a_question_tied_with_a_hardest_one_is_not_easiest`, `test_a_question_tied_for_worst_is_not_easiest` |
| M8 | tied-with-best may be hardest | `test_a_question_tied_with_the_best_is_not_hardest` |
| M9 | ties by number as text | `test_ties_follow_the_paper_not_the_number_as_text` |
| M10 | question numbers not normalised | `test_string_and_integer_question_numbers_are_one_question` |
| M11 | repeated question counted twice | `test_a_question_repeated_in_one_submission_counts_once` |
| M12 | blanks not reported | endpoint `test_a_blank_answer_is_counted_and_reported` |
| M13 | legacy not-attempted level ignored | `test_a_result_from_before_answer_status_uses_the_level` |
| M14 | marks not clamped | `test_marks_are_clamped_to_the_question` |
| M15 | HTML entities left in text | `test_an_entry_carries_the_papers_own_question_as_plain_text` |
| M16 | raw marks, not share of the question's marks | 5 tests incl. `test_difficulty_is_the_share_of_each_questions_own_marks` |
| M17 | zero-mark questions not skipped | `test_a_zero_mark_question_is_skipped` |
| M18 | contributing-nothing paper counted as analysed | `test_a_paper_with_nothing_readable_is_not_counted_as_analysed` |
| M19 | view feeds every submission (withdrawn, ungraded) | endpoint `test_a_withdrawn_students_marks_do_not_count` |

Every restore matched the committed blob's sha256, and the worktree had no
changes after each run (only the untracked mutant settings file, deleted
afterwards). Note on M19: it is killed by the withdrawn test only. An
ungraded submission has no grading feedback, so the helper skips it on its
own, and the view's graded filter and the helper's skip are both needed for
it to stay out.

Housekeeping: no `test_*h240*` database left on Postgres after the runs; no
stray `manage.py test` processes; mutation worktree removed.

## Gates 3-10

This is a read-only reporting endpoint. There are no writes or billing, and
no new authorization surface: the existing `teacher_course_access_q` check
on the assignment is untouched. The new fields expose only numbers and the
assignment's own question text to the teacher who owns it. The raw
`feedback` read is registered with the H-127 guard as teacher-only. There
is no concurrency or migration (`makemigrations --check` checked at
landing). Gate 8 tier: logic-only (reporting feature on an existing
endpoint). LOCAL-REAL is sufficient.

## Known limits (stated, not hidden)

- The endpoint's 15-minute cache is invalidated by the teacher's user
  scope, as before this change. Hardest/easiest share the freshness of the
  numbers already on the page.
- A teacher who overrides many grades will see fewer papers analysed.
  `excluded_manual_grade_change` says how many, so an empty card can be
  explained.
- The discrimination check (hard question vs. flawed question) is deferred
  by the user.
