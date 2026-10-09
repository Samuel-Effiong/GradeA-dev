# Evidence — student email-invite active-on-creation

Worktree: `GAP-student-invite-active`, branch `task/student-invite-active`,
off `beta` `c3e64fd`. This evidence at commit `2a2f68e`.

## 1. Background

`POST course/<pk>/students` (`classrooms/views.py` -> `enroll_student_by_email`
in `classrooms/services/enrollment.py`) is the dedicated "invite a student by
email" endpoint. It used to create a not-yet-existing student
`is_active=False` with an unusable password, a 6-digit `activation_token` /
24h `activation_expires`, and email an activation link
(`send_course_invitation_email`). An existing but still-`is_active=False`
account had its token silently reissued on re-invite. `direct-add-student`
and bulk/CSV roster import are explicitly OUT of scope and keep the old
`set_unusable_password()`/placeholder-email scheme exactly as before.

SM-assigned fix: apply the same "active-immediately-with-temporary-password"
pattern already shipped for license-invited teachers
(`billing/license_service.py`, commit `c3e64fd`) and school admins
(`classrooms/serializers.py`) — create the student active, generate a real
random password, email it, force a password change on first login, and
graduate their enrollment to ENROLLED the moment they actually log in
(rather than the moment they're invited).

## 2. Implementation

**`classrooms/services/enrollment.py`** — `enroll_student_by_email`
rewritten around three cases:

- No account at all -> `_create_new_student`: `is_active=True` immediately,
  a real temporary password via the new shared helper
  `users/services.py::generate_temporary_password` (extracted from
  `billing/license_service.py`'s `_generate_teacher_password`, now used by
  both flows), `must_change_password=True`, no `activation_token` set at
  all. Enrollment is created `PENDING` (not `ENROLLED`) — see part 3 below
  for why. New notification `send_student_login_invitation_email` replaces
  the old activation-link email, using `settings.STUDENT_FRONTEND_DOMAIN`
  with login-credentials framing.
- Existing account, `is_active and not must_change_password` (i.e.
  genuinely already onboarded) -> enrolled `ENROLLED` immediately, no
  password touched, `send_added_to_course_email` unchanged.
- Everything else (a `must_change_password=True` account still mid-
  onboarding, invited to a second course before ever logging in for the
  first — OR a legacy `is_active=False` row left over from before this
  change / not yet run through the part-4 backfill) -> fresh password,
  `is_active` force-set `True`, `must_change_password=True`, `PENDING`
  enrollment, resend the new login email.

**Deliberate deviation from the literal spec, confirmed by SM**: the
re-invite gate is `is_active and not must_change_password`, not
`must_change_password` alone. A pure `must_change_password`-only gate would
treat a legacy `is_active=False` row (created under the old scheme, which
never set `must_change_password`, so it defaults to `False`) as "already
onboarded" and wrongly fast-path it to `ENROLLED` + "you're already active,
sign in" — even though the account has no usable password and cannot
actually log in. Requiring both `is_active` AND not-mid-onboarding, plus
force-setting `is_active=True` unconditionally in the reset branch, makes
that branch self-heal any legacy row it happens to encounter. Proved by
`test_legacy_pending_row_self_heals_instead_of_fast_pathing` and the GATE
mutant below.

**`users/serializers.py`** — `CustomTokenObtainPairSerializer.validate`:
on a successful login where `user.user_type == UserTypes.STUDENT`, calls
the new `activate_pending_enrollments_on_login(student)`
(`classrooms/services/enrollment.py`), which bulk-`.update()`s every
`PENDING` `StudentCourse` row for that student to `ENROLLED`. Fires on
every student login (a cheap no-op query when nothing is pending), not
just a tracked "first login." A local (function-body) import is used to
dodge a real circular import: `classrooms.services` (via
`roster_import.py` -> `classrooms.serializers`) imports
`users.serializers`, so a module-level import here would be circular.

Note: SM's original instruction referenced hooking near an existing
`AuditAction.AUTH_LOGIN` emission "around line 417" in this file — that
code exists only on `integration/epic-a`, not on `beta` (confirmed via
`git show integration/epic-a:users/serializers.py`). Hooked instead at the
functionally-equivalent point in `beta`'s actual `validate()` (right after
`user.reset_login_lockout()`).

**`classrooms/management/commands/backfill_pending_student_invites.py`**
(new, part 4) — a one-off cutover command, not an auto-run schema
migration (it sends real email and mutates real user state). Selects every
`is_active=False` `STUDENT` with a non-empty `activation_token`, converts
each to the new scheme (real password, `is_active=True`,
`must_change_password=True`) and resends the new login email for their
oldest `PENDING` enrollment. `--dry-run` reports only, touches nothing,
sends no mail. Idempotent by construction: the selection query itself
excludes any row this command (or a real login) has already resolved, so
a second run — or a student who has since logged in for real — is never
revisited. A student with no `PENDING` enrollment left is skipped rather
than silently activated with no course context to email about.

**Cleanup**: `ensure_fresh_activation_token`/`_create_pending_student`
(the old activation-token-regen path, internal to
`enroll_student_by_email`) deleted outright — their only caller was
rewritten. `renew_student_activation` / `course/renew-student-token` /
`auth/register/student` are explicitly **NOT** removed and **NOT** a
pending decision: `classrooms/services/roster_import.py`'s
`_import_row_with_email` (bulk/CSV import, out of scope for this task)
independently creates `is_active=False` students with `activation_token`
and sends the old-style bulk-invite email, so those paths stay reachable
regardless of what ships here.

## 3. Test suite

`classrooms/tests.py` — three new classes:

- `EnrollStudentByEmailActiveImmediatelyTest` (4 tests) — new student is
  active with no activation token and a working temporary password
  (`check_password`, not just presence in the mocked call); already-
  onboarded existing account is enrolled immediately with no password
  reset; a student invited to course A (never logs in) then course B gets
  a genuinely new, working password for course B and the old one no
  longer validates; the legacy-row self-heal case.
- `ActivatePendingEnrollmentsOnLoginTest` (3 tests) — two `PENDING`
  enrollments in different courses both flip to `ENROLLED` on one real
  login through `CustomTokenObtainPairSerializer`; a student with no
  `PENDING` enrollments is an unaffected no-op; a non-student login never
  touches `StudentCourse` at all.

`classrooms/tests_backfill_pending_student_invites.py` (new, 5 tests) —
`--dry-run` touches nothing and sends no mail; a real run converts and
emails correctly; a second run is a no-op; a student who has since logged
in for real (own password, `must_change_password=False`) is never
revisited even though a stale `activation_token` is still on the row; a
student with no pending enrollment is skipped, not silently activated.

`classrooms/tests_cross_school_enrollment.py` — one pre-existing test
(`NewAccountsStillWork.test_an_unknown_email_creates_a_pending_student`)
updated: it asserted the old `is_active=False` scheme for a brand-new
single-add invite through the same endpoint. Found by the first full
regression run after this change (see below), fixed to assert
`is_active=True` + `must_change_password=True` (enrollment itself is
unaffected, still starts `PENDING`).

```
python manage.py test classrooms.tests \
  classrooms.tests_backfill_pending_student_invites \
  classrooms.tests_cross_school_enrollment \
  classrooms.tests_recalculate_final_grades \
  users.tests_login_lockout \
  billing.tests.test_license_service \
  --settings=settings_worktree --keepdb
```

**Ran 90+ tests across these labels, all OK** (73 in the three
student-invite-specific labels alone; see individual run logs in the
session transcript — not separately archived here since the full
regression below is the authoritative superset).

## 4. Mutation testing

6 mutants (`mutate.py.txt`) against the changed logic across
`classrooms/services/enrollment.py` (`enroll_student_by_email`,
`_create_new_student`, `activate_pending_enrollments_on_login`) and
`classrooms/management/commands/backfill_pending_student_invites.py`.
Applied one at a time from a collision-safe backup copy; restore verified
by md5 against the pre-mutation original after every mutant (never `git
checkout`) — the script raises `SystemExit` on any mismatch, and the run
completed without one. `git status --short` showed only the untracked
evidence directory throughout.

**Result: 6 / 6 KILLED**, first pass, no fixes needed.

| id | protection weakened | expected test | also caught by |
|----|---|---|---|
| ACT | new student created `is_active=False` again | `test_new_student_is_created_active_with_no_activation_token` | `test_an_unknown_email_creates_a_pending_student` (cross-school suite) |
| GATE | re-invite gate drops the `is_active` check, so a legacy `is_active=False`/`must_change_password=False` row is wrongly treated as already onboarded | `test_legacy_pending_row_self_heals_instead_of_fast_pathing` | — |
| PENDING | new student's enrollment created `ENROLLED` instead of `PENDING` | `test_new_student_is_created_active_with_no_activation_token` | `test_an_unknown_email_creates_a_pending_student` |
| LOGIN | login-flips-to-`ENROLLED` query filters on `ENROLLED` instead of `PENDING`, so nothing ever flips on login | `test_two_pending_enrollments_both_flip_on_one_login` | — |
| DRYRUN | backfill command's `--dry-run` flag stops gating the conversion, so a dry run mutates real state and sends real mail | `test_dry_run_touches_nothing_and_sends_no_mail` | — |
| IDEMP | backfill selection query drops the `is_active=False` filter, so a second run (or a student who's since logged in) is reprocessed | `test_student_who_has_since_logged_in_is_never_revisited` | `test_idempotent_second_run_is_a_no_op` |

Raw log: `mutation_log.jsonl`. Full results incl. md5s:
`mutation_results.json`.

## 5. Full regression

Session-unique isolated DB, `settings_worktree.py` ->
`test_student_invite_active`. Log redirected to a file, never piped
through `tail` before backgrounding.

```
python manage.py test --settings=settings_worktree --keepdb --parallel 4
```

First run (before the cross-school-enrollment test fix): **4597 tests,
484.079s, FAILED (failures=1, skipped=28)** — the one failure was the
stale `is_active` assertion described in section 3, not a defect in the
change itself. Fixed (commit `2a2f68e`), re-ran:

**Second run: 4597 tests, 512.921s, OK (skipped=28).**

Full log lived at
`/tmp/claude-1000/.../scratchpad/full_regression_student_invite_active_20260925_100248_run2.log`
(scratchpad, wiped between sessions) — sha256
`bc653a17ad04a571a10cb7f98026991e5b638f1c358643c4c9c93ce231c8bb61`, hashed
after the run completed. Tail archived here as
`full_regression_summary.txt`.

Independently reproduced by senior-manager in their own isolated env:
4597 tests, OK, 28 skipped — matches.

## 6. Tree state

`git status --short` throughout mutation testing and both regression runs:
clean except this untracked evidence directory. No mutation, test run, or
regression left any tracked file modified.

Commits on this branch, oldest first: `231f1dc` (WIP checkpoint - parts
1-3 implementation), `2579b4a` (tests for parts 1-3, including the
SM-requested legacy-row self-heal test), `b153d7e` (part 4: backfill
command + tests), `2a2f68e` (fix for the stale cross-school-enrollment
test found by the first full regression run).
