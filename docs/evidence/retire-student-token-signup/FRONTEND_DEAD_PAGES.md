# Frontend pages that go dead when the code-based student sign-up is removed

For the founder to pass to the frontend team. Nothing here changes before landing (B) of `task/retire-student-token-signup` is deployed, and (B) only ships after the backfill runbook (`RUNBOOK_backfill.md`) has run on production.

## Dead after landing (B)

| Frontend page (student app, `STUDENT_FRONTEND_DOMAIN`) | What it does today | After (B) |
|---|---|---|
| `/register/student/<token>?email=<email>` | The student completes their invitation: enters first/last name and a password, and posts to `POST /auth/register/student`. | The backend route is gone (404). No email links here any more. **Remove the page.** |
| The "link expired, request a new one" state of that page | Shown when `/auth/register/student` answers `200` with `renewal_url`/`expired_token`; posts the token to `POST /course/student/renew-student-token`. | Both backend routes are gone. **Remove it.** |

Emails that linked to that page are removed in (B): the course invitation email (`send_course_invitation_email`, already without callers), the bulk-enrollment email (`send_bulk_enrollment_email`, without callers since landing A), and the token-renewal emails to the student and teacher (`send_token_renewal_emails`).

**Old emails still in inboxes:** after (B), a student who clicks an old invitation link reaches the dead page, whose backend call answers 404. Suggest the frontend replaces the page with a static "This invitation link is no longer used. Check your email for your login details, or ask your teacher" message rather than removing the route outright, at least for a while.

## Stays as it is

| Page | Why it stays |
|---|---|
| Student `/login` | This is where every student now starts: active from the moment they're added, with the temporary password from the login-credentials email. Their pending enrollments activate on first login. |
| Student change-password screen | `must_change_password` is set on new students. It's **informational only**: the server does not enforce it (product decision, `users/authentication.py`), so the frontend decides whether to prompt. |
| Teacher `/verify-email` and `POST /auth/verify` | Untouched (founder: leave teacher `/auth/verify` alone). |

## Open question for the founder (affects landing B's scope)

`POST /auth/otp` with `otp_type=VERIFY_EMAIL` still works for a **student** row that has no `email_verified_at` (manual-add and roster students are created active but unverified). It calls `send_user_activation_email`, which puts a fresh 6-digit code on the student's row and emails a link to the **student** app's `/verify-email` page. Completing it goes through `/auth/verify`, which needs the email plus the code and is per-email throttled, so it's not a takeover path. But it means a student row can get a code again after the backfill, and the student app keeps a `/verify-email` page that nothing in the new flow sends students to.

Options:
- (1) Leave it. The student `/verify-email` page stays alive.
- (2) In (B), refuse `VERIFY_EMAIL` for students (their email is effectively verified by the login-credentials email reaching them). The student `/verify-email` page then goes dead too.
