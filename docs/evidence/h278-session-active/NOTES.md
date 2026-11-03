# H-278: one active session per school / per teacher (backend)

Owner-approved 2026-11-02 (via SM grade-automator-plus-39). Branch
`task/h278-session-active` from beta f5d2d5773; worktree
`Grade-Automator-Plus-h278`; test DB `test_AutoGrader_h278` (only this
worktree's settings name it). Venv outside the worktree:
`/tmp/claude-1000/-home-bond-servant-in-training-Documents-Projects-Grade-Automator-Plus/8eb9e903-a8dd-4901-a907-442e8f3a1e4e/scratchpad/h274_g10_env`
(freeze sha256 52da179f…147e; beta's requirements files are identical to the
ones it was built from). NO full suite (owner rule 2026-11-02); near runs only.

## Rules (owner) and design

| Rule | Design |
|---|---|
| a. `Session.is_active`, default False, no backfill | `BooleanField(default=False)`; migration `classrooms/0017` (no open branch has a classrooms 0017; SM confirmed) |
| b. at most one active per school / per teacher, in the DB | two partial `UniqueConstraint`s: `fields=["school"]` with `owner_type=SCHOOL, is_active=True`; `fields=["teacher"]` with `owner_type=INDIVIDUAL, is_active=True` |
| c. settable on create and PATCH by whoever CanManageSession already lets edit | serializer field writable; permissions unchanged (a licensed teacher stays read-only). Note: CanManageSession already lets a Super Admin edit any session, INDIVIDUAL included; unchanged |
| d. marking one clears the previous, one transaction, row lock | in `perform_create`/`perform_update`: `transaction.atomic()`, lock the scope's owner row (`School` for SCHOOL, the teacher's `CustomUser` for INDIVIDUAL) with `select_for_update`, read the scope's active session, clear it with `save(update_fields=["is_active"])` (signals bump the cache), then save |
| e. clearing allowed; delete leaves none | nothing is auto-picked |
| f. `is_active` on every session; `?is_active=` filter | `filterset_fields = ["is_active"]` |
| g. audited | SM ruling (option 1): `AuditAction.SESSION_ACTIVE_CHANGE`, one per request, only when the flag of the target actually changes; target `Session` = the session marked or cleared; metadata `{changed_fields: ["is_active"], is_active, previous_active_session_id, source: "api"}`; `school_id` = the session's school (SCHOOL) else the actor's; replaces the generic STATE_CHANGE row. `previous_active_session_id` is the scope's active session before the request (on a clear, the session itself; null when there was none). The key is added to the generic `ALLOWED_KEYS` too (the per-action list may only hold keys that are there; `tests_metadata.py`) |
| h. no new user-facing wording | none added; the audit verb is internal (SM approved) |

Merge note (SM): on top of H-273, SM adds `"Session": "Session"` to its
target_type label map; not on this branch.

## Red tests and expectations (written before the red run, on beta + tests)

Test: `classrooms.tests_h278_session_active` (30 tests). The event name is
pinned as the string "SESSION_ACTIVE_CHANGE" so the module imports before
the fix.

Before the fix, on this commit: **every one of the 30 tests FAILs or ERRORs;
none passes.** Kinds: `TypeError` (`Session() got unexpected keyword
'is_active'`) where a test creates an active session; `FieldError` (cannot
resolve `is_active`) where a test reads the active set; `KeyError`
(`is_active` missing from the response) or wrong event action where the API
answers but ignores the field.

After the fix: OK (30).

This red commit is made with SKIP=mypy (mypy: `is_active` is not a field
yet), as SM accepted for a red commit only. Every later commit and the tip
pass the full pre-commit with no SKIP.

## Red results and a test correction (fixes-worker, 2026-11-02)

Red run on 41d355d5: `Ran 30`, FAILED (failures=5, errors=43); all 30 tests
appear among the 48 FAIL/ERROR headers (subtests add the rest). Kinds: 32
FieldError, 5 TypeError, 5 KeyError, 5 wrong action ('STATE_CHANGE'), as
written.

The first green attempt showed two mistakes in the tests, not the code:
- `Session.save()` runs `full_clean()`, which checks the new constraints in
  Python (ValidationError) before the database is reached. The two DB-rule
  tests now set the flag with `QuerySet.update()`, so they prove the database
  constraint itself.
- The concurrency test assumed 20 events per round. From round 2 on, the
  previous round's winner is a no-op (no event) when it happens to run first,
  and it can be seen twice as "previous" when it is marked again mid-round.
  The test now asserts the exact chain: the previous ids are the last round's
  winner plus every target, minus this round's winner, as a multiset; one
  event per request that changed the flag.

Expectation for the corrected tests on the pre-fix code (this commit, run in
a detached worktree): every one of the 30 tests FAILs or ERRORs, none passes.
This commit is made with SKIP=mypy (red only; `is_active` is not a field yet).

## school_id of the event (SM ruling, 2026-11-02) and one added guard

SM: `school_id` must NOT fall back to the actor's school. SCHOOL session:
the session's school; INDIVIDUAL session: None. (A teacher whose licence
ended keeps `school_id`; the fallback would file their own switches in that
school's admin log.) `emit()` falls back whenever `school_id` is None, so
the fix adds a sentinel `audit.emitter.NO_SCHOOL` that stores None; no other
caller changes.

New tests (on a1332fc0, before the fix):
- `test_a_former_licence_teachers_own_switch_is_not_filed_under_a_school`:
  a teacher with `school_id` set, not under licence, switches an INDIVIDUAL
  session. Expected before the fix: **FAIL** (`school_id` is school A, not
  None). After: OK, and the row is absent from `school-admin-audit-events`.
- `test_a_super_admins_switch_is_filed_under_the_school` (added at mutant
  planning: no test noticed a `school_id` that fell back to the actor's
  school, because a school admin's school is the session's). Expected before
  the fix: PASS (it is a guard; its mutant is M-h278.10 below).

INDIVIDUAL-scope lock (SM note): `select_for_update` on the teacher's
`CustomUser` row, inside the switch's transaction and only for its length.
It serialises with any other write that locks the same user row (for
example the student upload-async door's lock on its own user row, and any
`select_for_update` of that teacher), which wait for the switch to finish.

Also in this commit: a typing annotation on the concurrency test's Counter (mypy
found it once mypy ran again; 3eadf679 had skipped it). No behaviour change.

## Fix results (2026-11-02)

- a1332fc0: the switch; module 30/30 OK (then 32/32 with the two tests above).
- ddb8ec06 red: `Ran 32`, FAILED (failures=1): only the former-licence test
  (`school_id` was school A), as written (`logs/red_run_ddb8ec06.txt`).
- b7438964: `NO_SCHOOL`; module + `audit.tests_emitter`: Ran 100, OK.

## Mutants (expectations, on b7438964, before any mutant run)

One per guard; each in a disposable detached worktree at b7438964, restored
by sha256 against the committed blob. Label: `classrooms.tests_h278_session_active`.

| Mutant | Change | Expected killer(s) |
|---|---|---|
| M-h278.1 | migration 0017: drop the `one_active_session_per_school` AddConstraint | `test_two_active_sessions_in_one_school_are_refused` |
| M-h278.2 | migration 0017: drop the `one_active_session_per_teacher` AddConstraint | `test_two_active_sessions_of_one_teacher_are_refused` |
| M-h278.3 | views: no `select_for_update` on the School row | `ConcurrentSwitches...test_school_switches` (a non-200 or two active) |
| M-h278.4 | views: no `select_for_update` on the teacher's user row | `ConcurrentSwitches...test_individual_switches` |
| M-h278.5 | views: the previous active session is not cleared | the switch tests (school, individual, create-that-switches) |
| M-h278.6 | views: no `filterset_fields` | `test_the_list_filters_on_is_active` |
| M-h278.7 | serializer: `is_active` not in fields | the API tests (create/patch/list/retrieve) |
| M-h278.8 | views: emit on every flag-carrying request (`if True:`) | `test_no_ops_leave_only_the_generic_row` |
| M-h278.9 | views: `previous_active_session_id` always None | the switch and clear event tests |
| M-h278.10 | views: a SCHOOL session's event passes `school_id=None` (actor fallback) | `test_a_super_admins_switch_is_filed_under_the_school` |
| M-h278.11 | views: an INDIVIDUAL session's event passes `school_id=None` (actor fallback) | `test_a_former_licence_teachers_own_switch...` |
| M-h278.12 | emitter: `NO_SCHOOL` handled like None | `test_a_former_licence_teachers_own_switch...` |

M-h278.3/4 are races: a survivor is re-run once and, if it survives again,
reported as a survivor, not explained away.

Blob sha256 at b7438964: views.py 27a97238…4b11, models.py 0b221354…8fbd,
serializers.py de2dc0d8…7456, migrations/0017 bb67275f…caf0,
audit/emitter.py 4cb06816…37e8.

## Near run (expectations, written before the run; held until SM says the Batch 32 run is done)

Labels: `classrooms audit AutoGrader.tests_cache_matrix_selftest
AutoGrader.tests_cache_matrix_measurement
AutoGrader.tests_cache_matrix_tenant_isolation
AutoGrader.tests_cache_invalidation_coverage` (the AutoGrader modules read
`session-list` through the cache). Counted with `build_suite` on the tip:
**1079 tests** (classrooms + audit: 1017 on beta f5d2d577, + 32 new = 1049).
Command: `manage.py test --settings=settings_worktree --parallel 4 --noinput`
with the venv above, DB `test_AutoGrader_h278`, fresh (no --keepdb).

Expected: **Ran 1079, OK**; skips only the opt-in reasons (real AI, load,
network, the Redis fork tests); none of the H-278 tests skips. Plus
`makemigrations --check` "No changes detected" and mypy (pre-commit, all
files) Passed.

## Mutant results (fixes-worker, 2026-11-02)

**M-h278.1-12: 12/12 KILLED**, each by the tests named in the table (logs in
`mutants/`); every file restored and its sha256 matched the committed blob.
- M-h278.3 / .4 (no lock): the concurrency test failed in all 10 rounds
  (no re-run needed).
- M-h278.5 also kills the concurrency tests (a switch without the clear is
  refused by `full_clean`).
- M-h278.12 also kills `test_an_individual_switch`: with the sentinel branch
  disabled, the emitter rejects the non-UUID `school_id` and writes no event.
- Three logs are committed as `gzip -n -9` (over the large-file limit): M-h278.3 raw sha256 662ee577…d6cb, gz 91bbde16…7dc4; M-h278.4 raw 1dd4f19f…9bef, gz f81a6675…c620; M-h278.5 raw d7f08c21…1e7b, gz b81c4aa1…8622.

## Near run result (fixes-worker, 2026-11-02, after SM's go: Batch 32 run done)

Tip f3ab44fa (docs-only after b7438964), 19:06:58-19:10:16, under
systemd-inhibit; no other GAP test run active; load 5.30 at start.
Pre-checks: mypy (pre-commit, all files) Passed; makemigrations "No changes
detected".

**Ran 1079 tests in 157.188s, OK (skipped=2)**, rc=0. Count as expected.
The 2 skips are one opt-in reason, "a benchmark, run by label with
AUDIT_BENCH=1 only" (audit). That reason was not in the list I wrote
(real AI, load, network, Redis fork); it is opt-in, and none of the H-278
tests skips. No test_AutoGrader_h278* DB is left.
Log: `logs/near_run_f3ab44fa.txt.gz` (raw sha256 143adcd7cd6e1ab565e12b015b440fbb73050059ab831a231eee8783feca0a2b, gz a855880e05c88fe06e77683497c6ef04b2b6f19e1575cb83f0c6b6227de88a87).
