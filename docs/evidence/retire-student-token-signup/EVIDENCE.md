# Retire the code-based student sign-up — landing (A)

Branch `task/retire-student-token-signup` off beta `be78221`. Founder decision (relayed by the SM, 2026-09-28), the H-47 follow-through: students imported from a spreadsheet use the ready-to-use flow, and the old code-based student sign-up is then removed. Written by the Security Engineer.

Two landings:
- **(A) this one:** stop minting student codes, plus the founder's backfill runbook for the rows that already have one.
- **(B) later**, only after the backfill has run on production and reports zero students holding a code: remove `POST /auth/register/student`, `POST /course/student/renew-student-token`, their serializers, the renewal and invitation emails, and dead student `activation_token` code, with a guard test that the routes 404. Teacher `/auth/verify` is untouched.

**Status: (A) BUILT on the branch, local gates pass. NOT landed.**
- The first version (`b10c798`) was REJECTED by the Verification Engineer; the fix is in §1a.
- The re-verification at `65321ec` was REJECTED on two new blockers; the fix is in §1b.
- See `VERIFICATION.md` for both verdicts. This is the second rework, awaiting re-verification.

## 1b. Second rework: last_login without the cache fan-out; deactivated accounts stay off

**Blocker 1: `UPDATE_LAST_LOGIN` fanned out on every login.** simplejwt stamps `last_login` with `save()`, and `post_save` → `clear_user_cache` bumps the user, any-user and **global** generations and sweeps nine key patterns. So every login went cold on every student's course list and the superadmin dashboards.

Fix (SM ruling):
- `UPDATE_LAST_LOGIN` is back **off**, with a comment in settings saying why.
- The new `users.services.stamp_last_login(user)` is a queryset `update(last_login=now)`, which sends no signal. `last_login` is in no cached payload, so nothing needs invalidating.
- It is called by:
  - the password login (`CustomTokenObtainPairSerializer.validate`, after a successful authenticate);
  - **Google sign-in**, which mints its tokens itself and so would otherwise leave no trace. That was the Verification Engineer's non-blocking note, fixed here too.

**Blocker 2: a teacher's add re-enabled an account someone deactivated.** The admin "Mark selected users as inactive" action is a plain `is_active=False`. `enroll_student_by_email` sent every not-(active and signed-in) account down the reset branch, which force-set `is_active=True` and emailed credentials. Before (A) this was a bug on the single-add path only; (A) extended it to roster imports.

Fix (SM product rule, both paths, plus the backfill):
- A teacher's action **never** re-enables an inactive account unless `was_never_activated(student)`. That means inactive, `email_verified_at` is None, and no `last_login` / `UserActivity`.
- Every old activation door stamps `email_verified_at`: `/auth/register/student`, `/auth/verify` and Google sign-in. So a legacy pending row has none, while an account that was ever used has one or a sign-in signal.

What happens to any other inactive account:
- **No write, no email, no enrollment.** `AccountDisabledError` (an `EnrollmentError`) carries "This student's account is disabled. Contact support if they should have access."
- **Roster row:** `status: "skipped"` with that message.
- **Single add:** 400 with that message.
- The check runs **after** the cross-school gate, so it never tells a teacher anything about another school's accounts.
- **Backfill (SM confirmed):** a deactivated row holding a code gets the code cleared only. It is not converted or emailed, and it has its own counter "code-only cleared (deactivated account, left inactive, not emailed)". `RUNBOOK_backfill.md` is updated.

**Accepted edge case (SM):** an account deactivated **before it was ever used** (never verified, never signed in) can't be told apart from a legacy pending row. Nothing records who set `is_active=False`, so it is treated as one and re-invited.

**Tests:**

`users/tests_last_login_stamp.py`:
- A student login and a teacher login each stamp `last_login` with **0 `bump_many` and 0 `delete_cache_patterns` calls**. A recording wrapper on the receiver's two helpers checks this through the real `/auth/login`.
- A failed login stamps nothing.
- A Google sign-in stamps `last_login`.
- `UPDATE_LAST_LOGIN` is off.

`classrooms/tests_roster_ready_to_use.py::DeactivatedAccountIsNeverReenabledTests` (the Verification Engineer's "Dee" scenario, one test for each "was used" signal):
- The roster skips a deactivated student who signed in, one with only `UserActivity`, and one who was only verified.
- Single add refuses a deactivated student.
- Single add still invites a never-activated legacy row. The roster's legacy-row test already existed.
- Every refusal asserts: still inactive, own password still works, no enrollment, no email.

`classrooms/tests_backfill_pending_student_invites.py::test_a_deactivated_account_only_loses_its_code`.

**Gates for the rework:**
- **Reproduce-first** on `65321ec`'s code (`prefix_65321ec_rework_failing.txt`): 5 fail. They are the student and teacher login fan-out tests, Google not stamping, `UPDATE_LAST_LOGIN` being on, and the backfill converting a deactivated row. The failed-login test is a guard and passes on both. The roster/single-add module imports the new message constant, so it can't load on the old code; the Verification Engineer's scratch test reproduced that behaviour on `65321ec`, and D1–D4 prove the refusal.
- **Mutation:** `mutate.py`, **23 mutants, 23 killed**, with every anchor asserted unique. S5 was replaced, because the setting it mutated is gone. The 8 new mutants are:
  - S5: the password login doesn't stamp.
  - S6: Google sign-in doesn't stamp.
  - S7: the stamp goes through `save()` (fan-out).
  - S8: `UPDATE_LAST_LOGIN` is back on.
  - D1: the refusal is removed.
  - D2: `email_verified_at` is ignored.
  - D3: sign-in is ignored by `was_never_activated`.
  - D4: the roster reports a disabled account as failed.
  - D5: the backfill converts a deactivated row.
- **Regression:** `classrooms` + `users` + the 4 AutoGrader cache modules (`tests_cache_dashboard_2329`, `tests_cache_user_fanout`, `tests_cache_dashboard_wide`, `tests_cache_superadmin_1522`), with `EXEMPT_EMAIL_DOMAINS=` → **1027 OK** (skipped=4).
- **mypy:** whole-repo `pre-commit run mypy --all-files` → Passed.
- **Full suite:** per 0b's rule 2026-09-29, it's covered by the batch run. The earlier full run at `65321ec` gave 4888 tests with 1 failure, the H-44 pdf flake, which also flakes in isolation. It had 0 "Blocked real outbound".

## 1a. Rework after verification: only never-signed-in students get a new password

The rejection: `enroll_student_by_email` treated `must_change_password` as "still onboarding". That flag is informational and only cleared by `/auth/change-password`, so a student who signs in and simply uses the app keeps it for good. Re-importing such a student into a second course reset their password, emailed a new one and left the enrollment PENDING. This was **pre-existing on the single-add path (beta and production)**; landing (A) extended it to every roster import.

**SM ruling, implemented in `enroll_student_by_email` (so both paths):** an existing student gets fresh credentials only if they have **never signed in**. That means either an `is_active=False` legacy row, or an account with no `last_login` **and** no `UserActivity` row (`has_signed_in`). Every other existing student is enrolled ENROLLED and gets the "added to course" email; their password and sessions are untouched. `must_change_password` is no longer read here.

**Why login now stamps `last_login`:** `UserActivity` is written only by middleware on *authenticated* requests, and the login request itself is anonymous. JWT login never set `last_login`. So a student who signed in once and did nothing else left no trace, and would still have been treated as "never signed in". Every successful sign-in now records it. This first used `SIMPLE_JWT["UPDATE_LAST_LOGIN"] = True`, which §1b **replaced** with a signal-free stamp. This is forward-only: sign-ins before the deploy show up only through `UserActivity`, which a real session practically always writes on its first request after login.

**Teacher-facing response:** a roster row for an existing student who has signed in now reports `status: "enrolled", type: "existing_student"`. `"invited"` / `"invitation"` means credentials were actually emailed. The single add's `is_new_student` has the same meaning. The new `type` value is a small frontend-visible addition.

**Placeholder `@student.local` students in the backfill (founder, 2026-09-28):** they're intentionally inaccessible to the student. The backfill clears their code but neither activates nor emails them. A count is reported and nothing is grouped per teacher.

## 1. What changed

| Where | Before | After |
|---|---|---|
| `classrooms/services/roster_import.py` `_import_row_with_email` (bulk/CSV rows with an email) | New student created **inactive with a 6-digit activation code** (24 h), `send_bulk_enrollment_email` linking to `/register/student/<code>`. Existing accounts enrolled ENROLLED whatever their state | Goes through `enrollment.enroll_student_by_email`, the single-add path. A new student is **active immediately** with a generated temporary password and gets `send_student_login_invitation_email`. An existing account passes the shared cross-school/staff gate: already onboarded → ENROLLED + "added to course" email; still onboarding or a legacy inactive row → promoted, fresh password, PENDING. **No code is minted.** Response contract unchanged (`skipped` / `failed` / `invited`) |
| `classrooms/services/enrollment.py` | `enroll_student_by_email(course, email)` | Optional `first_name/middle_name/last_name`, used only when a new account is created (never overwrites an existing account's names). The promotion path now also clears a legacy row's dead `activation_token`/`activation_expires` |
| `backfill_pending_student_invites` (founder-run cutover) | Printed each student's **email**; left the old code on converted rows; left rows with no pending enrollment inactive **with their code** | Prints **ids only** (safe for Railway logs); clears the code on every row it touches; rows with no pending enrollment stay inactive but lose the code; flags `@student.local` placeholder addresses (credentials email can't be delivered); reports "Inactive students still holding a code: N", **the gate for landing (B)** |

Rows **without** an email were already on the ready-to-use path (`DirectAddStudentSerializer`) and are unchanged.

`must_change_password` is set on new and promoted students but is **informational only**. The server doesn't enforce it (product decision, `users/authentication.py`).

## 2. Gates

| Gate | Result | Evidence |
|---|---|---|
| 1 Regression | Whole `classrooms` + `users` apps: **848 tests OK** (4 skipped). Two existing tests pinned the old behaviour (an inactive roster student with a code) and were updated to the new contract: `test_bulk_enrollment.test_bulk_add_via_tsv_string`, and `tests.ActivationTokenValidityWindowTest` (now pins that the roster mints **no** code; the 24 h window on the one remaining minter, `renew_activation_token`, is pinned in `users.tests_models_and_admin`) | test runs |
| 2 Mutation | **15 mutants, 15 killed** (`mutate.py`, every anchor asserted unique): roster names dropped, already-enrolled not skipped, new-student names ignored, promotion keeps the code; backfill convert keeps the code, orphan code not cleared, dry run clears codes, placeholder code not cleared, output names the email, placeholder converted like a real address; and for the rework: `must_change_password` back as the onboarding signal (the rejected behaviour), `last_login` ignored, `UserActivity` ignored, every row reported as "invited", login not stamping `last_login`. The unique-anchor guard caught two anchors that became ambiguous during the rework (the orphan and placeholder branches both clear the code) and stopped the harness instead of mutating the wrong site | `mutation_log.txt`, `mutation_results.json` |
| 4 Adversarial | The login path is exercised for real: the password captured from the emailed-credentials call logs in (200) and flips the enrollment PENDING → ENROLLED. A cross-school student and a staff address are refused, with the account and code left untouched. The Verification Engineer's Kim scenario (active, `must_change_password` set, a real `/auth/login`, enrolled elsewhere, re-added) keeps her password and is ENROLLED, on **both** the roster and the single add. The reverse (never signed in) gets a new password | `tests_roster_ready_to_use` (`RosterEmailRowsAreReadyToUseTests`, `SingleAddExistingStudentTests`) |
| 9 Isolation | Cross-school gate and staff refusal (above); an existing onboarded student's password and names aren't touched | `tests_roster_ready_to_use` |
| 10 Full suite | **Pending slot** | — |

## 3. Production

`RUNBOOK_backfill.md`: dry run → execute → verify, for the founder on Railway. Output is ids only. Landing (B) waits for "Inactive students still holding a code: 0".

## 4. Frontend

`FRONTEND_DEAD_PAGES.md`: the student app's `/register/student/<token>` page, its renew state, and (per the SM's ruling that (B) refuses `/auth/otp` `VERIFY_EMAIL` for students, with an indistinguishable generic reply) the student app's `/verify-email` go dead with (B). It also lists the two response changes landing (A) makes to existing screens.

## 5. Notes

- `send_bulk_enrollment_email` has no callers after this landing (and `send_course_invitation_email` already had none). They're removed in (B) with the other dead student-code code, not here, to keep (A) minimal.
- The legitimate path for an old pending student after (A) but before the backfill: being added to any course again (single add or roster) promotes and re-invites them.
