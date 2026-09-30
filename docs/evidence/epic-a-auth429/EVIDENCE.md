# The sign-in locks answer with the coded envelope (v2's S6a N3)

Branch `task/epic-a-auth429`, cut from phase2/epic-a `522f818` (S8 in). Phase 2 only. The verifier is v2. No migration.

Scope, per the SM's rulings:
- RESET_LOCKED and VERIFY_LOCKED: yes, additive.
- ACCOUNT_LOCKED on login: included, additive, keeping the 401.
- register_student's failure-budget 429: out of scope. It is a backlog row, not an account lock.

## What changed
**One rule for all three: add, never replace.** `AutoGrader.reason_codes.add_coded_envelope(data, code, message, *, code_value=None)` adds `reason_code`, `error_class`, `remediation`, `retryable`, `params` and `reference` to a body that already exists. It never replaces a key the body has, so every field the auth docs promise stays exactly as it was. It sets `code` only if the body has none.

It does **not** add `error`, the envelope's display sentence. These bodies already carry their text as `message` or `detail`, and a second text key would change what the renderer shows. A test pins that the top-level `message` is unchanged for each lock.

| Lock | Before | Now |
|---|---|---|
| reset-password (`_reset_locked_response`) | 429; `field_errors` {code "RESET_LOCKED", message, locked_until, retry_after_seconds}; `Retry-After` | the same, plus the envelope |
| /auth/verify (H-53) | a plain DRF `Throttled` 429; `field_errors` {detail}; `Retry-After`; **no code**, so a client could not tell it from the per-IP limit | the same, plus `code` "VERIFY_LOCKED" and the envelope. The per-IP limit is unchanged and has no code (tested). |
| login (`CustomTokenObtainPairSerializer`) | a 401 `AuthenticationFailed`; `field_errors` {detail}; the `account_locked` code only on DRF's `ErrorDetail`, not in the JSON | the same 401, message and `ErrorDetail.code`, plus `code` "account_locked" in the JSON (the F8 pattern) and the envelope |

**How it's wired:**
- The reset lock's view builds its own Response, so it calls the helper directly.
- The other two raise `users.exceptions.EnvelopedThrottled` / `EnvelopedAuthenticationFailed`: DRF's own exceptions, carrying the reason code. `custom_exception_handler` adds the envelope to DRF's response, so DRF still sets the status, `detail`, `Retry-After` and `WWW-Authenticate`.

**The catalogue:** RESET_LOCKED, VERIFY_LOCKED and ACCOUNT_LOCKED move from `AUDIT_ONLY_CODES` to user-facing specs in `REASON_CODES` (429, 429 and 401; USER; retryable).
- **PENDING QA CATALOGUE APPROVAL (SM condition).** QA owns user-facing catalogue additions. These three codes are proposed alongside S7d's; they may reach **staging**, but **not beta**, until QA agrees.

**OpenAPI:**
- `/auth/verify`: the "two different 429s" paragraph, plus an "Address locked" example beside the per-IP "Throttled" one.
- reset-password: its lock paragraph names the added keys.
- login: its 401 now describes the lock and its keys.

## For the frontend: does the 429/401 body shape change? (0b's question)
**Additively only.**
- Every key, value, status code and header each lock sent before is unchanged: the displayed `message`, `detail` text, `locked_until`, `retry_after_seconds`, `Retry-After` and `WWW-Authenticate`. The renderer still shows the same message.
- New keys appear inside `error.field_errors`: `reason_code`, `error_class`, `remediation`, `retryable`, `params` and `reference`, on all three locks.
- **The one new key a client may branch on:** `code`. It now appears on the verify lock ("VERIFY_LOCKED") and in the login lock's JSON ("account_locked", which was only on DRF's ErrorDetail before). The reset lock already had `code`.
- The per-IP rate limit's 429 and every non-lock auth error are unchanged (tested).
- A client that ignores unknown keys sees no difference. One that reads `code` can now tell the verify lock from the per-IP limit, which is the point of the change.

## No new existence oracle (SM condition)
- **/auth/verify:** the lock is per address, known or not (H-53). `test_a_locked_unknown_address_answers_exactly_like_a_known_one` locks both and compares them. They match on status, key set, every value except `reference` (per request) and the seconds in `detail`, and the presence of `Retry-After`.
- **login:** only a real account can be locked. The distinct 401 message and `account_locked` code already said so, so that is **pre-existing**. The added keys restate that code. `test_an_unknown_address_gains_nothing` shows an unknown address's 401 gains no key.
- **reset-password:** the informative RESET_LOCKED answer exists only for an account with a reset code. That is **pre-existing**, a founder decision of 2026-09-28, and the envelope adds no information to it.

## Tests updated on purpose
`users/tests_auth_audit_doors.py`: the catalogue test now accepts VERIFY_LOCKED as user-facing. Either list is the catalogue; the emitter only needs the code to be in it.

## Backlog (SM ruling 4)
register_student's failure-budget 429 is a plain Throttled with no code, the same gap. It goes to a backlog row and is not changed here.

## Gates (rule 15: changed modules + mutation + ONE owning-app regression; logs committed)
Run on **`b512ddf`**, one step at a time at 6G in 0b's slot. The changed-modules step would have stopped the run if red; it was green first time.

| Gate | Result |
|---|---|
| Reproduce-first | `522f818`'s `AutoGrader/reason_codes.py`, `users/exceptions.py`, `users/serializers.py` and `users/views.py` against `users.tests_auth_lock_envelope` (`prefix_522f818_failing.txt`): **7 tests, 6 failures, 1 error**. No envelope on any lock; the codes are audit-only; the helper doesn't exist. |
| Changed modules | `users.tests_auth_lock_envelope` (new), `users.tests_auth_audit_doors`, `AutoGrader.tests_reason_codes`, `users.tests_verify_email_budget`, `users.tests_login_lockout`, `users.tests_throttling`, `users.tests_auth_input_validation`, and, at 0b's request, `users.tests_reset_otp_budget` (L2's reset-429 pin), `users.tests_otp_no_oracle`, `users.tests_auth_audit_events`: **147 OK** (`changed_modules.txt`) |
| 2 Mutation | **6 mutants, 6 killed** (`mutation_log.txt`, `mutation_results.json`): L1 the envelope overwrites documented keys; L2 the reset lock has no envelope; L3 the handler adds none; L4 the verify lock has no code; L5 the login lock loses its legacy code; L6 a second text key changes the display. |
| 1 Regression (owning app) | `users`: **693 OK** (skipped=4) (`regression_users.txt`, trimmed; the full log is in GAP-evidence-logs) |
| mypy | whole-repo: **Passed** |
| Migrations | **No changes detected** |
