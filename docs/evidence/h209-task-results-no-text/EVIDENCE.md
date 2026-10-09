# H-209: what the assignment tasks print and return carries no error text

Author: Hardening Engineer (d5). Stacked on H-208 (`994febe1`; its final tip `5b9e9956`). Gated tip: `ab6606a6`. Written 2026-10-09 from the files in `gate_files/`; the clock times are read from the logs. Verified by Verifier 2 at `ab6606a6` (record in `verification_2/`); Verifier 1 pre-read the tests (one finding, fixed).

## What was wrong
A Celery task's result is stored in the result backend (Redis, one hour), and whatever a task prints goes to the worker's output. In `assignments/tasks.py`:
- `grade_all_submissions` (legacy bulk grading; nothing dispatches it today, a beat row could still name it) printed its traceback and stored the exception text and the traceback in its failure meta (`error`, `detail`).
- `auto_grade_due_assignment`, `send_assignment_due_reminder` and `send_new_assignment_posted_notification` swallowed ANY failure and RETURNED `"Error: <message> <traceback>"` as their stored result.
- Five `print()` calls held ids, a dict of ids and plain progress text (no names, emails, answer text or file names, read in the code).

## What happens to a user today when those scheduled tasks fail (read, not run)
The three scheduled tasks are created by `post_save` signals in `assignments/signals.py` as one-off `django_celery_beat` clocked tasks. Each catches every exception and returns a string, so Celery records the task as a SUCCESS: no retry, no failure state, no user-facing message, and before this row no error report either (nothing raised, nothing logged). A reminder that fails is simply not sent. An auto-grade that fails before it queues the per-submission grading (assignment lookup, teacher-reach check, session create, the ungraded query) leaves the assignment ungraded with no retry and no notice, and its one-off schedule is already spent; the teacher can still grade by hand. That is a separate finding, told to the Senior Manager, not fixed here. Not checked: whether beat catches up a missed clocked time after downtime.

## What changed
- `grade_all_submissions`: the failure meta carries the exception CLASS and the ids (`error`, `assignment_id`, `current_submission_id`); no `detail`, no text, no traceback; nothing is printed; progress goes to the logger with ids; the error is still re-raised, so error reporting receives it from the task machinery (frames are not added: another road exists).
- The three scheduled tasks return `"Error: <Class>"` and, because they swallow the failure and there is no other road to error reporting, write exactly ONE ERROR line with the class, the assignment id and the stack FRAMES (`AutoGrader.safe_logging.describe_error_for_log`, H-208): no text, no traceback.
- The `print()` calls became ids-only `logger.info` lines.
- Tests: `assignments/tests_task_results_carry_no_text.py` (8 tests). Every test raises an error whose text is a made-up marker and asserts first that what it inspects is non-empty; G2 asserts that the failure path ran before it asserts that nothing was printed (Verifier 1's finding).

## Gates
Tip `ab6606a6`, 2026-10-09. Scripts in `gate_files/scripts/` (`.txt`), logs gzipped. A clean first run: no chain stop.

| Step | Result |
|---|---|
| Small slot (new module + ownership failure + h38 tasks namespace), 18:37:42 | Ran 25, OK |
| (r) at the base `994febe1`, the test module from the tip, 18:38:11 | red set {T1,T2,T3,G1,G2,P1,P2}, each for its written reason (rule 22); control G3 green by design |
| (a) 497 tests (the modules that call the touched tasks, the cache test, the guard list) to 18:41:25 | OK, exit 0 |
| (b) 15 mutants K1 to K15, to 18:42:16 | 15 of 15 KILLED, restores verified, every failing set EXACTLY as written |
| (c) `c_h209.sh`, seven apps, 18:48:56 to 19:02:27 | Ran 6108 in 765.0 s, OK (skipped=26), stalled=0 |
| Verifier 2, 19:03 to 19:04:38 | VERIFIED: baseline 12 OK, 14 of 14 of his mutants killed with exact sets and written fragments |

## Lessons of H-208 applied before the first run
Every test was hand-traced against the real code; the expected sets were re-traced against ALL tests after the one test change (G2), K15 gained G2; the checker reads the full failure bodies; no frames assertion uses an error that was only constructed.

## Limits, said now (Verifier 2's findings included)
- Celery's own failure log (`celery.app.trace`) carries the exception repr and traceback when `grade_all_submissions` re-raises: row H-218 (LOW, design first). Not cured here.
- `upload_assignment_async` (`assignments/tasks.py` ~1080) stores `error=str(e)` in `BatchUploadSession.results`; by Verifier 2's reading it is not served to a teacher today (the tracked branch serves the sanitized error): row H-219, latent.
- `grade_batch_async`'s "Failed to clear scheduling info: {e}" and four `logger.exception` sites in this module log text and tracebacks; left alone.
- Sentry events from the three scheduled tasks are now messages (class, id, frames), not exception events.
- Not read: production Redis, log retention, external monitors, whether the django_celery_results admin table is empty in production.
- Worktree (rule 21): `Grade-Automator-Plus-h208-broker-text-not-logged-h209-task-results-no-text`.
