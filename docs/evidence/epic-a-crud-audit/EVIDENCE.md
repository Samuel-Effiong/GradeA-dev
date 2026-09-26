# Assignment CRUD / roster change / submission upload audit evidence (§6)

Worktree: `Grade-Automator-Plus-epic-a-crud-audit`, branch
`task/epic-a-crud-audit`, off `integration/epic-a` `148d32c` (already
carries T1/T2 foundation, credit-audit, the query API, and the retention
sweep).

## 0. Scope and naming note

The plan's §6 table (and the assignment message relaying it) calls the
emitter `emit_audit_event()`; the actual T1-built module is
`audit.emitter.emit()` (the plan's original draft name, pre-implementation
— every landed call site so far, e.g. `users/serializers.py`'s
`AUTH_LOGIN`/`AUTH_LOGOUT`, already uses `emit()`). Built against the real
function, not the plan's draft name.

Ten call sites across the three "genuinely per-view" categories still
missing instrumentation, per §6's own survey:

- **Assignment create** — `assignments/views.py` `create()` and
  `create_async()`.
- **Assignment update** — `partial_update()` and `update_async()`.
- **Assignment delete** — no override existed; added a `perform_destroy()`
  override (DRF's default `destroy()` calls it) since there was nothing to
  instrument in place.
- **Roster change** — `classrooms/views.py` `bulk_add_students()` and
  `remove_student()` (no single-student-add endpoint exists).
- **Submission upload** — `students/views.py` `upload_answers()`,
  `upload_answers_async()`, `batch_upload()`.

Every site follows the plan's own instrumentation rule: the `emit()` call
sits at the point that already knows the outcome (right after the row is
created/updated/deleted, or the background task is successfully queued) —
no new try/except blocks were added to manufacture a failure path that
didn't already exist. A bulk operation (roster bulk-add, batch upload) is
one action with a `metadata` count, not one event per row/file — same
precedent as `CREDIT_TRANSACTION`'s bulk-write instrumentation.

Metadata on every call site is drawn from `audit/metadata.py`'s existing
per-action allow-list (`ASSIGNMENT_CREATE`: `course_id`; `ASSIGNMENT_UPDATE`:
`course_id`, `changed_fields`; `ASSIGNMENT_DELETE`: `course_id`;
`ROSTER_CHANGE`: `course_id`, `item_count`/`succeeded_count`/`failed_count`,
`student_id`; `SUBMISSION_UPLOAD`: `assignment_id`, `file_type`,
`file_size_bytes`, `file_count`) — all of this was already designed in by
the T1 foundation work, not invented here.

## 1. Test suite

Three new files, one per category, ten tests total — each drives the real
HTTP endpoint once and asserts FR-A-01's literal wording: exactly one
`AuditEvent` (`AuditEvent.objects.count() == 1`, not `>= 1`), plus the
action, outcome, target, and metadata:

- `assignments/tests_epic_a_crud_audit.py` — create (sync + async),
  partial_update, update_async, destroy. Only the billed AI extraction is
  stubbed; the row creation/update/deletion is real.
- `classrooms/tests_epic_a_roster_audit.py` — bulk_add_students (2 real
  students via TSV paste, asserts `item_count`/`succeeded_count` in
  metadata), remove_student.
- `students/tests_epic_a_submission_upload_audit.py` — sync upload, async
  upload, batch upload (3 files, asserts `file_count == 3` and still
  exactly one event, not three). Confirms the STUDENT-actor sync-upload
  event carries no `actor_email` (X-4/`audit_student_no_pii_ck`), reusing
  `students.tests_submission_tenancy`'s established "fund the teacher's
  wallet, not the student's" credit-gate pattern.

`python manage.py test assignments.tests_epic_a_crud_audit classrooms.tests_epic_a_roster_audit students.tests_epic_a_submission_upload_audit --settings=settings_worktree --noinput -v 2`

- Found 10 test(s)
- **OK**

## 2. Mutation testing

8 mutants (`mutate.py`), one per call site's `emit()` call (the two
byte-identical `ASSIGNMENT_CREATE` blocks in `create()`/`create_async()`,
and the two byte-identical `ASSIGNMENT_UPDATE` blocks in
`partial_update()`/`update_async()`, were each written from the same
template, so those two mutants delete both copies at once and require
both sites' tests to fail together — still one mutant per genuinely
distinct code shape, not one per file). Applied one at a time from a
collision-safe backup, restore verified by md5 against the pre-mutation
file after every mutant (never `git checkout`); working tree confirmed to
hold only the intended instrumentation diff afterward (`git status`/`git
diff`, `md5sum` matching the pre-mutation value).

**Result: 8 / 8 KILLED**, clean on the first pass (after fixing the
mutation script's own precheck, which initially rejected the two
intentionally-doubled patterns as "not unique" — a script bug, not a test
or instrumentation bug).

Full per-mutant log: `mutation_log.jsonl.txt`. Script: `mutate.py.txt`
(paths inside it are absolute to this worktree, as run).

## 3. Regression

Run via `scripts/isolated-test-env.sh` (private Postgres 16 + Redis).

App-focused pass first: `assignments classrooms students audit`
(`--parallel 4`) — 1302 tests, OK (14 skipped), 86s.

Full suite: `python manage.py test --settings=settings_worktree --parallel 4 --noinput`

- Ran 4718 tests in 282.712s (~4.7 min)
- **OK (skipped=28)**
- 0 `FAIL`/`ERROR` lines anywhere in the log

Tail of the full run: `full_regression_summary.txt`.

## 4. Conclusion

All ten "genuinely per-view" call sites named in §6 (assignment CRUD,
roster change, submission upload) now emit exactly one well-formed audit
event per action, matching FR-A-01's literal acceptance criterion and the
existing `AUTH_LOGIN`/`CREDIT_TRANSACTION` conventions. No regressions
anywhere else in the codebase from this change.
