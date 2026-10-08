# H-164: "forgot password" works for an invited student who has never signed in (option A)

Branch `task/h164-reset-for-an-invited-student`, base beta 3f2ad13e (batch 13). Written by ed (Security Engineer), 2026-10-08. Severity LOW-MEDIUM (Senior Manager). Design note (the record):
`~/Documents/Projects/GAP-planning/H-164-forgot-password-for-an-invited-student-design.md`. **Nothing has been run on this branch yet.** Marks: READ = read in the code; NOT RUN = reasoning.

## The change (READ; NOT RUN), `users/views.py` only

1. `POST /auth/otp` with `RESET_PASSWORD`: the refusal "Email not verified." applies only if the account is also INACTIVE (`not email_verified_at and not is_active`). An ACTIVE never-verified account (an invited student) is sent a reset code, with the same neutral 202 as an unknown address; the reset lock is unchanged.
2. `POST /auth/reset-password`, on SUCCESS only: stamps `email_verified_at` (if empty) in the same `save()` as the new password; clears `must_change_password`; then stamps `last_login` (`stamp_last_login`, as every sign-in does).
No new Stripe or service call, no migration, no serializer change.

## Decisions the Senior Manager asked me to state

- **`must_change_password` is CLEARED by a successful reset.** Why: the flag stands for "the temporary password you were emailed is still in use"; a reset replaces it with the student's own, so keeping the flag would make the student change a password they just chose. `change_password` already clears it for the same reason (users/views.py, the `must_change_password = False` before its save). A test pins both directions (cleared for the invited student; an established user's false flag stays false).
- **The reset stamps `last_login`.** `reset_password` returns tokens (it signs the student in) but did not stamp `last_login`. Read at beta 3f2ad13e: `has_signed_in` (classrooms/services/enrollment.py:229) is `last_login is not None` or any `UserActivity`; `was_never_activated` (:243) needs `not is_active`, `email_verified_at is None` and not `has_signed_in`. **Stamping `email_verified_at` on an ACTIVE account changes neither** (the first needs an inactive row, the second ignores the field). But a reset that leaves `last_login` empty leaves the student "never signed in", and a teacher's later add of that student to another course would then take the "never signed in" branch of `enroll_student_by_email` and OVERWRITE the password they just chose with a fresh temporary one, emailing it. So the reset stamps `last_login` like every other sign-in (login serializer, Google sign-in). A test pins it (`test_a_later_add_to_another_course_keeps_the_password_they_chose`).
- **The "same save" claim is by reading, not by a test:** the stamp, the password and the flag are set on the one `user` object and written by the one `user.save()` that already existed (users/views.py reset_password). No test isolates "same save" from "two saves"; the tests pin the outcomes (stamped on success, not on a wrong code, not on the request).

## The enumeration condition, said precisely

At the request step: an unknown address answers 202; an invited ACTIVE student now answers the same 202 with the same bytes (tested byte for byte; before, it answered 400 "Email not verified.", which told a caller the address HAD an account). An INACTIVE never-verified row still answers 400 "Email not verified." (the Senior Manager: it stays refused; that 400 is the existing answer, also used by the API's documented hint to offer `VERIFY_EMAIL`, so I did not change it). **So the answers for "unknown" and "invited active" are the same, and "inactive never-verified" still differs from both: the old oracle for THAT case is unchanged, not widened.** If the Senior Manager wants the inactive case neutral too, the 400 hint for the student site goes with it; that is a separate decision.

## Tests (written first, 22f26279; NOT RUN)

`users/tests_reset_for_an_invited_student.py`, eleven tests built on the real invitation path (`enroll_student_by_email`): the request step (a code is sent to an invited student; the reply is byte-identical to an unknown address's; an inactive never-verified row is still refused; a locked reset sends nothing and answers the same; the request alone does not verify) and the reset step (success stamps the email and sets the password; a wrong code stamps nothing; the flag is cleared; an established account's flag is not invented; the reset signs the student in; a later add keeps the chosen password).

## Written expectations, before any run

- **Step 0 (reproduce-first)**: `users/views.py` as at 3f2ad13e under the new module: **Ran 11, SEVEN red**: `test_an_invited_student_who_never_signed_in_is_sent_a_reset_code`, `test_the_reply_is_the_same_as_for_an_address_with_no_account`, `test_a_locked_reset_sends_nothing_and_answers_the_same`, `test_a_successful_reset_sets_the_password_and_stamps_the_email`, `test_a_successful_reset_clears_must_change_password`, `test_a_reset_signs_the_student_in`, `test_a_later_add_to_another_course_keeps_the_password_they_chose`.
  **Green on the old code, by design:** `test_an_inactive_never_verified_row_is_still_refused`, `test_the_request_alone_does_not_verify_the_email`, `test_a_wrong_code_stamps_nothing`, `test_a_reset_leaves_an_established_accounts_flag_alone`; the first three are seen red by a mutant (R2, R4, R7); **the last (an established account's flag) is isolated by no mutant**: a guard, not a proof.
- **Step 1**: `makemigrations --check` no changes; the new module (Ran 11, OK), `tests_otp_no_oracle`, `tests_reset_otp_budget`, `tests_auth_endpoints`, `tests_token_revocation`, `tests_google_auth`, `tests_login_lockout`, `tests_throttle_client_identity`, `classrooms.tests_support_add_by_email` and the repo-wide guard modules: OK, no FAIL or ERROR line. Rule 20 (the cache payload test) does not apply: no serializer or cached route code changes (the reset's `save()` fires the existing receivers).
- **Step 2, mutants (7)**, each fails exactly the tests named: R1 (the old refusal back): the code-sent test, the byte-identical-reply test, the locked-reset test; R2 (nobody refused): the inactive-refused test; R3 (no stamp on success): the success test; R4 (the request stamps the email): the request-alone test; R5 (the flag kept): the flag test; R6 (no `last_login` stamp): the signs-in test and the later-add test; R7 (a wrong code stamps): the wrong-code test.
- **Step 3**: the users app and the classrooms app, one serial run each: OK.
- Nothing is re-run without the Release Engineer's word; a difference is reported, not repaired in place.

## Known limits, in plain words

- The student site's behaviour on the old 400 is not read; after this row a student who presses "forgot password" gets the code. A student of the OLD scheme (inactive) is still refused: they use Google sign-in, the verify-email link, or a teacher's remove-and-add (see the H-152 answer).
- A licence-invited teacher (active, never verified, emailed temporary password) now also gets the reset road: the same shape, the same rule.
- The reset code is still 6 digits with the existing budget (5 wrong codes, 30-minute lock): the exposure of an invited student's account to a code guess is what an established account's already is.

## Not shown by any run so far

Production behaviour (mail delivery, the real throttle key). The student site's handling of the reply is not read. The established-account flag test is a guard no mutant isolates. Nothing else outstanding from the written expectations.

## Results (gate on c82882a4ec86a141fae44fd65403dfbfb1f0aa08, base 3f2ad13e; raw logs in this folder)

Script run_h164_gate.sh ecb10be877ae6a4d, each step once, one outer inhibit each, on the Release Engineer's GO. All as written.

| Step | Time (WAT, from `date`) | Result |
|---|---|---|
| 0 reproduce-first (old code) | 2026-10-08 16:31 | Ran 11, 7 red, exactly the seven named above (prefix_base_production_failing.txt) |
| 1a makemigrations --check | 16:31:26 | no changes (makemigrations_check.txt) |
| 1 new + related modules + guards | 16:31:29 to 16:33:31 | Ran 471, OK (skipped=3) (modules_and_guards.txt.gz) |
| 2 mutants R1..R7 | 16:33:31 to 16:34:42 | 7 of 7 KILLED, each failing set the written one; SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN all empty (mutation_log.txt, mutation_results.json, mutant_logs/) |
| 3 users + classrooms, serial | 16:38:05 to 16:40:39 | Ran 1171, OK (skipped=4) (regression.txt.gz) |

Step 3 ran at c82882a4, before this record was committed; this commit is docs only over it. Load at the starts: 3.01 (step 1), 2.15 (step 3). Rule 20 not applicable (no serializer or cached-route change).
