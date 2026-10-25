# H-221 notes

- **The row** (the Senior Manager's wording after the read): on `POST /auth/reset-password`, two refusals wrote the
  wrong event, INVALID_REQUEST with no account.
- **What happened before** (beta `7286717e`, `users/views.py` `reset_password`):
  - The H-164 branch (never verified, and switched off or holding admin power) raised without an event of its own.
  - So did the H-202 branch (verified, switched off).
  - `AuditMiddleware` (`audit/middleware.py:55-56`) then saw an anonymous 400 on a door with no stored event. It
    wrote its fallback (`audit/request_audit.py` `emit_anonymous_refusal`): AUTH_LOGIN, FAILURE, reason
    INVALID_REQUEST ("a malformed request"), no account, no school.
  - The reason was untrue, the account was lost, and the school's admin could not see the attempt.
- **What changes:**
  - **H-164** records **INVALID_CODE, FAILURE**, naming the account. This is ruling (a): the code verify (`users/views.py`
    1066) and the school-admin registration (1974) record for the same H-164/H-203 rule.
    - It is not literal when the code was right. We accept that: one code for one rule across the three routes.
  - **H-202** records **ACCOUNT_DEACTIVATED, DENIED** (`denied=True`), naming the account. This is ruling (b): the code
    Google sign-in records for the same state (`users/views.py` 2253-2254 and 2101). It is already in
    `AUDIT_ONLY_CODES`.
  - Both calls come before the `raise`. `ATOMIC_REQUESTS` is not set, so the row stays, as with the route's other
    calls.
- **Production files:** `users/views.py` only, two `sign_in_failed` calls inside `reset_password`, plus their
  comments. The seam check below proves it by the syntax tree.
- **What does not change** (side effects ruled acceptable by the Senior Manager):
  - **The answer:** the same `ParseError("Invalid email, OTP code, or new password.")`, 400, byte for byte the same
    as an unknown address. T5 compares the full body and status. `emit` never raises (FR-A-11).
  - **The login and OTP state:**
    - `sign_in_failed` touches no login counter (`register_failed_login` is only in the login serializer), no
      lockout, no user and no OTP.
    - H-202's `register_failure()` is unchanged, and H-164 still spends no attempt. T6 shows both.
  - **One event per request:** the route's own event is now stored, so the middleware's fallback no longer writes.
  - **The cap counters** (`audit/failed_auth_cap.py`) change, and this affects audit volume only.
    - The fallback had no account, so it counted in the global bucket only.
    - H-164's FAILURE with an account now counts in the account's bucket (floor 5, limit 30 an hour) and the global
      one. H-202's DENIED with an account counts in the account's bucket only.
    - In an hour with more than 30 failures on one account, that account's later failure rows are held back
      sooner, with summary rows still written.
  - **The metric** `reason_code_rate` is tagged INVALID_CODE or ACCOUNT_DEACTIVATED instead of INVALID_REQUEST.
- **Other refusals read** (none has the gap):
  - unknown email or no OTP row (1439), locked (1465), expired (1470), wrong code (1483, 1491) each record their own
    event;
  - the serializer's 400 (a missing field, or a password-rule failure) keeps S2's INVALID_REQUEST by design. It runs
    before any account is looked up;
  - `POST /auth/otp` is in `EXCLUDED_ROUTES`, so none of its answers record an event, by design.
- **Docs:** `docs/GRADE_A_PLUS_REASON_CODES.md` §8 (the two audit-only rows), and the H-221 row in
  `docs/HARDENING_BACKLOG.md`.
- **Not shown by the tests:** the cap's per-account sharing under real volume (no test makes 30 attempts in an hour).
  The cap's own tests are in the gate list.

## Expected reds against 7286717e (written before any run)

The red run takes `users/views.py` back to the base and runs the new module as at the tip.

```
users.tests_h221_reset_audit (4 red, 2 green)
  test_a_never_verified_switched_off_account_is_recorded_as_a_wrong_code     "'INVALID_REQUEST' != 'INVALID_CODE'"
  test_a_never_verified_account_with_admin_power_is_recorded_as_a_wrong_code "'INVALID_REQUEST' != 'INVALID_CODE'"
  test_a_switched_off_account_is_recorded_as_refused_for_deactivation        "'FAILURE' != 'DENIED'"
  test_a_switched_off_account_with_a_locked_reset_is_recorded_the_same_way   "'FAILURE' != 'DENIED'"
  GREEN (controls): test_every_refusal_answers_byte_for_byte_like_an_unknown_address,
                    test_the_account_and_its_code_are_left_as_before
Total: 4 FAIL blocks, no ERROR; "Ran 6 tests". The script counts them.
```

- In each red test the event count check passes first, because the fallback is exactly one event. Then the outcome
  is checked, then the reason, so the H-164 tests fail on the reason (the fallback's outcome is FAILURE too) and the
  H-202 tests on the outcome.
- Each fragment is the first line of unittest's message for a string `assertEqual`.
- **One red traced by hand: `test_a_switched_off_account_is_recorded_as_refused_for_deactivation` at the base.**
  1. `DoorBase.setUp` clears the locmem cache. The test makes a verified, switched-off TEACHER with a school, and a
     real code from `generate_code()`.
  2. The serializer passes. The user and the OTP row are found.
  3. The H-164 test is skipped, because `email_verified_at` is set. In the H-202 branch the OTP is not locked, so
     `register_failure()` sets attempts to 1 (an `.update()`, no event). Then `ParseError` → 400, with no event of
     the route's own.
  4. The middleware finds no stored event. `emit_anonymous_refusal`: POST, anonymous, view `auth-reset-password` (a
     door). `_outcome_for_status(400)` gives FAILURE, so it writes FAILURE, INVALID_REQUEST, target None.
  5. The cap admits it: anonymous FAILURE with no target goes to the global bucket, count 1 of 300.
  6. The test sees one new event; `event.outcome` is `'FAILURE'`, so it fails "'FAILURE' != 'DENIED'".
- **A control traced by hand: the byte-for-byte test at the base.**
  1. The unknown address finds no user, so it records INVALID_CODE with no account and raises the generic
     `ParseError`.
  2. The four refusals raise the same `ParseError` text.
  3. `custom_exception_handler` and the renderer add no field that differs per request.
  4. So the bodies and statuses match before and after the change, and the test is green both times.

## Mutants (expected sets and fragments traced against every assertion of both battery modules)

The battery is the new module and `users.tests_auth_audit_doors`. Its accounts are verified and active, so it
reaches neither branch. A mutant is KILLED only if each expected test fails AND every one of its blocks holds the
written fragment (rule 22, checked by `mutate.py`).

| Id | Break | Expected to fail | Fragment |
|---|---|---|---|
| M1 | the H-164 call removed | T1, T2 | `'INVALID_REQUEST' != 'INVALID_CODE'` (the fallback) |
| M2 | the H-202 call removed | T3, T4 | `'FAILURE' != 'DENIED'` (the fallback) |
| M3 | the H-164 reason changed to ACCOUNT_DEACTIVATED | T1, T2 | `'ACCOUNT_DEACTIVATED' != 'INVALID_CODE'` |
| M4 | the H-202 event no longer DENIED | T3, T4 | `'FAILURE' != 'DENIED'` |
| M5 | the H-202 reason changed to INVALID_CODE | T3, T4 | `'INVALID_CODE' != 'ACCOUNT_DEACTIVATED'` |
| M6 | the H-202 event names no account | T3, T4 | `None != UUID('` |
| M7 | the H-164 event names no account | T1, T2 | `None != UUID('` |
| M8 | the H-202 call made only when the reset is not locked | T4 | `'FAILURE' != 'DENIED'` (the fallback) |
| M9 | the H-164 branch also spends an attempt | T6 (its two H-164 subtests) | `1 != 0` |

- M6 and M7: an event with no account is still written. The cap puts it in the global bucket, count 1 of 300.
- M4: a FAILURE that names the account is under the per-account floor, so it is written too.

## The seam

`seam_check.py` compares the base and tip syntax trees of `users/views.py`. Everything outside `reset_password`'s
body must match, including its decorators and arguments. Inside it, the tip with exactly the two new calls taken out
must match the base.

The two calls are named by their place: the call just before the `raise` in the If that reads `_holds_admin_power`
(INVALID_CODE), and in the If `not user.is_active` (ACCOUNT_DEACTIVATED, `denied=True`).

Before the pack (by parsing source only, no test run), it passes on the tip and stops on three bad inputs:
- the base against itself ("not exactly the two new calls");
- M9's source ("differs by more than the two calls");
- a change elsewhere in `views.py` ("something outside reset_password's body changed").

## Gate

- **Script:** `run_h221_gate.sh.txt`, the H-204 gate's form.
- **The red run:** 4 blocks.
- **Before the test runs:** makemigrations, then the changed-file list (only `users/views.py` outside the tests and
  `docs/`), then the seam check.
- **Own + near + guards:**
  - own: `users.tests_h221_reset_audit`;
  - users: `tests_auth_audit_doors`, `tests_auth_audit_events`, `tests_switched_off_means_out`,
    `tests_reset_for_an_invited_student`, `tests_reset_otp_budget`, `tests_auth_endpoints`,
    `tests_auth_lock_envelope`, `tests_roads_that_sign_in_or_activate`, `tests_token_revocation`,
    `tests_rate_limited_envelope`, `tests_throttling`, `tests_google_auth`;
  - audit: `tests_failed_auth_cap`, `tests_route_coverage`, `tests_anonymous_admin_denial_cap`, `tests_metrics`,
    `tests_state_change`, `tests_enums`, `tests_metadata`;
  - `AutoGrader.tests_reason_codes`;
  - the 21 guards.
- **The mutants:** 9, with a 1410 s limit and the 300 s stall watchdog.
- **Base:** `7286717e`.
