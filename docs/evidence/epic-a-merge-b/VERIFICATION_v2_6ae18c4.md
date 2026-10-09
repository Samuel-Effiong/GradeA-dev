# Verification (auth part + settings hunk): the (b) merge-down @ 6ae18c4

**Verifier:** Verification Engineer 2 (v2), for the auth part and the settings hunk (SM). 1a reads the H-53 part. **Resolution author:** ed; the merge was performed by 0b. **Date:** 2026-09-30.
**Commit:** phase2/epic-a **6ae18c4** = merge of beta abeda10 into 19b2072 (ed's `users/views.py`, SHA256-checked, plus the 11-hunk patch). 0b's author-side gates were committed as docs at 2c950aa (changed modules 209 OK, users 686 OK), and Gate 10 is running there.

Every run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`, RACE_COST 600/200), from a scratch worktree detached at 6ae18c4. Rule 15: v2's probes and mutants plus the auth changed modules.

**Verdict: VERIFIED-WITH-NOTES.** N1 is a config-invariant hardening item and does not block staging.

## Static (`git show --remerge-diff 6ae18c4`)
- **settings.py:** a clean union. S1b's four `FAILED_AUTH_*` keys, a blank line, then H-53's `VERIFY_EMAIL_MAX_FAILURES` / `VERIFY_EMAIL_LOCK_SECONDS`. An AST union check over the whole file (base 755aa27 against both parents): Epic A's 4 new names and 6 new list/dict entries, and beta's 2 new names, are **all present**. Neither side removed anything. `MIDDLEWARE` still ends with `audit.middleware.AuditMiddleware`.
- **classrooms/views.py:** an import union (Epic A's audit imports plus beta's `SCOPE_COURSE` import).
- **users/views.py `AuthViewSet.verify`:** matches the SM ruling.
  - Locked → `sign_in_failed(... "VERIFY_LOCKED", denied=True)`, then H-53's 429.
  - `refuse(message, account, reason_code)` records exactly one FAILURE per attempt (`INVALID_CODE` or `CODE_EXPIRED`), with `lock_triggered` only on the guess that spends the budget.
  - Success and `clear_verify_failures` are unchanged.
- **audit/emitter.py:** a DENIED with no account returns `(None, True)`, so no floor and the global cap (SM ruling). A known account's DENIED is unchanged. The other hand edits (enums `VERIFY_LOCKED`, `AUDIT_ONLY_CODES`, docstrings, tests, the cache-guard allow-list) match ed's README.

## Evidence
| Check | Result |
|---|---|
| Auth changed modules (`users.tests_auth_audit_doors`, `audit.tests_failed_auth_cap`, `tests_emitter`, `tests_route_coverage`, `AutoGrader.tests_reason_codes`, `users.tests_verify_email_budget`, `tests_throttling`, `tests_auth_input_validation`) + all v2 probes (S1, S1b, S2 R1, S5, S6a, merge-b) | **221 OK** |
| P1: a locked **unknown** address sprayed 25× through `/auth/verify` (caps floor 2 / target 4 / global 6; one IP per attempt) | statuses 400×5, then 429×20. Individual rows: 5 FAILURE `INVALID_CODE` + 1 DENIED `VERIFY_LOCKED` (= the global cap), then DENIED **global** summaries at 1 and 10. Every row has target None, and **the address is stored nowhere** |
| P2: a **known** account (in a school) locked then sprayed 25× | bounded at the per-target limit; the summaries carry the account and its school; the account stays unverified. See N1 |
| ed's suggested mutants (`vf_mergeb_mutants.py`, 5) | **5/5 KILLED**: no-account DENIED uncapped again (also by v2's P1); a locked attempt leaves no event (also by P1); `denied=False`; `lock_triggered` dropped; `VERIFY_LOCKED` not catalogued |

## Notes
- **N1 (config invariant, recommend a check).** DENIED and FAILURE share one per-target counter. In P2 the caps were deliberately smaller than H-53's budget (target limit 4 < `VERIFY_EMAIL_MAX_FAILURES` 5), and **the guess that set the lock, the only row with `lock_triggered`, was suppressed into a FAILURE summary, as were all DENIED `VERIFY_LOCKED` rows**. With the defaults (floor 5, target 30; verify budget 5, reset `PasswordResetOTP.MAX_ATTEMPTS` 5, `CustomUser.MAX_LOGIN_ATTEMPTS` 5) the lock-setting attempt falls inside the floor. That holds only at the boundary (5 = 5), and every value is env-tunable. Recommendation: a system check or test that `FAILED_AUTH_TARGET_FLOOR >= max(VERIFY_EMAIL_MAX_FAILURES, PasswordResetOTP.MAX_ATTEMPTS, CustomUser.MAX_LOGIN_ATTEMPTS)`, so the event that sets a lock is always written individually.
- **N2.** A 429 raised by the `/auth/verify` view (H-53) is recorded by its own named DENIED event. S2's "no event for a 429" rule is still right, because that rule concerns throttle refusals with no view-side event.

Logs: `runs/mergeb_run1.log`, `runs/mergeb_run2_mutants.log`. Probe: `tests_vf2_mergeb_probe.py`.
