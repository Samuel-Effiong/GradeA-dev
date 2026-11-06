# Epic C1: build notes

Branch `task/epic-c1`, base staging **8aafa47dd**. Plan 11
(task/plan-e1-c1 31b158382). The rulings and deviations decided before
any code was written are in EXPECTATIONS.md (rows 1–10). This file adds
what came up during the build. Logs: `GAP-a0-runs/c1/`.

## Owner ruling D5 (2026-11-06): a change to FR-C-08

FR-C-08's documented rule changes as follows:
- Every Unicode Cf (format) and Cc (control) character is removed before
  the trim and length checks.
- A whitespace control (tab, newline, CR) becomes a space first.
- A name that is empty after removal gives TAG_NAME_MISSING.
- Visible punctuation and emoji are unchanged while the owner's decision is
  pending. That rule is one row of `tags.normalize.CHARACTER_RULES`.

## Deviations found during the build

| # | What | Done | Source |
|---|---|---|---|
| 11 | `tags/tests_models.py` deleted a teacher who has courses with assignments. That fails today in `assignments/signals.py:110`, before any tag code runs: `course.teacher` raises DoesNotExist during the cascade. | The test uses a teacher with no courses and still proves the CASCADE. The signal is not touched in C1. | **H-298** (pre-existing; a bug fix goes to beta with owner permission); SM accepted |
| 12 | Billing's append-only guard is registered on AuditEvent and treats an AuditEvent insert inside `zero_credit_scope` as a ledger insert. The emitter then drops the event silently (FR-A-11). | Every tag write stays inside its zero-credit scope. Its audit event is written just after the scope closes, inside the same transaction. `tags.tests_services.RollbackTest` pins this: a rolled-back write leaves neither the row nor the event. When H-299 lands, the emit placement stays as it is (SM: don't churn it). | **H-299** (Lane 1: exempt AuditEvent in billing/immutable.py); SM accepted |
| 13 | makemigrations wrote the unique key and the CHECKs as separate AddConstraint operations, which the H-217 migration-safety check refuses. | They are in the CreateModel options. The tables are new and empty, so this creates the same constraints with no lock on existing rows. `check_migration_safety.py` reports "additive only". | SM accepted |
| 14 | A backfilled link that a teacher keeps in a set. | It stays TOPIC_BACKFILL, so `backfill_topic_tags --reverse` (a rollback of a bad backfill) removes it. Only a link the teacher adds becomes USER. | SM ruling (ii) |
| 15 | TAG_NAME_TAKEN. | params `{tag_type: <code>, name: <stored spelling>}`. The message shows the TagType label (`display`), with no new label text. | SM ruling (i) |

## Gates

| Log | Step | Result |
|---|---|---|
| 06-gate-models | models | 1 error: the deviation 11 test (H-298) |
| 07-gate-models | models | Ran 201, OK |
| 08-gate-service | service | 5 errors: audit events dropped (deviation 12, H-299) |
| 09-gate-service | service | Ran 348, OK; C1_RACE delete_attach delete_won=96 attach_won=4 |
| 10-rollback-pin | service | tags.tests_services Ran 41, OK (RollbackTest added) |
