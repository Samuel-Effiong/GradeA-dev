# AUTHZ-PATCHPW — PATCH /users/<id> password and email takeover

Branch `task/authz-patch-password` off beta `4b902fc`. Found while checking the Security Lead's question during AUTHZ-T1/T2. Commits: `aa0de82` (part 1, password), `566b447` (part 2, email, first version), and `2eac98b` (**part 2 reworked per the founder's decision of 2026-09-28**, replacing `566b447`'s mechanism).

> **Founder decisions, 2026-09-28** (relayed by the SM): part 1 approved as is (the frontend already uses `/auth/change-password`). Part 2 **replaced**: `PATCH /users/<id>` refuses ANY email change, for EVERY caller, **super admins included** (confirmed as final, not a provisional flag). No `current_password`, no lockout coupling. An unchanged email is still accepted because frontends send the whole object; the frontend already blocks email editing, and this is the backend enforcement.

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
- **Email (reworked, `2eac98b`):** on any update (`self.instance is not None`), if `email` is in `attrs` and differs from the stored address after the same `lower().strip()` that `validate_email` already applied to the incoming value, the request is refused: `400` on `email`, `"Email address can't be changed."`. That holds for every caller: self, super admin on another account, super admin on their own. Account creation (`self.instance is None`) is untouched. The whole request is refused, so other fields in it are not saved. The `current_password` field, `_require_current_password`, the login-lockout coupling and the Google special case are all removed. `CustomUserSerializer` is only used for updates by `CustomUserViewSet` (`PATCH /users/<id>`; PUT is not allowed), so the rule doesn't reach any other flow (grepped every call site and subclass).

## Consequences of the rework
1. **Findings from the first version that no longer apply:** the Google/unusable-password case (every account is now refused the same way), session revocation on email change and re-verification of a new address (no email change can happen on this route).
2. **Legitimate email changes:** there's now no API path to change an account's email. If support needs one, it has to be a deliberate, separate flow (e.g. a staff-only action with re-verification). None exists today.
3. **`email: null` from the frontend** (it's how the API renders `@student.local` addresses): already a field-level `400` on beta (the model's `email` is non-null), before `validate()` runs. Unchanged, not introduced here.

## Frontend contract (for the founder relay)
- `PATCH /users/{id}` body may no longer include `password` for a self-PATCH → `400 {"success": false, "error": {"field_errors": {"password": ["The password cannot be changed through this endpoint. Use POST /auth/change-password instead."]}}}`. The frontend's password-change UI must post to `/auth/change-password` (it may already; grep of this repo found no frontend to check — **the frontend team must confirm**).
- `PATCH /users/{id}` with an `email` different from the stored one (any caller, any account) → `400 {"success": false, "message": "Email: Email address can't be changed.", "error": {"field_errors": {"email": ["Email address can't be changed."]}}}`. The same address, including case/whitespace variants, is accepted, so sending the whole profile object back keeps working. No new request fields. Exact wire shapes (captured from the real endpoint) in `wire_shapes.txt`.

## Gates
| Gate | Result |
|---|---|
| 1 Regression | Part 1 + first part 2: `users` app + 6 other modules, **527 tests, 1 failure** (the known `.env` `EXEMPT_EMAIL_DOMAINS` case, `regression_summary.txt`). Reworked part 2 (`2eac98b`): `tests_patch_email`, `tests_patch_password`, `tests_authorization`, `tests_forced_password_change`, `tests_remaining_branches` → **75/75 OK**. Full suite: **pending slot** |
| 2 Mutation | Reworked 2026-09-28: **9 mutants, 9 killed** (`mutate.py`, `mutation_results.json`, `mutation_log.txt`). P1-P3 (part 1: rejection removed / over-broad / self-check inverted), E1 guard removed, E2 fires on an unchanged email, E3 no normalisation before comparing, E4 guard only for the account owner (the super-admin case), E5 guard also on create, E6 refusal reported on the wrong field. The harness now asserts every anchor is unique: the first run of this rework mutated `_is_acting_on_self` instead of the email guard for E1/E4/E5 (same text at the same indent), which showed up as those mutants being killed by password tests. Caught, fixed and rerun. The first version's 12-mutant record is in git history (`1db6e56`) |
| 4 Adversarial | The stolen-token chain (email PATCH → OTP → reset) is closed at step 1; the password PATCH is closed; a mixed request is refused whole; form-encoded bodies are rejected; PUT is 405; the old `current_password` no longer unlocks anything; repeated attempts don't touch the login-lockout counter |
| 9 Isolation | Another teacher, and a teacher against their own student: refused, target unchanged. **Super admin on another account: refused** (founder decision). Positive controls: an unchanged email with other edits (own account and super admin on another), plain field edits, and account creation all succeed |
Tests: `users/tests_patch_password.py` (12), `users/tests_patch_email.py` (13).

## Status
Part 1 (`aa0de82`) is unchanged and was VERIFIED for correctness by the Verification Engineer (`VERIFICATION.md`, which covers `199d928`). Part 2's rework (`2eac98b`) and this evidence need the Verification Engineer to re-verify the delta, then a full-suite slot. Not landed.
