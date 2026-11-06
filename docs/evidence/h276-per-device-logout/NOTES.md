# H-276: logout signs out this browser/device only

**Reported by the owner 2026-11-02:** logging out on one browser signs the user out
everywhere (other browsers, the phone). **Owner decision 2026-11-02: "Per-browser only".**
Logging out of Chrome must not affect Edge, another browser or the phone. There is no
"sign out of all devices" option. A password change or reset still signs out everywhere.
Built by the SM (fixes-worker is on H-274).

## Cause
H-39 / AUTHZ-T1 (docs/evidence/authz-token-epoch/EVIDENCE.md, consequence 1): logout calls
`revoke_all_sessions()`, which bumps the per-user `token_epoch`, killing every token the
user holds. That evidence already named the follow-up: "per-session logout with a `sid` claim
plus a per-request denylist lookup ... Not done."

## Design
1. **Session id.** `EpochRefreshToken.for_user` stamps a new `sid` (uuid4) claim. Every
   sign-in path mints through it (5 sites in users/views.py + the login serializer's
   `token_class`). simplejwt copies all custom claims to the access token and keeps them on
   rotation (`set_jti/set_exp/set_iat` only), so one browser keeps one `sid` for as long as
   it keeps refreshing.
2. **Revocation store.** New model `users.RevokedSession(sid uuid PK, user FK, revoked_at)`.
   Durable and fail-closed (a DB read; if the DB is down the request fails anyway). No cache
   layer, so none of the H-1 cache rules apply.
3. **Logout.** Blacklist the posted refresh (as today), then revoke the caller's session: the
   `sid` of the access token on the request, plus the posted refresh's `sid` when that refresh
   belongs to the same user (a user can never revoke someone else's session by posting their
   token). `token_epoch` is NOT bumped.
   **Legacy fallback:** if the caller's access token has no `sid` (minted before deploy),
   logout falls back to today's `revoke_all_sessions()`, so there's never a window where a
   logged-out access token stays alive. That window closes on its own once pre-deploy tokens
   expire (refresh lifetime 2 days).
4. **Check.** `MustChangePasswordJWTAuthentication.authenticate` and
   `EpochTokenRefreshSerializer.validate` reject a token whose `sid` is revoked (401, same
   "Token has been revoked" text as the epoch check, so no new wording). Cost: one indexed PK
   lookup per authenticated request, only for tokens that carry a `sid`.
5. **Pruning.** A revoked `sid` can't mint new tokens (its refresh is blacklisted), so every
   token carrying it expires within REFRESH_TOKEN_LIFETIME of logout. Logout deletes rows
   older than REFRESH_TOKEN_LIFETIME + 1 day: bounded and self-cleaning, no beat task.
6. **Unchanged:** password change/reset, set_unusable_password, the admin deactivation bump.
   They all still bump `token_epoch` (sign out everywhere).

## Tests that must change (expected break)
- `users/tests_token_revocation.py::test_logout_on_one_device_signs_out_every_device`: it pins
  the old behaviour the owner has now reversed. It's replaced by its opposite plus the legacy
  fallback test.

## Gate plan
Red run of the new tests on the unchanged beta f5d2d577 → fix → users + audit + AutoGrader
regression → one mutant per guard (sid stamp, auth check, refresh check, logout revoke, no
epoch bump, same-user guard, legacy fallback, prune) → full regression → evidence.

## Red run (unchanged beta f5d2d577, new tests only)
`python manage.py test users.tests_h276_per_device_logout`: Ran 10, FAILED (failures=4, errors=2).
Reproduces the owner's report: `test_logging_out_of_one_browser_keeps_the_others_signed_in`
fails for edge AND phone. Also failing: no sid claim, the epoch is bumped, the refresh-chain test
(no sid), and the prune test (no model). The 4 passing tests are guards that must stay green:
instant sign-out of the logged-out browser, password change signs out everywhere, a failed logout
revokes nothing, someone else's refresh can't sign them out.
