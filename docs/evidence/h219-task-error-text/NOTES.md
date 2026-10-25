# H-219 notes

- **The row** (new in this pack): H-209's evidence (`docs/evidence/h209-task-results-no-text/EVIDENCE.md` line 37) and
  Verifier 2's read (`verification_2/V2_RECORD_H209.md`, "Read-only answer for row H-219"):
  - "`upload_assignment_async` … stores `error=str(e)` in `BatchUploadSession.results`" (not served today);
  - "`grade_batch_async`'s 'Failed to clear scheduling info: {e}' and four `logger.exception` sites in this module log
    text and tracebacks; left alone".
- **What the read found** (beta `b4af529f`, `assignments/tasks.py`):

  | Site | Today | Change |
  |---|---|---|
  | `upload_assignment_async` `:1368` | `session.update_result(file_name, "FAILED", error=str(e))` | `error=describe_background_task_error(e, fallback_message)`: the tracked row's own sentence, with the same fallback (now one variable) |
  | `grade_batch_async` `:1477` | `logger.error(f"Failed to clear scheduling info or create session: {e}")` | `logger.error("…: assignment=%s %s", assignment_id, describe_error_for_log(e))`: H-209's form, and it gains the id |
  | `send_assignment_due_reminder` `:1809` (teacher), `:1858` (student) | `logger.exception("Failed to queue due reminder email for …", extra={ids})` | `logger.error("…: %s", describe_error_for_log(exc), extra={ids})`: H-208's form |
  | `send_new_assignment_posted_notification` `:1939` | `logger.exception(…, extra={ids})` | the same |
  | `prerender_assignment_pdfs` `:2035` | `logger.exception("[PDF] pre-render failed for assignment %s (%s view)", …)` | `logger.error("…: %s", …, describe_error_for_log(exc))` |

  - The level stays ERROR: these are real faults. So they are still Sentry events, but now as messages without the
    exception, as H-209's three are.
- **Left alone, and why:**
  - `:1113` and `:1353` store `str(exc)` of a `TaskCancelledError`, whose text is ours ("Task cancelled by user.",
    `students/task_tracking.py:314/334`).
  - `:2016` and `:2024` log the text of `PDFRendererUnavailable`/`PDFRendererBusy`. Read at their raise sites
    (`assignments/pdf_renderer.py:790, 868, 938`, the Senior Manager's condition), each carries only our fixed sentence
    and two integers (the queue count and the limit). No outside text.
  - `:583` and `:611` already log the class only (H-38 N3).
- **The Senior Manager's rulings (25 October 2026):** approved as proposed; confirm the PDF raise sites first (done,
  above); behaviour must not change.
- **Behaviour stays the same:**
  - return values ("Queued N …", "Pre-rendered: …"), FAILED/CANCELLED statuses, the tracked rows and audit events are
    untouched;
  - only `results[].error` for a failed assignment upload changes, from raw text to the tracked row's sentence. By
    Verifier 2's reading that field is not served today: `session_results` serves it only for sessions with no tracked
    tasks, which this upload never makes.
- **Production files:** `assignments/tasks.py` only. The seam check proves it by the syntax tree.
- **Docs:** the new H-219 row (after H-221's), and `docs/backend/BACKEND_REFERENCE.md`'s notification passage.
- **Existing tests:** none changed. A grep for each log text and for `assertLogs("assignments.tasks"` finds only
  `students/tests_h38_n3_refusal_audit.py` (430-481), which covers `:583` and `:611`. `tests_upload_task_retry_policy`
  263 reads only the failed upload's status. `tests_prerender`'s failure test asserts only the result.
- **The test's marker is named `MARKER`, not `SECRET`:** detect-secrets flagged the first name as a keyword. The value
  is a made-up text, not a credential.
- **Not shown:** a real broker outage (the send is replaced so that it raises); Sentry itself.

## Expected reds against b4af529f (written before any run)

The red run takes `assignments/tasks.py` back to the base and runs the new module as at the tip.

```
assignments.tests_h219_task_error_text (6 red, 1 green)
  S1 test_a_failed_upload_stores_the_tracked_rows_sentence_not_the_text   "unexpectedly found in"
  S2 test_a_failed_batch_session_is_logged_by_class_and_id                "unexpectedly found in"
  S3 test_a_failed_teacher_reminder_is_logged_by_class_only               "is not None"
  S4 test_a_failed_student_reminder_is_logged_by_class_only               "is not None"
  S5 test_a_failed_new_assignment_notice_is_logged_by_class_only          "is not None"
  S6 test_a_failed_pre_render_is_logged_by_class_only                     "is not None"
  GREEN (control): K1 test_a_reminder_that_sends_logs_no_error
Total: 6 FAIL blocks, no ERROR; "Ran 7 tests". The script counts them.
```

- **S3-S6:** `assert_class_only` checks `exc_info` first. At the base `logger.exception` sets it, so the first check
  fails with "(…) is not None".
- **One red traced by hand: S1 at the base.**
  1. `upload_assignment_file` (as imported into `assignments.tasks`) is replaced to raise `RuntimeError(MARKER)`. The
     payload is real enough for `rebuild_uploaded_file` and `sha256_of_upload_payload`, which run before the call.
  2. `except Exception as e` calls `mark_processing_task_failure`, whose tracked error is
     `describe_background_task_error(e, fallback)`. A `RuntimeError` is not whitelisted, so that is the fallback
     sentence.
  3. Then `update_result(file_name, "FAILED", error=str(e))` stores MARKER.
  4. `assertNotIn(MARKER, entry["error"])` fails: "'h219-made-up-error-text' unexpectedly found in …".
  5. After the change the entry holds the same fallback sentence as the tracked row, so the next check (equal) holds.
- **S2 traced by hand.**
  1. `Assignment.objects.get` is replaced to raise; the user id and assignment id are random, so the H-38 refusal is
     skipped.
  2. The `try` fails on the first line, and the `except` logs line 1477.
  3. After it: no assignment, so no rubric refusal; no submissions, so nothing is dispatched.
  4. At the base the line holds MARKER; after the change it holds the id and "error=RuntimeError frames=…".
- **S6 traced by hand.** `render_assignment_pdf` raises inside `get_or_render`, which re-raises the original
  (`assignments/pdf_cache.py`), so each view's `except Exception` logs one line. The result is
  "Pre-rendered: nothing (already cached)".
- **A control traced by hand: K1.** The send is replaced and succeeds: one call, no ERROR record, "Queued 1 assignment
  due reminder emails.". The same at the base.

## Mutants (expected sets traced against every assertion of the three battery modules)

The battery is the new module, `assignments.tests_prerender` and `assignments.tests_task_results_carry_no_text`.
- `tests_prerender`'s failure test asserts only the result.
- H-209's module drives only the tasks' outer handlers (a patched `Assignment` that raises), and its source checks look
  for `print` and traceback building, which no mutant adds.
- So neither reaches these sites.

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| W1 | the upload's stored error back to `str(e)` | S1 (`unexpectedly found in`) |
| W2 | the batch line carries the text again | S2 (`unexpectedly found in`) |
| W3 | the teacher reminder logged with `logger.exception` | S3 (`is not None`) |
| W4 | the student reminder, the same | S4 (`is not None`) |
| W5 | the new-assignment notice, the same | S5 (`is not None`) |
| W6 | the pre-render failure, the same | S6 (`is not None`) |
| W7 | the teacher reminder line loses the error's class | S3 (`'RuntimeError' not found in`) |

- **W7:** `exc_info`, the text check and the level pass; the class check fails.

## The seam

`seam_check.py` compares the base and tip syntax trees of `assignments/tasks.py`:
- everything outside the five task functions must match, and so must their decorators and arguments;
- each of the five must have changed;
- at the tip none holds `logger.exception`, an f-string log call, or `str(...)` stored as a FAILED `update_result`
  error. A CANCELLED one keeps the cancellation's own fixed text.

Before the pack (by parsing source only, no test run), it passes on the tip. It stops on four bad inputs:
- the base against itself;
- an edit outside the five functions;
- `logger.exception` put back in one site;
- `str(e)` put back in the stored error.

## Gate

- **Script:** `run_h219_gate.sh.txt`, the H-210 gate's form. The red run expects 6 blocks and "Ran 7 tests". The exit
  code is captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only `assignments/tasks.py` outside `docs/` and the
  test file), then the seam check.
- **Own:** the new module.
- **Near:** `assignments.tests_prerender`, `assignments.tests_task_results_carry_no_text`,
  `assignments.tests_upload_task_retry_policy`, `assignments.tests_upload_pipeline`, `assignments.tests` (the
  due-reminder tests), `students.tests_h38_n3_refusal_audit`, `students.tests_h38_tasks_namespace`,
  `students.tests_broker_text_not_logged`, `students.tests_broker_outage`, and the Senior Manager's nine (below).
- **The Senior Manager's nine (added 25 October 2026, from a search for every test module that names a changed
  function).** Each was read against the five changed functions. None depends on the old text, so none was changed:

  | Module | What it reaches | Depends on the old text? |
  |---|---|---|
  | `assignments.tests_security` | `upload_assignment_async` is patched out in the view (399, 415, 535); the task body never runs | No |
  | `billing.tests.test_h38_part2_removed_teacher_routes` | a view test (294) that expects the H-38 guard's 404 before the task is queued | No |
  | `billing.tests.test_refusal_handling` | D6 (465-495) reads `grade_engine_async`'s tracked entries, not a changed site | No |
  | `classrooms.tests_teacher_access_sweep` | a source check (359) that `grade_batch_async` still calls `teacher_may_reach`; it still does (`tasks.py:1453`, outside the changed line) | No |
  | `students.tests_batch_item_results` | `grade_batch_async`'s normal path (449); its unclassified failure (289) is `upload_answers_engine_async`'s | No |
  | `students.tests_h215a_auto_grade_resilience` | `grade_batch_async`'s normal path (260, 302); the raising `Assignment` stand-in (310-345, 491) is used only with `auto_grade_due_assignment` | No |
  | `students.tests_s6d_rubric_gate` | the rubric refusal's entry (274-299), written by `_refuse_batch_without_rubric`, which is unchanged | No |
  | `assignments.tests_upload_batch_scale` | posts to the plain `upload` route (`views.py:865`), which does not queue `upload_assignment_async`; its errors come from that route | No |
  | `assignments.tests_upload_batch_billing` | the same route; `UNREADABLE_PDF_ERROR` and `IN_PROGRESS_MESSAGE` come from it, not from the task | No |

  - So the nine are regression cover around the changed functions, not tests of the changed lines. The new module is
    what covers those.
- **Guards:** the 21.
- **The mutants:** 7, with a 1230 s limit and the 300 s stall watchdog.
- **Base:** `b4af529f`.
