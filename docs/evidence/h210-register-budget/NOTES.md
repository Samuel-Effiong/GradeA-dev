# H-210 notes

- **The row:** `register_school_admin` had only the per-network RegisterThrottle (10/hour per IP): no per-address
  failure budget and no pause.
- **What the read found** (beta `7286717e`):
  - The door serves only older pending SCHOOL_ADMIN rows; new admins are created active.
  - Its token is `secrets.token_urlsafe(32)`, valid 7 days, so guessing is not feasible (posture, not an exposure).
  - The same token is the `activation_token` that `POST /auth/verify` accepts, and verify already had H-53's
    per-address budget: 5 attempts, then a 30-minute lock (`users/throttling.py`).
- **The Senior Manager's rulings (25 October 2026):**
  1. A SHARED budget with `/auth/verify` (the same secret), not a second one.
  2. A locked address gets verify's 429 `VERIFY_LOCKED`, the same for an unknown address.
  3. The lock-out trade below is accepted, as it was for H-53.
  - Adding `Throttled` to the except clause is approved, with a test that the locked answer is a 429 and not a 500:
    B1 is that test (mutant N8 shows it).
- **What changes** (`users/views.py` `register_school_admin`, using H-53's helpers unchanged, on the same keys):
  - after the serializer passes and the email is normalised, before the database lookup:
    `verify_lock_until(email)` and `reserve_verify_attempt(email)`;
  - while the address is locked, or past the budget: `EnvelopedThrottled` 429 `VERIFY_LOCKED`, verify's own text and
    `Retry-After`. The event is `sign_in_failed(account_for_email(email), "school_admin_invitation",
    "VERIFY_LOCKED", denied=True)`, as verify records;
  - a refused token (no row, or admin power) or an expired one: if this attempt spent the budget,
    `lock_verify_address(email)`, and its own INVALID_CODE or CODE_EXPIRED event carries `lock_triggered: True`;
  - a success: `clear_verify_failures(email)` refunds the attempts, as verify does;
  - `except (ParseError, ValidationError, Throttled)`: without `Throttled`, the 429 would fall into `except
    Exception` and answer 500;
  - the route's OpenAPI text names the 429.
  - The cache calls are not undone when the atomic block rolls back, so an attempt stays spent. If the cache is down
    the budget fails open and logs, as H-53's does.
- **Production files:** `users/views.py` only: the method, its decorator text, and `Throttled` added to one import. The
  seam check proves it by the syntax tree.
- **No account state is told:**
  - the lock is keyed on the hashed normalised address and applies to unknown addresses, so known and unknown
    addresses get the same 429 (B7);
  - before the budget is spent, the answers are unchanged (K3).
  - As with verify, a locked answer tells a caller that this address has been tried too often. That is not account
    state.
- **The lock-out trade (accepted, ruling 3):**
  - Anyone who knows a pending admin's address can lock it for 30 minutes with 5 wrong tokens.
  - Because the budget is shared, that also blocks `/auth/verify` and the invitation re-send through `/auth/otp` for
    that address (B6).
  - The 7-day invitation outlives the lock. This is the same trade H-53 accepted for verify.
- **Stated risk for the test suite:**
  - The test runner does not clear the cache between tests. A test class that keeps the real cache could carry
    attempts on one address from test to test.
  - The new module overrides the cache with locmem and clears it in `setUp`. The modules that call this door make at
    most four requests each.
  - The gate's near modules and the batch's full run would show it if it happened.
- **Not shown by the tests:** behaviour under a real Redis outage (the fail-open path is H-53's and is unchanged).

## Expected reds against 7286717e (written before any run)

The red run takes `users/views.py` back to the base and runs the new module as at the tip.

```
users.tests_h210_register_budget (8 red, 3 green)
  B1 test_after_five_wrong_tokens_even_the_right_one_is_refused          "200 != 429"
  B2 test_an_address_with_no_account_is_locked_the_same_way              "400 != 429"
  B3 test_the_budget_is_shared_with_email_verification                   "200 != 429"
  B4 test_the_guess_that_spends_the_budget_is_flagged                    "None != True"
  B5 test_a_locked_attempt_is_recorded_as_refused                        "'FAILURE' != 'DENIED'"
  B6 test_a_locked_address_is_sent_no_new_invitation                     "!= 'invite-h210-token'"
  B7 test_the_locked_answer_does_not_tell_whether_the_address_has_an_account "200 != 429"
  B8 test_a_lock_alone_refuses_after_the_attempt_window                  "200 != 429"
  GREEN (controls): K1 test_under_the_budget_the_right_token_still_signs_in,
                    K2 test_a_success_refunds_the_attempts,
                    K3 test_under_the_budget_the_answers_are_unchanged
Total: 8 FAIL blocks, no ERROR; "Ran 11 tests". The script counts them.
```

- B6's first line is "'<the new random token>' != 'invite-h210-token'", so the written fragment is its fixed end.
- B7 is red on its first check: at the base the known address with the right token signs in (200).
- **One red traced by hand: B5 at the base.**
  1. A pending SCHOOL_ADMIN is made with token `invite-h210-token`. Five wrong tokens from five IPs each answer 400,
     with INVALID_CODE events. The base has no budget.
  2. A sixth wrong token: the serializer passes, and the lookup finds no row with that token, so `audit_failure =
     (account, "INVALID_CODE")` and `ParseError`.
  3. The except clause records `sign_in_failed(..., "INVALID_CODE")`: FAILURE, naming the account.
  4. The test filters the new events to AUTH_LOGIN and finds exactly one, so it fails "'FAILURE' != 'DENIED'".
- **A control traced by hand: K1, after the change.**
  1. Four wrong tokens: attempts 1 to 4. None spends the budget (`verify_budget_spent` is `>= 5`), so there is no
     lock.
  2. The right token: not locked, attempt 5 is reserved, and `verify_attempt_over_budget(5)` (`> 5`) is false.
  3. The row is found, the password is set, it is saved, and the attempts are refunded: 200 with tokens.
  4. At the base it is a plain 200 too, so the test is green both times.

## Mutants (expected sets traced against every assertion of the three battery modules)

The battery is the new module, `users.tests_auth_audit_doors` and `users.tests_register_throttle_school_admin`. Neither
older module makes enough attempts on one address to reach the budget.

A mutant is KILLED only if each expected test fails AND every one of its blocks holds the fragment written for that
test (rule 22, in `mutate.py`).

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| N1 | the lock never read (attempts still counted) | B8 (`200 != 429`) |
| N2 | no attempt reserved (only the lock read) | B1 (`200 != 429`), B2 (`400 != 429`), B4 (`None != True`), B5 (`'FAILURE' != 'DENIED'`), B6 (`!= 'invite-h210-token'`), B7 (`200 != 429`) |
| N3 | its own bucket, not verify's | B3 (`200 != 429`), B6 (`!= 'invite-h210-token'`), B8 (`200 != 429`) |
| N4 | the budget spent but the address never locked | B6 (`!= 'invite-h210-token'`) |
| N5 | no event for the locked attempt | B5 (`0 != 1`) |
| N6 | no `lock_triggered` | B4 (`None != True`) |
| N7 | a success does not refund | K2 (`4 is not None`) |
| N8 | `Throttled` not caught (a 500) | B1, B2, B3, B7, B8 (`500 != 429`), B5 (`'FAILURE' != 'DENIED'`) |
| N9 | only addresses with an account are locked | B2 (`400 != 429`), B7 (`400 != 429`) |

- **N1:** within the attempt window the count alone still refuses a 6th try, so only B8 (a lock with no count) sees
  the lock go unread. This is why B8 exists.
- **N4:** the 6th try is still refused by the count. Only `/auth/otp`, the shared lock's other reader, shows the lock
  was never set.
- **N5:** a 429 leaves no fallback event (`emit_anonymous_refusal` skips 429s), so B5 sees no event at all.
- **N8:** the 500 path leaves the middleware's SERVER_ERROR FAILURE event, so B5 fails on the outcome.

## The seam

`seam_check.py` compares the base and tip syntax trees of `users/views.py`:
- everything must match except `register_school_admin` (body and decorator; its arguments must match) and the `from
  rest_framework.exceptions import` line;
- that import must gain exactly `Throttled`;
- the method must actually change.

Before the pack (by parsing source only, no test run), it passes on the tip ("93 lines at the base, 134 at the tip")
and stops on two bad inputs:
- the base against itself ("the import does not gain exactly Throttled");
- an edit elsewhere in `views.py` ("something outside register_school_admin and the import changed").

## Gate

- **Script:** `run_h210_gate.sh.txt`, the H-221 gate's form. The red run expects 8 blocks.
- **Before the test runs:** makemigrations, then the changed-file list (only `users/views.py` outside the test and
  `docs/`), then the seam check.
- **Own + near + guards:**
  - own: `users.tests_h210_register_budget`;
  - every module that calls the door: `users.tests_register_throttle_school_admin`,
    `users.tests_admin_power_principle`, `users.tests_auth_audit_doors`, `users.tests_auth_endpoints`,
    `users.tests_activation_email_domain`, `classrooms.tests_school_admin_active_invite`,
    `classrooms.test_school_admin_otp_deadend`, `classrooms.test_school_admin_invitation_email`,
    `audit.tests_history`;
  - H-53's budget users: `users.tests_verify_email_budget`, `users.tests_switched_off_means_out`,
    `users.tests_reset_for_an_invited_student`, `audit.tests_checks`;
  - neighbours: `users.tests_auth_lock_envelope`, `users.tests_throttling`, `users.tests_rate_limited_envelope`,
    `audit.tests_failed_auth_cap`, `audit.tests_route_coverage`, `AutoGrader.tests_reason_codes`;
  - the 21 guards.
- **The mutants:** 9, with a 1410 s limit and the 300 s stall watchdog.
- **Base:** `7286717e`.

## The gate on 1021f147 (granted by the Senior Manager, 25 October 2026, 13:56 WAT)

Run 14:06 to 14:11:36 WAT inside one `systemd-inhibit`, gate exit 0. Logs are in `gate_1021f147/` (gzipped).
- Available memory at the start was 10G, so the cap was 8G.
- Load was 1.07 0.99 1.41 at the start and 9.56 5.58 3.18 at the end: other work on the laptop began as the mutants
  finished.

- **Red run** (`users/views.py` as at 7286717e, the new module as at the tip):
  - 8 FAIL blocks, each holding its written fragment, no ERROR, no OK line.
  - "Ran 11 tests in 1.053s", "FAILED (failures=8)".
  - The three controls were green. The source was restored.
- **makemigrations --check:** exit 0, no changes.
- **Seam:** "SEAM OK: only register_school_admin and the Throttled import changed". The changed-file check passed.
- **Step 1** (own, the door's modules, H-53's users, the neighbours, the 21 guards), 14:06:49 to 14:09:39:
  - "Ran 664 tests in 144.687s", "OK".
  - 664 test lines were counted in the log.
- **Step 2, mutants** (own database, rules 17, 18 and 22), 14:09:39 to 14:11:32:
  - All 9 KILLED, each with "Ran 38 tests".
  - Every mutant's failing tests are exactly its expected set, with nothing missing and nothing extra.
  - Every block of every expected test held the fragment written for that test.

| Id | Failing / expected |
|---|---|
| N1 | 1 / 1 (B8) |
| N2 | 6 / 6 |
| N3 | 3 / 3 |
| N4 | 1 / 1 (B6) |
| N5 | 1 / 1 (B5) |
| N6 | 1 / 1 (B4) |
| N7 | 1 / 1 (K2) |
| N8 | 6 / 6 (B1 is the 429-not-500 test) |
| N9 | 2 / 2 |

- The source was clean after the mutants, and the mutant database was dropped. No watchdog fired.
- The Senior Manager read the logs and ruled the gate GREEN.
