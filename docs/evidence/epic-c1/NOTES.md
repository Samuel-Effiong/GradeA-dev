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
| 12 | Billing's append-only guard is registered on AuditEvent and treats an AuditEvent insert inside `zero_credit_scope` as a ledger insert. The emitter then drops the event silently (FR-A-11). | Every tag write stays inside its zero-credit scope. Its audit event is written just after the scope closes, inside the same transaction. `tags.tests_services.RollbackTest` pins this: a rolled-back write leaves neither the row nor the event. When H-299 lands, the emit placement stays as it is (SM: don't churn it). **2026-11-08:** H-299 is on staging (908856152) and merged here (01a39a0c9). The emit stays after the scope by choice; the `_audit` docstring now says so. | **H-299** (Lane 1: exempt AuditEvent in billing/immutable.py); SM accepted |
| 13 | makemigrations wrote the unique key and the CHECKs as separate AddConstraint operations, which the H-217 migration-safety check refuses. | They are in the CreateModel options. The tables are new and empty, so this creates the same constraints with no lock on existing rows. `check_migration_safety.py` reports "additive only". | SM accepted |
| 14 | A backfilled link that a teacher keeps in a set. | It stays TOPIC_BACKFILL, so `backfill_topic_tags --reverse` (a rollback of a bad backfill) removes it. Only a link the teacher adds becomes USER. | SM ruling (ii) |
| 18 | D5 kept visible punctuation and emoji (pending the owner). | The owner's visible-character rule (section below) and the new code TAG_NAME_HAS_SYMBOLS. **Reversed 2026-11-08** (section at the end): the rule and the code are gone. | Owner, 2026-11-06, via the SM |
| 15 | TAG_NAME_TAKEN. | params `{tag_type: <code>, name: <stored spelling>}`. The message shows the TagType label (`display`), with no new label text. | SM ruling (i) |
| 16 | `tags/tests_api.py` expected the "item" not-found text when another teacher, or an unknown id, PUTs tags on an assignment. | That case answers with the house H-296 text for an assignment: "We couldn't find that assignment. It may have been deleted." (`get_object_or_404`, as every assignment route does). A foreign or unknown **tag** still gets the "item" text. Both are existing approved wording, and neither tells the caller whether the thing exists. | H-296 house wording |
| 17 | The query-count test measured the first request of the test, which also warms per-request caches (5 queries against 3). | An unmeasured warm-up request first. After that, page sizes 2 and 7 cost the same number of queries. | test fix |
| 19 | The backfill counted a topic name with symbols in `skipped_bad_name`. | It is counted in its own key, `skipped_symbols`. Empty and too-long names stay in `skipped_bad_name`. Either way the topic is skipped, nothing is written, and the Topic is unchanged. Skip vs strip for such names is still with the owner. **Withdrawn 2026-11-08:** the red (e368a179b) and the code (325f115d5) were already committed when the reversal arrived, so they are undone by new commits, not dropped from history; `skipped_symbols` no longer exists. | SM ruling (a) |

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
| 22-red-skipped-symbols | backfill, red | tags.tests_backfill Ran 9: 1 failure, 1 error, as expected (deviation 19) |
| 23-gate-skipped-symbols | backfill | tags.tests_backfill + the snapshot: Ran 14, OK |
| 24-mutants (mutants/) | battery, old rule | M1–M14 + M11b: 15 of 15 killed, each restore sha256-matched. Superseded by 29 |
| 25-gate6-500-tags | Gate 6, old rule | Ran 2, OK, flat. Superseded by 30 |
| 26-near-run | near run, old rule | **stopped** when the 2026-11-08 reversal arrived; not a result |
| 27-red-symbols-reversed | reversal, red | Ran 62: 13 failures, 33 errors, all from the old refusal, the `skipped_symbols` key, the U+2019 mapping or the registered code |
| 28-gate-symbols-reversed | reversal | tags + the snapshot: Ran 129, OK; C1_GATE6 flat 2/2; C1_RACE delete_won=96 attach_won=4; makemigrations --check: no changes |
| 29-mutants (mutants-29/) | battery | M1–M14 + M11b + MS: 16 of 16 killed, each restore sha256-matched |
| 30-gate6-500-tags | Gate 6 | Ran 2, OK; 2 queries for 2 tags and for 500, on page 1, page 5, `tag_type` and `q` |
| 31-near-run | near run | **stopped**: the SM asked for the staging merge (H-299) first; not a result |
| merge 01a39a0c9 | base | origin/staging 908856152 (H-299 only) merged; `_audit` docstring reworded (fddc3b209) |
| 32-mutants-merged (mutants-32/) | battery, merged base | M1–M14 + M11b + MS: 16 of 16 killed, each restore sha256-matched |
| 33-gate6-merged | Gate 6, merged base | Ran 2, OK; 2 queries for 2 tags and for 500 |
| 34-near-run-merged | near run, merged base | tags classrooms assignments audit billing + AutoGrader.tests_migration_rollback_defaults, under the machine lock: **Ran 4637, OK (skipped=15)**; C1_GATE6 flat 2/2; C1_RACE delete_won=97 attach_won=3. See the skips section. |

## The backfill and the audit trail

`backfill_topic_tags` writes TAG_CREATE for each tag it makes and TAG_DELETE
for each tag `--reverse` removes, with the SYSTEM actor (a command has no
user) and ids and labels only. It writes no event for a link, and nothing
during a dry run: the dry run runs inside a transaction that is rolled back.

## Owner ruling (2026-11-06): the visible-character rule, a second change to FR-C-08

> **Reversed by the owner on 2026-11-08.** Kept here as the record of what
> was built and removed; see the last section.

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
topic whose name has symbols is counted in `skipped_symbols` (deviation 19),
is never written, and its Topic is left unchanged.

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

## Owner ruling (2026-11-08): the visible-character rule reversed

Tag names can contain symbols, punctuation and emoji again. Via the SM:
- **Removed:** TAG_NAME_HAS_SYMBOLS in full: the check, the reason code,
  its message and its catalogue entry. No new wording is needed, because a
  message is being removed.
- **Kept:** D5 (strip Cf/Cc, collapse whitespace; empty after the strip
  gives TAG_NAME_MISSING), the 100-character cap, the 50-tag cap and the
  duplicate rules.
- **Code:** `tags/normalize.py` and `backfill_topic_tags.py` are
  byte-identical to 445ae769a, the last commit before the 2026-11-06 rule.
- **Backfill:** a Topic name with symbols is copied, normalized like any
  other. There is no `skipped_symbols`; the other skip counts are unchanged.
- **History:** new commits on top, none rewritten: expectations
  6c178042d, code 559d6b648, battery 2a14cb4a5.

Consequences of restoring 445ae769a, now pinned by tests:
- **U+2019 is no longer mapped to '.** "Newton’s Laws" and "Newton's
  Laws" are two different tags. Raised with the SM.
- **"Year ½"** is accepted. Its NFKC form is "Year 1⁄2", and that is
  the stored name.
- **Zero-width joiners inside an emoji sequence are still removed** (they
  are Cf, under D5), so "👨‍👩‍👧" is stored as
  three separate emoji.

Tests:
- The tests that pinned refusal now assert acceptance. None is skipped.
  - The 26 refused names are kept as typed.
  - The SM's examples are accepted: "Reading & Writing", "C#", "Year ½",
    and an emoji name.
  - API create and rename with symbols succeed.
  - A backfilled Topic name with symbols is copied.
  - The catalogue asserts that TAG_NAME_HAS_SYMBOLS is gone.
- Plants 21 and 21b are replaced by one plant, MS, in the battery: the
  symbol refusal re-added to `normalize`. It was killed in 29 with 6
  failures.

## Final near run: the skips (log 34)

All 15 skips are opt-in by environment variable, and none is missing
infrastructure. Chromium is present: none skipped for a missing browser.
- 9 load tests (`RUN_LOAD_TESTS=1`)
- 4 real AI calls, which are billed (`RUN_REAL_AI=1`)
- 2 audit benchmarks (`AUDIT_BENCH=1`)

The pre-loss baseline is the E1a near run (`e1a/near-run-1e28a9952.log`):
Ran 2954, OK (skipped=16). That log was written at low verbosity, so it
names no skips, and its label set differed from this run's. The comparison
is by count only: 15 now against 16 then, with no new kind of skip.

## Owner ruling (2026-11-08): U+2019 is the straight apostrophe

Via the SM: a tag typed with the curly apostrophe U+2019 is the same tag
as one typed with a straight `'`. On top of 3f50ef40d (already on staging).

Expectations, written before any code:
- **normalize:** U+2019 becomes `'` before the duplicate check and before
  storing. "Newton’s Laws" and "Newton's Laws" give one key,
  `newton's laws`, and the stored display name has the straight `'`. The
  mapping does not change the length (one character for one).
- **Only U+2019.** U+2018 and the double quotes U+201C and U+201D are kept
  as typed, and so are look-alikes the owner was not asked about: U+02BC
  (modifier letter apostrophe) and U+2032 (prime). "‘Single’" is stored as
  "‘Single'".
- **API:** creating "Newton’s Laws" after "Newton's Laws" (or the other
  way around) reuses the first tag (200, the same id, the straight `'`).
  Renaming another tag to the curly spelling is refused with
  TAG_NAME_TAKEN, as any other duplicate is.
- **Backfill:** it gets the mapping through `clean_tag_name`. A Topic named
  with U+2019 becomes a Unit tag with `'`, and it reuses the teacher's
  existing straight-apostrophe tag. The Topic itself is unchanged.
- **No new wording.** No reason code, message or migration is added.
- **Accepted as they are, for information:** "Year ½" is stored in its
  NFKC form "Year 1⁄2", and the zero-width joiners in an emoji family are
  removed (D5), so it is stored as separate emoji. Both stay pinned by
  tests.
- **Plant:** the mapping removed. It must be killed.
- **Logs:** 35 red, 36 green, 37 plant, 38 targeted near run (tags and the
  dark snapshot).
