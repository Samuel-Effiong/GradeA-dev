# AUTHZ-T1/T2 — access token outlives logout and password change/reset

Branch `task/authz-token-epoch` off beta `4b902fc`. One cure for both, per the Senior Manager's ruling: a per-user session epoch. Plan approved by Security Lead.

## Verified on beta HEAD first — `prefix_beta_4b902fc_failing.txt`
Real login → real JWT auth. Sanity passes; three exploits fail: the same access token still returns **200** after logout, after change-password, and after reset-password.

## Design
- `CustomUser.token_epoch` (PositiveInteger, default 0; migration `0039`, constant default so it's a metadata-only add on PostgreSQL).
- Every token carries an `epoch` claim (`users/tokens.py`, `EpochRefreshToken`, used by the login serializer and all 5 `for_user` sites; access tokens and rotated refresh tokens inherit it).
- `MustChangePasswordJWTAuthentication` (name kept for settings/schema) rejects a token whose claim differs from the user's epoch → 401. **No extra query and no cache**: simplejwt already loads the user row per request, so no H-1 cache interaction and no staleness. The refresh view (`EpochTokenRefreshSerializer`) checks too, so a signed-out device gets a clean 401 instead of new tokens that die on first use.
- Bump: automatically on `set_password` / `set_unusable_password` (F() expression, so concurrent changes can't lose one); `save()` adds the column to `update_fields` (covers the 5 `save(update_fields=["password", …])` callers); logout calls `revoke_all_sessions()`. Not bumped on user creation for the normal `/auth/register` path (`create_user()` sets the password before the first insert, while `_state.adding` is still True). The Google-registration branch does bump 0→1 at creation, since it inserts the row first and sets an unusable password in a second `save()` — harmless (no token exists yet for a brand-new row to revoke), but the row starts at epoch 1 rather than 0 on that one path (found in review by the Verification Engineer, 2026-09-28; not fixed, since fixing it has no observable effect and isn't worth the reordering risk). Not bumped on Django's hasher-upgrade re-hash during login (`check_password` override), which would otherwise sign users out as they log in (tested with a real md5→pbkdf2 upgrade through the login endpoint).
- change-password and reset-password mint new tokens after the bump, so the changing device stays logged in; every other device is signed out.

## Deploy / rollback
- Tokens minted before deploy have no claim; a missing claim counts as epoch 0 = every existing user's epoch. **Nobody is signed out at deploy.** A legacy token dies only when its user next changes credentials or logs out (tested with a hand-built no-claim token, both directions).
- Deploy order: migrate, then app. If the app runs before the migration, the column is missing and requests fail, so migrate first (standard).
- Rollback: old code ignores the column and the claim; behaviour returns to today's. Harmless.

## User-visible behaviour changes (for the Senior Manager to put to the founder)
1. **Logging out anywhere signs the user out of every device** (their access and refresh tokens die). Password change/reset already revoked refresh tokens everywhere; logout now matches. Later option: per-session logout with a `sid` claim plus a per-request denylist lookup (extra store read on every request, fail-open/closed decision, H-1 cache rules); about twice the code. Not done.
2. Anything that sets a password now signs that user out everywhere: admin/teacher re-invites with a generated password (`classrooms/services/enrollment.py`, `billing/license_service.py`) and `PATCH /users/{id}` with a `password` field (see below).
   - **PATCH /users/{id} looked at, as the Security Lead asked.** `CustomUserViewSet` allows `PATCH` on the caller's own row and `CustomUserSerializer.update()` calls `set_password()`, so a user CAN change their password there. The frontend is not in this workspace (`../CRM-frontend` is an unrelated project), so I cannot tell whether the Grade A+ frontend uses it; it is assumed possible. After the fix such a PATCH returns 200, bumps the epoch, and the caller's token then returns 401 (probe: PATCH 200, new password works, epoch 1, old access token 401). It returns no fresh tokens, unlike `/auth/change-password`. I did NOT add tokens there: **the route itself is a separate, worse problem** (next paragraph), and giving it a token response would bless it. Acceptable for this change because a client using it just has to log in again.
   - **NEW FINDING, reported to the Security Lead (pre-existing, not introduced here):** that PATCH changes the password with **only a bearer token, no `current_password` and no OTP**, unlike `/auth/change-password`. So a stolen access token is enough to set a password the attacker knows and keep the account permanently, which the epoch cure cannot prevent (the attacker only needs the token once). Suggested fix (separate task): make `password` non-writable on `PATCH /users/{id}` and route all password changes through `/auth/change-password`.
3. Access token lifetime is **unchanged (1 day)**. Shortening it is a separate one-line settings change once the frontend confirms it refreshes on 401; epoch already gives instant revocation, a shorter TTL only limits theft with no logout or credential change.

## Follow-up, explicit (not silent)
- **H-3 command** (`remediate_student123_passwords`, branch `task/h3-student-password-remediation`) resets passwords with `QuerySet.update()`, which bypasses the model and will NOT bump epochs. Add `token_epoch=F("token_epoch") + 1` to that update once both branches are on beta. Impact today is nil: none of the targeted accounts has ever logged in, so no token exists to revoke.
- Merge note: `task/authz-verify-password` also edits `views.py` verify()/register; expect a small conflict in that hunk only. Mint tokens after the save (already the case here).

## Gates
| Gate | Result |
|---|---|
| 1 Baseline/regression | `users` app + 45 other test modules touching passwords/tokens/enrollment/billing licences (classrooms, billing, dashboard, students, assignments): **1,426 tests, 1 failure** = `tests_email_domain_rules…test_nothing_is_exempt_by_default`, the known `.env` EXEMPT_EMAIL_DOMAINS environmental failure. Full suite not run (blocked for all sessions). |
| 2 Mutation | 11 mutants, **11 killed, 0 survivors** (`mutate.py`, `mutation_results.json`, `mutation_log.txt`): logout doesn't bump, auth ignores epoch, refresh ignores epoch, missing claim invalid, set_password / set_unusable_password / update_fields path / hasher-upgrade / F()-vs-read-modify-write, tokens not stamped, stale-lower epoch accepted |
| 4 Adversarial | stolen access token dead the instant logout / change / reset happens; stolen refresh dead; token with epoch above or below current rejected; claim edited without the key rejected; failed logout (bad refresh) revokes nothing |
| 9 Isolation | user B's tokens and epoch untouched by user A's logout; other devices behave as stated above |
| Checks the Security Lead asked for | missing claim == epoch 0 (test + mutant M4); hasher-upgrade does not bump (test + mutant M8); mint-after-save |
Tests: `users/tests_token_revocation.py` (22 tests).

## RESUME (shutdown checkpoint)
Current step: fix committed and evidence written (96f2e8d); handed to Verification Engineer, awaiting their sign-off. Also reported PATCH /users/{id} password bypass as a new finding (now its own task, see task/authz-patch-password).
Next exact command: none pending from me — check for a reply from Verification Engineer or Security Lead before acting further. If resuming cold: `cd Grade-Automator-Plus-authz-token-epoch && git log --oneline -3` then read EVIDENCE.md top to bottom.
