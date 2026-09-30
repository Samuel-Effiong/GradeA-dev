# "Not you?" warning wording in the verification and reset emails

Branch `task/email-warning-copy` off beta `e7e4bdf`. Founder decision 2026-09-28 (item 3 of the SM's decision file): the "Not you?" link is **cancelled** (no link, no `/auth/not-you`, no tokens; `task/verify-email-not-you` is archived as `archive/verify-email-not-you`). This branch is the **wording-only** replacement.

## Change
| Email | Where | Old line | New line (founder-approved, verbatim) |
|---|---|---|---|
| Verification (`send_user_activation_email`, teachers and students) | `users/services.py`, `bottom_content` merge field of provider template `ynrw7gy0ye2l2k8e` | "If you did not create this account, you can safely ignore this email" | "Didn't sign up? Someone may have used your email address. Don't click the activation link. Just ignore this email and the account won't be activated. Never share this link or code with anyone, including Grade A+ staff." |
| Password reset (`POST /auth/otp`, `RESET_PASSWORD`) | `users/views.py`, `RESET_EMAIL_WARNING` constant, interpolated into the plain-text body | "If you did not request a password reset, you can ignore this email and your account will remain secure." | "Didn't ask for this? Someone may be trying to get into your account. Don't share this code with anyone, including Grade A+ staff. Your password hasn't been changed. If you didn't request this, you can ignore this email." |

Nothing else changes:
- "This link expires in 15 minutes." stays in the verification email.
- The reset email keeps its code and its support address.
- Both warnings are built from concatenated string literals only to satisfy flake8's line length; each renders as one line.

**Not changed, by SM ruling:** the school-admin invitation and licence-teacher invite emails reuse the same provider template, but "Didn't sign up?" doesn't fit an invitation, and the founder named only these two emails.

**Merge note:** L2 (`task/authz-l2-otp-counter`) edits the same `/auth/otp` block, adding an early return when the code is locked. This branch changes only the email body lines and a module-level constant, so the hunks don't overlap. If L2 lands first, the rebase or merge should be clean. Check it anyway.

## Is "the account won't be activated" true?
The founder asked for this check (decision file item 3). Short answer: **yes for anyone who doesn't control the mailbox, with one guessing gap now tracked as H-53.**

How an account registered with someone else's address can become active:

| Door | Can a stranger use it without the email? |
|---|---|
| `POST /auth/verify` with the emailed code (the link) | Only by guessing the code. See **H-53** below |
| `POST /auth/register/student` with the code | Students only (H-47 scoped it to `user_type=STUDENT, is_active=False`). Same code, same guessing question |
| Google sign-in with that address | Needs the mailbox's Google account, i.e. the owner. It also sets an unusable password, so a password an intruder chose dies |
| Staff admin "activate users" action | Deliberate staff action, not reachable by the intruder |

**H-53 (SM backlog, owner ed, batch-3).** `/auth/verify` has only the per-IP `VerifyEmailThrottle` (5/hour) against a 6-digit code valid for 15 minutes. There is no per-account failure budget, which is the same pattern as AUTHZ-L2. With enough IPs, the person who registered with another's address could guess the code and activate the account without the link.
- It's not cheap: about 1 in 200,000 per guess.
- But against a distributed attacker the sentence "won't be activated" isn't strictly guaranteed until H-53 adds an L2-style per-account lock.
- Found incidentally while checking the claim; not investigated further, per the no-hunting rule.

## Tests (`users/tests_email_warning_copy.py`, 8)
Verification email, from `send_user_activation_email` with the provider task mocked:
- The approved warning is present, verbatim, for a teacher and for a student.
- The old "If you did not create this account" line is gone.
- **No link other than the activation link.** Every string merge field except `activation_url` is checked for `http(s)://`, `href=` and `www.`.

Reset email, from the real `POST /auth/otp` with `safe_delay` mocked:
- The approved warning is present, verbatim.
- The code is still in the email.
- The old "remain secure" line is gone.
- **No link at all** (same pattern).

The tests use their own literal copies of the founder text, not the constants, so a change to the constant is caught.

## Gates
| Gate | Result |
|---|---|
| Reproduce-first | On beta's wording, **5 fail** (the 3 "approved warning present" tests and the 2 "old line gone" tests), and the 3 no-link guards pass. See `prefix_beta_e7e4bdf_failing.txt` |
| After | 8/8 OK |
| Mutation | Not run as a harness. The change is two string literals, and each test compares the full founder text. Any character change to either literal fails the matching "approved warning present" test, as the prefix run shows for the wholesale case |
| Regression | whole `users` app, `EXEMPT_EMAIL_DOMAINS=` → **595 OK** (skipped=4), `regression_users_app.txt` |
| mypy | whole-repo `pre-commit run mypy --all-files` → Passed |
| Full suite | per 0b's rule 2026-09-29, covered by the batch run, not per fix |
