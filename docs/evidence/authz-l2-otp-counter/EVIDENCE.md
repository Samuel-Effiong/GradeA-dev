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
| 1 Baseline/regression | full `users` app: 508 tests, 1 failure = `tests_email_domain_rules…test_nothing_is_exempt_by_default`, the same env-caused failure as in the H-3 evidence (shared `.env` EXEMPT_EMAIL_DOMAINS has yopmail; made hermetic on `task/h39-network-guard`). Full `--parallel 4` suite NOT run (blocked for all sessions by the permission layer until the founder adds allow rules; not worked around). |
| 2 Mutation | 8 mutants, **8 killed, 0 survivors** (`mutate.py`, `mutation_results.json`, `mutation_log.txt`) |
| 4 Adversarial | many-IP cycle loop now evaluates ≤ 5 guesses total; resend can't clear a lock; correct code refused while locked; locked resend sends no email and is indistinguishable to the caller; parallel wrong guesses all counted (20/20) |
| 9 Isolation | one account's lock/guesses never affect another account; success deletes the row and the next reset gets a full budget |
| Legit UX | mistype→resend→reset works; resend after lock expiry gives a full budget; resend older than 15 min row is valid; stale partial attempts don't haunt a later reset |
Tests: `users/tests_reset_otp_budget.py` (15 tests) + `users/tests_throttling.py`.

## Behaviour change (approved by the Senior Manager)
- **A locked user waits out the 30-minute lock.** Re-requesting a code no longer clears it (that was the vulnerability). While locked, "resend" sends nothing and returns the normal generic reply.
- **Lockout message changed** from "Request a new code and try again later." to "Please try again in about 30 minutes." (the old advice no longer helps).
- **Frontend ticket for the founder:** the frontend's "we sent you a code" copy is WRONG while the account is locked, because no code is sent and the API reply is deliberately identical to a normal send. The frontend should surface the reset-password lockout message and stop implying a new code is on its way. Backend cannot signal this from `/auth/otp` without creating an enumeration signal.
- **Trade-off (recovery denial):** the budget is per account, so anyone who knows a victim's email can burn 5 guesses every 30 minutes and keep that victim's password RESET locked. Login with the existing password is unaffected. Before the fix an attacker could do the same and then wipe the lock, so this is no worse, but it is now the only lever left. The fix logs a `password_reset_otp_locked` warning (user id and attempt count only, no email or code) on each lock so this abuse is visible; the audit app is not on beta, so a logger is used.

## Residual risk (not fixed here)
Assumptions: 6-digit code (1,000,000 values), attacker limited only by the per-account budget: ≤5 guesses per 30-minute lock cycle, or ≈4 per 15-minute code-expiry window, so at most **~384 guesses/day** per account whatever the number of IPs. That is under **0.04%/day** per targeted account, but a patient attacker reaches meaningful odds over months against one account. Failed guesses send the victim no email. Options, deliberately NOT done here: escalating lock, 8-digit code, audit-app event. Tracked as a LOW item in `docs/HARDENING_BACKLOG.md` ("AUTHZ-L2 follow-up").

## RESUME (shutdown checkpoint)
Current step: fix, backlog follow-up item and lock-event logging all committed (53cb7ef); handed to Verification Engineer, awaiting sign-off. No open questions to Security Lead.
Next exact command: none pending from me — wait for Verification Engineer/Security Lead. If resuming cold: `cd Grade-Automator-Plus-authz-l2-otp-counter && git log --oneline -3`.
