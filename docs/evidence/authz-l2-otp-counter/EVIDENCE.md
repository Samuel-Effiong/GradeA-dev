# AUTHZ-L2 — reset-OTP guess budget wiped by re-request

Branch `task/authz-l2-otp-counter` off beta `4b902fc`. Finding source: `task/security-exploit-replay` FINDINGS.md row "AUTHZ-L2" (called AUTHZ-L2 only; the old "H-42" label is stale). Old red-team branch was NOT trusted: the bug was re-verified on beta HEAD first.

## Verified on beta HEAD (before any fix) — `prefix_beta_4b902fc_failing.txt`
Real endpoints (`/auth/otp`, `/auth/reset-password`), per-IP throttles widened to model many IPs:
- 40 cycles of "5 wrong guesses, re-request" → **200 of 200 guesses evaluated** (budget should be 5).
- After a lock, one `/auth/otp` cleared it, and the **correct code then reset the password (200)**.
- Locked account still got a fresh email on re-request.
Two adjacent defects found while scoping (same code path):
- `register_failure` was a read-modify-write: 20 parallel wrong guesses were counted as **7**, so a parallel burst also beat the budget even without the wipe.
- `created_at` was stamped once (`auto_now_add`), so a code re-sent 16+ min after the row was created arrived already expired (legit resend UX bug).
Correction to the finding's framing: each re-request also issues a NEW random code, so guesses do not accumulate against one secret. The real defect is the unbounded guess *rate* per account, gated only by a per-IP throttle.

## Fix (no migration)
Scope decision: the budget is per **account** (the OTP row), never refilled by a re-request, so it holds however many IPs are used. `PasswordResetOTP` in `users/models.py`:
- `generate_code()`: if locked → return `None`, lock/code/attempts untouched. If the lock has expired, or the previous code has expired → refill attempts. Otherwise carry attempts forward. Stamps `created_at` on every issue. Done under `select_for_update`.
- `register_failure()`: atomic `F()` increment (same pattern as `register_failed_login`).
- `/auth/otp` view: when `generate_code()` is `None`, no email is sent and the reply is identical to the normal send (no enumeration signal).
- Lockout message no longer says "Request a new code" (that no longer helps): now "Please try again in about 30 minutes."
- `OTPRequestThrottle` docstring corrected (it is spam control now, not the budget's guard).
- `users/tests_throttling.py::test_generate_code_clears_the_lockout` asserted the vulnerable behaviour; it was **inverted on purpose** (plus an expiry-refill test).

## Gates
| Gate | Result |
|---|---|
| 1 Baseline/regression | Original fix: full `users` app 508, 1 env failure (see H-39). **429 change (`a181184`):** full `users` app + `billing.tests.test_license_service` (which exercises reset-password) → **550 OK** (with `EXEMPT_EMAIL_DOMAINS` unset; this branch predates H-39). Two existing tests pinned the old 400 on the locked path and now assert 429: `tests_reset_otp_budget` (locked correct code) and `tests_throttling.test_brute_force_locks_out_even_with_correct_code`, whose security property (the correct code is refused and the password unchanged) still holds. Full suite: pending |
| 2 Mutation | **15 mutants, 15 killed** (`mutate.py`, every anchor now asserted unique; `mutation_results.json`, `mutation_log.txt`). The original 8 (M1–M8) plus 7 for the 429: locked request back to a generic 400, the budget-spending guess not answering 429, no `Retry-After`, wrong `code`, status 400 not 429, the renderer showing a numbered list instead of the message, attempt count hard-coded |
| 4 Adversarial | many-IP cycle loop now evaluates ≤ 5 guesses total; resend can't clear a lock; correct code refused while locked; locked resend sends no email and is indistinguishable to the caller; parallel wrong guesses all counted (20/20) |
| 9 Isolation | one account's lock/guesses never affect another account; success deletes the row and the next reset gets a full budget |
| Legit UX | mistype→resend→reset works; resend after lock expiry gives a full budget; resend older than 15 min row is valid; stale partial attempts don't haunt a later reset |
Tests: `users/tests_reset_otp_budget.py` (15 tests) + `users/tests_throttling.py`.

## Locked reset answers 429 (founder decision 2026-09-28, `a181184`)

While the account's reset code is locked, `POST /auth/reset-password` answers **429** with `Retry-After: <seconds>` and this envelope:

```json
{
  "success": false,
  "message": "For your security, password reset is paused on this account because the code was entered incorrectly 5 times. You can request a new code after 14:05 UTC (in 30 minutes). Your password has not been changed, and you can still sign in with your current password. If you didn't try to reset your password, someone else may have. Your account is still safe.",
  "error": {"field_errors": {
    "code": "RESET_LOCKED",
    "message": "<same text>",
    "locked_until": "2026-09-28T14:05:12.345678+00:00",
    "retry_after_seconds": 1800
  }}
}
```

- `code`, `locked_until` (UTC ISO) and `retry_after_seconds` live under `error.field_errors`, where the project's envelope puts every error payload (the billing refusals' `code` sits there too). The top-level `message` is the founder's text. For that, `users/renderers.flatten_errors` gained one narrow rule: an error body carrying a string `code` **and** a string `message` shows that message rather than a numbered list of every key. No existing response carries both, so nothing else changes, and tests pin the billing-style `error`+`code` body and a plain `message` field-error as unchanged.
- **The guess that spends the budget** (the 5th wrong code) already gets the 429, instead of one more generic 400 followed by a 429 on the next try.
- `/auth/otp` stays **generic** while locked (anti-enumeration), unchanged.
- The wording's "5" is `PasswordResetOTP.MAX_ATTEMPTS` and the time is `locked_until` in UTC. The frontend can localise it from `locked_until`.

## Behaviour change (approved by the Senior Manager)
- **A locked user waits out the 30-minute lock.** Re-requesting a code no longer clears it (that was the vulnerability). While locked, "resend" sends nothing and returns the normal generic reply.
- **Lockout reply:** superseded by the 429 above (it was a 400 with "Please try again in about 30 minutes.").
- **Frontend ticket for the founder:** the "we sent you a code" copy is still misleading while locked (no code is sent, and `/auth/otp`'s reply is deliberately identical to a normal send). But the reset-password screen can now show the 429's message and use `locked_until`/`Retry-After` to disable the form until it expires.
- **Trade-off (recovery denial):** the budget is per account, so anyone who knows a victim's email can burn 5 guesses every 30 minutes and keep that victim's password RESET locked. Login with the existing password is unaffected. Before the fix an attacker could do the same and then wipe the lock, so this is no worse, but it is now the only lever left. The fix logs a `password_reset_otp_locked` warning (user id and attempt count only, no email or code) on each lock so this abuse is visible; the audit app is not on beta, so a logger is used.

## Residual risk (not fixed here)
Assumptions: 6-digit code (1,000,000 values), attacker limited only by the per-account budget: ≤5 guesses per 30-minute lock cycle, or ≈4 per 15-minute code-expiry window, so at most **~384 guesses/day** per account whatever the number of IPs. That is under **0.04%/day** per targeted account, but a patient attacker reaches meaningful odds over months against one account. Failed guesses send the victim no email. Options, deliberately NOT done here: escalating lock, 8-digit code, audit-app event. Tracked as a LOW item in `docs/HARDENING_BACKLOG.md` ("AUTHZ-L2 follow-up").

## Status
The original fix was VERIFIED (`VERIFICATION.md`, at `c7b0bf3`). The 429 change (`a181184`) and this evidence need the Verification Engineer to re-verify the delta, then a full-suite slot after merging current beta.
