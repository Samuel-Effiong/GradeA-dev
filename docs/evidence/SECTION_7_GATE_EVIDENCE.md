# Section 7 (students) — verification evidence

Preserved record for Section 7 of `docs/CODEBASE_AUDIT_SECTIONS.md`.
Two passes: the review pass (2026-09-13) and the remediation pass
(2026-09-13/14) that the owner required before commit, applying the
ten-state verification gate to every significant fix. This file is the
durable copy; the session scratchpad is wiped between sessions.

**Read §9 before citing anything here.** The final production gate ran
against the committed tree; everything above §9 was measured on the
working tree that became that commit.

---

## 1. Tree under test

| | |
|---|---|
| Worktree | `../Grade-Automator-Plus-section-7-students-review` (`scripts/task-worktree.sh`) |
| Branch | `task/section-7-students-review` |
| Based on | `1373eae` — *Version the cache instead of wildcard-flushing it, and fix the test gate* (beta moved from `06516cf` to `1373eae` during this work; the branch was re-pointed and every stale copy of other sessions' uncommitted files was restored to `1373eae`, so the diff is this section's work only) |
| Test database | `test_section_7_students_review` (`--keepdb` runs) and `test_s7_fresh_teardown_227b` (fresh-create runs that exercise `DROP DATABASE`) |

Infrastructure, probed not assumed: **PostgreSQL 18.6** (`SELECT version()`),
**Redis 8.0.5** (`INFO server` over the configured broker URL). Broker,
result backend and cache are the project's real Redis; no LocMem, no
SQLite, no eager Celery.

## 2. What was found (review pass) and what was done about it

Severity: blocker / should-fix / nice-to-have. Standard: section of
`docs/CODE_REVIEW_STANDARDS.md`.

| # | Where | Std | Defect | Sev | Outcome |
|---|---|---|---|---|---|
| F-1 | `students/models.py` `BatchUploadSession.update_result` | §6 §4 | JSON-list read-modify-write with no row lock; concurrent batch tasks dropped each other's results, batch never completed | blocker | **Fixed** (`select_for_update`) |
| F-2 | `students/services.py` `upload_answers_engine` | §6 §4 | attempt-limit lock released before the save; concurrent uploads bypassed the limit | should-fix | **Fixed** (save under the lock) |
| F-3 | `students/services.py` proxy-upload matching | §4 §3 | `icontains…first()` filed an ambiguous name's work under an arbitrary student | blocker | **Fixed** (exact full-name match, else unique fuzzy match, else refuse) |
| F-4 | `students/services.py` `_populate_and_save_grade` | §6 §4 | full-row save from a stale instance reverted concurrent writes | should-fix | **Fixed** (`update_fields`) |
| F-5 | `students/task_tracking.py` status poll / cancel | §6 §10 | raw Redis errors became 500s after the DB already held the answer | should-fix | **Fixed** |
| F-6 | `notify_students_of_assignment_edit` | §7 | N+1 on `student.settings` | should-fix | **Fixed** |
| F-7 | attempt-limit error | §10 | bare `ValueError` shown as generic failure | should-fix | **Fixed** (`SubmissionLimitReachedError`, user-facing) |
| F-8 | dead code in 6 files | §2 | commented-out blocks; eradicate was disabled | nice | **Fixed**; rule enabled (R-7) |
| F-9 | `_mark_grading_claim_failed` | §7 §1 | third hand-copied wildcard list, no generation bump | nice | **Fixed** (`invalidate_submission_caches`) |
| R-1 | `assignments/tasks.py` redelivery skip | §6 | duplicate marked the ORIGINAL's tracked task SUCCESS mid-run; its later FAILURE could never be recorded | should-fix (owner: MUST FIX) | **Fixed** — see §4.1 |
| R-2 | `ai_grading_completed_at` DateField | §4 | dashboard "average grading time" = midnight − real time (probed: `-1 day, 8:16:00`) | should-fix (owner: MUST FIX) | **Fixed** — migrations 0027/0028, §4.2 |
| R-3 | `students/tasks.py` | §2 §12 | empty after F-8 | nice | **Removed** — §4.5 |
| R-4 | re-submission after grading | product | owner decided: no re-submission once graded | product rule | **Implemented** — §4.3 |
| R-5 | app boundary / import cycle | §1 | `students` locked+deleted `Assignment` directly; `students.services` ↔ `assignments.tasks` cycle | nice | **Fixed** — §4.6 |
| R-6 | `students/views.py`, `serializers.py`, `urls.py`, `admin.py` in no audit section | — | see §4.4: one tenancy **blocker** fixed; sync-AI endpoints tracked as **H-11 (release-blocking, owned)** | — | **Audited + tracked** |
| R-7 | flake8 `E800` ignored | §2 | eradicate effectively off | nice | **Rule ON**, 39-file frozen carve-out, burn-down H-12 |
| R-8 | double revoke in `cancel_processing_task` | §2 | two broadcasts per cancel | nice | **Fixed** |
| T-1 (new, remediation pass) | `assignments/tasks.py` `upload_answers_engine_async` | §6 §10 | marked the tracked task FAILURE before every retry; the terminal-status guard then blocked the retry's own SUCCESS from ever being recorded; terminal refusals were retried 3× (re-billing); a null batch session crashed the handler | should-fix | **Fixed** — §4.3 |
| V-1 (new, R-6 audit) | `students/views.py` upload endpoints | §3 | assignment looked up by bare id: any student could submit into any course, any teacher could batch-upload into any assignment | **blocker** | **Fixed** — §4.4 |

## 3. Test inventory (this section, after the remediation pass)

| Suite | Tests | Purpose |
|---|---|---|
| `tests_broker_outage`, `tests_task_tracking`, `tests_second_opinion_queue`, `tests_grading_idempotency` (the four named suites) | 57 → 60 | pre-existing; redelivery expectation corrected; broker-outage and single-revoke tests added |
| `tests_submission_concurrency` | 5 | F-1/F-2/F-4 barrier-synchronised real-thread races |
| `tests_proxy_upload_attribution` | 11 | F-3, F-7 |
| `tests_post_grading_submission_lock` | 21 | R-4 + T-1: service, API, task replay, threads, live HTTP |
| `tests_grading_redelivery_live` | 4 (5 scenarios) | R-1 on a real Celery worker, real Redis |
| `tests_grading_duration_migration` | 3 | R-2 migrations + dashboard on real Postgres |
| `tests_app_boundaries` | 6 | R-5 |
| `tests_submission_tenancy` | 6 | V-1, attacker-side |
| `tests_submission_update_freshness` | 4 | F-9 with legacy wildcard sweeps disabled |
| `tests_assignment_edit_notification` (+1) | | F-6 query flatness |

## 4. The ten-state gate, per fix

States: 1 Baseline/Regression · 2 Mutation · 3 Concurrency · 4 Adversarial · 5 Failure · 6 Stress/Scale · 7 Real infrastructure · 8 Live/End-to-end · 9 Security/Isolation · 10 Final gate (§9).

### 4.1 R-1 — redelivery must not finish the original's tracked task

Fix: `grade_engine_async`'s `SubmissionGradingInProgressError` branch now
compares the tracked row's `celery_task_id` with `self.request.id`. Same
delivery (a Redis redelivery, or a row not yet attached) → leave the row to
the claim holder. A separately dispatched duplicate (its own row, its own
message) → closed as SUCCESS+skipped as before.

| State | Evidence |
|---|---|
| 1 | `tests_grading_idempotency.GradeEngineAsyncSkipHandlingTest`: redelivery leaves the row STARTED and a subsequent `mark_processing_task_failure` records FAILURE; a separate duplicate is closed as skipped. The old test asserted the wrong outcome and was rewritten. |
| 2 | M8: `own_delivery` forced False → **FAILED (failures=1)**, restored by md5. |
| 3 | Live scenario 5: four submissions in flight on a 6-thread worker plus two redeliveries → every submission graded exactly once, AI called 4×, all six executions finished. |
| 4 | Scenario 1: the duplicate is a bit-identical replay of the original message (same task id, same kwargs) published while the original is held inside the AI call. |
| 5 | Scenario 2: original FAILS after the duplicate skipped → tracked task FAILURE with the classified message, submission FAILED (claim released). Scenario 4: a stale claim from a dead worker (RUNNING older than `GRADING_CLAIM_STALE_AFTER`) is reclaimed by the redelivered message. |
| 6 | Scenario 5 (above), worker concurrency 6, pool `threads`. |
| 7 | `celery.contrib.testing.worker.start_worker` bound to the project's real app: real Redis broker (`redis://127.0.0.1:6379/0`), real Redis result backend, `acks_late=True`, `visibility_timeout=3600`, real PostgreSQL. Private per-run queue `s7-redelivery-<hex>` so no other worker sees the messages; queue key deleted at teardown. |
| 8 | End-to-end from `apply_async` through the worker to the persisted grade, tracked task and result backend; scenario 3 recovers by re-dispatching after the failure. |
| 9 | N/A (no tenant boundary in this path; the claim is per-submission). |
| Result | `students.tests_grading_redelivery_live`: **4 tests OK** (first run 12.7 s; repeated inside the fresh-DB run in §6). |

### 4.2 R-2 — grading duration column

Fix: `ai_grading_completed_at` → `DateTimeField`. Migration 0027 (schema,
`AlterField`, reasoning in its docstring — old code keeps working against
the widened column in both directions), 0028 (data, `RunPython`, backfills
from `graded_at` which the same save sets milliseconds later; nulls
date-only values on never-graded rows; idempotent; reverse no-op).

| State | Evidence |
|---|---|
| 1 | `tests_grading_duration_migration`: migrate back to 0026, insert rows through the historical models exactly as old code left them (DATE in the column, precise `graded_at`), migrate forward: column type `timestamp with time zone` (from `information_schema`), backfilled value == `graded_at`, duration == 42 s, never-graded row nulled, untouched row untouched; backfill re-applied is a no-op; 0027 reverses to `date` and re-applies. |
| 2 | M9: backfill made a no-op → **FAILED (failures=1)**. |
| 3 | N/A (a one-shot migration). |
| 4 | Rows with a completion date but no `graded_at` (never-successful) are nulled, not counted. |
| 5 | Reverse migration applied and re-applied cleanly. |
| 6 | N/A here; the backfill is two set-based `UPDATE`s, O(rows) with no Python loop. |
| 7 | Real PostgreSQL 18.6, real `MigrationExecutor`. |
| 8 | Dashboard endpoint `/api/v1/super-admin/dashboard/ai_performance` as superadmin, real Redis cache: reported `avg_grading_processing_time` is positive and equals the mean of a backfilled historical row (30 s) and a row graded by the pipeline after the migration, within 50 ms. |
| 9 | N/A. |
| Also | `manage.py makemigrations --check`: no changes; `scripts/check_migration_safety.py`: additive (nullable AlterField). |

### 4.3 R-4 — no re-submission once successfully graded (+ T-1 task retry fix)

Rule: `graded_at is not None` closes the (student, assignment) row to the
student. Enforced in `students.services._check_student_may_resubmit`
under the row lock, pre-checked before the billed extraction
(`ensure_student_may_submit`), and pre-checked at every API entry
(`upload`, `upload-async`, `PATCH raw_input`) → 409 with the rule's
message. Teacher proxy uploads are NOT covered (recorded assumption, one
test flips if the owner decides otherwise). RUNNING is NOT closed
(previously agreed behaviour; H-13 asks for the product decision).

| State | Evidence |
|---|---|
| 1 | `tests_post_grading_submission_lock` (21 tests): refused before AI; refused under lock when the grade lands during extraction; ungraded still accepted; scope (other student / other assignment) unaffected; `remaining_attempts` 0 once graded; RUNNING accepted with claim untouched; API 409 on all three routes with no AI work and no tracked task; replay ×2, different filename, different bytes, different type all 409; another student 201; unauthenticated 401; wrong role 403; detail reports 0 remaining. |
| 2 | M11 (lock check off) **FAILED (1 failure, 1 error)**; M12 (service pre-check off) **FAILED**; M13 (view pre-check off) **FAILED**; M15a (refusal retried) **FAILED**; M15b (failure marked before retry) **FAILED**. |
| 3 | 8 real threads (`TransactionTestCase`) against a graded row → all 8 refused, row byte-identical. Upload racing the grade's own commit → no deadlock, grade lands, claim fields are the grader's, row closed afterwards. |
| 4 | Replays, altered content, altered filenames, altered media type; grade landing between the pre-check and the lock. |
| 5 | Task replay: a queued upload reaching a worker after the grade → refused inside the task, tracked FAILURE with the verbatim reason, never retried, never billed. Transient failure → retried, and the retry's SUCCESS is recorded (was impossible before T-1). |
| 6 | 12 concurrent real-HTTP clients (§8). |
| 7 | Real PostgreSQL row locks; real Redis cache on the retrieve path. |
| 8 | `LiveServerTestCase`: 12 concurrent multipart POSTs with a real JWT from 12 threads → all 409, no AI work, one row unchanged; unauthenticated → 401. |
| 9 | Student on a graded row cannot affect another student's row or their own other assignment (scope tests); role and auth boundaries proven at the API. |

### 4.4 R-6 — the unlisted views/serializers/urls/admin

Audited in full. Fixed: **V-1** (tenancy, §3 — `_assignment_open_to_student`
scopes the two student upload routes to ENROLLED enrolments,
`_assignment_taught_by` scopes batch upload to the teacher's own
courses; 404 so the id's existence is not confirmed), dead code (E800) in
both files, `read_only_fields` typo `grade_at`, four hard-coded
`remaining_attempts` computations centralised in the service, the
duplicated wildcard lists in `publish` and `mark-reviewed` replaced by
`invalidate_submission_caches`, closure errors mapped 500 → 409.
Tracked as **H-11 (release-blocking, owner Section 7 + frontend)**: the
three synchronous AI endpoints (§7 of the standards) and V-2..V-6.

| State | Evidence (V-1) |
|---|---|
| 1/4/9 | `tests_submission_tenancy` (6): attacker student → victim assignment: sync 404 with no AI work and no row; async 404 with no tracked task; WITHDRAWN/PENDING enrolment 404; enrolled student 201; attacker teacher batch-upload 404 with no session; own teacher 202. |
| 2 | M14 (bare-id lookup restored) → **FAILED (failures=4)**. |
| 8 | Through the DRF test client against the real URL conf; the same scoping is what the live-HTTP test in 4.3 passes through. |

### 4.5 R-3 — `students/tasks.py`

Repo-wide evidence before deletion: no `import students.tasks` /
`from students import tasks` / `students.tasks` string anywhere in code,
config, Dockerfile, scripts or celery-beat entries (the only hits were
the audit docs and `docs/backend/async-and-infrastructure.md`, updated);
`AutoGrader/celery.py` uses `autodiscover_tasks()`, which tolerates an app
without a tasks module. Deleted with `git rm`; `manage.py check` clean.

### 4.6 R-5 / R-8 — boundary, cycle, double revoke

`students.task_tracking.cleanup_cancelled_task_artifacts` now asks
`assignments.services.lock_placeholder_assignment_for_cleanup` (locks,
refuses if submissions exist) instead of locking and deleting the
assignment itself. `students.services` dispatches the formatter by
registered name (`celery_app.signature("assignments.tasks.formatted_grade_async")`),
removing the `students.services ↔ assignments.tasks` import cycle.
`cancel_processing_task` sends one revoke.

| State | Evidence |
|---|---|
| 1 | `tests_app_boundaries` (6): fresh-interpreter import of `students.services` leaves `assignments.tasks` out of `sys.modules`; the name resolves to the registered task; `grade_engine` dispatches a signature with that name; the cleanup service returns the locked placeholder, refuses with submissions, None when missing. `RevokeAppBindingTest` asserts exactly one broadcast. |
| 2 | M16 (cleanup ignores submissions) **FAILED**; M17 (second revoke restored) **FAILED**; M18 (import cycle reintroduced) → the test run itself dies with `ImportError: cannot import name 'StudentSubmissionSerializer' from partially initialized module 'students.serializers' (most likely due to a circular import)` — the mutant cannot even be loaded. |

### 4.7 Round-1 fixes F-1..F-9 (from the review pass, re-verified after rebase)

Mutation round 1 (7/7 killed, all restored by md5): M1 results lock, M2
upload lock, M3 grading full save, M4 first-match attribution, M5 N+1,
M6 broker guards, M7 limit-message passthrough. Concurrency: F-1 and F-2
are barrier-synchronised two-thread races on real connections. Failure:
F-5 with Redis errors injected at the result backend and the control
channel. F-9 freshness with the legacy wildcard sweeps neutralised in all
four signal modules (`tests_submission_update_freshness`): publish,
mark-reviewed and a failed grading claim each refresh the cached detail
on the generation counters alone; mutation round 3 removed each of the
three `invalidate_submission_caches` calls in turn → **3/3 FAILED**.

## 5. Mutation summary

| Round | Mutants | Killed | Restore verified |
|---|---|---|---|
| 1 (review pass) | 7 | 7 | md5, all |
| 2 (remediation) | 11 | 11 (M18 by import failure at load) | md5, all |
| 3 (cache freshness) | 3 | 3 | md5, all |

## 6. Connection teardown (H-2 standard)

The disproven `CloseConnectionsMixin` was NOT used (the H-2 session's
finding: `connections.close_all()` in `tearDown` only closes the test
thread's own connections). Every thread target here that touches the ORM
closes its connection in `finally`; the live-HTTP test's threads use only
`requests`; the in-process Celery worker closes each pool thread's
connection in a `task_postrun` handler and the worker thread's own in
`worker_shutdown` (Celery's Django fixup only closes obsolete ones).

Threaded suites run alone on a **freshly created** database (no
`--keepdb`), so `DROP DATABASE` is exercised:

> `tests_submission_concurrency tests_post_grading_submission_lock tests_grading_redelivery_live tests_grading_idempotency` — **47 tests, OK, exit 0, "Destroying test database" with no "other sessions using the database" line.**

(The first attempt, before the worker handlers, leaked **10 sessions**;
that is what the handlers fix.)

## 7. Static gates on the working tree

| Gate | Result |
|---|---|
| `manage.py check` | no issues |
| `makemigrations --check --dry-run` | no changes |
| `scripts/check_migration_safety.py` | additive |
| `pre-commit run --files <every changed file>` | black/isort/flake8/bandit/detect-secrets/mypy: **Passed** |
| `pre-commit run flake8 --all-files` (E800 now ON with the carve-out) | **Passed** |

## 8. Regression on the working tree (after rebase onto 1373eae)

`students assignments AutoGrader.tests_cache_dashboard_freshness
AutoGrader.tests_cache_superadmin_1522 AutoGrader.tests_cache_invalidation_coverage`
— 779 tests: 2 failures, both then fixed (a round-1 test whose premise the
new product rule supersedes, rewritten to exercise ungraded columns; the
follow-up dispatch test re-pointed at the new dispatch seam), and the
three affected modules re-run: **11 tests OK**. The full final gate is §9.

## 9. Final production gate — against the committed tree

Two code commits on `task/section-7-students-review`, both on top of
`1373eae`; this file's §9 is the only change in the docs-only commit that
follows them.

| Commit | Content |
|---|---|
| `54d3305` | the whole section 7 remediation (20 files, +1028/−544) |
| `98682e6` | two test suites the first full gate turned up: `users/tests_task_viewset` asserted the removed duplicate revoke; the grade-vs-upload race test patched one module attribute from two threads (fixed to a single shared patch; re-run 3× green) |

Everything below was run on **`98682e6`** with a **clean working tree**
(`git status --short` empty), so the tree under test IS the commit.

| Gate | Result |
|---|---|
| `pre-commit run --all-files` (black, isort, flake8 with E800 on, mypy, bandit, detect-secrets, gunicorn/webhook sync, and the file hygiene hooks) | **every hook Passed** |
| `manage.py check` | no issues |
| `makemigrations --check --dry-run` | no changes |
| `scripts/check_migration_safety.py --base 1373eae` | 0027, 0028: additive only |
| Full test suite, **fresh database** (`DROP`/`CREATE`, no `--keepdb`), `--parallel 1`, real PostgreSQL 18.6 + Redis 8.0.5 | **3,922 tests — OK, 12 skipped — exit 0** (1,592 s) |
| Teardown | `Destroying test database for alias 'default'...` with **no** "other sessions using the database" line |

The first full gate, on `54d3305`, ran **3,922 tests, 8 failures, exit 1**
(the two suites named above; teardown already clean). Both were test
defects, not product defects; nothing in application code changed
between the two gates.

Cross-app coverage in that run: every app's suite, including
`AutoGrader/tests_cache_*` (H-1 families with legacy sweeps disabled),
`assignments`, `classrooms`, `users`, `billing`, `dashboard`.

### 9a. Post-run checks (owner's final-gate spec, 2026-09-14)

Measured after the §9 run, from the same worktree:

| Check | Result |
|---|---|
| `select count(*) from pg_database where datname like 'test_s7_fresh_teardown_227b%'` | **0** — the test database is gone |
| `select count(*) from pg_stat_activity where datname like 'test_s7_fresh_teardown_227b%'` | **0** — no leftover sessions |
| Suspend during the run | The gate ran 11:55:37 → 12:22:34 (log file create/last-write times; `Ran 3922 tests in 1592s`). `journalctl -k` for the day shows a single `PM: suspend exit` at **09:43:38**, two hours before the run started, and none inside the window. |
| Tree before / after | `git status --short` empty before the run (recorded in the log header: `HEAD 98682e6…`, dirty files 0) and empty after; `find <worktree> -newer <start marker>` (excluding `.git`, `__pycache__`, and this evidence file, which the docs-only commit wrote afterwards) returns nothing. |
| Identity now | commit `88360b5`, tree `c02e1202…`, working-tree fingerprint `e3b0c442…` (the SHA-256 of empty input — i.e. no diff from HEAD and no untracked files). The tested code tree is `98682e6`'s; `88360b5` differs from it only by this file. |

The run was not wrapped in `systemd-inhibit`; the journal is the evidence
that no suspend occurred. Future gates should use the inhibit wrapper so
the question does not arise.

## 10. Open items handed to the owner

* **H-11** (release-blocking, owner Section 7 + frontend): the three
  synchronous AI endpoints in `students/views.py`, plus V-2..V-6.
* **H-13**: decided and implemented on 2026-09-14 — see §11.
* **H-12** (low): E800 carve-out burn-down; the Section 8 session has
  agreed to remove the dashboard entries in its own change.
* Teacher proxy uploads after grading: the owner decided on 2026-09-14
  that they are refused too — see §11.
* Not merged: the branch is ready for review; merging is the owner's call.

## 11. Owner decisions of 2026-09-14 — proxy lock and H-13

Two rules added after the §9 gate, in a follow-up commit on the same
branch (a further full fresh-DB gate follows in §12):

1. **Graded row is immutable through every ordinary upload path** —
   teacher proxy uploads are now refused after grading (previously the
   recorded assumption allowed them).
2. **H-13: uploads are refused while a live grading claim exists**, for
   students and proxies alike; a stale claim (older than
   `GRADING_CLAIM_STALE_AFTER`, i.e. a dead worker's) does not lock the
   row. New user-facing `SubmissionBeingGradedError`, 409 at the API,
   final non-retried failure in the batch task.

| State | Evidence |
|---|---|
| 1 | `tests_post_grading_submission_lock` now 29 tests: proxy after grading refused (row byte-identical); proxy during RUNNING refused, claim untouched; proxy on an ungraded row still accepted; student upload during RUNNING refused before the billed call; claim taken during extraction caught under the lock; stale claim does not lock; sync upload and raw-text edit 409 while RUNNING; batch task for a graded student records a FAILED batch entry with the verbatim reason after exactly one extraction. `tests_submission_concurrency`, `tests_proxy_upload_attribution`, `tests_upload_pipeline` unchanged and green (46 tests across the four modules). |
| 2 | M22 proxy exempted from the lock → **FAILED (2)**; M23 live-claim check off → **FAILED (6)**; M24 staleness rule removed (any RUNNING locks) → **FAILED (1)**. All restored by md5. |
| 3 | 8 concurrent teacher proxy uploads against a graded row → all refused, row unchanged; the grade-vs-upload race now resolves to a refusal every time (the claim precedes the AI call), no deadlock, grade lands. |
| 4/5 | Claim taken between the pre-check and the row lock; replay through the batch task; a dead worker's stale claim. |
| 7/8 | Real PostgreSQL row locks; DRF client against the real URL conf for the 409s. |
| 9 | Proxy refusal is per (student, assignment) row; the student's other rows and other students are unaffected (existing scope tests). |

## 12. Gate 3 — merged tip, after the 2026-09-14 decisions

Branch state: `ac731a9` (proxy lock + H-13 refusal) then merge commit
`2d48d73` bringing in beta's `f593be1` (H-1 user-row fan-out); the only
merge conflict was the backlog owner table, resolved by keeping the H-1
session's H-10 row and this branch's H-11..H-13 rows. Working tree clean
(`git status --short` empty), tree `7255a6aa…`.

| Gate | Result |
|---|---|
| `pre-commit run --all-files` | every hook Passed |
| `manage.py check` / `makemigrations --check` / `check_migration_safety.py --base f593be1` | clean / no changes / additive |
| Full suite, fresh database, `--parallel 1`, under `systemd-inhibit --what=sleep` | **3,959 tests — OK, 12 skipped — exit 0** (1,211 s; 15:12:30 → 15:33:39) |
| Teardown | `Destroying test database` with no "other sessions" line; afterwards `pg_database` count for the test DB **0**, `pg_stat_activity` count **0** |
| Suspend | run held under a sleep inhibitor; `journalctl -k` shows **0** suspend entry/exit events from 12:30 onward |
| Tree during the run | `find -newer <start marker>` (excluding `.git`, `__pycache__`) returns nothing; clean before and after |

The 37 additional tests over §9 are the H-1 fan-out suite from `f593be1`
plus this branch's new proxy/H-13 tests.
