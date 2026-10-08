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

Nothing else issues a token: no other `for_user`, no `RefreshToken(` except the logout blacklist; the SimpleJWT views are the two at AutoGrader/urls.py 48-49.
**How such an account becomes verified legitimately:** only through the Django admin edit form (its "Account verification" section holds an editable "Email verified at") or the shell; `createsuperuser` and no management command set it. Meanwhile it signs in with its password. For the user: "for each super admin, open the Django admin, open the user and set 'Email verified at' once; until then forgot-password, the activation code and Google sign-in do not work for that account, by design".
**KNOWN LIMIT of the pin:** an `is_active=True` keyword inside a `create(...)` call is not seen by the scan (it would match every filter); the creation roads are on the list by the `"is_active": True` dict form where the code uses it.
