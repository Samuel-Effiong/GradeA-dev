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
