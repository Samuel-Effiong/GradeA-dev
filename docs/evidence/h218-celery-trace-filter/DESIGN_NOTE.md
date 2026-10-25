# H-218: Celery's own failure log carries the error's text and traceback (written design)

Builder a0, 25 October 2026. Read at beta `b4af529f` and in the installed packages. Files only: no worktree yet, no
runs. The Senior Manager's rulings for this row (item 5): design first; read Celery's trace call and pin the version;
write the not-cured list; stop and say so if more than the one trace logger is needed.

## Versions read

- Celery **5.5.3** (`celery/app/trace.py`, `celery/worker/request.py`, `celery/app/log.py`, `celery/exceptions.py`)
- Python **3.12.10** (`logging.Logger.handle`)
- sentry-sdk **2.68.0** (`integrations/logging.py`, `integrations/celery/__init__.py`)

## What Celery logs today, and where it goes

**The call.** `celery/app/trace.py` has one module logger, `logger = get_logger(__name__)`, so `celery.app.trace`
(`get_logger` re-parents it under `celery`; it is the same object as `logging.getLogger("celery.app.trace")`).
- **A failure** (`TraceInfo.handle_failure` → `_log_error`, lines 242-269):
  ```python
  context = {'hostname', 'id', 'name', 'exc': safe_repr(eobj), 'traceback': safe_str(einfo.traceback),
             'args': argsrepr or safe_repr(req.args), 'kwargs': kwargsrepr or safe_repr(req.kwargs),
             'description': policy.description, 'internal': einfo.internal}
  logger.log(policy.severity, policy.format.strip(), context,
             exc_info=exc_info if policy.traceback else None, extra={'data': context})
  ```
  - The format is `"Task %(name)s[%(id)s] %(description)s: %(exc)s"` (LOG_FAILURE and LOG_INTERNAL_ERROR), or
    `"Task %(name)s[%(id)s] %(exc)s"` for a rejection.
  - The policies: "raised unexpected" ERROR with the traceback; "raised expected" INFO without; "INTERNAL ERROR"
    CRITICAL with; rejected WARN with.
  - So one record carries the error's repr in the message, the traceback through `exc_info`, and a second copy of
    the traceback text plus the task's arguments in `record.args` and `record.data` (the same dict).
- **A retry** (`handle_retry`, line 203): `info(LOG_RETRY, {'id', 'name', 'exc': str(reason)})`, at INFO, with the
  same dict as `extra={'data': ...}`.
  - `Retry.__str__` is `f'Retry {humanize}: {self.excs}'`, and `excs` is the repr of the error passed in.
  - Our retries pass the error in: `AutoGrader/tasks.py:118` (email send), `assignments/tasks.py:510`, `:1162`,
    `:2030`. So a retry line repeats the error's text.
- **Ignored and success lines** carry no error. A success line carries the return value, which is ours (H-209 made
  those class-only).
- The eager path (`task.apply()`, as the tests use) builds the same tracer and calls the same `_log_error`. That is
  how Verifier 2's `test_f1` saw it red.

**Where the record goes.**
- **Console:** the project's `LOGGING` (`settings.py:31`, `disable_existing_loggers: False`) does not configure
  `celery*` or the root logger.
  - In a worker, Celery's own setup puts its handler on the root logger (`celery/app/log.py:104-110`), and the
    record reaches it by propagation.
  - In a web process, Python's last-resort handler prints WARNING and above to stderr.
- **Sentry events:** sentry-sdk's `CeleryIntegration` calls `ignore_logger("celery.app.trace")`, so no event or
  breadcrumb is made from this record. It reports the exception itself instead (see "not cured").
- **Sentry Logs:** these ARE made from it. `enable_logs=True` (`settings.py:210`), and `ignore_logger` does not add
  the name to `_IGNORED_LOGGERS_SENTRY_LOGS`.
  - The Logs handler copies every key of the message's argument dict as `sentry.message.parameter.<key>`: `exc`,
    `traceback`, `args`, `kwargs`.
  - It also copies the record's extra attributes, so `data` repeats them.
  - `scrub_log` (H-89) takes out email addresses only.
  - So in production a failed task's text, traceback and arguments plausibly reach Sentry Logs today. This is from
    reading the code, not seen.
- Sentry's hook wraps `Logger.callHandlers`, which runs after the logger's own filters (`Logger.handle`, Python 3.12:
  `maybe_record = self.filter(record)`, then `self.callHandlers(record)`). So one filter on this logger reaches the
  console and Sentry Logs alike.

## The change

### 1. `AutoGrader/safe_logging.py`: one new class, `CeleryTraceFilter(logging.Filter)`

Nothing else in the file changes; `describe_error_for_log` is reused, not edited.
- **It acts only on Celery's trace records:** those whose `record.args` is a dict holding the key `exc`. Every other
  record passes untouched.
- **It builds a new dict** with `hostname`, `id`, `name`, `description` and `internal` kept, and:
  - `exc`: `describe_error_for_log(error)` when `record.exc_info` holds the error (the failure lines);
  - otherwise (the retry line, which carries only the text) the fixed `"error text omitted"`;
  - `traceback`, `args` and `kwargs` are left out.
- **It sets** `record.args` and `record.data` to the new dict, and `record.exc_info = None`, `record.exc_text = None`.
- **It keeps the record** (`return True`). The level, the format, the task name and id, and "raised unexpected" stay,
  so a failure is still an ERROR line in the worker log.
- **It never raises.** A filter's exception would come out of Celery's tracer. If describing fails, `exc` becomes
  `f"error={type(error).__name__}"`, and the strip still happens.
- `describe_error_for_log` imports `AutoGrader.dispatch` at call time, by which point Django is set up in every
  process that runs a task.

### 2. `AutoGrader/celery.py`: install it once

- `logging.getLogger("celery.app.trace").addFilter(CeleryTraceFilter())`, after `app = Celery(...)`, guarded so a
  re-import adds no second copy.
- Every process imports this module through `AutoGrader/__init__.py`, tests included.
- A prefork worker's children are forked after the import, so they inherit the filter.
- Celery's own logging setup clears handlers on the root, `celery`, `celery.task` and `celery.redirected` loggers.
  It never removes filters, and never touches `celery.app.trace` (`celery/app/log.py:104-110`).
- **One logger only.** The row's case, a task raising, is logged by `celery.app.trace` alone (see the next section).

### The one-logger check (the Senior Manager's stop condition)

- `celery/worker/request.py:643` logs `"Task handler raised error: %r"` with the traceback on `celery.worker.request`.
- It fires only when the job did not return normally (`return_ok` false): the worker process lost, or a time limit.
  Those errors are Celery's own (`WorkerLostError`, `TimeLimitExceeded`), with Celery's own text.
- A task's own exception never reaches it: the pool child's tracer has already handled and logged it.
- So the cure for this row needs only the one logger, and **no stop is needed**.
- That line is put on the not-cured list, not taken. Taking it would be a second logger, which is the Senior Manager's
  call.

## Not cured (named, left alone)

- **The Redis result backend's copy.** `mark_as_failure` stores the exception's type, text and traceback for an hour
  (`CELERY_RESULT_EXPIRES = 3600`). Nothing serves it: the status route serves only the tracked row, and the other
  reader takes `.state` only (`students/task_tracking.py:477`).
- **Sentry's own Celery event.** `CeleryIntegration` reports the raised exception, with its text, as an event. It is
  scrubbed by `before_send` (addresses), frames have no locals (H-167), and there is no request body.
- **`celery.worker.request`'s "Task handler raised error"** (above): Celery's own errors, a second logger.
- **`report_internal_error`'s `warnings.warn`** (`trace.py:658`): an error raised outside the task body, Celery's own.
- **The `task-failed` event's traceback** (`request.py:635`): sent only when worker events are on. Nothing in
  settings turns them on; the worker command lives in Railway's dashboard, not the repo.
- **A real worker's console output.** Shown by reading `celery/app/log.py` only, not by a run of a worker process.
  The tests use the eager tracer, which calls the same `_log_error`.

## Tests (new module `AutoGrader/tests_h218_celery_trace_filter.py`)

- **Rule 22 mechanics:** no module-level import of the new class, so at the base each test fails for its own written
  reason instead of the whole module erroring on import.
- **Capture:** a handler added to the root logger for the test (it receives records after the logger filter, as the
  console and Sentry Logs do). It keeps each `celery.app.trace` record whole, so `record.args`, `record.data` and
  `exc_info` can be read.

| Id | Test | Red at the base for |
|---|---|---|
| T1 | **Verifier 2's `test_f1`, adopted:** `grade_all_submissions.apply(...)` with `grade_engine` raising `RuntimeError(SECRET)`; the arrange, act and assertions are kept, and only the capture changes (below) | "the made-up error text reached celery's own failure log" |
| T2 | a failed `upload_assignment_async` whose `prompt_text` carries a second marker: neither marker, nor "Traceback", in the whole record (`repr(record.__dict__)`, which holds `args` and `data`, what Sentry Logs copies) | "unexpectedly found in" |
| T3 | the email task's retry (`AutoGrader/tasks.py:118`, the send raising `RuntimeError(MARKER)`): the retry line holds no marker and reads "error text omitted" | "unexpectedly found in" |
| T4 | the failure line keeps its level and its use: ERROR, "raised unexpected", the task's name and id, and `error=RuntimeError` | "'error=RuntimeError' not found in" |
| K1 | control: a record on another logger with a dict argument holding `exc` is untouched | green at the base too |
| K2 | control: a successful task's line is unchanged | green at the base too |

- **Why T1's capture changes:** Verifier 2's `Capture` patches `Logger.handle` and records BEFORE calling the
  original, while a logger's filters run inside the original. So as written, `test_f1` reads the record before any
  logger filter and stays red even when the fix works.
  - The adopted test keeps `test_f1`'s name, arrange, act and assertions (`result.failed()`, "premise: Celery logged
    the failure", MARKER not in msg + exc_text), and reads the records from the handler instead.
  - Rule 19: its red at the base is shown by the red run, and its kill by mutant X1.
- **T4's red at the base:** the base line holds the repr `RuntimeError('…')`, not `error=RuntimeError`.

**Mutants** (each expected set to be traced against every assertion before the pack):
- X1 the filter not installed
- X2 `exc` kept as the repr
- X3 `exc_info` kept
- X4 `traceback` kept in the dict
- X5 `args`/`kwargs` kept
- X6 installed on `celery` instead of `celery.app.trace` (a parent logger's filter does not see a child's records)
- X7 the retry branch dropped
- X8 the filter returns False (the line disappears)

## Seam, near list, gate

- **Production files:** `AutoGrader/safe_logging.py` (one new class; the rest identical by syntax tree) and
  `AutoGrader/celery.py` (one import and the install). The seam check proves it.
  - Nothing outside those two, so the Senior Manager's search needs no widening.
- **Near:** the Senior Manager's list of 15, plus `students.tests_grading_redelivery_live`.
  - It needs the local Redis broker and Celery's in-process test worker on a queue made for the run (no skip switch),
    so it can run in the gate.
  - Its test 2 makes a task fail inside a real worker thread, so it is the one existing test where Celery's real
    failure path runs with the filter installed.
  - It is a `TransactionTestCase` that can wait up to 45 s per step, so it is slow. It is kept, not dropped.
- **Guards:** the 21, including `tests_log_scrubbing`, `tests_no_pii_in_logs` and `tests_sentry_scrubbing`.

## For the Senior Manager to rule

1. **The retry line (T3, X7).** It is the same logger and the same `exc` key, and our retries pass errors whose text
   H-208 keeps out of logs (a broker error's address). I recommend taking it.
2. **`args` and `kwargs` left out of the dict.** No format uses them; only Sentry Logs and the `data` extra read them.
   `upload_assignment_async`'s kwargs hold the teacher's prompt text and the file's bytes. I recommend leaving them
   out.
3. **`celery.worker.request`'s line:** not taken (a second logger; Celery's own errors). Confirm.
