# audit.E001: the failed-auth floor covers every sign-in lock threshold

Branch `task/epic-a-floor-check`, cut from phase2/epic-a `2c950aa` (after the beta merge-down). This is v2's N1 on the merge-down (`docs/evidence/epic-a-merge-b/VERIFICATION_v2_6ae18c4.md`), by SM order. The verifier is v2. No migration.

## Why
S1b's cap always writes an account's first `FAILED_AUTH_TARGET_FLOOR` (5) failed or refused sign-ins per window. Several events record a lock:
- the guess that spent a budget (`lock_triggered`, on /auth/verify and reset-password);
- the refusals after it (`VERIFY_LOCKED`, `RESET_LOCKED`, `ACCOUNT_LOCKED`).

They are individual rows only because every lock threshold is within that floor:
- `VERIFY_EMAIL_MAX_FAILURES` = 5;
- `CustomUser.MAX_LOGIN_ATTEMPTS` = 5;
- `PasswordResetOTP.MAX_ATTEMPTS` = 5.

An env change could raise a threshold or lower the floor. Then the lock event could be folded into a `FAILED_AUTH_CAPPED` summary, and the trail would lose the moment an account was locked. No test would fail.

## Change
- `audit/checks.py`: `check_failed_auth_floor_covers_every_lock`, an **Error** (`audit.E001`) when any threshold exceeds the floor. The error names each offending threshold and its value. `lock_thresholds()` lists the three thresholds in one place.
- `audit/apps.py`: `ready()` imports the checks, as `billing/apps.py` does for `billing.E001`. So `manage.py check`, and every deploy pipeline that runs it, fails loudly.

## Tests (`audit/tests_checks.py`)
- The shipped settings pass.
- The check is registered: `run_checks(tags=["audit"])` reports E001 at floor 1.
- A floor of 4 is an error that names all three thresholds; a floor equal to the largest threshold passes.
- Each threshold raised above the floor is caught and named. VERIFY uses `override_settings`; the two model constants use `patch.object`. A set-equality assertion ties the cases to `lock_thresholds()`, so a new threshold can't go untested.

## Gates
_pending_ (rule 15: the changed module + mutation + ONE owning-app regression, `audit`).
