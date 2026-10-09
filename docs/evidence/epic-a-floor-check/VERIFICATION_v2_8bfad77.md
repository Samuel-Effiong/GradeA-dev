# Verification: the floor ≥ threshold check @ 8bfad77

**Verifier:** Verification Engineer 2 (v2). **Author:** Security (ed). **Date:** 2026-09-30.
**Branch:** task/epic-a-floor-check @ 8bfad77 (cut from 2c950aa). It answers v2's N1 on the (b) merge-down (SM order: floor ≥ max lock threshold).

Runs were wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot, from a scratch worktree detached at 8bfad77.

**Verdict: VERIFIED-WITH-NOTES.** N1 needs an SM/founder decision on deploy configuration. N2 is a small extension to the spec.

## Evidence
| Check | Result |
|---|---|
| `audit.tests_checks` | **6 OK** |
| `manage.py check --tag audit` with the shipped settings | no issues |
| … with `FAILED_AUTH_TARGET_FLOOR=1` from the environment | **audit.E001**, naming all three thresholds; rc=1 |
| … with `VERIFY_EMAIL_MAX_FAILURES=6` | **audit.E001** naming `VERIFY_EMAIL_MAX_FAILURES: 6`; rc=1 |
| … with `FAILED_AUTH_TARGET_LIMIT=5`, or `FLOOR=5 LIMIT=5` | no issues. This passes although the first refusal after a lock would be summarised (N2) |
| `lock_thresholds()` completeness (grep of non-test code for attempt/failure budgets) | the per-account locks are exactly verify (H-53), login (`MAX_LOGIN_ATTEMPTS`) and reset (`PasswordResetOTP.MAX_ATTEMPTS`). `REGISTER_STUDENT_GLOBAL_FAILURE_LIMIT` is a global budget whose failures carry no target, so the floor does not apply to it |
| ed's gates | the prefix fails on 2c950aa; 5/5 mutants (F4 is killed by the fresh-process test); audit 259 OK (committed) |

## Notes
- **N1 (deploy, SM/founder decision).** Django system checks run only under management commands (runserver, migrate, check, test). The production image's `CMD` starts **gunicorn only** (`Dockerfile:84`); the repo has no migrate/check step. Unless Railway runs a pre-deploy command configured outside the repo, **audit.E001 never runs in the deployed process**, so a Railway env override (e.g. `FAILED_AUTH_TARGET_FLOOR=1`) would go unnoticed. The existing `billing.E001` has the same gap. Options:
  - add `python manage.py check --fail-level ERROR` (or `migrate`, which runs checks) to Railway's pre-deploy/start command; the founder sets this in Railway;
  - or evaluate the invariant at startup in `AuditConfig.ready()` (log at ERROR / refuse to start).
- **N2 (spec).** `floor ≥ max(threshold)` keeps every failure up to the lock-setting one. The **first refusal after the lock** (VERIFY_LOCKED / RESET_LOCKED / ACCOUNT_LOCKED) shares the per-target counter and is written individually only if `FAILED_AUTH_TARGET_LIMIT > max(threshold)`. `FLOOR=5 LIMIT=5` passes today's check. The module docstring claims "and the refusals after it". Either add the strict `LIMIT > max(threshold)` condition, which the defaults 30 > 5 satisfy, or narrow the docstring.

Log: `runs/floorcheck.log`.

---

## Re-check @ 63f71c3 (N2), 2026-09-30: **VERIFIED**
The delta from 8bfad77 is `audit/checks.py` and `audit/tests_checks.py` only (plus docs). The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, timeout) in 0b's slot.

| `manage.py check --tag audit` | Result |
|---|---|
| `FLOOR=5 LIMIT=5` (passed at 8bfad77) | **audit.E001**: "FAILED_AUTH_TARGET_LIMIT (5) is not above the lock threshold(s) {…: 5, …: 5, …: 5}"; rc=1 |
| `FLOOR=5 LIMIT=6` | no issues (the boundary is correct: T < limit) |
| `LIMIT=4` | **audit.E001** naming the limit; rc=1 |
| `audit.tests_checks` | **8 OK** |

The docstring now states both conditions (T ≤ floor, T < limit). ed's F6/F7 mutants are killed (committed). N1 (checks never run on deploy) stays open as the SM's backlog item for the founder.

Log: `runs/floorcheck_n2.log`.
