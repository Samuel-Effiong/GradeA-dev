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
