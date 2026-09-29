# Epic A django-stubs slice + beta merge: Verification (@0a057e4; lands in phase2/epic-a, never beta)

**(1) Merge 03807c5** (parents bf78aa3 + e7e4bdf). My re-merge conflicts in exactly `users/views.py` and `docs/HARDENING_BACKLOG.md`. **No file outside those two differs from 03807c5**, so there are no hand edits elsewhere. users/views.py: the resolution keeps Epic A's AUTH_LOGOUT emits (missing and invalid refresh token) and wraps beta's `revoke_all_sessions()` in the new try/FAILURE/re-raise. Backlog: every H-row from both parents is present (Epic A's H-40; beta's H-41, 42, 44, 46, 48, 49 and 50), with one row each for H-40 and H-46 and nothing dropped.

**(2) Logout order and the persistence of the failure row.** Revoke runs first; SUCCESS is emitted only after it succeeds; on an exception it emits FAILURE/SYSTEM/SESSION_REVOKE_FAILED and re-raises. No `ATOMIC_REQUESTS`, the action isn't atomic, and `emit` writes in its own `transaction.atomic()`. The new test is an APITestCase, so it proves emission but not commit. **I proved commit** with a throwaway TransactionTestCase (never committed), asserting it runs outside any transaction: revoke patched to raise gives HTTP 500, and the committed rows are exactly `[('FAILURE', 'SESSION_REVOKE_FAILED')]`, with no SUCCESS row. My extra mutant (swallow the exception instead of re-raising) is KILLED by `test_a_failed_session_revocation_is_recorded_as_a_failure_not_success`, joining your M1 and M2.

**(3) bf78aa3 is fixed at the source.** pyproject.toml is untouched (no ratchet). The only production edits are an `audit/views.py` `cast("CustomUser", self.request.user)` with a TYPE_CHECKING-only import (a runtime no-op; IsSchoolAdmin already rejects anonymous users) and one narrow `# type: ignore[assignment]` on the `sentry_sdk = None` optional-import fallback in audit/metrics.py. Everything else is test typing.

**Runs** (my detached checkout of 0a057e4): whole-repo `pre-commit run mypy --all-files` Passed; `audit` + `users.tests_auth_audit_events` 177 OK.

**Notes (non-blocking):**
(a) The logout audit tests mint plain `RefreshToken.for_user`. That's fine here (a fresh user is at epoch 0), but it's the same fragility ea9ef13 fixed in patch-password. `users.tokens.EpochRefreshToken` would be safer.
(b) For the SM: H-43 (the /auth/otp wording oracle) and H-45 (the redis_hygiene flake) were committed on task/register-landed-verify, which **never landed on beta**, so neither row is on beta or staging.

Full suite: covered by the staging run.

Verdict: VERIFIED. Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
