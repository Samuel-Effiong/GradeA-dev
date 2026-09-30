# Verification: the CodedError serialization slice @ b5cdad2

**Verifier:** Verification Engineer 2 (v2). **Author:** Hardening (d5). **Date:** 2026-09-30.
**Branch:** task/epic-a-codederror-pickle @ **b5cdad2** (fix 26a7a5e, unchanged since; 047c4f6 the mutation runner; 4905b89 a test-only switch to `AutoGrader.celery.app`; then docs). Off epic b2890d9. SM scope: not a hotfix; production's CELERY_RESULT_SERIALIZER is json.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot, from a scratch worktree detached at b5cdad2. Rule 15: v2's own probe (**not** adopted by d5, so it is independent) + d5's module + S6a/uploads modules + independence mutants. d5's AutoGrader regression (472 OK at 4905b89) and 6/6 mutants are cited.

**Verdict: VERIFIED-WITH-NOTES** (the notes are informational).

## The fix (static)
- `CodedError.args = (reason_code, params, None, display)`, and `__init__(reason_code=None, params=None, detail=None, display=None)` accepts them **positionally or by keyword**, so both Celery's json `cls(*exc_message)` and pickle's default reduce rebuild the same class, code, params and message.
- `__str__` returns the rendered message. `args` is set after `super().__init__`, so the DRF `APIException` base (`PayloadTooLarge`/`FileTooLargeError`) keeps its `.detail`/`status_code`.
- `detail` (log-only) is deliberately not in `args`: pickle keeps it via `__dict__`; json does not store it.
- **d5's attack points, checked:**
  - Non-JSON params: impossible. `_SCALARS = (str, int, float, bool)`, and anything else (e.g. Decimal) is refused when the error is constructed.
  - A subclass with its own `__init__`: none exist.
  - Callers reading `.args[0]` as the message: a grep of non-test code finds **none**.

## Evidence
| Check | Result |
|---|---|
| Reproduce-first (v2's probe, earlier) on b2890d9 | 7 classes all fail: pickle/copy 35 errors; Celery json + pickle 14 failures (`runs/codederror_repro.log`) |
| v2's probe + `AutoGrader.tests_codederror_serialization` + `tests_reason_codes` + `tests_uploads` @ b5cdad2 | **49 OK** |
| Classes covered by reflection | 9: `FileTooLargeError`, `FileTypeUnsupportedError`, `FileUnreadableError`, `PayloadTooLarge`, `StudentNameUnmatchedError`, `StudentNotOnRosterError`, `SubmissionEmptyError` + d5's `DefinedOnlyHere` + v2's `VfLateCodedError` (defined after import, registered nowhere) |
| R1 pickle (every protocol) / R2 copy+deepcopy / **R3 Celery prepare_exception → json wire → exception_to_python under json AND pickle** / R5 late subclass / R6 display + detail (pickle) | all hold: the same type, reason_code, params, `str()`, `coded_body`; `status_code` for the DRF-derived classes. The configured serializer is confirmed **json** |
| Independence mutants (`vf_codederror_mutants.py`) | **2/2 KILLED**, by d5's tests as well as v2's: IM1 params dropped from `args` (38F/92E); IM2 the fix only for non-test modules (9F/23E; `DefinedOnlyHere` and `VfLateCodedError` fail, which proves the fix is on the base class) |

## Notes
- **N1 (data in the result backend).** Under json the Celery result now stores `params`, which can include `file_name` and, for STUDENT_NOT_ON_ROSTER, `student_display` (a student's name). d5 pins `CELERY_RESULT_EXPIRES = 3600`, so it is kept for 1 h, and that is the backend teachers' task status already reads. It is acceptable; recorded here so the retention is a known choice.
- **N2 (backlog, from d5).** `AutoGrader/tests_celery_signals.py` leaves its own `Celery(...)` app current (`set_as_current` is never restored), so any test using `celery.current_app` after it sees the wrong app. v2's probe now uses `AutoGrader.celery.app` for the same reason.

Logs: `runs/codederror_run1.log`, `runs/codederror_run2_mutants.log`.
