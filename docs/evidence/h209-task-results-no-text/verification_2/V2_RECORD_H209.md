# Verifier 2: H-209 record (tip ab6606a6, d5's branch task/h209-task-results-no-text)

Verdict: VERIFIED. Run 19:03:03 to 19:04:38 WAT 2026-10-09, one slot (0b GRANT 19:03), load 3.5 at start.

## What was read
The production change to assignments/tasks.py (commit ba47aa34, carried to ab6606a6): auto_grade_due_assignment, send_assignment_due_reminder and send_new_assignment_posted_notification return "Error: <Class>" and write ONE logger.error line with the class and the stack frames (describe_error_for_log), no text and no traceback; grade_all_submissions stores class and ids only in its failure meta, no "detail", and still re-raises; the print calls became ids-only logger.info lines (grade-all progress, extraction start/saved, grading queued). Verifier 1 had pre-read d5's tests as fit.

## Results
- check: all 14 edits apply (exit 0).
- baseline: Ran 12 tests, OK. These go through the REAL task bodies on real rows (students.tests_h38_tasks_namespace's TasksFixture); only the failing dependency is replaced (grade_engine_async.delay, timezone.localtime, grade_engine, the extraction service). They read the returned value, every log record at every level with its formatted traceback, stdout/stderr, the stored processing-task row and the failure meta.
- mutants: 14 of 14 KILLED, failing set EQUAL to the expected set each time, every written fragment seen inside that test's own FAIL block (rule 22), restores 14 of 14 sha256 ok. Listed in h209_v2_expected_sets.txt and the mutants log.
- Not cured (finding, expected red, not counted against the verdict): test_f1 (findings step, Ran 1, 1 failure, fragment seen): when grade-all re-raises, Celery's own failure log (celery.app.trace) carries the exception repr and traceback. Row H-218 (LOW, design first). My other probes read the project's own loggers only.

## Read-only answer for row H-219 (reading at ab6606a6, no run)
assignments/tasks.py:1080 (upload_assignment_async) stores error=str(e) in BatchUploadSession.results. That column is read only by users/views.py session_results in its legacy branch (sessions with NO processing_tasks); the upload view creates a processing task for every file (batch_session=session) before launching, nothing deletes them, so upload sessions take the tracked branch, which serves processing_task.error (describe_background_task_error: whitelisted passthrough, fixed infra sentence or fallback, never str(e)). So raw text is stored but not served to a teacher today; the fields that would carry it are failure_list[].error / success_list[].error. LOW, latent. The other items outside the three tasks (grade_batch_async's "Failed to clear scheduling info: {e}", four logger.exception sites) log text and tracebacks; left alone by H-209.

Shas: probe 7b77ba7e2939cf4b, mutants 944805118a821d0a, script edf98a8c2f13ba24, expected 7cdca2547cb60fbd.
