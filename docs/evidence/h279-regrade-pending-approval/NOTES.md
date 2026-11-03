# H-279: an AI re-grade of a released grade waits for the teacher

- **Branch:** task/h279-regrade-pending-approval, from origin/beta e11e56536.
- **Design:** DESIGN.md at d8e5adcbd, approved by SM (design A).
- **Test DB:** test_AutoGrader_h279 (settings_worktree.py, untracked).
- **Venv:** outside the worktree.
- **Runs:** near runs only, no full suite. No trailer. American spelling. No
  new wording.

## Rulings

- **Owner, 2026-11-02 (ticket):**
  - Only the re-graded submission is affected; the Assignment status is not
    touched.
  - While a re-grade is held, the student and every analytic keep the
    released score and feedback.
  - The teacher approves the held result, and the student is then told as for
    a grade update.
- **SM approval of the design (relayed):**
  - Storage is a JSON blob, with exactly two columns: `pending_result` and
    `pending_graded_at`.
  - Q2: held rows are found through the pending filter only. The review queue
    keeps its meaning, and `mark_reviewed` never touches the held result.
  - Q3: the AI student summary moves to approval and is not dispatched for a
    held result. A test asserts one dispatch per approval and none at the held
    save.
  - Q4: no. `teacher_feedback` and formatted-grade edits act on the released
    text only and never promote.
  - Q5: the names are approved: `pending_result`, `pending_graded_at`,
    `has_pending_result`, `held_for_approval`. A static fence test keeps every
    student serializer from exposing them.
  - Notification plan N1–N10: approved as written.
  - Guard: a held row that is somehow unpublished must not be released by the
    first-release flip with the held result left beside it. Chosen: the
    first-release flip promotes the held result as part of the release.
    There is one mutant for this.
- **Owner, 2026-11-03:**
  - Q1, RELEASE THE NEWEST: approval promotes whatever is held at lock time.
    There is no stamp, no refusal, no reason code and no wording. The route
    answers with the promoted result. The optional
    `expected_pending_graded_at` parameter is dropped.
  - Q6, NOT NEEDED (an owner ruling, not deferred): a held result has no
    discard, now or later. The only ways out are approval and a hand edit.

## Refinements since DESIGN.md (from writing the tests)

1. **When a result is held.** It is held only when the row holds a
   *released grade*: `is_published` **and** `graded_at` set, read under the
   row lock at the save.
   - A first grading of a row that is published but has no grade writes as
     today. Production cannot reach that state, since publish refuses an
     ungraded row, but `students/tests_submission_concurrency.py::
     test_columns_changed_during_the_run_survive_the_final_save` builds it.
     That test keeps passing unchanged.
2. **publish-all's count.** `updated_count` is **unchanged**: a held row is
   already a graded row and was already counted. DESIGN §3.3a(4) said
   "flipped + promoted", which would have changed today's number; that is
   withdrawn. Test N4 pins "Successfully published 3 submissions." for new,
   released and held rows.
3. **Where the teacher sees the held result.**
   - `pending_result` and `pending_graded_at` are on
     `StudentSubmissionDetailSerializer` only, the teacher's page.
   - They are not on the shared list serializer. The teacher finds held rows
     with `?has_pending_result=true`, which is added to
     `TEACHER_ONLY_FILTERS`, so a student gets 403.
   - `StudentUploadAnswerSerializer` mirrors the detail page's keys (H-141),
     so it gets both keys as `SentAs(None)`. An upload is refused once a paper
     is graded, so the constant is always true.
4. **The stamp of a held result.** It is `held_result_stamp(row)`:
   `pending_graded_at|regraded_at`. The formatted-grade task, under the row
   lock:
   - if the held stamp matches, it writes `pending_result["formatted_grade"]`;
   - else, if the released stamp matches, it writes `formatted_grade` (this
     includes a result approved before its task ran, because promotion sets
     `graded_at := pending_graded_at`);
   - else it writes nothing (H-145).
5. **The service.** `promote_pending(submission_id, *, actor, notify=True)`
   runs in one atomic block under `select_for_update`:
   - If nothing is held, it returns None.
   - Otherwise it copies the result into the released columns through each
     model field's `to_python`, clears both pending columns, and does one
     instance save, which gives the GRADE_CHANGE, the cache invalidation and
     the final grade.
   - Through `on_commit`, it sends the "Updated grade" email when `notify` is
     set, and the AI student summary.
   - The callers are:
     - single publish, and publish-all for held rows (`notify=True`);
     - the first-release flip of a held row, which keeps its own first-release
       email (`notify=False`);
     - `update_grade`, which keeps its own email (`notify=False`) and saves
       the promoted values and the edit as one GRADE_CHANGE.
6. **After a held save, the returned instance.** It carries the released
   columns again, re-read under the lock, plus the pending ones. The sync
   grade route therefore answers with the released score and the held
   result, and `emit_grading_completed` records empty before/after plus
   `held_for_approval: true`. The label and model metadata are read from the
   held result.

## Red tests

| Module | Kind | Tests |
|---|---|---|
| `students/tests_h279_regrade_pending_approval.py` | TestCase, on `SupersededBase` (a released paper graded 7, worded, released) | 55 |
| `students/tests_h279_regrade_pending_concurrency.py` | TransactionTestCase, real threads | 4 |

The approval module's tests fall into these groups:
- held save
- not held
- nothing moves (student detail, answer document on both routes, final grade,
  three student dashboards and the teacher course dashboard)
- audit
- the teacher's view and filter
- the student fence (dynamic and static)
- promotion (single, publish-all, newest, route answer, cache, final grade,
  GRADE_CHANGE, summary count, nothing-held no-op, the unpublished guard)
- hand edit
- what does not approve (mark_reviewed, teacher_feedback, review queue)
- wording (formatted-grade task stamps)
- N1, N2, N4, N5, N7 (×2), N8, N9, N10

The concurrency module covers N3, N6, and an approval racing a held save
(5 rounds).

The stale-approval refusal test is not written: Q1 was ruled RELEASE THE
NEWEST, and its test is `test_the_newest_held_result_is_the_one_released`.

## Existing tests whose contract H-279 changes (predicted before any run)

These pin "an AI re-grade of a released paper reaches the student at once",
which the owner's ruling reverses. Each will be amended to the held-then-
approved form in the implementation commit, and listed again there with the
diff. Nothing is deleted.

| # | Test | Why it changes |
|---|------|----------------|
| X1 | classrooms/tests_student_final_grade_released_only.py::ReleaseIsWhatMovesTheStudentsNumber::test_a_regrade_after_release_moves_the_number_at_once | the number now moves at approval |
| X2–X7 | students/tests_regrade_formatted_grade.py::ARegradeClearsTheOldWording: test_the_regrade_clears_the_stored_formatted_grade, test_between_the_save_and_the_task_the_student_reads_no_old_wording, test_the_regrades_own_task_fills_it, test_a_failed_task_leaves_it_empty, test_the_first_gradings_task_does_not_put_text_back, test_it_is_cleared_by_the_same_update_as_the_score | on a released paper the released text now stays until approval (H-146 still holds for unreleased re-grades, and the promoted text replaces the old one at approval) |
| X8–X9 | students/tests_formatting_task_superseded.py::ARegradeSupersedesTheFirstGradingsTask: both tests | the released result's task still words the released result while a re-grade is held |
| X10 | students/tests_formatting_task_superseded.py::TheStamp::test_it_changes_with_a_regrade_and_with_a_manual_grade | a held re-grade does not change the released stamp |

Any other existing test that fails on the implementation is reported as
**unpredicted**, with its cause, and it is not amended without SM.

## Expectations (committed before any run)

**E1. Red run.** Both new modules, at this commit (code unchanged from e11e56536):

    manage.py test students.tests_h279_regrade_pending_approval \
      students.tests_h279_regrade_pending_concurrency \
      --settings=settings_worktree -v 2

- `Ran 59 tests`, `FAILED`.
- Exactly these 13 pass at red. They are controls and guards, true today
  and required to stay true:
  1. test_the_run_itself_is_finished
  2. test_a_grading_that_is_not_held_does_not_say_so
  3. test_the_held_save_writes_no_grade_change
  4. test_the_students_own_page_and_list (the names do not exist yet)
  5. test_no_serializer_but_the_teachers_page_reads_them
  6. test_the_names_are_read_nowhere_a_student_route_reads
  7. test_the_student_reads_it_at_once_though_cached (today the re-grade
     itself already shows 3)
  8. test_the_final_grade_follows (likewise)
  9. test_a_publish_with_nothing_held_changes_nothing
  10. test_n7_an_opted_out_student_gets_none_and_the_result_is_still_released
  11. test_n7_a_placeholder_address_gets_none
  12. test_n8_a_hand_edit_of_a_held_row_sends_one (today's update_grade
      email)
  13. test_n9_the_held_save_sends_none
- The other 46 FAIL or ERROR. Expected causes:
  - `AttributeError`/`FieldError`/`KeyError` on `pending_result`,
    `pending_graded_at` or `held_result_stamp`;
  - assertion failures where today's re-grade reaches the released columns
    (released columns, student reads, answer document, final grade,
    dashboards, the review flag);
  - no email or GRADE_CHANGE where approval should give one;
  - a summary dispatched at the held save;
  - a student filter answered 200 instead of 403.
- A `NoReverseMatch` or a setUp error is a test defect, not an expected red.
  It will be recorded and fixed in a new red commit, never in place.

**E2. Baseline near run.** The 18 existing modules that grade and release or
publish (listed below), at this commit:

    Ran N tests, OK

N is recorded from the run. They include all ten X tests above, which pass
today.

    audit.tests_history_guard classrooms.tests_final_grade_zero_score
    classrooms.tests_student_final_grade_released_only
    dashboard.tests_cache_matrix_status_summary
    students.tests_ai_reply_corrected_review
    students.tests_answer_document_before_release
    students.tests_answer_document_score_printing
    students.tests_answers_unreadable students.tests_formatting_task_superseded
    students.tests_grading_idempotency students.tests_grading_label_written
    students.tests_h242_submission_flags
    students.tests_manual_grade_formatted_grade
    students.tests_manual_grade_unreadable_answers
    students.tests_no_grade_tell_before_release
    students.tests_regrade_formatted_grade students.tests_student_upload_answer
    students.tests_submission_concurrency

**Later, before green:**
- The implementation's own expectations are committed first: the green run
  of the new modules, the near run with X1–X10 amended, and the mutant table.
- The mutants planned, one per guard:
  - the lock-time hold decision
  - the "released grade" condition (graded_at)
  - the held save's columns
  - single promote
  - publish-all promote
  - the unpublished-held guard
  - hand edit promotes first
  - replace
  - held stamp: write to pending
  - promoted-meanwhile stamp
  - summary moved to approval
  - email on promotion only, once
  - the student serializer fence
  - the student filter refusal
  - the `held_for_approval` key
  - the promotion as an instance save (cache and final grade)

## Log

- d8e5adcbd: DESIGN.md (step 1).
- this commit: NOTES, red tests, expectations E1 and E2 (SKIP=mypy: the red
  tests name `held_result_stamp`, which does not exist yet; red commit only).
- E1 red run at 452a0f04 (logs/red_run_452a0f04.txt): `Ran 59 tests`,
  `FAILED (failures=25, errors=25)`. Failures and errors are counted per
  subtest. 46 tests failed, and the 13 that passed are exactly the 13
  listed in E1. The causes are as expected: no attribute
  pending_result/pending_graded_at/held_result_stamp, KeyError
  'pending_result', and assertion failures. There was **one test defect**:
  N4PublishAllOverAMix.setUp (both of its tests) errored with
  `ValidationError: A student with the exact name 'Test Student' is
  already enrolled`. make_user gives every user the same name. It is fixed
  in the next red commit (distinct names), and E1 is re-run there. The
  expectation is unchanged: 59 tests, the same 13 pass, and N4's two now
  fail for the expected cause (no email for the held row / no
  pending_result attribute), not in setUp.
- E1 re-run at d7e54398 (logs/red_run_d7e54398.txt): `Ran 59 tests`,
  `FAILED (failures=26, errors=24)`. 46 failed and the same 13 passed, so
  **E1 is met**. N4 now fails for the expected causes: in
  test_each_student_is_told_once_with_the_right_email, the held row's
  "Updated grade" email is missing; in
  test_the_held_row_is_promoted_and_the_count_is_as_today, there is no
  `pending_result` attribute.
- E2 baseline near run at d7e54398 (logs/baseline_near_d7e54398.txt.gz,
  `--parallel 4`): `Ran 341 tests`, `OK`, so **E2 is met** (N = 341,
  including X1–X10). Raw sha256 aebc81f1…ddeb16b, gz (gzip -n -9) sha256
  c397230b…5e07bf.

## Implementation (cca16b03c)

What it is:
- **Migration:** `students/migrations/0036_studentsubmission_pending_result.py`.
  It adds two nullable columns with no backfill.
- **students/services.py:**
  - `HELD_RESULT_FIELDS`, `holds_a_released_grade`, `held_values`,
    `held_view`, `held_result_stamp`, `_hold_if_released` (called in
    `_populate_and_save_grade`'s final save, under the row lock),
    `take_held_result`, `promote_pending(submission_id, *, actor,
    notify=True)`, `promote_all_pending` and `_after_promotion` (on_commit:
    the update email when the row is released, and the AI summary).
  - The pipeline queues the formatting task with the held stamp and skips the
    summary for a held result.
  - `emit_grading_completed` reads the model and label from the held result
    and adds `held_for_approval: true`.
- **audit/metadata.py:** `held_for_approval` is allowed on GRADING_COMPLETED.
- **students/views.py:**
  - publish calls `promote_pending` before the first-release flip.
  - `update_grade` is split. `_save_hand_grade` runs under
    `select_for_update` in one atomic block: `take_held_result`, then the
    edit, then ONE save. `_after_hand_grade` runs after the commit (email,
    formatting task, answer).
  - `has_pending_result` is a teacher-only filter.
- **assignments/views.py:** publish-all calls `promote_all_pending` before its
  snapshot. The count is unchanged.
- **assignments/tasks.py:** `formatted_grade_async` uses the three stamp rules
  under the lock. The released write re-reads the row's numbers under the
  lock, so a result approved during the AI call is worded with its own
  numbers.
- **students/serializers.py:** the teacher detail serializer gets both
  fields. `StudentUploadAnswerSerializer` gets both as `SentAs(None)`.
- **One test added** after E1:
  `TheHeldResultsWording::test_approved_during_its_ai_call_the_task_words_the_approved_numbers`
  (the guard for M-h279.10). It was not in E1's red run. On e11e56536 it
  would error on `held_result_stamp`.
- **Hand edit and the AI summary.** `update_grade` does not dispatch the AI
  summary, as before H-279. A hand edit of a held row therefore releases it
  without a summary refresh. This is flagged to SM: Q3 named "approval", and
  the hand edit is an approval by the SM default. I did not add a new charge
  to the hand-edit route without a ruling.

X1–X10 amended in cca16b03c. In each, the old score or wording is asserted
first as the held (pre-approval) half, then the teacher approves through the
publish route, then the original after-assertion is checked. The diffs are
listed in EVIDENCE after the runs.

## Expectations for cca16b03c (committed before any run)

**E3. Green run.** Both new modules: `Ran 60 tests`, `OK`.

**E4. Migration check.** `manage.py makemigrations --check --dry-run`: no
changes.

**E5. Near run.** The 53 existing modules that touch the changed code (the
list is in logs/near_modules.txt, committed here) plus both new modules,
`--parallel 4`: `OK`, with N recorded from the run.
- X1–X10 pass in their amended form.
- Any failure is **unpredicted**. It is reported to SM before anything is
  amended.

**E6. Mutants.** One per guard, each in a disposable detached worktree at
cca16b03c, the file restored and checked by sha256 against the committed
blob. Each runs the two new modules. Expected: every mutant is killed by at
least the listed test.

| Mutant | Change | Expected killer(s) |
|---|---|---|
| M-h279.1 | the hold is decided from the instance loaded before the run, not the locked row | test_a_publish_landing_mid_run_still_holds_the_result |
| M-h279.2 | `holds_a_released_grade` drops the graded_at condition | test_a_first_grading_of_a_published_row_is_not_held |
| M-h279.3 | the held save writes the new result into the released columns too | test_every_released_column_is_left_as_it_was (+ "nothing moves") |
| M-h279.4 | single publish does not promote | test_single_publish_promotes_it, N1 |
| M-h279.5 | publish-all does not promote | test_publish_all_promotes_it, N4 |
| M-h279.6 | publish promotes only an already published row (the unpublished-held guard) | test_an_unreleased_row_that_somehow_holds_a_result_is_released_with_it |
| M-h279.7 | the hand edit does not take the held result | test_the_edit_applies_on_top_of_the_held_result |
| M-h279.8 | a second re-grade keeps the first held result | test_a_second_regrade_replaces_the_held_one, test_the_newest_held_result_is_the_one_released |
| M-h279.9 | the task for a held result writes the released text | test_its_task_words_the_held_result_and_leaves_the_released_text |
| M-h279.10 | the released write does not re-read the numbers under the lock | test_approved_during_its_ai_call_the_task_words_the_approved_numbers |
| M-h279.11 | the AI summary is dispatched at the held save | test_no_email_and_no_ai_summary_at_the_held_save, test_the_ai_summary_is_dispatched_once_per_approval |
| M-h279.12 | the promotion does not empty the hold | N2 (double click), N3 |
| M-h279.13 | the student detail serializer names pending_result | test_no_serializer_but_the_teachers_page_reads_them |
| M-h279.14 | `has_pending_result` is not teacher-only | test_a_student_cannot_filter_on_it |
| M-h279.15 | no `held_for_approval` key | test_grading_completed_says_held_and_changes_nothing_released |
| M-h279.16 | the promotion is a queryset update (no post_save) | test_the_final_grade_follows, test_the_student_reads_it_at_once_though_cached, test_one_grade_change_from_the_released_to_the_promoted |
| M-h279.17 | `promote_pending` reads the row without `select_for_update` | N3 / N6 (threads). **Not guaranteed**: a race may not show in a given run. A survivor is reported as such, not argued away. |

## E3 at cca16b03c: NOT met (logs/green_run_cca16b03.txt)

`Ran 60 tests`, `FAILED (failures=2, errors=2)`. E4 met (`No changes
detected`, rc=0).

- **Code bug (3 tests), with one cause.**
  - The failing tests:
    - test_single_publish_promotes_it: `graded_at` 08:33:00.265000 !=
      `pending_graded_at` 08:33:00.265740;
    - test_approved_before_its_task_ran_the_task_writes_the_released_row;
    - test_approved_during_its_ai_call_the_task_words_the_approved_numbers.
  - The cause: `DjangoJSONEncoder` cuts a time to milliseconds, and the
    promotion took `graded_at` from that JSON copy. The released stamp then
    differed from the held stamp, so the held result's formatting task was
    superseded after approval. Students would have read no formatted grade.
  - Fixed in the next commit: `take_held_result` sets `graded_at :=
    pending_graded_at`, which is the whole-precision column.
- **Test defect (1 test).** test_n10_a_promotion_that_fails_sends_none_and_changes_nothing
  expected the ValidationError to reach the test. The API's error handler
  answers 500 instead. The guarantees under test are unchanged: no email,
  released columns unchanged, the held result kept. The test now posts and
  asserts 500, then checks the same three things.
- E3 is re-run on the fix commit. The expectation is unchanged: `Ran 60`,
  `OK`. E5 and E6 then run on the fix commit, not cca16b03c.

## E3 re-run at 590f6237: met (logs/green_run_590f6237.txt)

`Ran 60 tests`, `OK`.

## E5 near run at 590f6237: NOT met (logs/near_run_590f6237.txt)

`Ran 845 tests`, `FAILED (failures=8)`.

| # | Test | Predicted? | Cause | Action |
|---|------|-----------|-------|--------|
| 1 | students.tests_regrade_formatted_grade::test_it_is_cleared_by_the_same_update_as_the_score (X7, amended) | X7 yes; the failure of the amendment no | my amendment's held-save check matched `"score"` inside the held result's JSON (captured SQL carries its parameters) | test defect in my own amendment: it now checks for a written column (`"score" =`). Fixed in 495d74ea |
| 2 | AutoGrader.tests_submission_audience_guard::test_rule_2… (action=update_grade) | no | I moved the serializer builds out of `update_grade` into helpers; the guard reads the action's own body | **code** changed back: the body is inline in `update_grade`, inside one atomic block. No test change. 495d74ea |
| 3 | AutoGrader.tests_student_feedback_guard::test_rule_1_every_raw_read_is_a_named_one | no | the same move: the raw `feedback` read sat in `_save_hand_grade`, not the named `update_grade` | **code**, as #2 |
| 4 | AutoGrader.tests_student_feedback_guard::test_the_scan_covered_the_repository | no | as #3 | **code**, as #2 |
| 5 | AutoGrader.tests_student_feedback_guard::test_rule_3_a_student_is_refused_every_review_queue_filter | no | the guard holds `TEACHER_ONLY_FILTERS` equal to the filterset's review-queue fields; `has_pending_result` is not a filterset field | **code**: it is refused through its own `TEACHER_ONLY_QUERY_PARAMS`, and `TEACHER_ONLY_FILTERS` is unchanged. No test change. 495d74ea |
| 6–8 | students.tests_student_upload_answer: AFirstUploadTest::test_the_keys_are_the_thirty_the_route_always_answered_with, OnlyTheAnswerProtectsTest::test_a_graded_paper_answers_with_every_staff_field_the_constant, TheTeacherStillReadsEverythingTest::test_the_teachers_page_of_a_graded_paper | **no** | H-141 pins the teacher page's keys, and the student upload answer's (the same keys in the same order), at 30. H-279 adds `pending_result` and `pending_graded_at` to the teacher page, and as `SentAs(None)` to the upload answer | **held for SM.** Not amended. Proposal: KEYS becomes 32, with the two names appended, and the constant-check test also asserts both are None on a student's upload |

E5 is re-run, whole, on the commit that settles #6–8. E6 (mutants) runs on that commit too, against the expectations above. The code is now 495d74ea; if SM accepts the proposal, only tests change.

- near_run_590f6237.txt is kept gzipped (gzip -n -9): raw sha256 11cb3099387ca1da2549a3e4a14cb3346347ca8bf7edfdf8131c4f73f80340e7, gz sha256 3ffcaf57a9b4f5a81432531bb785510b929ee449e3636626dda60d3f92ba97c6.

## SM rulings on the E5 report (2026-11-03)

1. **H-141 pins: the proposal is approved.** Committed as its own commit, 339da92e:
   - KEYS goes from 30 to 32, with `pending_result` and `pending_graded_at`
     appended. The order of the original 30 is unchanged.
   - CONSTANTS gains both as None, so every `assert_constants` on an upload
     answer checks them.
   - Two tests are added in OnlyTheAnswerProtectsTest:
     - test_a_student_upload_over_a_held_result_says_nothing_of_it
     - test_built_for_a_teacher_over_a_held_result_it_says_nothing_of_it
   - The upload route is a student's only (`IsStudent`; a teacher is refused
     it). So "a teacher upload" is asserted by building the route's
     serializer with a teacher's request over the held row.
2. **Q3 edge: KEEP AS IS.** Releasing a held result by hand edit does not
   refresh the AI summary, the same as any hand edit before H-279.
   `update_grade` dispatches no summary and gets no new charge. The SM tells
   the owner.
3. **Accepted:** the guard fixes in code (`update_grade` inline in one locked
   atomic block, `TEACHER_ONLY_QUERY_PARAMS`) and the X7 fix.

## Expectations for 339da92e (committed before the runs)

**E5 (re-run).** The same 53 + 2 modules, `--parallel 4`: `OK`. The test
count is 845 + 2 (the two H-141 tests added) = **847**.

**E6.** The 17 mutants in the table above, run on 339da92e against the two new
modules. Each is expected to be killed by the listed test(s). M-h279.17 is
not guaranteed.

The mutant changes are against these anchors:

| Mutant | File | Change |
|---|---|---|
| .1 | students/services.py | `holds_a_released_grade(stored)` becomes `(submission)` |
| .2 | students/services.py | the `and row.graded_at is not None` condition is dropped |
| .3 | students/services.py | the restore loop becomes `pass`, and the held save writes HELD_RESULT_FIELDS too |
| .4 | students/views.py | `promoted = None` |
| .5 | assignments/views.py | `promote_all_pending(...)` becomes `pass` |
| .6 | students/views.py | promote only `if submission.is_published` |
| .7 | students/views.py | `held = False` |
| .8 | students/services.py | an existing hold is kept (early return) |
| .9 | assignments/tasks.py | `if for_held:` becomes `if False:` |
| .10 | assignments/tasks.py | the refresh_from_db before the released write is removed |
| .11 | students/services.py | `if not held:` becomes `if True:` |
| .12 | students/services.py | take_held_result does not empty the hold |
| .13 | students/serializers.py | StudentSubmissionDetailStudentVersionSerializer names `pending_result` |
| .14 | students/views.py | `TEACHER_ONLY_QUERY_PARAMS = ()` |
| .15 | students/services.py | the `held_for_approval` line is removed |
| .16 | students/services.py | the promotion save becomes a queryset `.update()` |
| .17 | students/services.py | `select_for_update()` is removed from promote_pending |

## E5 at 339da92e: met

`Ran 847 tests`, `OK` (logs/near_run_339da92e.txt.gz, gzip -n -9; raw
sha256 0dbaf6ba285b3aa3a031cbd091e1da34bf3661d97471b613c184228a2b0dafa8, gz sha256 c77d0c61c894e2751d799d8fee1cd767fa7e446e41211ed86daf7a1aa6d10933).

## Owner ruling, 2026-11-03: the hand-edit approval refreshes the summary

This reverses the Q3 edge recorded above ("KEEP AS IS"). When
`update_grade` releases a held result, it dispatches `student_summary_async`
exactly once:
- on commit;
- only when the row read under the lock held a result.

So there is one summary charge per approval, whichever route gives it
(publish, publish-all or hand edit), and none at the held save. A hand edit
of a row with nothing held stays as before, with no summary. The SM is
asking the owner whether to extend that; it is not built.

Red tests (this commit; code unchanged from 339da92e), class
`AHandEditApprovalRefreshesTheSummary`:
- test_a_hand_edit_of_a_held_row_refreshes_the_summary_once
- test_a_hand_edit_with_nothing_held_dispatches_no_summary
- test_a_refused_hand_edit_of_a_held_row_dispatches_none (400, the result is
  still held)
- test_a_rolled_back_hand_edit_of_a_held_row_dispatches_none (the save
  raises DatabaseError, 500, the result is still held and the released
  columns are unchanged)

**E7. Red run** of that class at this commit: `Ran 4`, `FAILED`. Only
test_a_hand_edit_of_a_held_row_refreshes_the_summary_once fails (no
dispatch). The other three pass: they are guards, and must stay true.

After the code commit:
- **E3:** `Ran 64` (60 + 4), OK.
- **E5:** `Ran 851` (847 + 4), OK.
- **E6:** 18 mutants. **M-h279.18** removes the hand-edit dispatch; its
  killer is test_a_hand_edit_of_a_held_row_refreshes_the_summary_once.

## Runs at 7bcec03d (the hand-edit summary dispatch)

- **E7** red run at 6efe3014 (logs/red_run_6efe3014.txt): `Ran 4`,
  `FAILED (failures=1)`, and the one failure is
  test_a_hand_edit_of_a_held_row_refreshes_the_summary_once. **Met.**
- **E3** at 7bcec03d (logs/green_run_7bcec03d.txt): `Ran 64 tests`, `OK`.
  **Met.**
- **E5** at 7bcec03d (logs/near_run_7bcec03d.txt.gz; raw sha256 d8f35d85052c274898ca0204937d75b43c266377f7f020fd4547f75820f423e6, gz
  sha256 d4d92451a45a2165057244c2dadf78396111c23ea44b6d1167b33b8fa906b918): `Ran 851 tests`, `OK`. **Met.**
- **E6:** 18 mutants on 7bcec03d, in a disposable detached worktree (test DB
  test_AutoGrader_h279_mut). The results follow.

### E6 at 7bcec03d: met, 18 of 18 killed (mutants_7bcec03d/)

Each mutant ran both H-279 modules (`Ran 64`). Every file was restored by
sha256 against the 7bcec03d blob after each mutant ("restored ok" in
runner_summary.txt). The logs are gzipped; the hashes are in SHA256SUMS.raw
and SHA256SUMS.gz. The runner is mutate_h279.py.txt.

| Mutant | Change | Result | Killed by |
|---|---|---|---|
| M-h279.1 | the hold check reads the instance, not the locked row | killed (`FAILED (failures=1, errors=1)`) | test_a_publish_landing_mid_run_still_holds_the_result, test_a_first_grading_of_a_published_row_is_not_held |
| M-h279.2 | holds_a_released_grade ignores graded_at | killed (`FAILED (failures=1)`) | test_a_first_grading_of_a_published_row_is_not_held |
| M-h279.3 | the held save writes the new result over the released columns | killed (`FAILED (failures=29, errors=3)`) | test_a_replaced_held_results_task_writes_nothing, test_the_released_results_own_task_still_words_the_released_result, … (29 tests) |
| M-h279.4 | publish_grade promotes nothing | killed (`FAILED (failures=17, errors=1)`) | test_an_approval_racing_a_held_save_releases_each_result_at_most_once, test_an_unreleased_row_that_somehow_holds_a_result_is_released_with_it, … (18 tests) |
| M-h279.5 | publish-all promotes nothing | killed (`FAILED (failures=4)`) | test_each_student_is_told_once_with_the_right_email, test_n5_publish_all_twice_sends_one, … (4 tests) |
| M-h279.6 | publish_grade promotes only an already published row | killed (`FAILED (failures=1)`) | test_an_unreleased_row_that_somehow_holds_a_result_is_released_with_it |
| M-h279.7 | a hand edit does not take the held result | killed (`FAILED (failures=1, errors=1)`) | test_the_edit_applies_on_top_of_the_held_result, test_a_hand_edit_of_a_held_row_refreshes_the_summary_once |
| M-h279.8 | a second re-grade does not replace the held one | killed (`FAILED (failures=4)`) | test_a_replaced_held_results_task_writes_nothing, test_a_second_regrade_replaces_the_held_one, … (4 tests) |
| M-h279.9 | the formatted-grade task never words a held result | killed (`FAILED (errors=2)`) | test_its_task_words_the_held_result_and_leaves_the_released_text, test_the_wording_is_released_with_the_result |
| M-h279.10 | the task does not re-read the numbers before wording | killed (`FAILED (failures=1)`) | test_approved_during_its_ai_call_the_task_words_the_approved_numbers |
| M-h279.11 | the summary is dispatched at the held save | killed (`FAILED (failures=2)`) | test_no_email_and_no_ai_summary_at_the_held_save, test_the_ai_summary_is_dispatched_once_per_approval |
| M-h279.12 | promotion leaves the pending columns set | killed (`FAILED (failures=13)`) | test_an_approval_racing_a_held_save_releases_each_result_at_most_once, test_an_unreleased_row_that_somehow_holds_a_result_is_released_with_it, … (13 tests) |
| M-h279.13 | the student's serializer sends pending_result | killed (`FAILED (failures=2)`) | test_no_serializer_but_the_teachers_page_reads_them, test_the_students_own_page_and_list |
| M-h279.14 | a student may filter on has_pending_result | killed (`FAILED (failures=2)`) | test_a_student_cannot_filter_on_it |
| M-h279.15 | GRADING_COMPLETED drops held_for_approval | killed (`FAILED (failures=1)`) | test_grading_completed_says_held_and_changes_nothing_released |
| M-h279.16 | promotion saves by queryset .update() (no signals) | killed (`FAILED (failures=8)`) | test_an_approval_racing_a_held_save_releases_each_result_at_most_once, test_n2_a_double_click_sends_one, … (8 tests) |
| M-h279.17 | promote_pending reads without the row lock | killed (`FAILED (failures=3)`) | test_an_approval_racing_a_held_save_releases_each_result_at_most_once, test_n3_concurrent_single_publishes_tell_the_student_once, test_n6_publish_all_racing_single_publish_tells_the_student_once |
| M-h279.18 | the hand-edit summary dispatch is removed | killed (`FAILED (failures=1)`) | test_a_hand_edit_of_a_held_row_refreshes_the_summary_once |

## Owner ruling, 2026-11-03: "hand edit should refresh summary too"

This widens the ruling above; there is no separate ticket. `update_grade`
dispatches `student_summary_async` exactly once per successful grade edit,
on commit. It does not matter whether a result was held, or whether the row
is published. The summary is the teacher-facing `StudentCourse.ai_summary`,
so publish state does not gate it (the same as the dispatch after grading
today). The open question recorded above ("The SM is asking the owner
whether to extend that") is answered: yes.

Each hand edit is one summary charge to the editing teacher. It uses the
same charging path as today's refresh after grading: the untracked
`student_summary_async.delay(student_id, actor_id, course_id)`, through
`refresh_student_summary`. A dispatch failure is logged, never raised.

What does not change:
- A publish approval still gives 1.
- The held save still gives 0 (test_the_ai_summary_is_dispatched_once_per_approval,
  test_no_email_and_no_ai_summary_at_the_held_save).
- The student's email on a hand edit is unchanged.

Red tests (this commit; code unchanged from 7bcec03d), class
`AHandEditApprovalRefreshesTheSummary`, now 9 tests:
- test_a_hand_edit_of_a_held_row_refreshes_the_summary_once (as before)
- test_a_hand_edit_of_a_plain_row_refreshes_the_summary_once. This replaces
  test_a_hand_edit_with_nothing_held_dispatches_no_summary, whose contract
  the ruling reverses. Released, nothing held: 1.
- test_a_hand_edit_of_an_unpublished_row_refreshes_the_summary_once (new).
  Unpublished, nothing held: 1, and the row stays unpublished.
- test_a_refused_hand_edit_of_a_held_row_dispatches_none (as before; 400)
- test_a_refused_hand_edit_of_a_plain_row_dispatches_none (new; score above
  the maximum, 400)
- test_an_invalid_hand_edit_of_a_plain_row_dispatches_none (new; no
  "score", 400)
- test_a_rolled_back_hand_edit_of_a_held_row_dispatches_none (as before;
  the save raises, 500)
- test_a_rolled_back_hand_edit_of_a_plain_row_dispatches_none (new; the
  save raises, 500, the released columns are unchanged)

### Expectations (committed before any run)

**E7b. Red run** of that class at this commit: `Ran 9`,
`FAILED (failures=2)`. The two failures are
test_a_hand_edit_of_a_plain_row_refreshes_the_summary_once and
test_a_hand_edit_of_an_unpublished_row_refreshes_the_summary_once, because
the code still dispatches only when a result was held. The other seven pass;
they are guards and must stay true.

The code commit that follows makes the dispatch unconditional. It is
registered inside the atomic block after the save, so every early 400
return and every rolled-back save registers nothing. After it:
- **E3:** both H-279 modules, `Ran 68` (64 + 4 new), OK.
- **E5:** near run of the same 53 modules (logs/near_modules.txt),
  `Ran 855` (851 + 4), OK. Nothing outside H-279 pins "no summary on a
  hand edit" (checked by grep), so no other test is predicted to change.
- **E6:** the same 18 mutants on the code commit. M-h279.18 is re-anchored
  to the new, unconditional dispatch line and removes it. Its predicted
  killers: the held-row, plain-row and unpublished-row tests. That one
  mutant covers the plain-row case, so no second mutant is added (E6 = 18).
  M-h279.1–17 are unchanged; all are predicted killed.
