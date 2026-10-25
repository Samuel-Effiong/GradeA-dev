# H-198 notes

- **The row:** `POST /auth/otp` (the code request) told a caller the state of an address. Every state answered the
  unknown address's neutral 202, except two:
  - **C:** VERIFY_EMAIL for a verified, active account answered 400 "Email already verified. Please login.";
  - **G:** RESET_PASSWORD for an account that never verified its email and is switched off or holds admin power
    answered 400 "Email not verified.".
  - The ten states, and the sibling routes that have no such leak (renew-student-token always answers 410;
    request-change-password needs a session), are in the Builder's read-only proposal
    (`GAP-a0-runs/h198/PROPOSAL.md`).
- **The USER's rulings (25 October 2026, about 12:30 WAT, passed on by the Senior Manager):**
  - **G:** the neutral 202, with the `OTP_SENT_DETAIL` bytes.
    - A never-verified account WITHOUT admin power is sent the activation email instead of a reset code, with H-53's
      lock and the route's throttle applying as they do to VERIFY_EMAIL.
    - An account with admin power is sent nothing.
    - **This supersedes H-164's ruling ("stays refused, the 400 kept as a hint to offer VERIFY_EMAIL") for this
      answer only.** H-164's no-reset rule itself is unchanged: such an account still gets no reset code, and the
      reset step still refuses it.
  - **C:** the neutral 202, with nothing sent and nothing issued.
  - **The frontend was not read (the user's ruling):** skip the check. The OpenAPI text no longer promises the two 400
    hints ("send the user to sign in", "offer VERIFY_EMAIL instead").
  - **No copy change:** the neutral text stays as it is.
  - (The public backup repository, flagged during the read, is intended. Nothing to do.)
- **What changes** (`users/views.py` `otp`):
  - **C** returns `Response({"detail": OTP_SENT_DETAIL}, 202)`; nothing is made or sent.
  - **G** returns the same. Before that, if the account has no admin power and H-53 has not locked the address, it
    calls `send_user_activation_email(user)`, the very call VERIFY_EMAIL makes. A pending school admin therefore
    gets their invitation again, as VERIFY_EMAIL gives it.
  - The OpenAPI description says every valid request gets the same 202, and the 400 is for bad input only. The two
    400 examples are removed.
- **Production files:** `users/views.py` only, the `otp` method. The seam check proves it by the syntax tree.
- **Docs:**
  - `docs/backend/users-and-auth.md` §`auth/otp` (the table and the paragraph);
  - `docs/backend/security-and-tenancy.md` (the "residual enumeration leak" paragraph, now resolved, and a pointer to
    the self-registration candidate row);
  - the H-198 row;
  - **the H-164 row** (its "NOT cured: … (H-198)" sentence now says the 400 is superseded for that answer only, and
    that the no-reset rule is unchanged).
- **Existing tests that pinned C or G, changed in the tests commit (each now expects the neutral 202):**
  - `users.tests_reset_for_an_invited_student`:
    - `test_an_inactive_never_verified_row_is_still_refused` (G inactive; also compares the body with an unknown
      address's);
    - the helper `assert_request_refused` (202 and the unknown address's body; still no reset code, no reset mail).
      It is used by four tests: `test_the_request_refuses_a_never_verified_staff_account`,
      `test_the_request_refuses_a_never_verified_superuser_flag`,
      `test_the_request_refuses_a_never_verified_super_admin_type` and
      `test_the_request_refuses_a_command_line_superuser`;
    - `test_a_verified_admin_still_gets_the_already_verified_answer` (C), renamed
      `test_a_verified_admin_asking_for_verification_gets_the_neutral_answer`;
    - `test_that_refusal_says_what_the_inactive_refusal_says` is unchanged: both G answers are now the same neutral
      202.
  - `users.tests_switched_off_means_out`: `test_an_active_verified_account_is_still_told_it_is_verified` (C), renamed
    `test_an_active_verified_account_gets_the_neutral_answer`, and it now also checks no activation token is made.
  - `classrooms.tests_school_admin_active_invite`: `test_otp_verify_email_short_circuits_instead_of_resending` (C for
    an active school admin). The status is now 202; nothing re-sent and the token unchanged, as before.
  - How they were found: every test function that calls `/auth/otp` and mentions 400 was listed by a syntax-tree
    scan (7 functions). The 400s in `audit.tests_route_coverage` (a synthetic refusal), `users.tests_verify_email_budget`
    (`/auth/verify`) and `users.tests_patch_email` (`/auth/reset-password`) are not C or G. `users.tests_otp_no_oracle`
    covers only states that already answered 202.
- **Not shown by the tests:** what the frontend does with the 202 for C and G (not read, the user's ruling); real mail
  delivery (the mail task is replaced, as the existing tests do).

## Expected reds against 7286717e (written before any run)

The red run takes `users/views.py` back to the base and runs the new module and the three changed modules as at the
tip.

```
users.tests_h198_otp_neutral (5 tests red, 7 blocks; 3 green)
  R1 test_a_verified_active_account_asking_for_verification_is_told_nothing          "400 != 202"
  R2 test_a_never_verified_switched_off_account_asking_for_a_reset_is_told_nothing   "400 != 202"
  R3 test_that_account_is_sent_the_activation_email_and_no_reset_code                "Called 0 times"
  R4 test_while_the_address_is_locked_that_account_is_sent_nothing                   "400 != 202"
  R5 test_a_never_verified_account_with_admin_power_is_told_and_sent_nothing         "400 != 202" (3 blocks, one per shape)
  GREEN (controls): K1 test_c_sends_and_issues_nothing, K2 test_an_active_account_still_gets_a_reset_code,
                    K3 test_a_never_verified_account_still_gets_its_activation_email
users.tests_reset_for_an_invited_student (6 red)                                     "400 != 202" each
  test_an_inactive_never_verified_row_is_still_refused, the four test_the_request_refuses_* tests,
  test_a_verified_admin_asking_for_verification_gets_the_neutral_answer
users.tests_switched_off_means_out (1 red): test_an_active_verified_account_gets_the_neutral_answer  "400 != 202"
classrooms.tests_school_admin_active_invite (1 red): test_otp_verify_email_short_circuits_instead_of_resending  "400 != 202"
Total: 15 FAIL blocks, no ERROR. Every other test of the four modules is green. The script counts them.
```

- R3's fragment is the end of mock's message, "Expected 'delay' to have been called once. Called 0 times.". At the
  base G raises before anything is sent.
- **One red traced by hand: R3 at the base.**
  1. `setUp` replaces `users.services.send_email_task` (the activation mail) and `users.views.safe_delay` (the reset
     mail). A TEACHER is made inactive with `email_verified_at` None, and with no activation token: `create_user` sets
     none, and no signal sets one.
  2. RESET_PASSWORD: the serializer passes and the user is found.
  3. The G test is true (never verified, inactive), so at the base it raises `ParseError("Email not verified.")`
     before any send.
  4. `activation_mail.delay.assert_called_once()` fails "… Called 0 times."
- **A control traced by hand: K2, after the change.**
  1. An active, verified TEACHER asks RESET_PASSWORD. G is false (verified); H-202's branch is false (active).
  2. `get_or_create` makes the OTP row, `generate_code()` returns a code, and `safe_delay(send_email_task, …)` is
     called once (the replaced reset mail). The answer is 202 `OTP_SENT_DETAIL`, the same bytes as the unknown
     address's.
  3. It is the same at the base, so the test is green both times.

## Mutants (expected sets and fragments traced against every assertion of the four battery modules)

The battery is the new module, `users.tests_reset_for_an_invited_student`, `users.tests_switched_off_means_out` and
`users.tests_otp_no_oracle`. No mutant reaches `tests_otp_no_oracle`, whose states already answered 202; it is in
the battery to show none of them changes.

A mutant is KILLED only if each expected test fails AND every one of its blocks holds the fragment written for that
test (rule 22, in `mutate.py`).

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| M1 | C's 400 put back | R1, the verified-admin test, the active-verified test (`400 != 202`) |
| M2 | G's 400 put back | R2, R4, R5, the inactive test, the four request-refuses tests (`400 != 202`); R3 (`Called 0 times`) |
| M3 | C answers 202 with other bytes | R1 (`' != b'`) |
| M4 | G issues a reset code | R3, R4, R5, the inactive test, the four request-refuses tests (`True is not false`) |
| M5 | C re-sends the activation email | K1 (`to not have been called`); the active-verified test (`is not None`) |
| M6 | G with admin power is sent the activation email too | R5 (`to not have been called`) |
| M7 | G ignores H-53's lock | R4 (`to not have been called`) |
| M8 | G without admin power is sent nothing | R3 (`Called 0 times`) |

- **M2:** H-164's "same words" test compares two G answers, which are both 400 again and equal, so it passes.
- **M3:** the status checks still pass; only R1 compares C's bytes.
- **M6:** in the invited-student module the activation send is itself replaced, so only R5 sees it. The fragment is
  the part of mock's message shared by every subtest's block (the count grows from subtest to subtest).

## The seam

`seam_check.py` compares the base and tip syntax trees of `users/views.py`:
- everything must match except the `otp` method (body and decorator; its arguments must match);
- inside `otp`, every statement must match except the two ruled If statements (the C test and the G test, named by
  their test text);
- those two must end in the neutral `Response` and contain no `raise`.

Before the pack (by parsing source only, no test run), it passes on the tip and prints the two branches. It stops on
three bad inputs:
- the base against itself (the G branch does not end in the neutral reply);
- an edit elsewhere in `views.py` ("something outside otp changed");
- an extra statement elsewhere inside `otp` ("otp changed outside the two ruled branches").

## Gate

- **Script:** `run_h198_gate.sh.txt`, the H-210 gate's form. The red run covers four modules and expects 15 blocks.
- **Before the test runs:** makemigrations, then the changed-file list (only `users/views.py` outside `docs/` and the
  four test files), then the seam check.
- **Own:** the four modules.
- **Near:** every other module that calls `/auth/otp`:
  - `users`: `tests_otp_no_oracle`, `tests_auth_endpoints`, `tests_throttling`, `tests_throttle_client_identity`,
    `tests_rate_limited_envelope`, `tests_patch_email`, `tests_verify_email_budget`, `tests_final_branches`,
    `tests_token_revocation`, `tests_email_warning_copy`, `tests_reset_otp_budget`, `tests_google_auth`;
  - `classrooms.test_school_admin_otp_deadend`;
  - `audit.tests_route_coverage`.
- **Also near:** `users.tests_auth_audit_doors`, `users.tests_admin_power_principle`,
  `users.tests_activation_email_domain`, `AutoGrader.tests_reason_codes`, and the 21 guards.
- **The mutants:** 8, with a 1320 s limit and the 300 s stall watchdog.
- **Base:** `7286717e`.
