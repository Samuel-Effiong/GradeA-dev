# Email warning wording: Verification (@f0e259f, off beta e7e4bdf)
- **Diff:** wording only, two strings. The verification email's `bottom_content` keeps the 15-minute line and adds the founder's "Didn't sign up? …" text. The reset email's body keeps "Your Password reset code is: {otp_code}" and "Enter this code in the app to continue.", then interpolates the new module constant `RESET_EMAIL_WARNING`. The old "…ignore this email…" lines are removed. No link is added, and the activation link in the verification email is unchanged.
- **Tests** (my detached checkout of f0e259f): `users.tests_email_warning_copy` 8/8. The author's reproduce-first (5 fail on beta's wording, the 3 no-link guards pass) is recorded.
- **My mutant:** a "Not you? https://…" URL appended to `RESET_EMAIL_WARNING` is KILLED by `test_reset_email_has_no_link`. So the no-link guard really bites, which matters because the founder cancelled the link.
- **Batch-2 composition check:** trial merges of f0e259f with L2 d7054a2 (the same `/auth/otp` block) and then retire (A) 44e2893 (users/services.py) are both **clean**. On the combined tree, the wording + reset-budget + last-login-stamp tests pass: 35 OK.
- The evidence's claim check ("the account won't be activated") correctly scopes the exceptions (the H-53 /auth/verify guessing gap, Google by the mailbox owner, staff activation). No action needed from me.
Full suite: covered by the batch-2 run.
Verdict: VERIFIED. Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
