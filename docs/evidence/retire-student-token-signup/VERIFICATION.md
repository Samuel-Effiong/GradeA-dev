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

## Re-verification @65321ec: REJECTED

**My earlier finding is fixed.** My original Kim scenario (an active student who has signed in, bulk-imported into a second course) now gives `status=ENROLLED, password_unchanged=True`, and the roster row reads `enrolled / existing_student` instead of a misleading "invited". My scratch test plus tests_roster_ready_to_use, test_bulk_enrollment and tests_backfill_pending_student_invites: 36 OK. Author mutation log: 15/15, SURVIVORS [].

**BLOCKING 1: `SIMPLE_JWT["UPDATE_LAST_LOGIN"] = True` triggers a full user-cache invalidation on every login.** simplejwt stamps `last_login` with `user.save(update_fields=["last_login"])`, which fires `post_save` → `clear_user_cache`. That receiver has no `update_fields` filter on beta, on H-1 stage 3 (168d57e) or on step 4 (a86354b). Measured on 65321ec with a recording patch around the receiver (throwaway test, never committed), for one password login:
- UPDATE_LAST_LOGIN off: 0 bumps, 0 wildcard calls.
- UPDATE_LAST_LOGIN on: 1 `bump_many` over `anyusr`, `global` and `usr`, plus 1 `delete_cache_patterns` call with **9 keyspace patterns**.
So every login anywhere bumps the **global** generation, which turns cold every student's `course/my-courses` and the superadmin dashboards (H-15). On today's beta it also runs a 9-pattern SCAN sweep. After stage 3 it additionally bumps every superadmin; after step 4 the SCANs go but the global bump stays. Beta is live, so at term-start login volume this effectively disables those caches.
Fix: no cached payload renders `last_login` (no serializer includes it; `billing`'s `last_login_date` is a different field), so either (a) keep UPDATE_LAST_LOGIN off and stamp `CustomUser.objects.filter(pk=user.pk).update(last_login=now)` in the login serializer (no signal), or (b) have `clear_user_cache` return early when `update_fields == {"last_login"}`. Pin it with a test asserting a login causes zero bumps and zero wildcard calls.

**BLOCKING 2: a teacher's roster import re-enables an account a superadmin deliberately deactivated.** users/admin.py has the "Mark selected users as inactive" action (`queryset.update(is_active=False)`). In `enroll_student_by_email`, any student that isn't (active AND signed in) goes down the reset branch, which sets `is_active=True`, a new password, and emails credentials. `check_existing_account_may_join` doesn't look at `is_active`. Proven with the same scenario test on both trees: Dee has a password she knows, has signed in (`last_login` set), is enrolled in another course, and is then deactivated. The teacher bulk-imports her into a second course:
- beta e7e4bdf: `is_active_after=False, password_unchanged=True` (the old bulk path enrolled without reactivating).
- 65321ec: `is_active_after=True, password_unchanged=False`, "a teacher's roster import re-enabled an admin-deactivated account".
The single-add path already did this on beta (pre-existing); (A) extends it to every roster import. Fix: reactivate an inactive account ONLY when it has never signed in (a legacy pending row; `activation_token` present is a good extra signal). Otherwise return a failed row such as "This account has been deactivated". Test both the roster and single-add paths.

**Non-blocking:**
- Google sign-in mints tokens directly (EpochRefreshToken.for_user), not via the login serializer, so it never stamps `last_login`. A Google student who signs in but makes no further authenticated request leaves no trace, and `has_signed_in` would still say False. That's rare, but worth a line in the docs, or stamp it there too via fix (a).
- The reorder, runbook and backfill checks from my previous pass still hold.

Verdict: REJECTED. Full suite: covered by the batch-2 run, once the two blockers are fixed.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
