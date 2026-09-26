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
| 1 Baseline/regression | full `users` app: 508 tests, 1 failure = `tests_email_domain_rules…test_nothing_is_exempt_by_default`, the same env-caused failure as in the H-3 evidence (shared `.env` EXEMPT_EMAIL_DOMAINS has yopmail; made hermetic on `task/h39-network-guard`). Full `--parallel 4` suite NOT run (needs a slot from the Integration & Release Lead). |
| 2 Mutation | 7 mutants, **7 killed, 0 survivors** (`mutate.py`, `mutation_results.json`, `mutation_log.txt`) |
| 4 Adversarial | many-IP cycle loop now evaluates ≤ 5 guesses total; resend can't clear a lock; correct code refused while locked; locked resend sends no email and is indistinguishable to the caller; parallel wrong guesses all counted (20/20) |
| 9 Isolation | one account's lock/guesses never affect another account; success deletes the row and the next reset gets a full budget |
| Legit UX | mistype→resend→reset works; resend after lock expiry gives a full budget; resend older than 15 min row is valid; stale partial attempts don't haunt a later reset |
Tests: `users/tests_reset_otp_budget.py` (14 tests) + `users/tests_throttling.py`.

## Behaviour changes to be aware of (product/frontend)
- A locked user can no longer self-recover by re-requesting; they wait out the 30-minute lock. While locked, "resend" returns the normal generic reply but sends nothing. A frontend that says "we sent you a code" will be wrong for that window.
- Lock is per account, so a third party who deliberately fails 5 guesses can lock a victim out of password reset for 30 minutes (previously they could too, then wipe it; the lockout-DoS trade-off is inherent to any per-account budget; login has the same property).

## Residual risk (not fixed here)
Bounded but not zero: ≤5 guesses per 30-min lock cycle, or ≈4 per 15-min code-expiry window, per account regardless of IP count: at most ~384 guesses/day against a 1,000,000-code space ≈ under 0.04%/day per targeted account, and each attempt series is visible to the victim only via nothing (failed guesses send no email). Cheap hardening if wanted: longer code (8 digits) and/or an escalating lock, plus an audit event on lock. Not done: outside this finding's scope.
