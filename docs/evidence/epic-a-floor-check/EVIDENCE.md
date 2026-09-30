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
- **Registration, in a fresh process:** `manage.py check --tag audit` with `FAILED_AUTH_TARGET_FLOOR=1` in the environment exits non-zero and names `audit.E001`. This test is added at `16b524e`, because the first mutation run's F4 (the `apps.py` import removed) **survived**: the test module itself imports `audit.checks`, which registers the check in that process however the app is wired. Only a separate process proves that `AuditConfig.ready()` registers it.
- Each threshold raised above the floor is caught and named. VERIFY uses `override_settings`; the two model constants use `patch.object`. A set-equality assertion ties the cases to `lock_thresholds()`, so a new threshold can't go untested.

## Gates (rule 15: the changed module + mutation + ONE owning-app regression)
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout and `--noinput`, one step at a time.

| Gate | Result |
|---|---|
| Reproduce-first | `2c950aa`'s audit (no `checks.py`, the base `apps.py`) against `audit.tests_checks` (`prefix_2c950aa_failing.txt`): the module fails to import, because the check doesn't exist there. |
| Changed module | `audit.tests_checks`: **6 OK** on `16b524e` (`changed_modules.txt`) |
| `manage.py check` | shipped settings: **no issues** (`manage_check.txt`) |
| 2 Mutation | **5 mutants, 5 killed** on `16b524e` (`mutation_log.txt`, `mutation_results.json`). F1 (`>=`) is killed by the check itself: with the shipped values (all 5) it raises E001 at the test runner's startup system checks, so no test gets to run. F4 (not registered) is killed by the fresh-process test. The first run on `ed50d1b`, where F4 survived, is kept as `mutation_log_first_ed50d1b.txt`. |
| 1 Regression (owning app) | `audit`: **259 OK** on `ed50d1b` (`regression_audit.txt`). `16b524e` adds only a test. |
| Hooks | black, isort, flake8, mypy and bandit passed on both commits. |
