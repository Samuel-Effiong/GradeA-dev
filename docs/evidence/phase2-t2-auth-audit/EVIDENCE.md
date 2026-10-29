# T2 evidence — auth call-site instrumentation (login/logout)

Worktree: `Grade-Automator-Plus-phase2-t2-auth-audit`, branch
`task/phase2-t2-auth-audit`, rebased onto `integration/epic-a` `d960176`
(beta `6ab413b` + BE-A-04 PII cleanup). This evidence at commit `dbc2f50`.

Instruments the first two call sites of Epic A plan §6: `AUTH_LOGIN`
(`users/serializers.py`, `CustomTokenObtainPairSerializer.validate`) and
`AUTH_LOGOUT` (`users/views.py`, `AuthViewSet.logout`), using the T1
emitter (`audit/emitter.py`) built in `docs/evidence/phase2-t1-audit-event/`.

Branches instrumented:

- Login: success, account-locked (`DENIED`/`ACCOUNT_LOCKED`), wrong
  password (`FAILURE`/`WRONG_PASSWORD`), unknown email
  (`FAILURE`/`INVALID_CREDENTIALS`).
- Logout: success, missing refresh token
  (`FAILURE`/`VALIDATION`/`REFRESH_TOKEN_MISSING`), invalid/expired
  refresh token (`FAILURE`/`USER`/`REFRESH_TOKEN_INVALID`).

## 1. Test suite

`users/tests_auth_audit_events.py` — 7 tests, one per branch above, each
asserting **exactly one** `AuditEvent` row of the right action, with the
right `outcome`/`error_class`/`reason_code`/`target_id` (FR-A-01's
"exactly one well-formed event" acceptance criterion).

`python manage.py test users.tests_auth_audit_events --settings=settings_worktree --noinput`

- Found 7 test(s)
- **OK**

## 2. Mutation testing

12 mutants (`mutate.py`) against the two instrumented files, one
protection weakened per mutant: locked-account gate, wrong-password
detection, missing/altered emit calls, wrong reason codes, wrong
error_class, dropped target_id. Applied one at a time from collision-safe
copies, restore verified by md5 against the pre-mutation original after
every mutant (never `git checkout`), confirmed both programmatically
(`restored_md5_ok`) and by `git status --short` showing only the untracked
test file afterward.

**Result: 12 / 12 KILLED**, clean on the first pass — no connection
contention this time (single small app under test, not the full suite).

| id | protection weakened | expected test |
|----|---|---|
| L1 | locked-account check skipped entirely | `a_locked_account_emits...` |
| L2 | locked reason code renamed | `a_locked_account_emits...` |
| L3 | wrong-password detection never fires | `a_wrong_password_emits...` |
| L4 | failure event not emitted on AuthenticationFailed | `failure_event` |
| L5 | success event not emitted | `a_successful_login_emits...` |
| L6 | known user's target_id dropped on failure | `a_wrong_password_emits...` |
| L7 | failure error_class widened past USER | `failure_event` |
| G1 | missing-token event not emitted | `missing_refresh_token_emits...` |
| G2 | missing-token error_class miscategorised | `missing_refresh_token_emits...` |
| G3 | invalid-token event not emitted | `invalid_refresh_token_emits...` |
| G4 | invalid-token reason code renamed | `invalid_refresh_token_emits...` |
| G5 | success event not emitted (logout) | `a_successful_logout_emits...` |

## 3. Regression — full suite

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis,
CI's fake credentials — landed on beta after this branch was cut, so the
script was pulled in via the `integration/epic-a` rebase rather than
copied ad hoc as in the first attempt).

`python manage.py test --settings=settings_worktree -v 1 --noinput`
(every app, no labels, no `--keepdb`)

- Ran 4677 tests in 2367.664s
- **FAILED (failures=1, skipped=26)**

The one failure,
`test_stripe_timeout_on_the_allowed_checkout_leaves_no_local_change`
(`billing.tests.test_free_plan_activation_security`), is **not a
regression from this work**: it is the exact test fixed by gate-runner at
beta `6ab413b` ("Fix flaky stripe-timeout test: mock
get_or_create_customer too") — a deterministic bug (unmocked
`stripe.Customer.create` hitting CI's placeholder key) unrelated to
anything `users/` or `audit/` touches. This branch's regression run was
kicked off from a pre-`6ab413b` base; confirmed by matching the failure
message and commit description exactly, and independently re-confirmed
clean after rebasing onto `integration/epic-a` (which includes `6ab413b`)
with a scoped re-run (`audit`, `users.tests_auth_audit_events`,
`users.tests_auth_endpoints`, `users.tests_login_lockout` — 178/178 OK).
Not re-running the full 4677-test suite a second time for this alone, per
the reasoning the Senior Manager gave for gate-runner's own rebase: no
file this branch touches overlaps with what changed between the pre- and
post-rebase base.

Also worth recording for whoever re-runs this: the first attempt at this
regression died silently ~73 minutes in with no summary line, root-caused
to launching the test process via a manual `nohup ... &` inside a tool-call
shell instead of the harness's own background-task mechanism — the
process was reaped when that shell's process group was torn down (visible
in the isolated Postgres's own log as "unexpected EOF on client
connection with an open transaction"). Ruled out first: no OOM kill in
`syslog`, no suspend/resume event in that window. Re-run using the
harness's native background-task launch completed cleanly.

## 4. Conclusion

Both instrumented call sites emit exactly one well-formed `AuditEvent` per
branch, matching plan §6 and the outcome/error_class/reason_code vocabulary
from `04_epic_a_implementation_plan.md`. No regressions anywhere in the
codebase from this change.

Post-commit sha256 (from `git show 22a6016:<path>`, not the working copy —
pre-commit hooks can rewrite a file after it's written, so a pre-commit
hash is not evidence of what actually landed):

```text
98a7e1f26825ea71d0ff4fff8a6037c9394368964914290fdcd82cc5d916f023  users/serializers.py
9bf0cf251baae9b1606fad70b1f18e1bf1994186f54df0838e72ee0322080b04  users/views.py
6d7f11344cbf0e205176a1bdfaa3c745cde7e262abefe70c0e6ba2d1b518c512  users/tests_auth_audit_events.py
```
