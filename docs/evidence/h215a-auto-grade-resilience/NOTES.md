# H-215a notes

- **What changed** (approved by the Senior Manager, 24 October 2026; design section 3A plus section 4's guard):
  - The automatic grading at the due date (`auto_grade_due_assignment`) and the teacher's scheduled batch
    (`grade_batch_async`) send `only_if_ungraded=True` with every paper. The grading claim then also requires
    `graded_at IS NULL`, in the same atomic UPDATE. A paper graded after it was queued (by the teacher, or by
    an earlier delivery of the same message) is skipped when its run starts: no AI call, no charge, the grade
    already given stands. Its tracked item ends `SUCCESS` with `skipped: true` and step
    "Skipped - already graded". Its batch row is `"SUCCESS"` with `"note": "already graded"`.
  - `auto_grade_due_assignment` retries a database or broker outage: at most 5 times, after
    `min(60 * 2**retries, 900)` s plus 0-30 s. The retry carries the batch row's id. Every other error keeps
    today's answer, `"Error: <class>"`.
  - A retry with the row's id reuses the row and sends only the ungraded papers that have no item in that row
    with a celery id, that is, papers whose dispatch never reached the broker. New items are numbered after
    the row's highest. `total_files` is not changed.
- **Production files:** `students/services.py` (the claim and `grade_engine`), `students/exceptions.py` (new
  `SubmissionGradedMeanwhileError`), `assignments/tasks.py`, and `students/models.py`
  (`BatchUploadSession.update_result(note=None)`). No migration: the note is a key inside the `results` JSON.
- **The exception's name:** `SubmissionGradedMeanwhileError`, not the proposal's `SubmissionAlreadyGradedError`.
  That name already exists in `students/exceptions.py` for the upload rule (a closed submission). The Senior
  Manager approved the rename.
- **The batch row's word stays `"SUCCESS"`** (the Senior Manager's ruling (2)). The reader that decided it is
  the legacy branch of `users/views.py` `session_results`. It lists only `"SUCCESS"` and `"FAILED"` entries but
  counts every entry as completed, so a new word would be miscounted.
- **The flag is sent only when set.** `_dispatch_tracked_grading` adds `only_if_ungraded` to the message only
  when it is True, and `grade_engine_async` passes it to `grade_engine` only when it is True. So `grade-all`, a
  teacher's own grading, and every existing caller send and call exactly as before. Two existing test modules
  (`tests_credits_mid_batch`, `tests_item_retry_h38`) replace `grade_engine` with fakes that take only
  `processing_task_id`, and they stay valid.
- **The retry is a manual `self.retry`, not `autoretry_for`**, because it must carry `session_id`.
  `bind=True` keeps the registered name and the beat row's positional `args` working, and direct calls in
  tests still work.
- **After the fifth retry** Celery raises the outage itself, so the task ends `FAILURE`. That last failure is
  not written by the H-209 log line, which covers only the swallowed errors. Celery's own task-failure log
  records it. This is the hand-over point for the design's parts B and C, which are not in this row.
- **Failure point 6 (not in scope, named):** a redelivery after a worker death (`acks_late`) carries no
  `session_id`, so it still makes a second batch row and dispatches the papers still ungraded. It no longer
  grades anything twice, because every dispatched message carries the guard. A paper already queued by the
  first delivery and graded by then is skipped when the second message runs.
- **Duplicate batch-row lines (named, not changed):** a second delivery of one grading message that finds the
  paper graded writes a second "already graded" line to the batch row. The tracked branch of
  `session_results` counts items by their status, so the counts are right. Only the legacy `results` list
  gets an extra line.
- **Re-enable on save (design section 2, not changed):** `assignments/signals.py` `schedule_auto_grading`
  runs `update_or_create` on the one-off `PeriodicTask` with `enabled: True` on every save of an assignment
  that has a due date and auto-grade on. So a save after the due date re-arms the spent one-off, and the task
  can fire again. With H-215a such a re-fire grades only papers still ungraded, and a paper graded while its
  message waits is skipped. The re-arming itself is left for a later row.
- **Item numbers:** `item_index` has no unique constraint, so continuing after the row's highest is safe. The
  paper whose dispatch failed keeps its FAILURE item (no celery id). Its new item is the resend.
- **Paperless items** are left out of the resend's "already queued" set, because `NOT IN` over a NULL
  `submission_id` would match no paper.

## Expected reds against b02de3fb (written before any run)

The red run takes the four production files back to `b02de3fb` and runs the new module as at the tip.

```
students.tests_h215a_auto_grade_resilience
  APaperGradedMeanwhileIsNotGradedAgainTest
    test_an_auto_paper_graded_by_the_teacher_meanwhile_is_skipped
        "2 != 1 : the auto run reached the provider for a paper already graded"
    test_two_deliveries_of_one_auto_paper_charge_once
        ": the second delivery charged again"   (the counts depend on the charge rows per grading)
    test_a_scheduled_batch_paper_graded_meanwhile_is_skipped
        "2 != 1 : the scheduled batch run reached the provider for a paper already graded"
  TheFlagIsOnEveryAutomaticDispatchTest
    test_every_auto_grade_dispatch_carries_the_flag            "[None, None] != [True, True]"
    test_every_scheduled_batch_dispatch_carries_the_flag       "[None, None] != [True, True]"
  AnOutageIsRetriedTest
    test_a_database_outage_is_retried_once_with_the_first_countdown
        "0 != 1 : a database outage was not retried"
    test_each_transient_class_is_retried (8 sub-case blocks, each)
        "0 != 1 : an outage was not retried"
    test_the_countdown_doubles_to_fifteen_minutes_plus_up_to_thirty_seconds (ERROR)
        "module 'assignments.tasks' has no attribute '_auto_grade_retry_countdown'"
    test_a_broker_outage_mid_dispatch_resends_only_the_missed_papers
        "0 != 1 : a broker outage was not retried"
GREEN at b02de3fb and after (controls): test_the_teachers_own_regrade_still_grades_and_charges and the five
  tests of NothingElseIsRetriedTest. The script checks exactly 16 FAIL/ERROR blocks, 8 of them the sub-cases.
```

**One test traced by hand at the base (rule 22):** for `test_an_auto_paper_graded_by_the_teacher_meanwhile_is_skipped`:
1. The auto task dispatches one message without the flag; the base dispatcher has none.
2. The teacher's grading makes provider call 1 and sets `graded_at`; `grading_state` is DONE.
3. The captured message runs. The base claim accepts DONE (a re-grade), so the pipeline calls the provider a
   second time.
4. `assertEqual(2, 1, msg)` fails with "2 != 1 : the auto run reached the provider for a paper already graded".

## Mutants (expected sets traced against every assertion of both battery modules)

The battery is `students.tests_h215a_auto_grade_resilience` plus `assignments.tests_task_results_carry_no_text`.

| Id | Break | Expected to fail |
|---|---|---|
| G1 | the claim ignores `only_if_ungraded` | A1, A2, the scheduled-batch skip |
| G2 | the automatic grading dispatches without the flag | A1, A2, the auto flag test |
| G3 | the graded-meanwhile check is dropped (`SubmissionGradingInProgressError` instead) | A1 only. The in-progress skip marks the item, but writes no batch row, so `session.results[-1]` raises IndexError. A2 and the scheduled-batch skip pass, because neither charges nor calls the provider |
| G4 | every exception is retried | any-other-error and missing-assignment (retry called once), and `tests_task_results_carry_no_text` T1 (a direct call: `retry` re-raises the RuntimeError) |
| G5 | the tuple loses `ProcessingTemporarilyUnavailable` | the transient test (that sub-case) and the broker test (the launcher raises the wrapper) |
| G6 | the countdown does not double | the countdown test (the database test's retries=0 still gives 60-90) |
| G7 | `max_retries=50` | the database test |
| G8 | the retry drops the row's id | the broker test (the database test expects None anyway) |
| G9 | the resend ignores the celery id | the broker test (all three papers resent) |
| G10 | the scheduled batch dispatches without the flag | the scheduled-batch skip, the batch flag test |
