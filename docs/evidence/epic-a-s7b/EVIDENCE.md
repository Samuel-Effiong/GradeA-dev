# Epic A S7b: per-item retry (FR-A-07, 08a §4.4, §5; F4). Author's evidence

**Author:** 1a (grade-automator-plus-c2). The SM moved S7b from d5 on 2026-09-30. **Verifier:** v2. The author does not verify this.
**Branch:** `task/epic-a-s7b`, off S7a's tip `a9dd8bd` (the SM: "branch off S7a's tip for now"). The change is `0c45ad7`, plus three test-only commits (`60801a1`, `d600629`, and the stale-copy test in the evidence commit). S7a's updated tip `039bbc8` (its N1, and its base update onto epic `3d6575c` with the CodedError serialization fix) was merged in as `add3b1e`, with no conflicts. **S7b depends on S7a**, so it merges after S7a.

## What changed
| Endpoint | Body | Answer |
|---|---|---|
| `POST tasks/session/{session_id}/items/{item_id}/retry` | none | **202** with the item in session-results' shape; or **409 NOT_RETRYABLE** |
| `POST tasks/session/{session_id}/retry-failed` | optional `{"reason_codes": [...]}` | **202** `{"retried": [item_id], "skipped": [{"item_id", "reason_code"}]}` |

**The rule** (`students/item_retry.py`):
- A **grade** item (`BATCH_SUBMISSION_GRADING`) that has **FAILED** with a code the catalogue marks `retryable` (**PROVIDER_FAILURE**, **INSUFFICIENT_CREDITS_MID_BATCH**) is retried **in place**. It keeps the same row and `item_id`; `retry_count` goes up by 1; it goes back to PENDING with its code and error cleared; its `trace_id` becomes the retrying request's, so its `reference` equals that response's `X-Request-ID`. It is relaunched through `launch_processing_task` (`grade_engine_async`, a new Celery id), and `GRADING_REQUESTED` is emitted, exactly as grade-all does.
- **Anything else is 409 NOT_RETRYABLE, and nothing is launched**: an uncoded failure, a non-retryable code (for example RUBRIC_MISSING or FILE_UNREADABLE), or an item that hasn't failed.
- **An upload item is never retried in place, whatever its code**: its file isn't kept (F4). It answers "…its file isn't kept. Upload the file again." with **`params.resolution = "replace_file"`**, which tells a client which action to offer. NOT_RETRYABLE's message became a `{why}` placeholder. `why` is only ever a server constant, passed through `display`, never a param. The default text ("This item can't be retried as it is.") is unchanged.
- **Races:** the claim is one conditional UPDATE on the status, code and `retry_count` read. Two retries of one item at once give exactly one 202 and one 409 (`TwoRetriesOfOneItemAtOnce`, a TransactionTestCase with 2 threads and a barrier).
- **retry-failed** walks the session's failures in `item_index` order, retries each one that can be retried (only the given codes, when `reason_codes` is sent), and lists every other failure as skipped, with its code (null when it has none). A malformed `reason_codes` is 400.
- **Access:** the session must be the caller's own (`teacher=request.user`), and the item must belong to it. A malformed id is 404, not a 500. The permissions are `IsAuthenticated`, `IsTeacher` and `HasCreditBalance`, so an empty wallet gets the existing pre-flight 402 before anything is retried. A broker outage answers 503, with the item FAILED again (`launch_processing_task`).
- **Deferred:** `resolve` (F2/F3). Per F4, uploads are re-uploaded.
- **Serialization:** `ItemNotRetryable` is a `CodedError`. With the merged CodedError fix, its `args` carry `display`, so the upload refusal keeps its message through pickle and through Celery's json rebuild (`TheRefusalSurvivesSerialization`).

**Where retryable codes come from:** PROVIDER_FAILURE is raised by S6d (d5, in progress) and INSUFFICIENT_CREDITS_MID_BATCH by S7c. S7b keys only on the stored `reason_code`, so both work as soon as those slices store them. Until then, a grading failure is uncoded, and a retry answers 409 (the tests store the codes directly).

## Coverage: each touched production file, and the modules that test it
| Production file | Covering modules |
|---|---|
| `students/item_retry.py` (new) | `students.tests_item_retry` |
| `users/views.py` (the two TaskViewSet actions, `_uuid_or_404`) | `students.tests_item_retry`; the `users` regression; `audit.tests_route_coverage` (new write routes) |
| `AutoGrader/reason_codes.py` (NOT_RETRYABLE's `{why}`, `resolution`) | `AutoGrader.tests_reason_codes`, `AutoGrader.tests_codederror_serialization`, `students.tests_item_retry` |
| The repo-wide guards (addendum 2: a new module and new routes) | `AutoGrader.tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`, `tests_redis_test_isolation`, `audit.tests_route_coverage`, `audit.tests_history_guard`, `classrooms.tests_teacher_access_sweep`, `classrooms.tests_course_roster_scope_sweep` |

## Runs (rule 15; each under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`, its own test DB, `EXEMPT_EMAIL_DOMAINS` empty)
| Run | Tree | Result | Log |
|---|---|---|---|
| Reproduce first: the new module on S7a's **unchanged** tip | `a9dd8bd` | **10 of 10 fail** (every one: no `students.item_retry`) | `repro_a9dd8bd.txt` |
| Dev run: the module + `AutoGrader.tests_reason_codes` | pre-commit | 42 OK | `SUMMARY.txt` |
| Changed modules + **every repo-wide guard** (rule 15 addendum 2: a new module and new write routes): `students.tests_item_retry`, `AutoGrader.tests_reason_codes`, `tests_codederror_serialization`, `tests_no_wildcard_invalidation`, `tests_cache_invalidation_coverage`, `tests_migration_rollback_defaults`, `tests_redis_test_isolation`, `audit.tests_route_coverage`, `audit.tests_history_guard`, `classrooms.tests_teacher_access_sweep`, `tests_course_roster_scope_sweep` | `d600629` | **Ran 135; 134 OK, 1 FAIL**: `tests_no_wildcard_invalidation` on **`audit/bench_volume.py:70`, S8's benchmark harness `cache.clear()`, inherited from epic.** That is the known Gate 10 red that v2 recorded on S7a's re-check, and ed is moving the harness to a test path. **It is not in S7b's diff** (S7b touches nothing under `audit/`). `audit.tests_route_coverage` passes with the two new write routes. | `changed_modules_and_guards.txt` |
| ONE owning-app regression: `users` (the endpoints are on TaskViewSet), with `students.tests_batch_item_results` (the item shape the retry returns) | `d600629` | **Ran 704, OK** (skipped 4) | `regression_users.txt` |
| The module after the stale-copy test | working tree = the evidence commit | **Ran 12, OK** | `SUMMARY.txt` |

## Mutants (author's)
`mutate.py`; results in `mutants.txt`. Each is restored with a sha256 check. **10 of 10 killed.**

| Mutant | Killed by |
|---|---|
| R1 any failed code retried | the 409 test and retry-failed |
| R2 upload items retried in place | `test_an_upload_item_answers_upload_the_file_again` |
| **R3 the claim unfenced (pk only)** | **survived at first**: the 2-thread race test could not tell the claim from the view's pre-check, since the second request usually loads the item after the first already flipped it. **Killed by the new `test_a_stale_copy_of_an_item_retried_since_is_refused`**, which retries a copy loaded before another retry claimed the item. |
| R4 retry_count not bumped | 4 tests, including the race |
| R5 the code not cleared | the in-place retry test |
| R6 any teacher's session | the 404 test |
| R7 an item outside its session | the 404 test |
| R8 no GRADING_REQUESTED audit | the in-place retry test |
| R9 retry-failed ignores `reason_codes` | `test_only_the_named_codes_are_retried` |
| R10 the trace not refreshed | the in-place retry test (reference == X-Request-ID) |
