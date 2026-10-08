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

At the request step: an unknown address answers 202; an invited ACTIVE student now answers the same 202 with the same bytes (tested byte for byte; before, it answered 400 "Email not verified.", which told a caller the address HAD an account). **CORRECTION (2026-10-08, Verifier 1's probe v6): the sentence "inactive never-verified rows stay refused" held only for the REQUEST step. The RESET step had no such check: a code that already existed (issued while the account was active, then the account switched off) let the reset run, stamp `email_verified_at` and change the password, giving "verified and inactive". Fixed inside this row, see the Delta section.** An INACTIVE never-verified row still answers 400 "Email not verified." (the Senior Manager: it stays refused; that 400 is the existing answer, also used by the API's documented hint to offer `VERIFY_EMAIL`, so I did not change it). **So the answers for "unknown" and "invited active" are the same, and "inactive never-verified" still differs from both: the old oracle for THAT case is unchanged, not widened.** If the Senior Manager wants the inactive case neutral too, the 400 hint for the student site goes with it; that is a separate decision.

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

- The student site's behaviour on the old 400 is not read; after this row a student who presses "forgot password" gets the code. A student of the OLD scheme (inactive) is still refused at the request step, and (since the delta) at the reset step too: they use Google sign-in, the verify-email link, or a teacher's remove-and-add (see the H-152 answer).
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

## Delta: the reset step refuses a switched-off, never-verified account (Senior Manager's ruling, 2026-10-08 17:00)

**The hole (Verifier 1's v6, seen red on 1c970b9d):** the invited student asks for a code, the account is then switched off, the code is used: the reset ran, stamped `email_verified_at` and set the password. **The guard:** in `reset_password`, right after the user and code row are found and before the lock check: `if not user.email_verified_at and not user.is_active: raise ParseError("Invalid email, OTP code, or new password.")` (the generic refusal an unknown address gets, so the reply is the same). Nothing is stamped or changed, whatever code exists.

**The already-issued code is LEFT, not invalidated.** Why: a refusal writes nothing (no attempt counted, no row deleted), so the refusal cannot be used to probe or wear down anything; the guard looks at the account every time, so the code is useless while the account stays switched off; it expires by itself in 15 minutes. If the account is switched back on within that time, the code works for the owner of the mailbox, which is what it was issued for. Deleting would add a write to a refusal for no gain. A test pins this (`test_a_refused_reset_leaves_the_code_and_its_budget_alone`), and mutant R11 (the guard deletes the code) is killed by it.

**Tests (written first, 9bb0b532; Verifier 1's v6 adopted as the first of them, with its two assertions on the stamp and the password):** T12 `test_a_reset_is_refused_for_a_switched_off_never_verified_account` (400, no tokens, no stamp, password unchanged); T13 `test_that_refusal_is_the_same_as_for_an_address_with_no_account` (same status and bytes); T14 `test_a_refused_reset_leaves_the_code_and_its_budget_alone`; T15 `test_a_switched_off_account_that_verified_its_email_still_resets` (control, green on the old code: the guard is for never-verified accounts only).

**Written expectations, before any run (rule 19, which mutant kills which assertion):**
- Step 0 (the previous tip's `users/views.py` under the new module): **Ran 15, THREE red: T12, T13, T14** (T15 and the eleven older tests green).
- Step 1: makemigrations no changes; the new module + related modules + the repo-wide guards OK; 13 mutants (R1..R7 as before with the same failing sets, plus):
  - R8 the guard removed: T12, T13, T14 (this is the only mutant that kills T12's status, token, stamp and password assertions together).
  - R9 the guard refuses every switched-off account: T15 only.
  - R10 the guard answers in its own words: T13 only.
  - R11 the guard deletes the issued code: T14 only.
  - R12 the guard stamps the email, then refuses: T12 only (isolates the stamp assertion).
  - R13 the guard sets the password, then refuses: T12 only (isolates the password assertion).
- Verifier 1 saw v6 red on 1c970b9d in his own baseline; my T12 has the same two assertions and is expected red at step 0 (the same code). Until my step 0 has run, T12 is expected red, not shown red.
- **Owning-app regression: not run, my call.** The guard is one `if` inside `reset_password`; it touches no model, serializer, cached route or shared helper, and it can only add a refusal for an account that is both inactive and never verified. The related modules (OTP no-oracle, reset budget, auth endpoints, token revocation, Google auth, login lockout, throttle identity) and the repo-wide guards are in step 1. Rule 20 not applicable. If the Release Engineer wants users + classrooms again, I run it on his word.
- Not shown: the cases where a never-verified active user is later switched off in production by an admin tool I did not read; what the student site shows for a 400 on the reset.

### Delta results (gate on bbf20687b4f4f03209ec26b7f8b23c0d86e45a07, base 1c970b9d; raw logs `delta_*` in this folder)

Script run_h164_delta_gate.sh 5614cabdec2ec6b7, once, one outer inhibit, on the Release Engineer's GRANT. All as written. Times from `date`.

| Step | Time (WAT) | Result |
|---|---|---|
| 0 reproduce-first (1c970b9d's views.py) | 2026-10-08 17:42:35 | Ran 15, THREE red, exactly the written three: T12 and T13 as FAIL, T14 (code left) as ERROR (the old code deletes the code row, so the test's lookup raises); red all the same (delta_prefix_base_production_failing.txt) |
| 1a makemigrations --check | 17:42:50 | no changes |
| 1 new + related modules + guards | 17:42:53 to 17:44:39 | Ran 475, OK (skipped=3) (delta_modules_and_guards.txt.gz) |
| 2 mutants R1..R13 | 17:44:39 to 17:46:15 | 13 of 13 KILLED; failing counts equal the written sets (R8 three; R9, R10, R11, R12, R13 one each); expected-but-passed empty; SURVIVED, KILLED_NOT_AS_EXPECTED, BROKEN all empty (delta_mutation_log.txt, delta_mutation_results.json, delta_mutant_logs/) |

T12 is now SHOWN red (step 0) and green (the run on the guarded tip), with its stamp and password assertions each isolated by a mutant (R12, R13). Load at the starts: 3.33 and 2.90. The owning-app regression was not run, by my stated call above; the Release Engineer accepted it for now, the Senior Manager may overrule at Gate 1.

## N3 (Senior Manager's ruling, 2026-10-08): the licence-invited teacher and the never-verified account with admin power

**Read, with lines (users/views.py and billing/license_service.py at 9dc3a9ca):**
- **A licence-invited teacher** (billing/license_service.py `_get_or_invite_teacher`: school and individual-subscription checks before the account is made, 1190-1219; created active, never verified, `must_change_password` True, 1252-1268): sign-in has no check of its own for them. No production sign-in code reads `email_verified_at` (every use: users/views.py request step 1131, verify 978/1115, reset 1316/1347, change-password request 1392, school-admin activation 1762, Google 1944/1986-1996, classrooms/services/enrollment.py 260, classrooms/serializers.py 1059). The temporary password and the reset code open the same door. **The ruling: the road stays open.** What it creates, in the package's words: *"an invited teacher who has never signed in can use Forgot password; after that a re-invite no longer replaces their password"* (the licence re-add resends a fresh temporary password only while `must_change_password` is True, license_service.py 1231; a reset clears it).
- **Does a super admin have any extra step at sign-in?** I found none: no two-factor, TOTP or second step anywhere in `users/` or `AutoGrader/` (grep for two_factor, 2fa, totp, mfa, otp_required: only migrations of the reset-code table matched). Sign-in for them is the password alone.
- **What `create_superuser` leaves as `user_type` (users/models.py 110-114 and 51-62):** the field default, **TEACHER**. `create_superuser` sets `is_staff` and `is_superuser` True and `is_active` True and nothing else; it does not set `user_type` or `email_verified_at`. The code already says so (users/views.py 704-706, "a createsuperuser account - is_superuser but user_type TEACHER", H-19). The Django admin ticks `is_staff`, `is_superuser` and the type independently (users/admin.py 54). **So the three marks can differ, and the condition names all three**; a test pins the command-line shape (`test_the_request_refuses_a_command_line_superuser` asserts the type is TEACHER).

**The change (users/views.py):** `_holds_admin_power(user)` = `is_staff or is_superuser or user_type == SUPER_ADMIN`. The request step refuses `not email_verified_at and (not is_active or _holds_admin_power)` with today's words ("Email not verified.", status 400); the reset step refuses the same condition with the generic answer an unknown address gets (the delta guard's words). A VERIFIED super admin is unchanged. **Said plainly:** the request step's refusal for such an account is a 400, not the neutral 202, exactly as for an inactive never-verified account today: a guesser can learn that an address is an unverified account (this was already so for the inactive case, and the ruling is to keep today's words so nothing NEW is told).

**Tests (first, 078106a4; 15 new, 30 in the module):** licence teacher: sent a code (LREQ), reset stamps/clears/signs in (LRESET), a licence re-add after a reset sends no new temporary password and the chosen password signs in through the login route (LREADD). Refusals, request step: staff only, superuser flag only, SUPER_ADMIN type only, the command-line shape, and same words as the inactive refusal; reset step: the same four and same answer as an unknown address. Controls: a verified super admin is still sent a code and can still reset.

**Written expectations, before any run (rule 19; mutate.py fdb8a852fd7d0b4b lists every expected set):**
- Step 0 (9dc3a9ca's views.py under the module): **Ran 30, TEN red**: the four request-step refusals, the request same-words test, the four reset-step refusals, the reset same-as-unknown test. Green on the old code by design (characterisation, each seen red by a mutant): the three licence tests (LREQ by R1, LRESET by R3/R5/R6, LREADD by R5) and the two verified-super-admin controls (CVREQ by R18, CVRESET by R9/R21).
- Step 1: makemigrations no changes; module + related modules + repo-wide guards OK; **22 mutants**: R1..R13 as before with their sets widened where the new tests also fail (R1 adds LREQ; R2 adds the four request refusals and the request same-words; R3 adds LRESET; R5 adds LRESET and LREADD; R6 adds LRESET; R8 adds the four reset refusals and the reset same-words; R9 adds CVRESET; R10 adds the reset same-words; R12/R13 add the four reset refusals), plus: R14 helper forgets is_staff (QSTAFF, RSTAFF); R15 forgets is_superuser (QFLAG, RFLAG); R16 forgets the type (QTYPE, RTYPE); R17 request has no admin-power refusal (the four request refusals + same-words); R18 request refuses a verified admin too (CVREQ); R19 request answers an admin in its own words (same-words only); R20 reset has no admin-power refusal (four reset refusals + reset same-words); R21 reset refuses a verified admin too (CVRESET); R22 reset answers an admin in its own words (reset same-words only). **Said plainly: the command-line tests QCMD and RCMD are killed only by R17/R2 and R20/R8 (removing the whole arm), not by any single dropped condition, because each of the two flags alone is enough to refuse it.**
- Not shown: any real mailbox or throttle; what the super-admin screen does with an account that is refused; a Django-admin-created staff user whose flags are set after creation.
- Users-app regression: ONCE, on the final tip after Verifier 1 (Senior Manager's order); rule 20 not applicable.

### N3 first attempt STOPPED at step 0 (2026-10-08 18:36, tip 5e9ce771; raw log `n3_first_attempt_5e9ce771_prefix_base_production_failing.txt`)

Step 0 ran 30 tests and found **13 red against 10 written**; the script stopped itself ("step 0 differs from the written expectation"), nothing after step 0 ran, the source was restored. Nothing was re-run.
- The ten written tests were red, as written.
- **Three EXTRA reds, none written: the three licence tests.** Reason, read from the log: they failed in their own fixture (`licence_invite`, line 373: `AssertionError: 0 != 1`), before reaching any code under test. My fixture counted calls on the mocked task itself, but the invitation mail is sent as `send_email_task.delay(...)` (billing/license_service.py 1313) inside a `transaction.on_commit` callback (1337), which a test transaction does not run unless it is captured. The tests would have been red on EVERY tip, the fixed one too: **they proved nothing, and their later assertions (stamp, flag, re-add, sign-in) have never run.** My expectation that they would be green on the old code was a prediction about assertions that were never reached.
- Fix (a tests-only change, no run yet): the helper call is wrapped in `captureOnCommitCallbacks(execute=True)` and the count is read from `.delay`. Expectation for a re-run, unchanged: Ran 30, ten red (the same ten); the three licence tests green on the old code IF their later assertions are right, which no run has yet shown.
- The Senior Manager's rule: a red or a stop is kept and reported; a re-run waits for his word through the Release Engineer.

### N3 re-run: what I read by hand BEFORE it, and what I expect (Senior Manager approved the re-run, once)

The three licence tests' later assertions had never run, so I read each against the code (nothing run):
- **Setup (`licence_invite`)**: the helper `_get_or_invite_teacher` sends the mail through `transaction.on_commit(_dispatch)` (billing/license_service.py 1337), and `_dispatch` calls `send_email_task.delay(...)` once with no condition (1313-1321); the test now runs the call inside `captureOnCommitCallbacks(execute=True)` and counts `.delay`. Creation itself already worked in the first attempt (the four assertions before the count passed).
- **LREQ (code sent)**: the request step at 9dc3a9ca refuses only `not verified and not active`; the teacher is active, a plain TEACHER, not staff, not superuser, so it reaches the code send; the send is `users.views.safe_delay`, patched in setUp exactly as for the student test T1.
- **LRESET (stamps, clears, signs in)**: the reset at 9dc3a9ca already stamps `email_verified_at`, clears `must_change_password`, saves and `stamp_last_login` (a queryset update), the same lines T6, T8 and T10 exercise for a student; the response is 200 with tokens for any user type (users/views.py 1382-1389, no per-type branch).
- **LREAD (re-add keeps the chosen password)**: the existing-teacher branch finds the account (`teacher_account_for_email`), same school, no individual subscription, and resends only `if user.must_change_password` (license_service.py 1231); the reset cleared it, so no second `.delay` call. Sign-in goes through `auth/login`, whose serializer has a lockout and a STUDENT-only branch (users/serializers.py 467-496): nothing teacher-specific.

**Expectation for the re-run (written before it):** step 0 on 9dc3a9ca's `users/views.py`: Ran 30, TEN red (the same ten: four request refusals, request same-words, four reset refusals, reset same-as-unknown). **The three licence tests are expected GREEN at step 0, and the reason is that the road they exercise (an active, never-verified teacher asking for and using a reset code) is already open at 9dc3a9ca: the H-164 change itself opened it for every active never-verified account.** They are not red on the old code because the old code differs for them; they are seen red only by mutants R1 (LREQ), R3/R5/R6 (LRESET) and R5 (LREAD). The two verified-admin controls are also green at step 0 (seen red by R9, R18, R21).

### N3 results (re-run on f7f33822f5f305c4cb76367a022937a18df87331, base 9dc3a9ca; raw logs `n3_*` in this folder; the first attempt's log is kept beside them)

Runner run_h164_n3_gate.sh c702f0e69d0bf574, once, one outer inhibit, on the Release Engineer's GRANT, the one re-run the Senior Manager approved. Times from `date`. The runner finished (exit 0); it reported one mutant as KILLED_NOT_AS_EXPECTED.

| Step | Time (WAT) | Result |
|---|---|---|
| 0 reproduce-first (9dc3a9ca's views.py) | 2026-10-08 18:43:59 | Ran 30, TEN red, exactly the ten written; the three licence tests and the two verified-admin controls GREEN, as written (n3_prefix_base_production_failing.txt) |
| 1a makemigrations --check | 18:44:19 | no changes |
| 1 new + related modules + guards | 18:44:22 to 18:46:33 | Ran 490, OK (skipped=3) (n3_modules_and_guards.txt.gz) |
| 2 mutants R1..R22 | 18:46:33 to 18:49:31 | 21 KILLED, **1 KILLED_NOT_AS_EXPECTED (R2)**, SURVIVED [], BROKEN [] (n3_mutation_log.txt, n3_mutation_results.json, n3_mutant_logs/) |

### ADDED AFTER THE RUN (named with reasons; nothing was repaired or re-run)

1. **R2 (the request step refuses nobody): one written test PASSED.** Written: the four request refusals, the inactive-refusal test AND `test_that_refusal_says_what_the_inactive_refusal_says`. Found: the first five failed; the same-words test passed. Reason: R2 removes the whole refusal, inactive case included, so both sides of that test's comparison answer 202 and are equal. **My written set was wrong, not the tests:** that test compares an admin account with an inactive one, and R2 changes both the same way. The test is still seen red by R17 (admin arm removed) and R19 (own words). No test is unguarded by this.
2. **R15 (the helper forgets is_superuser): two EXTRA failures**, `test_that_refusal_says_what_the_inactive_refusal_says` and `test_that_reset_refusal_is_the_same_as_for_an_address_with_no_account`. The runner counts a mutant as killed when every expected test failed, so it printed KILLED. Reason: my two same-words tests build their admin account with `is_superuser=True` ONLY, so dropping that flag lets the account through there too. I wrote the set from the flag tests alone and forgot which flag the same-words tests use. The written R15 set (flag tests only) is a subset of the real one.
3. Every other mutant failed exactly its written set (no extras, nothing missing).

**Plain reading:** 21 of 22 mutants were killed exactly as written; R2 and R15 differ from my written sets for the two reasons above, both errors in my expected sets, none a survivor. `mutate.py` is left as run (fdb8a852fd7d0b4b) so the record matches the run.
