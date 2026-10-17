# Evidence — remove the must_change_password hard block

Worktree: `GAP-urgent-remove-password-block`, branch
`task/urgent-remove-password-change-block`, off `beta` `46d2dea`. This
evidence at commit `ffb435e`.

## 1. Background

Urgent, user-directed change (SM-assigned): `must_change_password` was
being enforced server-side by
`users/authentication.py::MustChangePasswordJWTAuthentication` — any
authenticated request from an account with `must_change_password=True`
got a 403 (`PasswordChangeRequired`) unless it hit
`auth-change-password` or `auth-logout`. This blocked license teachers,
students, and school admins created with a system-generated password
(all three invite flows landed recently: `billing/license_service.py`,
`classrooms/services/enrollment.py`'s student-invite-active work, and
`classrooms/serializers.py`'s school-admin-invite-active work) from doing
anything else until they changed their password.

Product decision: remove the hard block. `must_change_password` stays on
the model and becomes a client-visible flag so the frontend can show a
warning/nudge instead of the API enforcing it.

**Note on process**: the harness's own permission classifier flagged the
first attempt to run tests against this change with reason "Security
Weaken" (recognizing this as removing a security control). Per operating
rules, that denial was not routed around through another tool or
sub-agent — it was surfaced to the user directly in-session, who gave
explicit approval to proceed before any test was run or any commit made.

## 2. Implementation

**`users/authentication.py`**:

- `MustChangePasswordJWTAuthentication.authenticate()` no longer checks
  `must_change_password` or raises anything — it calls
  `super().authenticate(request)` and returns the result unchanged,
  identical in behavior to plain `JWTAuthentication`.
- The class itself is kept, not deleted or renamed:
  `AutoGrader/settings.py:1059`'s `DEFAULT_AUTHENTICATION_CLASSES`
  references it by dotted path, and `users/schema.py`'s
  `OpenApiAuthenticationExtension` (`MustChangePasswordJWTScheme`) targets
  it by string name (`target_class =
  "users.authentication.MustChangePasswordJWTAuthentication"`). Deleting
  or renaming it would break `DEFAULT_AUTHENTICATION_CLASSES` resolution
  and the generated OpenAPI schema. Both were checked directly (grep) and
  confirmed to still resolve since the class stayed in place.
- `PasswordChangeRequired` (the 403 exception class) removed outright —
  grepped for any other reference across the codebase; none existed.
- `PASSWORD_CHANGE_ALLOWED_VIEW_NAMES` left in place, docstring updated to
  note it's no longer read for enforcement, in case anything else still
  references it.

**`users/serializers.py`**:

- `CustomUserSerializer.Meta.fields` gained `must_change_password`.
- `extra_kwargs` marks it `{"read_only": True}`, the same pattern already
  used for `is_active` — a client can never set or clear it via this
  serializer (PATCH `/users/<id>` or any other endpoint backed by it).
- This serializer already backs the login response
  (`CustomTokenObtainPairSerializer.validate`,
  `user_data = CustomUserSerializer(self.user).data`), so this one field
  addition surfaces `must_change_password` at login and on every other
  endpoint returning a user through this serializer, with no separate
  wiring.

No other files changed.

## 3. Test suite

`users/tests_forced_password_change.py` — existing
`ForcedPasswordChangeEnforcementTests` class rewritten:

- `test_flagged_user_is_blocked_from_an_arbitrary_protected_endpoint`
  (previously asserted a 403) renamed to
  `test_flagged_user_is_not_blocked_from_an_arbitrary_protected_endpoint`
  and now asserts 200 — the core regression proof that the block is gone.
- `test_flagged_user_can_still_reach_change_password`,
  `test_flagged_user_can_still_reach_logout`,
  `test_after_changing_password_the_flag_clears_and_access_is_normal`,
  `test_a_normal_user_sees_zero_behavior_change`,
  `test_token_refresh_is_unaffected_by_the_flag`,
  `test_login_itself_is_unaffected_by_the_flag` — all kept unchanged
  (still valid: the flag's lifecycle — set on creation, cleared on
  password change — is untouched, only its enforcement is removed).

New class `MustChangePasswordIsExposedOnLoginTests` (3 tests):

- `test_login_response_reports_true_for_a_flagged_user` — logs in a
  `must_change_password=True` account through the real `POST auth/login`
  endpoint and asserts `response.data["user"]["must_change_password"]`
  is `True`.
- `test_login_response_reports_false_for_a_normal_user` — same, for an
  ordinary account, asserts `False`.
- `test_field_is_read_only_and_cannot_be_client_set` — authenticates as a
  flagged user, `PATCH`es `must_change_password: False` at
  `user-detail`, and confirms the request succeeds (200 — the endpoint
  itself isn't blocked) but the flag is unchanged in the database
  afterward, proving the field is genuinely read-only rather than just
  omitted from a write-serializer's docs.

Every test authenticates via real JWTs (`RefreshToken.for_user(...)`
plus `client.credentials(HTTP_AUTHORIZATION=...)`), never
`force_authenticate`, since that bypasses `authentication_classes`
entirely and would never exercise
`MustChangePasswordJWTAuthentication.authenticate()` at all.

```
python manage.py test users.tests_forced_password_change \
  --settings=settings_worktree
```

**Ran 10 tests. OK.**

Also run, as a broader sanity check given this touches shared
authentication and the shared user serializer: `users.tests_login_lockout`,
`users.tests`, `billing.tests.test_license_service`, and
`classrooms.tests_school_admin_active_invite` (the other two invite flows
that produce `must_change_password=True` accounts).

```
python manage.py test users.tests_login_lockout users.tests \
  billing.tests.test_license_service \
  classrooms.tests_school_admin_active_invite \
  --settings=settings_worktree
```

**Ran 61 tests. OK.** No regressions from exposing the new field or
removing the block.

## 4. Mutation testing — explicitly not done

Given the urgency of this change and its size (a ~15-line behavioral
diff in one method, plus a two-line serializer addition), mutation
testing was not run for this task. This is a deliberate call, not an
oversight: the diff is small enough that the targeted tests above (in
particular the renamed
`test_flagged_user_is_not_blocked_from_an_arbitrary_protected_endpoint`,
which fails immediately if enforcement is reintroduced in any form, and
the three `MustChangePasswordIsExposedOnLoginTests` cases, which fail if
the field is dropped, renamed, or made writable) already pin down every
behavior this change touches. SM concurred with skipping it for this
task specifically, given the urgency.

## 5. Full regression

Session-unique isolated DB, `settings_worktree.py` ->
`test_urgent_remove_password_block`. Log redirected to a file, never
piped through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --keepdb --parallel 4
```

**Ran 4598 tests in 509.406s. OK (skipped=28).** Clean on first attempt —
no test needed fixing for this change.

Full log lived at
`/tmp/claude-1000/.../scratchpad/full_regression_urgent_remove_password_block_20260925_123053.log`
(scratchpad, wiped between sessions) — sha256
`8c4e4f9ef23605f4b9df1688b3e1329f75b9b9860a77111dda1db6bf1fae2134`, hashed
after the run completed. Tail archived here as
`full_regression_summary.txt`.

## 6. Tree state

`git status --short` throughout the test run and regression: clean
except this untracked evidence directory. No test run or regression left
any tracked file modified.

Commits on this branch: `ffb435e` (this session — the entire change: auth
enforcement removal, serializer field, and test suite update).
