# Evidence — school-admin invite active-on-creation

Worktree: `GAP-school-admin-invite-active`, branch
`task/school-admin-invite-active`, off beta `c3e64fd`. This evidence at
commit `fd08415`.

## 1. Background

`SchoolWithAdminSerializer.create()` (`POST schools/create_with_admin`)
used to create the school admin `is_active=False` with an unusable
password, sending a 7-day activation-token link to
`/register/school-admin` before the account could be used at all. Agreed
fix (SM-assigned): apply the same "active-immediately-with-temporary-
password" pattern already shipped for license-invited teachers
(`billing/license_service.py`, commit `c3e64fd`) — create the admin
active, generate a real random password, email it, force a password
change on first login.

## 2. Implementation (classrooms/serializers.py)

- `_generate_school_admin_password(user)` — near-copy of
  `billing/license_service.py`'s `_generate_teacher_password`: retries
  `get_random_string` over a 66-symbol alphabet until
  `validate_password` accepts it. Kept as a separate copy rather than a
  shared import since the two invite flows live in different apps with
  no existing dependency between them.
- `SchoolWithAdminSerializer.create()` now creates the admin
  `is_active=True`, sets a generated password via `set_password`, and
  sets `must_change_password=True` — no `activation_token`/
  `activation_expires` are set at all.
- A deliberate divergence from the teacher-invite reference pattern:
  `email_verified_at` is set at creation time here (the teacher path
  leaves it unset). Without it, `POST /auth/otp` (`VERIFY_EMAIL`) would
  still treat this now-active admin as unverified and fall through to
  `resend_school_admin_invitation()`, emailing a dead activation-token
  link whose own completion endpoint (`/auth/register/school-admin`)
  rejects any `is_active=True` row. Marking the email verified at
  creation (a superadmin just handed them working credentials directly —
  the same trust level as a self-verified link) makes `/auth/otp`
  short-circuit to "already verified, please login" instead. Proved by
  `VerifyEmailOtpDoesNotDeadEndANewlyCreatedAdminTests`.
- `_send_school_admin_invitation_email(user, school, generated_password=None)`
  — signature gained the optional trailing `generated_password` param so
  `resend_school_admin_invitation()` (which still serves admin rows
  created before this change, under the old
  `is_active=False`/`activation_token` lifecycle) keeps its old behavior
  untouched. When `generated_password` is provided: the link is
  `https://{settings.SCHOOL_ADMIN_FRONTEND_DOMAIN}/login` (not a
  generic `FRONTEND_DOMAIN`), copy says "log in directly with the
  password below," and the "expires in 7 days" line is dropped. The
  `activation_url` merge-data key name is unchanged (shared CTA template
  `ynrw7gy0ye2l2k8e`).
- `resend_school_admin_invitation()` docstring updated to note it now
  only serves pre-existing pending rows, since `create()` no longer
  produces any.

**Cleanup held, not done**: `POST auth/register/school-admin`
(`users/views.py:1308`) is now unreachable for any new invite (proved by
`OldRegistrationCompletionEndpointIsUnreachableForNewAdminsTests`,
asserting 400 end-to-end rather than by inspection) but is left in place
— SM will bring cleanup decisions to the user once everything's in.

## 3. Test suite

`classrooms/tests_school_admin_active_invite.py` (new, 9 tests, 5
classes):

- `NewSchoolAdminIsActiveImmediatelyTests` — active with no activation
  token, working temporary password + `must_change_password=True`,
  `email_verified_at` set, generated password never appears in any log
  record (`logging.Handler` subclass capture, not attribute assignment).
- `SchoolAdminInvitationEmailIsALoginLinkTests` — invitation email links
  to `/login` on `SCHOOL_ADMIN_FRONTEND_DOMAIN` (not a token URL, not
  `register/school-admin`), and body carries the real temporary password
  (checked against the account via `check_password`, not just presence
  in the merge data) with no "expires in 7 days" / "Complete your
  registration" language.
- `CreateWithAdminEndpointTests` — end-to-end `POST
  schools/create_with_admin`: response contains no password, admin is
  active in the DB; the emailed password actually logs in via `POST
  auth/login`.
- `OldRegistrationCompletionEndpointIsUnreachableForNewAdminsTests` —
  `POST auth/register/school-admin` returns 400 for a newly created
  admin.
- `VerifyEmailOtpDoesNotDeadEndANewlyCreatedAdminTests` — `POST
  auth/otp` (`VERIFY_EMAIL`) returns 400 without calling
  `resend_school_admin_invitation` and without touching
  `activation_token`.

Also run: the two existing suites this change touches
(`classrooms/test_school_admin_otp_deadend.py`,
`classrooms/test_school_admin_invitation_email.py`, both left untouched,
still exercising the old is_active=False/activation_token path directly
against hand-built fixtures) and `billing/tests/test_license_admin_user_guard.py`
(a stale docstring comment fixed, no assertion changes — see commit
`5218279`).

```
python manage.py test classrooms.tests_school_admin_active_invite \
  classrooms.test_school_admin_otp_deadend \
  classrooms.test_school_admin_invitation_email \
  billing.tests.test_license_admin_user_guard \
  --settings=settings_worktree -v 2
```

**Ran 42 tests. OK.**

One test-authoring bug found and fixed in this pass (commit `fd08415`):
`test_otp_verify_email_short_circuits_instead_of_resending` patched
`users.services.resend_school_admin_invitation`, which doesn't exist —
`users/services.py` imports it locally inside the function
(`from classrooms.serializers import resend_school_admin_invitation`, to
dodge a circular import), so the patch target had to be
`classrooms.serializers.resend_school_admin_invitation`, where the local
import actually resolves it from. Confirmed the fix makes the test
exercise the real call by checking it against the mutation battery
below (VER mutant) rather than trusting the green run alone.

## 4. Mutation testing

6 mutants (`mutate.py.txt`) against the two changed functions in
`classrooms/serializers.py`. Applied one at a time from a collision-safe
backup copy, restore verified by md5 against the pre-mutation original
after every mutant (never `git checkout`), confirmed both
programmatically (`restored_md5_ok`) and by `git status --short` showing
only the untracked evidence directory throughout.

**Result: 6 / 6 KILLED**, first pass, no fixes needed.

| id | protection weakened | expected test | also caught by |
|----|---|---|---|
| ACT | new admin created `is_active=False` again | `test_admin_is_created_active_with_no_activation_token` | 3 e2e/OTP tests |
| VER | `email_verified_at` not set at creation | `test_admin_email_is_marked_verified_at_creation` | OTP dead-end test |
| MCP | `must_change_password` not set on the new admin | `test_admin_is_created_with_a_working_temporary_password` | e2e response test |
| LINK | invitation link reverts to the dead `/register/school-admin?token=...` URL | `test_invitation_email_links_to_login_on_the_school_admin_domain` | — |
| PWDTXT | temporary password dropped from the email body | `test_invitation_email_body_carries_the_temporary_password` | login-flow e2e test |
| EXP | `bottom_content` reverts to the stale "expires in 7 days" line | `test_invitation_email_body_carries_the_temporary_password` | — |

Raw log: `mutation_log.jsonl`. Full results incl. md5s:
`mutation_results.json`.

## 5. Full regression

Isolated env `audit-lead-schooladmin` (private Postgres 16 + Redis),
`settings_worktree.py` → `test_school_admin_invite_active`. Log
redirected to a file, never piped through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --noinput
```

**Ran 4595 tests in 1132.895s. OK (skipped=26).**

Full log: `full_regression_summary.txt` (tail — the complete log is
1132s of output and wasn't archived in full; the tail captures the
final traceback context plus the summary line and confirms no
failures/errors were reported before it).

## 6. Tree state

`git status --short` throughout steps 3-5: clean except this untracked
evidence directory. No mutation, test run, or regression left any
tracked file modified.

Commits on this branch, oldest first: `5218279` (WIP checkpoint —
serializers.py implementation + draft test file), `fd08415` (this
session — fixed the OTP test's mock patch target).
