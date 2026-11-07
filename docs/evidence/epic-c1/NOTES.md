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
| 18 | D5 kept visible punctuation and emoji (pending the owner). | The owner's visible-character rule (section below) and the new code TAG_NAME_HAS_SYMBOLS. | Owner, 2026-11-06, via the SM |
| 15 | TAG_NAME_TAKEN. | params `{tag_type: <code>, name: <stored spelling>}`. The message shows the TagType label (`display`), with no new label text. | SM ruling (i) |
| 16 | `tags/tests_api.py` expected the "item" not-found text when another teacher, or an unknown id, PUTs tags on an assignment. | That case answers with the house H-296 text for an assignment: "We couldn't find that assignment. It may have been deleted." (`get_object_or_404`, as every assignment route does). A foreign or unknown **tag** still gets the "item" text. Both are existing approved wording, and neither tells the caller whether the thing exists. | H-296 house wording |
| 17 | The query-count test measured the first request of the test, which also warms per-request caches (5 queries against 3). | An unmeasured warm-up request first. After that, page sizes 2 and 7 cost the same number of queries. | test fix |

## Gates

| Log | Step | Result |
|---|---|---|
| 06-gate-models | models | 1 error: the deviation 11 test (H-298) |
| 07-gate-models | models | Ran 201, OK |
| 08-gate-service | service | 5 errors: audit events dropped (deviation 12, H-299) |
| 09-gate-service | service | Ran 348, OK; C1_RACE delete_attach delete_won=96 attach_won=4 |
| 10-rollback-pin | service | tags.tests_services Ran 41, OK (RollbackTest added) |
| 11-gate-api | API | 9 backfill errors (not built yet) + 4 API failures (deviations 16, 17) |
| 12-gate-api | API | Ran 412, OK (backfill excluded); C1_RACE delete_won=93 attach_won=7; then mypy refused 3 typings at commit |
| 13-gate-api | API | after the typing fixes: Ran 412, OK; C1_RACE delete_won=96 attach_won=4 |
| 14-gate-backfill | backfill | tags.tests_backfill: 1 failure (dry-run notice was on the RESULT line) |
| 15-gate-backfill | backfill | full C1 set, Ran 421: the same 1 failure (my edit missed after black re-wrapped the line) |
| 16-gate-backfill | backfill | full C1 set: Ran 421, OK; C1_RACE delete_won=100 attach_won=0 |
| 17-backfill-after-lint | backfill | after the flake8 B904 fix: Ran 13, OK |
| 18-red-symbols | symbols, red | Ran 53: 7 failures, 2 errors (the normalize module failed to import) |
| 19-red-symbols | symbols, red | Ran 66: 41 failures, 1 error, as expected; snapshots green |
| 20-gate-symbols | symbols | full C1 set: Ran 426, OK; C1_RACE delete_won=86 attach_won=14 |
| 21*, 21b | symbols, plants | see the visible-character section |

## The backfill and the audit trail

`backfill_topic_tags` writes TAG_CREATE for each tag it makes and TAG_DELETE
for each tag `--reverse` removes, with the SYSTEM actor (a command has no
user) and ids and labels only. It writes no event for a link, and nothing
during a dry run: the dry run runs inside a transaction that is rolled back.

## Owner ruling (2026-11-06): the visible-character rule, a second change to FR-C-08

It replaces the D5 "visible: keep" row. After the Cf/Cc strip and the
whitespace collapse, a tag name may contain only:
- letters of any script (L*)
- combining marks (M*)
- numbers (N*)
- the space
- - ' . ( )

The typographic apostrophe U+2019 counts as ' and is stored as '.
Everything else is refused with the new code TAG_NAME_HAS_SYMBOLS: 400,
VALIDATION, not retryable, no remediation. Its message is the owner's,
word for word: "Tag names can only use letters, numbers, spaces and - ' . ( )".

The checks run in this order:
1. Empty, which gives TAG_NAME_MISSING.
2. Characters, which gives TAG_NAME_HAS_SYMBOLS.
3. Length, which gives TAG_NAME_TOO_LONG.

The rule applies to create, rename and the backfill. In the backfill, a
topic whose name fails is counted in `skipped_bad_name`, is never written,
and its Topic is left unchanged.

Edge case, raised with the SM: NFKC turns a vulgar fraction such as ½ into
1, U+2044, 2. U+2044 FRACTION SLASH is a math symbol (Sm), so "Year ½" is
refused, even though ½ itself is a number (No). This is pinned as a refused
case. If the owner wants it allowed, the change is one expectation and one
line.

Red run: `19-red-symbols`. Ran 66: 41 failures and 1 error, as expected; the
five snapshots are green. The 41 failures are the 30 refused classes, the
apostrophe case, the order and table checks, and the API and backfill cases.
`18-red-symbols` was the same run before mypy made the test look up
`ALLOWED_MARKS` at run time instead of importing it.

Green: `20-gate-symbols`. Full C1 set: Ran 426, OK; the snapshots are
byte-identical; C1_RACE delete_won=86 attach_won=14.

Plants, each run against tags normalize, api and backfill. Each restore was
from a saved copy, sha256 ebbde4c0…eb33, and matched:

| Log | Plant | Result |
|---|---|---|
| 21-plant-no-char-check | the character check removed | killed: 38 failures |
| 21-plant-no-apostrophe | did **not** apply: the match string was wrong, so the run tested unmutated code (OK). Not counted. | — |
| 21b-plant-no-apostrophe | the U+2019 mapping removed | killed: 1 failure, 1 error |
| 21-plant-marks-plus-amp | `&` added to ALLOWED_MARKS | killed: 4 failures |
