# Retire the code-based student sign-up — landing (A)

Branch `task/retire-student-token-signup` off beta `be78221`. Founder decision (relayed by the SM, 2026-09-28), the H-47 follow-through: students imported from a spreadsheet use the ready-to-use flow, and the old code-based student sign-up is then removed. Written by the Security Engineer.

Two landings:
- **(A) this one:** stop minting student codes, plus the founder's backfill runbook for the rows that already have one.
- **(B) later**, only after the backfill has run on production and reports zero students holding a code: remove `POST /auth/register/student`, `POST /course/student/renew-student-token`, their serializers, the renewal and invitation emails, and dead student `activation_token` code, with a guard test that the routes 404. Teacher `/auth/verify` is untouched.

**Status: (A) BUILT on the branch, local gates pass. NOT landed. The first version (`b10c798`) was REJECTED by the Verification Engineer (see `VERIFICATION.md` and §1a); this is the rework, awaiting re-verification.**

## 1a. Rework after verification: only never-signed-in students get a new password

The rejection: `enroll_student_by_email` treated `must_change_password` as "still onboarding". That flag is informational and only cleared by `/auth/change-password`, so a student who signs in and simply uses the app keeps it for good. Re-importing such a student into a second course reset their password, emailed a new one and left the enrollment PENDING. This was **pre-existing on the single-add path (beta and production)**; landing (A) extended it to every roster import.

**SM ruling, implemented in `enroll_student_by_email` (so both paths):** an existing student gets fresh credentials only if they have **never signed in**. That means either an `is_active=False` legacy row, or an account with no `last_login` **and** no `UserActivity` row (`has_signed_in`). Every other existing student is enrolled ENROLLED and gets the "added to course" email; their password and sessions are untouched. `must_change_password` is no longer read here.

**Why login now stamps `last_login`:** `UserActivity` is written only by middleware on *authenticated* requests, and the login request itself is anonymous. JWT login never set `last_login`. So a student who signed in once and did nothing else left no trace, and would still have been treated as "never signed in". `SIMPLE_JWT["UPDATE_LAST_LOGIN"] = True` makes every successful `/auth/login` record it. This is forward-only: sign-ins before the deploy show up only through `UserActivity`, which a real session practically always writes on its first request after login.

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
