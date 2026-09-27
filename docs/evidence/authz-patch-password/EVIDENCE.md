# AUTHZ-PATCHPW — PATCH /users/<id> password and email takeover

Branch `task/authz-patch-password` off beta `4b902fc`. Found while checking the Security Lead's question during AUTHZ-T1/T2. Two commits: `aa0de82` (password), `566b447` (email), so either can be reviewed or reverted alone.

## Verified on beta HEAD first
Probe on real endpoints, stolen access token only, no `current_password`:
1. `PATCH /users/{me}` `{"password": "attacker-chosen"}` → **200**; the attacker's password then authenticates.
2. `PATCH /users/{me}` `{"email": "attacker@..."}` → **200**, `email_verified_at` kept.
3. `POST /auth/otp {RESET_PASSWORD}` for the new address → **202**, code goes to the attacker.
4. `POST /auth/reset-password` with that code → **200**; permanent takeover from a token the attacker no longer even needs.
Both parts fail on beta's serializer (`prefix_beta_4b902fc_password_failing.txt`, `prefix_password_fix_only_email_failing.txt`).

## Fix
`CustomUserSerializer.validate()` (`users/serializers.py`):
- **Password:** if `"password" in attrs` and the caller is acting on their OWN row (`_is_acting_on_self`), reject with 400 naming `/auth/change-password`. Not silently dropped. Superadmin-on-another-account and `POST /users` (creation, `self.instance is None`) are unaffected — same code path, different `self.instance`.
- **Email:** if the caller is acting on their own row AND the email in `attrs` differs from the stored one (case/whitespace-insensitive, since `validate_email` normalises before this runs), a `current_password` field (write-only, not a model field) is required and checked with `user.check_password()`. A wrong password calls `user.register_failed_login()` — the SAME per-account lockout `/auth/login` uses — so a stolen token cannot brute-force the profile form as a guessing oracle; a correct one calls `reset_login_lockout()`. `must_change_password` and forced-change flows do not go through this PATCH (grepped: only `/auth/change-password` and `/auth/request-change-password` call `set_password`/`generate_code` for that flow; confirmed by test).

## Findings recorded rather than fixed (per the Security Lead's scoping)
1. **Google/unusable-password accounts:** `_require_current_password` raises a clear 400 on the `email` field ("cannot be changed... for example Google sign-in") before asking for a password that does not exist. No new flow invented.
2. **Session revocation on email change:** NOT wired to `authz-token-epoch` (independent branch; coupling them would block either from landing alone). Follow-up: once both are on beta, call `self.instance.revoke_all_sessions()` after a successful email change, matching password change. Recorded, not built here.
3. **Re-verification of the new address:** the new email becomes the reset/login identity immediately, with no confirmation click. Residual risk, not built here: register as a follow-up (send a confirmation link to the new address before it becomes active). Out of scope per the Lead's instruction ("do not build it now").

## Frontend contract (for the founder relay)
- `PATCH /users/{id}` body may no longer include `password` for a self-PATCH → `400 {"success": false, "error": {"field_errors": {"password": ["The password cannot be changed through this endpoint. Use POST /auth/change-password instead."]}}}`. The frontend's password-change UI must post to `/auth/change-password` (it may already; grep of this repo found no frontend to check — **the frontend team must confirm**).
- `PATCH /users/{id}` body changing `email` for a self-PATCH now requires a new field `current_password` (string) alongside it, or `400` on `current_password`. Submitting the SAME email needs no `current_password`. A wrong `current_password` returns `400` on that field and counts toward the account's login lockout (5 tries / 15 min, matching `/auth/login`). An account with no usable password (Google) gets `400` on `email`. Exact wire shapes in `wire_shapes.txt`.

## Gates
| Gate | Result |
|---|---|
| 1 Regression | `users` app + 6 other test modules referencing `CustomUserSerializer`/`user-detail`/`user-list`/`/users` (classrooms, billing, dashboard, students, assignments, ai_processor): **527 tests, 1 failure** = the known `.env` `EXEMPT_EMAIL_DOMAINS` environmental failure (`regression_summary.txt`). Full suite not run. |
| 2 Mutation | 12 mutants across both commits, **12 killed, 0 survivors** (`mutate.py`, `mutation_results.json`, `mutation_log.txt`): password rejection removed/over-broad, self-check inverted, email guard removed/fires-on-no-change, wrong password not counted / lockout check removed / wrong password accepted, unusable-password case removed, success doesn't clear the counter, guard applied to other users, `current_password` not consumed (would leak to the model) |
| 4 Adversarial | full takeover chain (password PATCH, and separately email PATCH → OTP → reset) closed; form-encoded body also rejected; mixed request (password + other fields) refused whole, nothing half-applied; PUT (already 405) confirmed not a bypass; wrong `current_password` counted against lockout, and locked out then refuses even the CORRECT password |
| 9 Isolation | another teacher, a teacher against their own student, a student against a teacher, and a school admin against a teacher in their school: all refused (403/404), target's password/email unchanged; superadmin-on-other-account unaffected (positive control) |
Tests: `users/tests_patch_password.py` (12), `users/tests_patch_email.py` (13).
