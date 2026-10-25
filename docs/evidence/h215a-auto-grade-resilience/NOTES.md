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
- **The retry gives `args=(assignment_id,)` and `kwargs={"session_id": ...}`** (fix 69699386, found by the
  Senior Manager's pre-read of 33b01646). Beat sends the id positionally (`assignments/signals.py`, `"args":
  json.dumps([str(instance.id)])`). Celery 5.5.3's `signature_from_request` keeps the request's args when
  `retry` is given none, so e8d537e6's kwargs-only retry sent the id twice. Every real retry would have
  failed with `TypeError: ... got multiple values for argument 'assignment_id'`, before the task body ran.
  The tests patched `retry` out, so they missed it. The new test
  `test_a_beat_start_retried_for_real_completes_and_sends_the_missed_papers` starts the task positionally,
  as beat does, with Celery's own retry. `apply()` runs the retried message eagerly
  (`Task.apply`: `retval.sig.apply(retries=retries + 1)`). Mutant G11 restores the kwargs-only retry.
- **A paper whose run failed in the pipeline is not resent** (checked at the Senior Manager's request). Its
  item in the row has a celery id, so `_papers_not_yet_sent` leaves it out, by design: a pipeline failure is
  the sweep's (part B) to retry, or the teacher's through the item retry route. A paper sent before the
  outage and graded since then has a grade, so it is not in the ungraded set at all.
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
- **One item per paper (the Senior Manager's ruling, 16f358d7).** Before it, a retry made a new item for the
  failed paper. `users/views.py` `session_results` then read "4 / 3" (:2923-2924), `is_complete` False forever
  (:2933), and one FAILED line of class SYSTEM, unclassified and not retryable, for a paper graded later
  (:2916, :2935; `item_results.py:61`). Now `_relaunch_failed_dispatches` takes the paper's item in this row
  that is FAILURE with no celery id. It resets that item in place with one conditional UPDATE on
  `pk, status FAILURE, celery_task_id null`: `PENDING`, `retry_count + 1`, `reason_code` and `error` cleared,
  `started_at` and `finished_at` None. It then launches the item again through the real launcher, so a second
  outage leaves it FAILURE with no celery id, ready for the next retry. Reused items are launched first;
  papers with no item get new ones after the row's highest index.
- **Two failed items for one paper cannot exist today** in a row that a retry reads:
  - a paper gets a new item only when it has no item in the row (`_papers_not_yet_sent` drops papers with a
    queued item, and `_relaunch_failed_dispatches` takes the rest that have a failed one);
  - the run-time RUBRIC_MISSING refusal (`_refuse_batch_without_rubric`) is the one other writer of items. It
    records refused items for every ungraded paper, numbered from 1, and the task then returns
    "Refused: RUBRIC_MISSING" with no retry, so no later run reads that row.
  The highest index is taken all the same. The refused items' numbers can repeat the row's earlier ones; that
  is the existing S6d numbering, not changed.
- **No G13 (the reset UPDATE without the celery-id-null filter).** No test can tell. The item is chosen by a
  query that already requires a null celery id, and `_papers_not_yet_sent` has already dropped every paper
  with a queued item, so the UPDATE never meets an item with a celery id in a single run. The filter guards a
  race between two runs of the same retry (one launches the item and sets its id between the other's read and
  its UPDATE), and a test cannot produce that race without concurrency. Dropping the filter from the choosing
  query as well is just as invisible, for the same reason.
- **Paperless items** are left out of the resend's "already queued" set, because `NOT IN` over a NULL
  `submission_id` would match no paper.

## Expected reds against b02de3fb (written before any run)

The red run takes the four production files back to `b02de3fb` and runs the new module as at the tip.
The written reds against 69699386 (a new item for the failed paper), each the first assertion to fail in the
test's own block. The gate shows them under mutant G12, which restores that path:
- broker test and beat test: "Lists differ: [1, 2, 3, 4] != [1, 2, 3]";
- `test_two_outages_on_one_paper_reuse_its_item_twice`: "Lists differ: [1, 2, 3, 4, 5] != [1, 2, 3]"
  (the second outage hits a new item, then two more are made);
- `test_after_a_retry_the_batch_reads_complete_with_no_failure`: "'4 / 3' != '3 / 3'".

The beat test's red against e8d537e6 (before fix 69699386), written beforehand:
`False is not true : auto_grade_due_assignment() got multiple values for argument 'assignment_id'`. The gate
shows it through mutant G11, which is e8d537e6's retry, and the script looks for
"got multiple values for argument 'assignment_id'" in that test's block of the G11 log.

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
    test_a_beat_start_retried_for_real_completes_and_sends_the_missed_papers
        "'Error: ProcessingTemporarilyUnavailable' != 'Auto-grading started for 2 submissions.'"
        (the base swallows the outage; the task succeeds with that string)
    test_two_outages_on_one_paper_reuse_its_item_twice
        "'Error: ProcessingTemporarilyUnavailable' != 'Auto-grading started for 2 submissions.'"
  TheTeacherSeesOneLinePerPaperTest
    test_after_a_retry_the_batch_reads_complete_with_no_failure
        "'2 / 3' != '3 / 3'"   (item 1 graded, item 2 FAILURE, paper 3 never got an item)
GREEN at b02de3fb and after (controls): test_the_teachers_own_regrade_still_grades_and_charges and the five
  tests of NothingElseIsRetriedTest. The script checks exactly 19 FAIL/ERROR blocks, 8 of them the sub-cases.
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
| G5 | the tuple loses `ProcessingTemporarilyUnavailable` | the transient test (that sub-case), the broker test (the launcher raises the wrapper), the beat and two-outage tests (not retried: "Error: ..." returned), the route test ("2 / 3") |
| G6 | the countdown does not double | the countdown test (the database test's retries=0 still gives 60-90) |
| G7 | `max_retries=50` | the database test |
| G8 | the retry drops the row's id | the broker test; the beat and two-outage tests (the retried run makes a second row and sends all three papers); the route test (two rows: the unpacking raises). The database test expects None anyway |
| G9 | the resend ignores the celery id | the broker test; the beat and two-outage tests (three papers counted); the route test (paper 1 gets a second item, which is skipped as already graded: "4 / 3") |
| G10 | the scheduled batch dispatches without the flag | the scheduled-batch skip, the batch flag test |
| G11 | the retry gives kwargs only (e8d537e6's retry) | the beat, two-outage and route tests (TypeError, the task fails), the database and broker tests (`args` missing from the retry call: KeyError) |
| G12 | a retry makes a new item instead of reusing the failed one (69699386) | the broker, beat, two-outage and route tests, each with its written red against 69699386 |
