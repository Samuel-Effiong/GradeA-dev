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
  - `users.tests_auth_endpoints.OTPEndpointBranchTests` (added after the red gate on 039c6135, see
    `red_gate_039c6135/RED_GATE.md`). Each now compares the body with an unknown address's:
    - `test_verify_email_on_an_already_verified_account_is_refused` (C), renamed
      `test_verify_email_on_an_already_verified_account_gets_the_neutral_answer`; it also checks no activation token
      is made;
    - `test_reset_password_on_an_unverified_account_is_refused` (G, switched off, no admin power), renamed
      `test_reset_password_on_an_unverified_account_gets_the_neutral_answer`; it checks the activation email is sent
      and no reset code is made.
- **How they were found.** The first search looked for the route only inside each test function. It found 7, and
  missed the two above, whose class holds the route in `setUp` (`self.url`). The second search, after the red gate,
  is by syntax tree over every test file that names `auth-otp` or `/auth/otp` anywhere. For each test that asserts a
  400, directly or through a helper, it marks whether the test reaches the route: directly, through a module constant,
  through a class or `self.` attribute set to it, or through a helper method that does. A grep then checked the other
  refusal forms (`is_client_error`, `HTTP_4xx`, `!= 202`, `status_code in`): none is asserted on this route. No test
  file imports a helper or base class that carries the route.

  | Test file (names the route) | Tests asserting a 400 | Of those, reaching `/auth/otp` | C or G? |
  |---|---|---|---|
  | `users.tests_auth_endpoints` | 17 | `test_an_invalid_otp_type_is_rejected` | No: a bad `otp_type`, the serializer's 400 (bad input, kept) |
  | | | the two tests above | **Yes, C and G** (changed) |
  | | | (the other 14: logout, request-change-password, register-school-admin, reset-password) | No: other routes |
  | `audit.tests_route_coverage` | 4 | `test_a_route_that_is_not_a_door_records_nothing` | No: a unit test of the audit middleware; the 400 is fed into a fake request, the view never runs |
  | `users.tests_patch_email` | 8 (5 through `assertRefused`) | `test_the_stolen_token_takeover_chain_is_closed` | No: its code request is for an unknown address and is not asserted; the 400 is `/auth/reset-password`'s |
  | `users.tests_reset_otp_budget` | 1 | `test_an_ordinary_wrong_guess_is_still_the_generic_400` | No: `request_code()` asks for a code for an active account; the 400 is the wrong guess at `/auth/reset-password` |
  | `users.tests_verify_email_budget` | 9 (6 through `spend_budget`) | `test_a_resent_code_neither_arrives_nor_unlocks` | No: the 400s are `/auth/verify`'s; its code request asserts 202 |
  | | | `test_an_expired_code_spends_the_budget_too` | No: names `/auth/otp` only in its docstring; the 400 is `/auth/verify`'s |
  | `users.tests_reset_for_an_invited_student` | 15 (11 through helpers) | none | No: every 400 is `/auth/reset-password`'s or `/auth/verify`'s; its code-request tests were changed by this row and assert 202 |
  | `users.tests_switched_off_means_out` | 3 | none | No: `/auth/verify` and `/auth/reset-password`; its C test was changed by this row |
  | `classrooms.tests_school_admin_active_invite` | 1 | none | No: the old registration-completion route; its C test was changed by this row |
  | `users.tests_google_auth` | 11 | none | No: `/auth/google` |
  | `users.tests_throttling` | 1 | none | No: `/auth/reset-password` |
  | `classrooms.test_school_admin_otp_deadend`, `users.tests_email_warning_copy`, `users.tests_final_branches`, `users.tests_h198_otp_neutral`, `users.tests_otp_no_oracle`, `users.tests_rate_limited_envelope`, `users.tests_token_revocation` | 0 | none | No 400 asserted |
- **Not shown by the tests:** what the frontend does with the 202 for C and G (not read, the user's ruling); real mail
  delivery (the mail task is replaced, as the existing tests do).

## Expected reds against 7286717e (written before any run)

The red run takes `users/views.py` back to the base and runs the new module and the four changed modules as at the
tip. (The first pack wrote 15 blocks over four modules; `users.tests_auth_endpoints` and its two reds were added after
the red gate on 039c6135.)

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
users.tests_auth_endpoints (2 red)                                                   "400 != 202" each
  test_verify_email_on_an_already_verified_account_gets_the_neutral_answer,
  test_reset_password_on_an_unverified_account_gets_the_neutral_answer
Total: 17 FAIL blocks, no ERROR. Every other test of the five modules is green. The script counts them.
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

The battery is the new module, `users.tests_reset_for_an_invited_student`, `users.tests_switched_off_means_out`,
`users.tests_otp_no_oracle` and `users.tests_auth_endpoints` (added after the red gate). No mutant reaches
`tests_otp_no_oracle`, whose states already answered 202; it is in the battery to show none of them changes. In
`tests_auth_endpoints` only `OTPEndpointBranchTests` calls the route. Its other four tests (an unknown address, a bad
`otp_type`, F, I) are not C or G, so only the two changed tests are reached. They are `EP-C` and `EP-G` below.

A mutant is KILLED only if each expected test fails AND every one of its blocks holds the fragment written for that
test (rule 22, in `mutate.py`).

| Id | Break | Expected to fail (fragment) |
|---|---|---|
| M1 | C's 400 put back | R1, the verified-admin test, the active-verified test, EP-C (`400 != 202`) |
| M2 | G's 400 put back | R2, R4, R5, the inactive test, the four request-refuses tests, EP-G (`400 != 202`); R3 (`Called 0 times`) |
| M3 | C answers 202 with other bytes | R1, EP-C (`' != b'`) |
| M4 | G issues a reset code | R3, R4, R5, the inactive test, the four request-refuses tests, EP-G (`True is not false`) |
| M5 | C re-sends the activation email | K1 (`to not have been called`); the active-verified test, EP-C (`is not None`) |
| M6 | G with admin power is sent the activation email too | R5 (`to not have been called`) |
| M7 | G ignores H-53's lock | R4 (`to not have been called`) |
| M8 | G without admin power is sent nothing | R3, EP-G (`Called 0 times`) |

- **EP-C, in assert order:** status, then the body against the unknown address's, then no activation token.
  - M1 fails the status.
  - M3 passes the status and fails the body.
  - M5 answers the same 202 bytes. The real activation road runs (only its mail task is replaced) and sets a token, so
    the third check fails: "'…' is not None", the token in the quotes.
- **EP-G, in assert order:** status, then the body, then the activation send called once (replaced at
  `users.views.send_user_activation_email`), then no reset code.
  - M2 fails the status.
  - M8 passes the status and body and fails the send: "… Called 0 times.".
  - M4 passes the first three checks and fails the last: "True is not false".
  - M6 and M7 do not change this account's road: it has no admin power, and the address is not locked.

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

- **Script:** `run_h198_gate.sh.txt`, the H-210 gate's form. The red run covers five modules and expects 17 blocks.
- **Before the test runs:** makemigrations, then the changed-file list (only `users/views.py` outside `docs/` and the
  five test files), then the seam check.
- **Own:** the five modules (`users.tests_auth_endpoints` moved here from near).
- **Near:** every other module that calls `/auth/otp`:
  - `users`: `tests_otp_no_oracle`, `tests_throttling`, `tests_throttle_client_identity`,
    `tests_rate_limited_envelope`, `tests_patch_email`, `tests_verify_email_budget`, `tests_final_branches`,
    `tests_token_revocation`, `tests_email_warning_copy`, `tests_reset_otp_budget`, `tests_google_auth`;
  - `classrooms.test_school_admin_otp_deadend`;
  - `audit.tests_route_coverage`.
- **Also near:** `users.tests_auth_audit_doors`, `users.tests_admin_power_principle`,
  `users.tests_activation_email_domain`, `AutoGrader.tests_reason_codes`, and the 21 guards.
- **The mutants:** 8, with a 1320 s limit and the 300 s stall watchdog.
- **Base:** `7286717e`.
