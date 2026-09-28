# Retire student sign-up codes (A): Verification

Branch `task/retire-student-token-signup` @ b10c798 (code 07af5e9), off beta be78221.

## BLOCKING: a roster re-import now resets an active student's password
Emailed roster rows now go through `enroll_student_by_email`. Its "still onboarding" branch fires on `not (student.is_active and not student.must_change_password)`, i.e. for any student with `must_change_password=True`. That flag is informational and not enforced, and only `/auth/change-password` clears it. So a student who logs in with their temporary password and just uses the app keeps it indefinitely. For such a student, a re-import into another course now:
- resets their password to a new temporary one (and, with token-epoch, signs them out of every device),
- emails a new password,
- and puts the new enrollment in PENDING until their next login.
Before (A) the bulk path enrolled existing accounts ENROLLED and never touched the password. Proven with the same scenario test on both trees (throwaway checkouts, never committed). Kim is an active STUDENT with a password she knows (a real `/auth/login` gives 200), `must_change_password=True`, and is enrolled in another course. The teacher bulk-imports her row into a second course:
- beta be78221: `status=ENROLLED password_unchanged=True`, test passes.
- b10c798: `status=PENDING password_unchanged=False`, "re-import reset an active student's password".
The single add already behaved this way (pre-existing), but (A) extends it to every roster import, and importing the same class into a second course is a normal thing to do. The response still says "invited", so the teacher gets no hint that passwords were reset. Suggested fix (a product call, so check with the SM): only reset for genuinely-never-onboarded rows, i.e. `is_active=False` legacy rows, and/or accounts with no sign-in signal. `UserActivity` is the real one; `last_login` isn't maintained (see H-3). An active student with a temp password should just be ENROLLED with the added-to-course email. Pin it with a test like this scenario, on both the roster and the single-add paths.

## Checked and fine
- **The reorder (already-enrolled check before the gate):** safe. It only changes the outcome for an account that is already enrolled in THIS course, which now gets "skipped" instead of "failed". That reveals nothing the teacher can't already see on their own roster, and creates nothing. `enroll_student_by_email` still applies the cross-school/staff gate for everything else, and raises on a duplicate under its course lock, so a concurrent double import gives "failed: already enrolled", not a duplicate row.
- **The two rewritten tests:** `test_bulk_add_via_tsv_string` now pins the new contract (active, must_change_password, no code), and `ActivationTokenValidityWindowTest` pins "the roster mints no code". The claim that the remaining minter's window is pinned elsewhere is true: `users/tests_models_and_admin.py` ~178-186 asserts `renew_activation_token` gets `ACTIVATION_TOKEN_VALIDITY`.
- **Runbook claims:** they hold. The landing-(B) gate `pending_students()` is scoped to `user_type=STUDENT, is_active=False`, non-empty token, so pending TEACHER verify codes (same column) can't keep it above 0. The dry run counts before writing (N+M), and execute ends at 0, matching the runbook's lines 23, 40 and 51. The output is ids only.
- **Note:** this branch adds a third `docs/evidence/*/mutate.py`, so it needs d52364c's mypy `exclude: ^docs/` (beta-batch-1) to land first or with it.

Verdict: REJECTED until the re-import no longer resets an active student's password, with a test pinning that.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
