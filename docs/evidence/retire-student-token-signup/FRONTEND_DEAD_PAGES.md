# Frontend pages that go dead when the code-based student sign-up is removed

For the founder to pass to the frontend team. Nothing here changes before landing (B) of `task/retire-student-token-signup` is deployed, and (B) only ships after the backfill runbook (`RUNBOOK_backfill.md`) has run on production.

## Dead after landing (B)

| Frontend page (student app, `STUDENT_FRONTEND_DOMAIN`) | What it does today | After (B) |
|---|---|---|
| `/register/student/<token>?email=<email>` | The student completes their invitation: enters first/last name and a password, and posts to `POST /auth/register/student`. | The backend route is gone (404). No email links here any more. **Remove the page.** |
| The "link expired, request a new one" state of that page | Shown when `/auth/register/student` answers `200` with `renewal_url`/`expired_token`; posts the token to `POST /course/student/renew-student-token`. | Both backend routes are gone. **Remove it.** |
| `/verify-email?email=<email>&token=<code>` (student app only) | A student verifies an email address with a 6-digit code. It was reachable when `POST /auth/otp` `VERIFY_EMAIL` was called for an unverified student. | (B) refuses `VERIFY_EMAIL` for student accounts (SM ruling, 2026-09-28), with the same generic reply as any other call so it can't reveal which addresses are students. Nothing sends a student here any more. **Remove it.** The teacher app's `/verify-email` is unaffected. |

Emails that linked to that page are removed in (B): the course invitation email (`send_course_invitation_email`, already without callers), the bulk-enrollment email (`send_bulk_enrollment_email`, without callers since landing A), and the token-renewal emails to the student and teacher (`send_token_renewal_emails`).

**Old emails still in inboxes:** after (B), a student who clicks an old invitation link reaches the dead page, whose backend call answers 404. Suggest the frontend replaces the page with a static "This invitation link is no longer used. Check your email for your login details, or ask your teacher" message rather than removing the route outright, at least for a while.

## Stays as it is

| Page | Why it stays |
|---|---|
| Student `/login` | This is where every student now starts: active from the moment they're added, with the temporary password from the login-credentials email. Their pending enrollments activate on first login. |
| Student change-password screen | `must_change_password` is set on new students. It's **informational only**: the server does not enforce it (product decision, `users/authentication.py`), so the frontend decides whether to prompt. |
| Teacher `/verify-email` and `POST /auth/verify` | Untouched (founder: leave teacher `/auth/verify` alone). |

## Changes to existing screens from landing (A)

- **Roster import results:** a row for an existing student who has already signed in now comes back as `status: "enrolled"` with a new `type: "existing_student"`. Previously every emailed row said `"invited"`. `"invited"` / `"invitation"` now means login credentials were actually emailed. The results screen should show both.
- **Single add** (`POST /course/<id>/students`): `is_new_student: true` now means "credentials were emailed"; `false` means "an existing student was enrolled".
