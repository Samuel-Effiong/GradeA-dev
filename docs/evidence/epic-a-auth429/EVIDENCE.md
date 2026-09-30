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

## No new existence oracle (SM condition)
- **/auth/verify:** the lock is per address, known or not (H-53). `test_a_locked_unknown_address_answers_exactly_like_a_known_one` locks both and compares them. They match on status, key set, every value except `reference` (per request) and the seconds in `detail`, and the presence of `Retry-After`.
- **login:** only a real account can be locked. The distinct 401 message and `account_locked` code already said so, so that is **pre-existing**. The added keys restate that code. `test_an_unknown_address_gains_nothing` shows an unknown address's 401 gains no key.
- **reset-password:** the informative RESET_LOCKED answer exists only for an account with a reset code. That is **pre-existing**, a founder decision of 2026-09-28, and the envelope adds no information to it.

## Tests updated on purpose
`users/tests_auth_audit_doors.py`: the catalogue test now accepts VERIFY_LOCKED as user-facing. Either list is the catalogue; the emitter only needs the code to be in it.

## Backlog (SM ruling 4)
register_student's failure-budget 429 is a plain Throttled with no code, the same gap. It goes to a backlog row and is not changed here.

## Gates
_pending_
