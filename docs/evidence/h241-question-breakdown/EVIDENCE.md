# H-241: full question breakdown, card limit 3, and who struggled with each question

Endpoint: `GET teacher-admin/dashboard/assignments/<assignment_id>`
(`dashboard/views.py:TeacherAdminDashboardView.assignments`). User-directed
follow-up to H-240 ("implement the question breakdown and increase the
default limit to 3. Also includes the student names"). Branch
`task/h241-question-breakdown`, code at `3071427d` + test follow-up
`6a277277`, on beta `e9ede4c0`.

## What changed

- **`question_breakdown` (new field).** Every question on the paper, with
  the same entry shape as the cards. Order: questions with at least 5
  students counted, hardest first; then questions with fewer students
  (same order, `enough_data: false`); then questions nobody has a mark on
  (`average_percentage: null`), in paper order. It is returned whatever
  the ranking status (`not_enough_data` / `no_difference` included).
- **Card limit.** `hardest_questions` / `easiest_questions` now default to
  3 each (`LIST_LIMIT`, was 2). They can be changed with `?question_limit=`,
  read through the existing `_bounded_int_param` helper (positive integer,
  at most 100). A bad value returns a 400 that names the parameter. H-240's
  rules still apply: never more than half the ranked questions, and no
  question on both lists. The cards are the two ends of the breakdown.
- **Students on each entry.**
  - `struggling_students`: earned under half that question's marks
    (`STRUGGLING_BELOW = 0.5`, strictly below). Lowest score first, ties
    by name. Each has `student_id`, `full_name`, `score_awarded`,
    `max_points`, `percentage`, `left_blank`.
  - `unread_students`: the AI could not read or find the answer
    (ILLEGIBLE / NOT_FOUND_IN_DOCUMENT). These are papers to check, not
    students to reteach, so they are kept apart from the struggling list.
    Sorted by name.
  - A paper whose grade was changed by hand (marks no longer add up to the
    score) appears in neither list, the same as it is left out of the
    averages.
- **Entry fields added:** `enough_data`, `struggling_students`,
  `unread_students`. `average_percentage` is now nullable.
- **View.** Graded submissions are loaded with `select_related("student")`,
  so naming students adds no query per student. The cache key is now
  `:view__assignments:{question_limit}:qd2`, so each limit gets its own
  cached answer and nothing cached before deploy is served after it.
  `AutoGrader/tests_cache_invalidation_coverage.py` registers the new key.
- **`dashboard/question_difficulty.py`.** Per-question tallies are now a
  small `_Tally` dataclass, which mypy can check.

## Gate 1: tests and regression

- `dashboard/tests_h241_question_breakdown.py`: **18 tests, OK**.
  - **Rules layer (12 tests):** breakdown order, cards are the two ends,
    a question with too few students flagged and placed after the trusted
    ones, a question with no marks placed last (including after a question
    with only a few marks), the breakdown present when nothing is ranked,
    no internal field leaked, struggling threshold and order, blank
    answers flagged, unread students named separately, a hand-changed
    paper names nobody, and each question lists only its own students.
  - **Endpoint layer (6 tests):** feedback built the way production builds
    it. Covered: breakdown served hardest first; struggling and unread
    names and `student_id`s; default limit 3 and `?question_limit=1`; one
    cache entry per limit; a 400 for `abc` / `0` / `-2`; the same query
    count for 5 students and for 15 (measured with `execute_wrapper`).
- `dashboard/tests_h240_question_difficulty.py` updated for the new fields
  and limit: **39 tests, OK**.
- **Full regression on `3071427d`** (`dashboard students classrooms AutoGrader assignments`):
  **3279 tests, OK (skipped=16)**, 778s, no failures. `6a277277` adds one
  test and changes no other file.
- **On the final commit:** `dashboard AutoGrader` was run again at
  landing; the result is in the landing note below.
- `pre-commit` on every changed file passed: black, mypy, flake8, isort,
  bandit, detect-secrets, and the PII-logging guard.
  `makemigrations --check`: no changes.
- `manage.py spectacular`: Warnings 68 (53 unique) / Errors 33 (8 unique).
  That is the same baseline as H-237/H-240, and no message refers to the
  new code.

## Gate 2: mutation

The driver and method are the same as H-240: anchors are checked to be
unique, each file is restored from `git show <commit>:<path>` and its
sha256 checked against the committed version, and the chain runs in a
separate detached worktree on its own test database
(`test_h241_qb_mutants`). It runs both test files (57 tests).

**Run 1, on `3071427d`: 19/20 killed.** The survivor was N2: without the
"no marks" sort key, a question nobody had marked sorted as 0%. With
too-few-student questions also present, it could jump ahead of them.
This was a missing test, so
`test_an_unmarked_question_comes_after_a_thinly_marked_one` was added
(`6a277277`).

**Run 2, on `6a277277`: 20/20 killed.**

| Mutant | Rule broken | Killed by |
|---|---|---|
| N1 | too-few-student questions not placed after trusted ones | `test_a_question_short_of_the_minimum_is_shown_marked_after_trusted_ones` |
| N2 | unmarked questions not placed last | `test_an_unmarked_question_comes_after_a_thinly_marked_one` |
| N3 | exactly half counted as struggling | `test_students_under_half_the_marks_are_named_lowest_first` |
| N4 | struggling threshold lowered | same, + endpoint `test_struggling_and_unread_students_are_named` |
| N5 | struggling list unsorted | same two |
| N6 | struggling ties not broken by name | `test_students_under_half_the_marks_are_named_lowest_first` |
| N7 | unread list unsorted | `test_an_unread_answer_names_the_student_for_checking_not_reteaching` |
| N8 | blanks not flagged | `test_a_blank_answer_is_named_and_flagged`, + endpoint |
| N9 | unread students not named | 6 tests |
| N10 | unread answer counted as 0 | 8 tests |
| N11 | caller's limit ignored | `test_the_limit_is_the_callers`, + 2 endpoint |
| N12 | default limit back to 2 | `test_at_most_three_of_each_by_default`, + 2 |
| N13 | `enough_data` with one student | 6 tests |
| N14 | hand-changed papers kept (names and marks) | `test_a_hand_changed_paper_names_nobody`, + 2 |
| N15 | view does not pass the limit | `test_the_default_card_limit_is_three`, `test_each_limit_has_its_own_cached_answer` |
| N16 | cache key ignores the limit | `test_each_limit_has_its_own_cached_answer` |
| N17 | students not loaded with submissions (query per student) | `test_naming_students_costs_no_query_per_student` |
| N18 | limit read without validation | `test_a_bad_limit_is_a_400_naming_it` |
| N19 | breakdown not returned | 4 tests |
| N20 | wrong percentage on a struggling entry | `test_students_under_half_the_marks_are_named_lowest_first` |

Every restore matched the committed file's sha256. After both runs the
worktree had no changes except the untracked mutant settings file, which
was deleted afterwards.

## Gates 3-10

This is a read-only reporting endpoint. There are no writes, no billing,
no concurrency and no migration. Authorization is unchanged: the existing
`teacher_course_access_q` check on the assignment still applies. The new
data is the names and per-question marks of students enrolled (not
withdrawn) in the teacher's own course, which the same teacher already
sees on the submission pages. No feedback text is exposed, and the raw
`feedback` read is still the H-127-registered teacher-only reader.
Gate 8 tier: logic-only. LOCAL-REAL is sufficient.

## Known limits (stated, not hidden)

- On a large class the struggling list on a hard question can hold most
  of the class, because it is not capped. That is deliberate: it is a list
  for the teacher to act on, and the response is cached.
- "Struggled" is a fixed 50% threshold, not tuned per question type.
- Freshness is the same as H-240: a 15-minute cache, invalidated per
  teacher.
