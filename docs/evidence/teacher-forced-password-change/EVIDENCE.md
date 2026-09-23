# Evidence — teacher-invite forced password change

Worktree: `Grade-Automator-Plus-teacher-forced-password-change`, branch
`task/teacher-forced-password-change`, off beta `d281b7f`. This evidence
at commit `52b1f2b`. Not Epic A / audit logging — a standalone fix on beta.

## 1. Background

A teacher invited via a school license got `set_unusable_password()` and no
way to ever complete registration (dead `/register/teacher` link, no
backend endpoint for it). Agreed fix: generate a real random password at
invite time, email it, force the teacher to change it before doing
anything else — strict server-side enforcement, not a frontend convention.

## 2. Password generation + email (billing/license_service.py)

`_get_or_invite_teacher` (both the new-account branch and the
still-inactive resend branch) now generates a password via
`_generate_teacher_password` — `get_random_string` over a 66-symbol
alphabet, retried until `django.contrib.auth.password_validation.validate_password`
accepts it — sets it with `set_password`, and sets the new
`CustomUser.must_change_password` field (migration `0037`, default
`False`, set only on this path). The plaintext is threaded through
`_send_teacher_invitation` into `merge_data` for the email template and is
never passed to `logger` anywhere in this path (the existing
`logger.info("Queued teacher invitation email to %s...")` call, unchanged,
only logs the email address — confirmed by test, see §4).

Per an SM amendment mid-implementation, the activation link changed from
the dead `/register/teacher?token=...&email=...` to
`/verify-email?email=...&token=...` — the same path/param names
`users/services.py`'s `send_user_activation_email` already uses for
self-registered teachers, so there is one shared frontend page and one
shared backend endpoint (`auth/verify`, unchanged) for both onboarding
tracks.

A resend to a still-inactive teacher (an admin re-inviting the same email)
regenerates the password every time — the previous plaintext can't be
recovered from the stored hash to reuse in a second email — and does
re-set `must_change_password=True` each time rather than just relying on
it already being `True` from account creation (locked down by
`test_resending_an_invitation_to_a_still_inactive_teacher_issues_a_fresh_password`,
which explicitly flips the flag off between invites to prove the resend
branch itself sets it).

## 3. Server-side enforcement (users/authentication.py)

`DEFAULT_PERMISSION_CLASSES` was ruled out as the enforcement point:
several viewsets (e.g. `CustomUserViewSet`) override
`permission_classes`/`get_permissions()` per-view, bypassing the global
default. `DEFAULT_AUTHENTICATION_CLASSES` is applied uniformly — grepped
the codebase; the only per-view `authentication_classes` override is
`AutoGrader/health.py`'s health checks (`@authentication_classes([])`).

`MustChangePasswordJWTAuthentication` wraps `JWTAuthentication`: once a
user is resolved, if `must_change_password` is `True` and
`request.resolver_match.view_name` isn't in
`{"auth-change-password", "auth-logout"}`, it raises
`PasswordChangeRequired` (`APIException`, `status_code=403`,
`default_code="password_change_required"`). The two allow-listed view
names were verified against the real router (`DefaultRouter` basename
`"auth"`) via `reverse()`, not assumed:

```
>>> reverse("auth-change-password")
/api/v1/auth/change-password
>>> reverse("auth-logout")
/api/v1/auth/logout
```

Login (`TokenObtainPairView`) and refresh (`TokenRefreshView`) are
naturally unaffected — confirmed by reading
`rest_framework_simplejwt.views.TokenViewBase`, which sets
`authentication_classes = ()` and `permission_classes = ()`, not assumed
from the library's docs:

```
>>> TokenViewBase.authentication_classes
()
>>> TokenViewBase.permission_classes
()
```

`change_password` (`users/views.py`) clears `must_change_password` back to
`False` on a successful change.

**Known, deliberate gap** (documented in the class docstring, not
backlogged — not a live risk since nothing in the real API client uses it):
`SessionAuthentication`, also in `DEFAULT_AUTHENTICATION_CLASSES` for the
browsable API, is not wrapped, so a session-authenticated request bypasses
this check.

## 4. Test suite

- `billing/tests/test_license_service.py::TestTeacherInvitePasswordGeneration`
  (5 tests): usable generated password + `must_change_password` set on
  invite; `/verify-email` link format (and NOT `/register/teacher`);
  generated password never appears in any `assertLogs`-captured log
  record; a resend issues a genuinely fresh password (old hash rejected,
  new one accepted, flag re-set); a normal self-registered signup is
  unaffected.
- `users/tests_forced_password_change.py::ForcedPasswordChangeEnforcementTests`
  (7 tests), using **real JWT authentication** (`RefreshToken.for_user(...).access_token`
  in the `Authorization` header), never `force_authenticate` — DRF's
  `force_authenticate` sets `request._force_auth_user` directly and skips
  the `authentication_classes` chain entirely, so it would never exercise
  `MustChangePasswordJWTAuthentication`:
  - flagged user blocked (403/`password_change_required`) on an arbitrary
    protected endpoint (`user-detail`)
  - flagged user can still reach `change-password`
  - flagged user can still reach `logout`
  - after a successful change, the flag clears in the DB and normal access
    is restored (re-authenticating with the fresh access token
    `change-password` issues, as the real frontend would)
  - a normal user (`must_change_password=False`, the default) sees zero
    behavior change
  - token refresh is unaffected (asserts `TokenViewBase`'s empty
    `authentication_classes` in practice, not just in theory)
  - login itself is unaffected

`python manage.py test users.tests_forced_password_change billing.tests.test_license_service.TestTeacherInvitePasswordGeneration --settings=settings_worktree --noinput`

- Found 12 test(s)
- **OK**

## 5. Mutation testing

9 mutants (`mutate_pwdchange.py`) against password generation/email,
enforcement, and the change-password clear. Applied one at a time from
collision-safe copies, restore verified by md5 against the pre-mutation
original after every mutant (never `git checkout`), confirmed both
programmatically (`restored_md5_ok`) and by `git status --short` showing
a clean tree matching the intended changes afterward.

**Result: 9 / 9 KILLED** (one, P2, initially survived on the first pass —
see below — fixed and reconfirmed clean).

| id | protection weakened | expected test |
|----|---|---|
| P1 | new-teacher branch: password not generated (still `set_unusable_password`) | `test_new_teacher_gets_a_usable_generated_password` |
| P2 | resend branch: `must_change_password` not (re-)set | `test_resending_an_invitation_to_a_still_inactive_teacher_issues_a_fresh_password` |
| L1 | activation link reverted to the dead `/register/teacher` path | `test_activation_link_uses_the_shared_verify_email_page` |
| A1 | `must_change_password` check inverted (never enforces) | `test_flagged_user_is_blocked_from_an_arbitrary_protected_endpoint` |
| A2 | `change-password` removed from the allow-list | `test_flagged_user_can_still_reach_change_password` |
| A3 | `logout` removed from the allow-list | `test_flagged_user_can_still_reach_logout` |
| A4 | `default_code` changed (frontend can't match it) | `test_flagged_user_is_blocked_from_an_arbitrary_protected_endpoint` |
| A5 | `status_code` changed from 403 | `test_flagged_user_is_blocked_from_an_arbitrary_protected_endpoint` |
| C1 | `change-password` no longer clears the flag | `test_after_changing_password_the_flag_clears_and_access_is_normal` |

**P2 initially survived**: the mutant only removed the `must_change_password`
write in the *resend* branch, but the test only asserted the flag was
`True` at the end — which it already was, left over from account
*creation*, regardless of whether the resend branch itself set it. Fixed
by strengthening the test to explicitly flip the flag to `False` between
the first invite and the resend, so a passing test can only mean the
resend branch itself re-set it. Re-ran the full battery after the fix:
9/9 killed clean, first pass.

## 6. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis, CI's
fake credentials), on current beta (`d281b7f`), which already includes
gate-runner's fast-password-hasher speedup (`e784740`/`de6d191`) — full
runs took ~11-13 min, not the ~40 min noted on branches predating it.

`python manage.py test --settings=settings_worktree -v 1 --noinput`
(every app, no labels, no `--keepdb`)

- Ran 4565 tests in 682.710s
- **OK (skipped=26)**

One earlier run of this exact command (mid-session, before the final
commit) came back `FAILED (failures=1)` on
`AutoGrader.tests_redis_test_isolation.RedisTestIsolationTests.test_a_concurrent_process_cannot_flush_our_cache`
— H-9's own acceptance test, tripped because another session
(`epic-a-credit-audit` worktree, privacy-guard's legitimate re-run after
an `integration/epic-a` merge) was running its own full suite
concurrently and collided on Redis. Confirmed with the Senior Manager
before treating it as anything other than environmental contention; not a
regression in this work. Re-ran once that other run finished: clean, as
recorded above.

## 7. Conclusion

`must_change_password` is enforced uniformly at the authentication layer
for every endpoint except `change-password`/`logout`, login/refresh are
structurally unaffected (verified by reading the actual library classes),
and a normal account sees zero behavior change. No regressions anywhere
in the codebase from this change. One documented, deliberate gap
(`SessionAuthentication` unwrapped) is noted in code rather than
backlogged, per Senior Manager guidance — not a live risk today.

Post-commit sha256 (from `git show 52b1f2b:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written):

```text
PLACEHOLDER
```
