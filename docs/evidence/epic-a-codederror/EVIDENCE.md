# Epic A: a coded failure survives serialization

**Branch:** `task/epic-a-codederror-pickle`, off the epic tip `b2890d9`. **Author:** Hardening (d5). **Verifier:** v2. The SM made it a small, non-urgent slice of its own (2026-09-30) that lands before S6d, which needs it.

## The defect

`CodedError` (S6a, `AutoGrader/reason_codes.py`) rendered its message into `Exception.args` and took `params`, `detail` and `display` keyword-only. Anything that rebuilds an exception from its args therefore failed:

- **Celery's json result backend (production's `CELERY_RESULT_SERIALIZER`).** Celery 5.5.3 stores a failed task's exception as `{exc_type, exc_module, exc_message: exc.args}` and rebuilds it with `cls(*args)`. `cls(<message>)` raised, and Celery **silently substituted a plain `Exception`**, so the class and the reason code were lost.
- **Pickle (eager tasks, tests).** `cls(<message>)` failed the same way, and Celery stored an `UnpickleableExceptionWrapper`.

Found while building S6d, whose RUBRIC_MISSING is the first coded error to leave a task (`grade_engine_async` re-raises it). v2 reproduced it independently on `b2890d9`: all 7 coded classes fail under both serializers.

## Affected staging routes: none

No user-facing route loses a reason code on staging today:

- Production serializes results as **json** (above), so the fix had to cover `cls(*args)`, not only pickle.
- **The status route** (`TaskViewSet.task_status`) serves the tracked task row. Its error text is written by the worker from the **live** exception (`mark_processing_task_failure` → `describe_task_error`), before any serialization. It carries no reason code before or after this slice; per-item codes are S7a's contract. The route's old bare `AsyncResult` fallback was removed as a security fix.
- **Upload refusals are returned as dicts**: the upload tasks catch `UPLOAD_REFUSALS` and return, so S6b/S6c's coded refusals never reach the result backend.
- **Only `.state` is read** from the result backend (`students/task_tracking.py`, the stale-status check).

So the fix rides the next normal staging refresh, with no hotfix (the SM's ruling).

## The fix

`CodedError.args` is now `(reason_code, params, None, display)`, and the constructor takes those four positionally as well as by keyword (existing keyword calls are unchanged). So:

- `cls(*args)` (Celery json) rebuilds the same class, code, params and message, including a `display` override (for example "460 bytes", not the raw int);
- pickle's default reduce (`cls(*args)` then `__dict__`) does the same, and keeps the log-only `detail`;
- `__str__` returns the rendered message, as before, for plain and DRF-derived classes alike.

`detail` is deliberately **not** in `args`, so it never goes to the result backend under json.

## Personal data in the stored form (the SM's conditions)

- **Nothing new is stored.** Every param is a placeholder already rendered into the stored message, except `FILE_TOO_LARGE`'s `dimension`, which names a unit. The log-only `detail` is not stored under json.
- **No param is an email or an id:** a test asserts that over every spec.
- **Stored results expire:** `CELERY_RESULT_EXPIRES = 3600` (1 hour), and a test pins it at a day or less.

## Tests (`AutoGrader/tests_codederror_serialization.py`)

Every `CodedError` subclass is found by reflection (so a new one is covered without being listed), plus `DefinedOnlyHere`, a subclass that exists only in the test module:

- pickle at every protocol, and copy/deepcopy: same class, code, params, message, status and coded body, with `__dict__` (and so `detail`) restored;
- Celery's own `prepare_exception` → a real json wire trip → `exception_to_python`, under json and pickle;
- a `display` override survives json;
- a real Celery task that fails with one returns the real class, code and params;
- the personal-data and expiry checks above.

## Runs (every run under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, with `EXEMPT_EMAIL_DOMAINS` empty)

| Run | Tree | Result | Log |
|---|---|---|---|
| **Reproduce-first:** the new module on the unchanged base (a disposable worktree with only the test file added, its own DB, dropped after) | `b2890d9` | **8 tests: 18 failures and 48 errors** across the per-class subtests: pickle and copy, Celery json and pickle, the display override, and the real task. The three checks that don't depend on the defect pass: reflection, no email/id params, and expiry | `repro_b2890d9.log` |
| The fix: the new module, S6a's own suite (`AutoGrader.tests_reason_codes`, since the constructor changed), and every module that raises coded errors (`AutoGrader.tests_uploads`, `assignments.tests_file_reason_codes`, `students.tests_proxy_upload_attribution`, `audit.tests_trace_server_owned`) | the fix | **84 ran, OK** | `fix_modules.log` |

v2's own probe (`GAP-v2-handover/tests_vf2_codederror_pickle_probe.py`, log `runs/codederror_repro.log`) reproduced the defect independently on `b2890d9`. It is v2's, kept out of the branch so that verification stays independent.


## Noted, not changed

For the classes that mix in a DRF exception (`FileUnreadableError`, `FileTypeUnsupportedError`, `SubmissionEmptyError`, `FileTooLargeError`, `PayloadTooLarge`), DRF's own `__init__` overwrites `detail` with the display message, so their log-only `detail` has never been kept. That predates this slice (S6a/S6b) and doesn't affect any response; it is recorded for S7a's contract work.
