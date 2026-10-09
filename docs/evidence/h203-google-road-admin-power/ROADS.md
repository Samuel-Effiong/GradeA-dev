# Every road that issues a login token, marks an email verified or switches an account on (H-203)

**THE PRINCIPLE** (also the docstring of `users/admin_power.py`): an account with admin power that has never been verified can be entered only with its password; no road that proves only control of a mailbox (a reset code, a verification code, a Google identity) signs it in, verifies it or activates it. "Admin power" = any one of user type SUPER_ADMIN, is_staff, is_superuser.

By reading, users/views.py and classrooms at e11a0083 (H-164's final tip); line numbers are from there. The list is PINNED by `users/tests_roads_that_sign_in_or_activate.py`: a new site fails that test until someone decides and adds it to its `ROADS` with a mark.

| # | Road | Where | Mark |
|---|---|---|---|
| 1 | `POST /auth/login` (password) | TokenObtainPairView | the principle's own door |
| 2 | `POST /auth/refresh` | TokenRefreshView | needs a live refresh token, its epoch and an active user |
| 3 | `POST /auth/verify` | views.py ~926-1014 | CLOSED in H-164 |
| 4 | `POST /auth/otp` VERIFY_EMAIL | ~1124-1145 | CLOSED in H-164 |
| 5 | `POST /auth/otp` RESET_PASSWORD, `POST /auth/reset-password` | ~1147-1390 | CLOSED in H-164 (N3) |
| 6 | change-password | ~1531-1539 | needs an authenticated session and the current password or an OTP |
| 7 | `POST /auth/register/school-admin` (invitation token) | ~1768-1806 | CLOSED in H-203 (a pending row with admin power is refused with the wrong-token answer) |
| 8 | `POST /auth/google-auth`, existing account | ~1985-2101 | CLOSED in H-203 (refused with the failed-sign-in answer, nothing written) |
| 9 | `POST /auth/google-auth`, new account | ~1940-1980 | creates a new flagless TEACHER row; no existing account touched |
| 10 | `enroll_student_by_email` / `check_existing_account_may_join` | classrooms/services/enrollment.py | CLOSED in H-203 (a STUDENT-typed row with admin power gets the not-a-student answer) |
| 11 | school-admin creation by a super admin | classrooms/serializers.py | a super admin's action; no token issued to the new account |
| 12 | Django admin "Mark selected users as active" and the edit form | users/admin.py | operator tool behind staff login |
| 13 | management commands / scripts | scale_my_students.py, backfill_pending_student_invites.py, add_whitelist.py | operator-run, no tokens |
| 14 | licence invitation of an EXISTING teacher (`LicenseSubscriptionService._get_or_invite_teacher`, via create-licence, add-teachers and the single add) | billing/license_service.py | CLOSED in H-203 (found by Verifier 2's pre-read: it set a fresh password and MAILED it to a never-signed-in account whatever its marks; now a never-verified account with admin power and a verified-and-switched-off account get the not-a-teacher answer before anything is written) |

Nothing else issues a token: no other `for_user`, no `RefreshToken(` except the logout blacklist; the SimpleJWT views are the two at AutoGrader/urls.py 48-49.
**How such an account becomes verified legitimately:** only through the Django admin edit form (its "Account verification" section holds an editable "Email verified at") or the shell; `createsuperuser` and no management command set it. Meanwhile it signs in with its password. For the user: "for each super admin, open the Django admin, open the user and set 'Email verified at' once; until then forgot-password, the activation code and Google sign-in do not work for that account, by design".
**KNOWN LIMIT of the pin:** an `is_active=True` keyword inside a `create(...)` call is not seen by the scan (it would match every filter); the creation roads are on the list by the `"is_active": True` dict form where the code uses it.

## Roads that SET A PASSWORD or MAIL A CREDENTIAL (widened pin, 2026-10-09)
A road that sets a password and mails it signs nobody in by itself, but it hands the mailbox the way in; the pin now lists every place that sets a password (`SET_PASSWORD`, `MAKE_PASSWORD`, `PASSWORD_KEYWORD`) or makes or mails a temporary password (`CREDENTIAL_MAIL`) with its mark. By reading, at this tip:
| Site | Mark |
|---|---|
| licence invitation, existing teacher | calls `holds_admin_power` (H-203, road 14) |
| licence invitation, new teacher | creates a flagless TEACHER |
| `enroll_student_by_email`, never-signed-in existing student | calls `holds_admin_power` through `check_existing_account_may_join` (H-203, road 10) |
| `enroll_student_by_email`, new student | creates a STUDENT |
| school admin created by a super admin (classrooms/serializers.py) | creates a new, verified row |
| users/views.py: reset-password | calls the helper (H-164) |
| users/views.py: change-password | the principle's own door (live session) |
| users/views.py: school-admin registration | calls the helper (road 7) |
| users/serializers.py update | the signed-in account's own profile update |
| user manager `create_user` | creates only |
| backfill command, remediation command, benchmark / QA scripts | operator tools |
**Limit of the pin:** by text; `password=` as a dict key or a serializer field declaration is not seen, and a function that sets a password through a helper with another name is seen only if the name is added to `CREDENTIAL_MAIL`.
