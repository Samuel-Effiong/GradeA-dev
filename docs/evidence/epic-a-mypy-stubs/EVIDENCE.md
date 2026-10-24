# Epic A: django-stubs mypy compatibility and the beta merge

Branch `task/epic-a-mypy-stubs`, cut from `phase2/epic-a` `96bb182`. Lands in `phase2/epic-a` (never beta), then staging = beta + phase2/epic-a. Author: Integration & Release Engineer (grade-automator-plus-0b). SM-approved Epic A completion slice.

## Why

Refreshing staging with beta `e7e4bdf` failed at the merge commit's mypy hook. beta's `mypy-django-stubs` checks every module not in its ratchet, and the Epic A modules postdate that ratchet (generated on `4b902fc`, which has no `audit/`). Whole-repo mypy on beta + phase2/epic-a: **45 errors, all in Epic A files**.

## Commits

1. `bf78aa3`: the 45 fixes, on the Epic A side **before** the merge, so the merge commit carries only conflict resolutions and a re-merge reproduces it. Reproduced first with beta's `pyproject.toml` + `.pre-commit-config.yaml` copied in uncommitted (then restored): same 45 errors, same files.
   - `timedelta` from `datetime`, not `django.utils.timezone` (16 sites: `audit/tests_retention_sweep.py`, `users/tests_auth_audit_events.py`).
   - Test fixtures set in `setUpTestData` declared on the class (`audit/tests_emitter.py`), and `client: APIClient` declared where tests replace the Django client (`audit/tests_query_api.py`, `audit/tests_admin_action.py`).
   - `audit/views.py` school-admin queryset: `cast("CustomUser", request.user)` (TYPE_CHECKING-only import); `IsSchoolAdmin` has already rejected anonymous callers. No runtime change.
   - `audit/metrics.py`: optional `sentry_sdk` import keeps its `None` fallback with a narrow `type: ignore[assignment]`.
   - No ratchet entries added.
2. `03807c5`: merge beta `e7e4bdf`. Two conflicts, both only where both sides edited the same lines:
   - `users/views.py` logout. Beta (AUTHZ-T1) bumps the session epoch; Epic A records `AUTH_LOGOUT`. Resolved as: revoke first, then record SUCCESS. If revocation raises, record FAILURE / `SYSTEM` / `SESSION_REVOKE_FAILED` and re-raise (SM requirement: the event must still be recorded on that path). `emit()` writes in its own atomic block and there is no `ATOMIC_REQUESTS`, so the failure row survives the re-raise.
   - `docs/HARDENING_BACKLOG.md`: both new table rows kept (H-40 from Epic A, H-46 from beta).
3. `f0355e0`: tests for the logout interaction (`users/tests_auth_audit_events.py`):
   - success bumps `token_epoch` by 1 and records SUCCESS;
   - `revoke_all_sessions` raising `DatabaseError` → HTTP 500, exactly one FAILURE event (`SYSTEM`, `SESSION_REVOKE_FAILED`, actor = the user).

## Results (tip `f0355e0`)

| Check | Result |
|---|---|
| Whole-repo `pre-commit run mypy --all-files` | Passed |
| `makemigrations --check` | No changes |
| `audit` + `users` apps (`--parallel 4`) | 764 OK, 4 skipped, 0 blocked network calls |
| M1: failure-path emit removed | `test_a_failed_session_revocation...` FAILS |
| M2: SUCCESS recorded before revoke, no failure emit | `test_a_failed_session_revocation...` FAILS |

## Open

- Independent verification (Verification Engineer), including the logout ordering and the failure path.
- After VERIFIED: merge into `phase2/epic-a`, then refresh staging (beta + phase2/epic-a) with makemigrations and the overlap tests.
