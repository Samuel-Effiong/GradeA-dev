# H-218 notes

- **The row** (new in this pack): H-209's evidence (`docs/evidence/h209-task-results-no-text/EVIDENCE.md` line 36):
  "Celery's own failure log (`celery.app.trace`) carries the exception repr and traceback when `grade_all_submissions`
  re-raises: row H-218 (LOW, design first). Not cured here." Verifier 2 saw it red with probe `test_f1`.
- **The design** came first, as the row says. It is copied here as `DESIGN_NOTE.md`, and the Senior Manager approved
  it on 25 October 2026.
- **Versions read:** Celery 5.5.3, Python 3.12.10, sentry-sdk 2.68.0.

## What Celery logs, and where it goes

- **A failure:** `celery/app/trace.py` `_log_error` (lines 242-269) calls
  `logger.log(policy.severity, "Task %(name)s[%(id)s] %(description)s: %(exc)s", context,
  exc_info=..., extra={'data': context})`.
  - `context` holds `exc` (the error's repr), `traceback` (its text), `args` and `kwargs` (the task's arguments),
    `hostname`, `id`, `name`, `description` and `internal`.
  - "raised unexpected" is ERROR with `exc_info`.
- **A retry:** `handle_retry` logs `"Task %(name)s[%(id)s] retry: %(exc)s"` at INFO. Here `exc` is `str(Retry)`, which
  is "Retry in 30s: RuntimeError('…')": the repr of the error passed to `self.retry(exc=...)`.
- **The eager path** (`task.apply()`) builds the same tracer and calls the same functions.
- **The console:** in a worker, through the root handler Celery installs. In a web process, through Python's
  last-resort handler.
- **Sentry, by reading the code (not seen; nothing here claims anything about Sentry's servers):**
  - sentry-sdk's `CeleryIntegration` calls `ignore_logger("celery.app.trace")`, which keeps the record out of events
    and breadcrumbs.
  - It does not add the name to `_IGNORED_LOGGERS_SENTRY_LOGS`, and `enable_logs=True` (`settings.py:210`).
  - So the Sentry Logs handler would receive the record. It copies each key of the message's argument dict as a
    `sentry.message.parameter.<key>` attribute, and the record's extra attributes (`data`) as well.
  - `scrub_log` (H-89) removes email addresses only.
  - Sentry's hook wraps `Logger.callHandlers`, which Python 3.12 calls after the logger's filters (`Logger.handle`).
- **What T2 shows:** the whole record after the filter, `repr(record.__dict__)`, as every handler on the logger
  receives it. That is its message, its argument dict, `data` and `exc_info`. T2 does not run Sentry.

## The change

- **`AutoGrader/safe_logging.py`:** a new class, `CeleryTraceFilter`, and two constants (`_CELERY_TRACE_KEPT`,
  `RETRY_TEXT_OMITTED`); `import logging` is added. Nothing else in the file changes.
  - It acts only on records whose argument dict has `exc`; any other record passes untouched.
  - The new dict keeps `hostname`, `id`, `name`, `description` and `internal`.
  - `exc` becomes `describe_error_for_log(error)` when `exc_info` holds the error, or "error text omitted" when only
    the text is there (a retry line).
  - `traceback`, `args` and `kwargs` are left out. `record.args` and `record.data` become the new dict, and
    `exc_info` and `exc_text` become None.
  - It returns True, so the line, its level, the task's name and id stay.
  - **Never raises:** `describe_error_for_log` is called inside `try … except Exception`, which falls back to
    `error=<Class>`.
- **`AutoGrader/celery.py`:** `import logging`, the import of the class, and
  `logging.getLogger("celery.app.trace").addFilter(CeleryTraceFilter())`, guarded against a second copy.
  - Every process imports it through `AutoGrader/__init__.py`, and prefork children inherit it.
  - Celery's own logging setup clears handlers, never filters, and never on this logger (`celery/app/log.py:104-110`).
- **One logger only** (the Senior Manager's stop condition, checked): a task's own exception is logged only by
  `celery.app.trace`.
- **The Senior Manager's rulings (25 October 2026):** take the retry line; leave out `args`, `kwargs` and
  `traceback`; do not take `celery.worker.request`; keep the never-raise rule and test it.

## Not cured (named, left alone)

- **The Redis result backend's copy** of the exception (`CELERY_RESULT_EXPIRES = 3600`). Not served: the status route
  serves only the tracked row, and the other reader takes `.state`.
- **Sentry's own Celery event** (`CeleryIntegration`): scrubbed by `before_send`, no locals (H-167).
- **`celery.worker.request`'s "Task handler raised error: %r"** (`request.py:643`). It fires only for a lost worker
  process or a time limit, which are Celery's own errors. Taking it would be a second logger.
- **`report_internal_error`'s `warnings.warn`** (`trace.py:658`): an error outside the task body, Celery's own.
- **"Process cleanup failed: %r"** (`trace.py:564`, worker only): its argument is not a dict, so the filter passes it.
  It is the backend's or Django's cleanup error, not the task's.
- **The `task-failed` event's traceback:** sent only when worker events are on, and nothing in settings turns them on.
- **A real worker process's logging setup:** shown by reading `celery/app/log.py`, not by running a worker process.
  `students.tests_grading_redelivery_live` runs Celery's in-process test worker with the filter installed, but asserts
  nothing about logs.

## The tests (`AutoGrader/tests_h218_celery_trace_filter.py`)

- **Capture:** `TraceRecords`, a handler on `celery.app.trace` itself, receives each record after the logger's
  filters, whole.
  - It lowers the logger to INFO for the block, because retry and success lines are INFO and the logger inherits
    WARNING. Eager `apply()` builds its tracer inside the block, so the tracer's success-logging switch reads INFO.
- **Why T1's capture differs from Verifier 2's:** their `Capture` patches `Logger.handle` and records before calling
  the original. A logger's filters run inside the original, so as written it reads the record before any filter.
  - T1 keeps `test_f1`'s name, arrange (`grade_engine` raising, `update_state` patched, `grade_all_submissions.apply`),
    act and assertions (`result.failed()`, "premise: Celery logged the failure", MARKER not in message + traceback
    text).
  - It reads the records from `TraceRecords` instead.
- **The class is not imported at module level**, so at the base each test fails for its own reason.

| Id | Test | What it holds |
|---|---|---|
| T1 | `test_f1_celerys_own_failure_log_for_a_failed_grade_all_carries_no_text` | Verifier 2's probe, adopted |
| T2 | `test_the_whole_record_holds_no_text_no_traceback_and_no_arguments` | a failed `upload_assignment_async` with `prompt_text` = PROMPT_MARKER: neither marker nor "Traceback" in `repr(record.__dict__)`, no `kwargs` key |
| T3 | `test_a_retry_line_holds_no_text` | the email task's three eager retry lines: no MARKER, "error text omitted" |
| T4 | `test_the_failure_line_keeps_its_level_name_id_and_the_errors_class` | `error=RuntimeError`, ERROR, the task's name, its id |
| T5 | `test_a_describe_failure_still_strips_and_keeps_the_line` | `describe_error_for_log` patched to raise: the line still comes, without MARKER and `exc_info`, with `error=RuntimeError`, at ERROR, and the task still failed |
| K1 | `test_a_record_on_another_logger_is_untouched` | control: another logger's dict with `exc` keeps its text |
| K2 | `test_a_successful_send_line_is_unchanged` | control: a success line (no `exc`) keeps its return value |

## Expected reds against b4af529f (written before any run)

The red run takes `AutoGrader/safe_logging.py` and `AutoGrader/celery.py` back to the base, and runs the new module as
at the tip.

```
AutoGrader.tests_h218_celery_trace_filter (5 red, 2 green)
  T1 test_f1_celerys_own_failure_log_for_a_failed_grade_all_carries_no_text   "the made-up error text reached celery's own failure log"
  T2 test_the_whole_record_holds_no_text_no_traceback_and_no_arguments        "unexpectedly found in"
  T3 test_a_retry_line_holds_no_text                                          "unexpectedly found in"
  T4 test_the_failure_line_keeps_its_level_name_id_and_the_errors_class       "'error=RuntimeError' not found in"
  T5 test_a_describe_failure_still_strips_and_keeps_the_line                  "unexpectedly found in"
  GREEN (controls): K1 test_a_record_on_another_logger_is_untouched, K2 test_a_successful_send_line_is_unchanged
Total: 5 FAIL blocks, no ERROR; "Ran 7 tests". The script counts them.
```

- **T1, T2, T5 at the base:** the line holds `RuntimeError('h218-made-up-error-text')`.
- **T4 at the base:** the line holds the repr, not `error=RuntimeError`. The unpack finds exactly one failure line.
- **T5 at the base:** patching `AutoGrader.safe_logging.describe_error_for_log` does not change the task.
  `assignments.tasks` holds its own reference, and nothing at the base calls the module's.
- **One red traced by hand: T3 at the base.**
  1. `AutoGrader.tasks._send_email_impl` is replaced to raise `RuntimeError(MARKER)`.
  2. `send_email_task.apply(kwargs=…)` runs the eager tracer.
  3. The body's `except` calls `self.retry(exc=exc, countdown=30)`:
     - `request.retries + 1 = 1`, which is not above `max_retries=3`;
     - so `Retry(exc=exc, when=30, is_eager=True)` is raised (`throw=True`).
  4. The tracer's `except Retry` calls `handle_retry`, which logs
     `info(LOG_RETRY, {'id', 'name', 'exc': "Retry in 30s: RuntimeError('h218-made-up-error-text')"})`. That is INFO,
     which the lowered logger lets through.
  5. `apply()` then calls `retval.sig.apply(retries=1)`, then 2 and 3. That makes three retry lines. The fourth attempt
     (`retries + 1 = 4 > 3`) raises the error itself, which is the failure line.
  6. `len(retries) == 3` holds. At the base, `repr(record.__dict__)` of the first retry line holds MARKER in its
     argument dict, so `assertNotIn` fails: "'h218-made-up-error-text' unexpectedly found in …".
  7. After the change the dict's `exc` is "error text omitted" (no `exc_info` on a retry line), and both checks hold.
- **A control traced by hand: K2, at the base and at the tip.**
  1. `_send_email_impl` returns "h218-sent", so the body returns it.
  2. The eager tracer was built while the logger was at INFO, so its success-logging switch is on. It logs
     `info(LOG_SUCCESS, {'id', 'name', 'return_value': "'h218-sent'", 'runtime': …})`.
  3. That dict has no `exc`, so the filter returns True before touching it.
  4. The message "Task AutoGrader.tasks.send_email_task[<id>] succeeded in …s: 'h218-sent'" holds `'h218-sent'`, at
     INFO. Green at both.

## Mutants (expected sets traced against every assertion of the three battery modules)

The battery is the new module, `students.tests_broker_text_not_logged` (H-208) and
`assignments.tests_task_results_carry_no_text` (H-209).
- H-208's capture keeps records by its own logger names.
- H-209's grade-all tests (the only `apply()` there) read stdout and the stored meta, not the log.
- So no mutant reaches either module.
- K1 and K2 pass under every mutant.

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| X1 | the filter not installed | T1 (F1), T2, T3, T5 (`unexpectedly found in`), T4 (`'error=RuntimeError' not found in`) |
| X2 | `exc` kept as the repr | T1 (F1), T2, T5 (`unexpectedly found in`), T4 (`'error=RuntimeError' not found in`) |
| X3 | `exc_info` kept | T1 (F1, the traceback text), T2 (`unexpectedly found in`), T5 (`is not None`) |
| X4 | `traceback` kept in the dict | T2 (`unexpectedly found in`, MARKER in the traceback's last line) |
| X5 | `args` and `kwargs` kept | T2 (`unexpectedly found in`, PROMPT_MARKER, the second check) |
| X6 | installed on `celery`, the parent | as X1: a parent logger's filter does not see a child's records |
| X7 | the retry line keeps its text | T3 (`unexpectedly found in`) |
| X8 | the filter returns False (the line disappears) | T1 (`premise: Celery logged the failure`), T2, T4, T5 (`not enough values to unpack`), T3 (`0 != 3`) |
| X9 | the never-raise `try` removed | T5 (`h218-describe-broke`: the patched error comes out of the eager tracer, `trace.py` `if eager: raise`, so T5 is an ERROR) |

- **F1** is "the made-up error text reached celery's own failure log".
- `mutate.py --check` (by parsing only, no test run): "9 mutants: every anchor found once, all parse".

## The seam

`seam_check.py` compares the base and tip syntax trees of both files:
- every base top-level statement must appear unchanged and in order at the tip;
- the added statements must be exactly:
  - `import logging`, `_CELERY_TRACE_KEPT`, `RETRY_TEXT_OMITTED` and `CeleryTraceFilter` in `safe_logging.py`;
  - `import logging`, the class's import, `_trace_logger` and the guarded install in `celery.py`;
- the filter returns only True, raises nothing, and calls `describe_error_for_log` only inside a `try` with
  `except Exception`;
- the install is on `celery.app.trace`.

Before the pack (by parsing source only, no test run), it passes on the tip. It stops on six bad inputs:
- the base against itself;
- an added import;
- an edit inside `describe_error_for_log`;
- `return False`;
- the `try` removed;
- the install on `celery`.

## Gate

- **Script:** `run_h218_gate.sh.txt`, the H-219 gate's form. The red run expects 5 blocks and "Ran 7 tests". The exit
  code is captured by the start command.
- **Before the test runs:** makemigrations, the changed-file list (only `AutoGrader/celery.py` and
  `AutoGrader/safe_logging.py` outside `docs/` and the test file), then the seam check.
- **Own:** the new module.
- **Near:** the Senior Manager's 15 (from a search of the test modules that touch the Celery trace logger,
  `safe_logging`, `AutoGrader.celery`, `task_failure` or the logging setup), plus `students.tests_grading_redelivery_live`.
  - `AutoGrader.tests_celery_signals`, `AutoGrader.test_broker`, `AutoGrader.tests_codederror_serialization`
  - `assignments.tests_file_reason_codes`, `assignments.tests_task_results_carry_no_text`,
    `assignments.tests_upload_task_retry_policy`
  - `billing.tests.test_refusal_handling`, `billing.tests.test_refusal_handling_gates`
  - `students.tests_app_boundaries`, `students.tests_async_edit_path`, `students.tests_batch_item_results`,
    `students.tests_broker_text_not_logged`, `students.tests_grading_idempotency`,
    `students.tests_identity_reason_codes`, `students.tests_task_tracking`
  - `students.tests_grading_redelivery_live`: it needs the local Redis broker and Celery's in-process test worker on a
    queue made for the run (no skip switch). It is a `TransactionTestCase` that waits up to 45 s per condition.
- **Step 1 limit: 1800 s** (rule 12's most for a targeted run). H-219's step 1 (632 tests) took 315 s, which leaves
  room for the live test's waits.
- **Guards:** the 21, including `tests_log_scrubbing`, `tests_no_pii_in_logs` and `tests_sentry_scrubbing`.
- **The mutants:** 9, with a 1410 s limit (90 s × 9 + 600) and the 300 s stall watchdog.
- **Base:** `b4af529f`.
- **The batch:** H-227, H-219 and this row each add a backlog row after H-221's. The batch build resolves that by
  keeping all three rows.
