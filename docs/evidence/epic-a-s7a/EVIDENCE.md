# Epic A S7a: the per-item model and results contract (FR-A-07, 08a §4.3, §5). Author's evidence

**Author:** 1a (grade-automator-plus-c2), assigned by the SM on 2026-09-30. **Verifier:** v2. The author does not verify this.
**Branch:** `task/epic-a-s7a`, created from phase2/epic-a `fda47d7` with the base-ref script, fast-forwarded to `4124d73`. The change is `a52d133`; epic `522f818` was merged in as `4c8ba13` (no conflicts; S8). The SM asked for a base update onto d5's CodedError pickle fix when it merges; it isn't in epic yet.

## What changed
**Model** (`students/migrations/0029_background_task_item_result.py`, AddFields only):

| Field | Type | Why |
|---|---|---|
| `reason_code` | CharField(64), `default=""`, **`db_default=""`** | the failure's FR-A-06 code; empty means a success or an unclassified fault |
| `retry_count` | PositiveIntegerField, `default=0`, **`db_default=0`** | S7b's in-place retries |
| `item_index` | PositiveIntegerField, null | the item's 1-based position in its batch, in upload order (older rows have none) |
| `trace_id` | UUIDField, null | the dispatching request's server trace, which is the item's `reference` (QA-ERR-04; older rows have none) |

The two NOT NULL columns carry a `db_default`, so a code-only rollback still inserts rows (H-56); `AutoGrader.tests_migration_rollback_defaults` ran.

**Tracking** (`students/task_tracking.py`):
- `create_processing_task(item_index=...)` records `trace_id = resolve_trace_id()`, the same server id as `X-Request-ID`, which Celery propagates.
- `mark_processing_task_failure` stores `reason_of(error)`'s code: a `CodedError`, or one of the two refusals.
- `record_refused_item` makes a FAILURE item, with no Celery task, for a file refused before dispatch.

**session-results** (`users/views.py`, `users/serializers.py`, built in the new `students/item_results.py` so that S7b's retry endpoints reuse it). This is **backward compatible**: `status`, `file_name`, `task_id`, `error` and `context` keep their meaning.

| Per item (added) | Per session (added) |
|---|---|
| `item_id`, `item_index`, `submission_id`, `reason_code`, `error_class` (**SYSTEM** for a failure with no code), `message` (= `error` for a failure), `remediation`, `retryable`, `retry_count`, `reference`, `replaced_existing` | `failure_codes` ({code: count}; **"UNCLASSIFIED"**, a documented sentinel and not a ReasonCode, counts failures with no code), `stopped_at_item` (the lowest `item_index` with `INSUFFICIENT_CREDITS_MID_BATCH`; S7c raises that code), `resumable` (any failed item `retryable`) |

- **`reference`** is the trace id as **hex**, the same string as `X-Request-ID` and a sync body's `reference`, so a client can match them. `uuid.UUID(reference)` is the item's `AuditEvent.trace_id`. (My first dev run found the dashed form differed from the header; it was fixed before commit.)
- **`replaced_existing`** is lifted from S6c's task meta; that was the S7a half of F3.
- A legacy session (results JSON, no tracked items) still answers. Its entries don't have the new item keys (the serializer fields are optional), and its failures count as UNCLASSIFIED.

**Per-item refusals** (the SM's S6b scope ruling: the two batch problems belong to S7a):
- **batch-upload** (`students/views.py`) used to validate every file first and 413 the **whole batch**. Now a too-large file is refused as ITS item (FAILURE, `FILE_TOO_LARGE`, no Celery task), and the rest are dispatched.
- **assignments upload-async** (`assignments/views.py`) validated **inside** the dispatch loop, so a later bad file raised after earlier tasks were queued (a half-queued session). Now a malformed file (`FILE_UNREADABLE`) or a too-large one is refused as its item, and the rest run.
- Both answer **202** with every file in `tasks`; a refused item has `task_id: null` and its `item_id`. `TaskInfoSerializer.task_id` allows null, and `item_id` is added.

**The legacy grading paths are tracked** (`assignments/tasks.py`, `_dispatch_tracked_grading`):
- `grade_batch_async` (a scheduled batch) and `auto_grade_due_assignment` fanned out untracked `grade_engine_async.delay`, so their sessions answered only from the legacy results list, with no codes.
- Both now create a `BATCH_SUBMISSION_GRADING` item per submission, with its `item_index`, launch it through `launch_processing_task`, and emit `GRADING_REQUESTED`, exactly as grade-all does. If the scheduled batch's session could not be created, the items are still tracked; grading is never dropped.
- Auto-grade's task result used to return `str(e)` plus a traceback (QA-ERR-03). It now logs the exception and returns a fixed line.
- grade-all numbers its items.

## Design decisions for the verifier and the SM
1. `item_index` is **1-based**, in upload or dispatch order (08a §4.3's example "p07 → item_index 7").
2. A batch whose files are **all** refused still answers **202** with every item FAILURE. A batch endpoint always answers per item, rather than switching to a whole-request 413 when nothing is left.
3. `trace_id` is recorded **when the item is created**, not when it fails. So a refused-before-dispatch item has one, and an item created by a beat task gets the worker's propagated or fresh id.
4. UNCLASSIFIED is a sentinel key in `failure_codes` only. The item's own `reason_code` stays null.

## Coverage: each touched production file, and the modules that test it
| Production file | Covering modules |
|---|---|
| `students/models.py`, `students/migrations/0029_…` | `students.tests_batch_item_results`, `AutoGrader.tests_migration_rollback_defaults`; the `students` regression |
| `students/task_tracking.py` | `students.tests_batch_item_results`, `students.tests_task_tracking`; the `students` regression; the callers' modules in `classrooms`, `billing` and `ai_processor` (below) |
| `students/item_results.py` | `students.tests_batch_item_results`; `users.tests_task_viewset`, `users.tests_task_status_scoping`, `users.tests_remaining_branches`, `users.tests_auth_endpoints` (the `users` regression) |
| `students/views.py` (batch-upload) | `students.tests_batch_item_results`, `students.tests_submission_tenancy`, `students.tests_epic_a_submission_upload_audit`, `students.tests_post_grading_submission_lock` |
| `users/views.py`, `users/serializers.py` | the `users` regression; `students.tests_batch_item_results` (session-results end to end) |
| `assignments/views.py` (upload-async, grade-all), `assignments/serializers.py`, `assignments/tasks.py` | `students.tests_batch_item_results`, the `assignments` regression (incl. `tests_course_ownership_*`, `tests_upload_task_retry_policy`, `tests_file_reason_codes`) |
| Callers of the tracking helpers in other apps (unchanged code) | `classrooms.tests_student_summary_task`, `classrooms.tests_student_summary_tracking`, `billing.tests.test_refusal_handling`, `billing.tests.test_refusal_handling_gates`, `billing.tests.test_h38_part2_removed_teacher_routes`, `ai_processor.tests_answer_benchmark_failures` |

## Runs (rule 15 and its model-change addendum; each under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, its own test DB, `EXEMPT_EMAIL_DOMAINS` empty)
| Run | Tree | Result | Log |
|---|---|---|---|
| Reproduce first: the final new module on the **unchanged** epic | `4124d73` | **10 of 10 fail** (3 failures, 9 errors): no item fields, no codes stored, whole-batch 413, untracked legacy grading | `repro_4124d73.txt` |
| Changed modules outside the regression apps: the callers in `classrooms`, `billing` and `ai_processor`, `AutoGrader.tests_migration_rollback_defaults` (H-56 guard) and `AutoGrader.tests_reason_codes` | `4c8ba13` | **Ran 179, OK**, 370 MB peak | `changed_modules.txt` |
| **Model-change regression** (rule 15 addendum): `students` + `users` + `assignments`, the apps that read or write the new fields, as one serial run | `4c8ba13` | **Ran 1635, OK** (skipped 18), 383 MB peak, 4:22 | `regression_students_users_assignments.txt` |

`SUMMARY.txt` is the run sequence. The logs are trimmed to the test ids that ran plus the summaries, with emails redacted. A dev run before the commit (10 tests) found one defect: the item `reference` was in dashed UUID form, while `X-Request-ID` is hex. It was fixed before `a52d133`.

## Mutants (author's)
`mutate.py`; results in `mutants.txt`. Each is restored with a sha256 check. **11 of 11 killed.**

| Mutant | Killed by |
|---|---|
| S1 the failure's code not stored | 5 tests (18 failures), including the 30/12 |
| S2 no item_index in batch-upload | the shape test and the 30/12 |
| S3 batch-upload refused whole again | the per-item 413 test and the 30/12 |
| S4 upload-async refused whole / half-queued again | `test_upload_async_is_never_half_queued` |
| S5 `reference` in dashed form | the shape test (reference == X-Request-ID) |
| S6 an uncoded failure not SYSTEM | `test_an_unclassified_failure_is_visible_as_system_with_no_code` |
| S7 `replaced_existing` not lifted | the overwrite test and the shape test |
| S8 auto-grade untracked again | `test_auto_grade_creates_tracked_items` |
| S9 no UNCLASSIFIED sentinel | the unclassified test |
| S10 no trace recorded | the item-model test and the shape test |
| S11 a scheduled batch untracked again | `test_a_scheduled_batch_creates_tracked_items` |
