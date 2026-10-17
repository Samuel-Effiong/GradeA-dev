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

---

## Fix round: H-38 (v2's H1 on 923b2b8) @ **0ce44d6**
v2 rejected 923b2b8 on H1: the retry routes authorised by batch-session ownership only, so a teacher removed from a school could still retry (and re-grade, billed to them) that school's items. v2's record is committed verbatim at `8ec3410` (`VERIFICATION_v2_923b2b8.md`).

**Commits:**
- `7fd064d`: the fix.
- `09de32f`: a test for an item with no assignment.
- `2e17531`: the SM ruling, a plain "This course wasn't found."
- `e9a41d9`: 0b's base update onto epic `c393d17` (S6d), with my resolved `assignments/tasks.py`.
- `c6e54ec`: the race-regression fix (below).
- `0ce44d6`: the A-B-A claim test (below).

### What changed
- **retry:** **404** unless the item's course can be reached by the requesting teacher now (`reachable_courses`). The course is the assignment's course, or the batch session's when the item has no assignment. The check runs **before** `refusal_for`, so nothing about an unreachable item is said: an upload item there is 404, not "re-upload". The claim carries the same condition.
- **retry-failed:** an unreachable item is skipped with `reason_code: null`. This is checked before the `reason_codes` filter, so its code is never reported.
- **`grade_engine_async`:** re-checks reachability when the run starts. A lost-access run fails with `CourseNotReachableError`: uncoded (SM ruling), "This course wasn't found.", error class USER. It happens **before any provider call**, which covers retries, scheduled gradings and queued items that start after a removal. After the base update it sits after S6d's `reason_of()` classification.
- **Sweep:** `classrooms.tests_teacher_access_sweep` is a source scanner, not a route list. `TasksNamespaceRoutesFollowTheRule` adds both retry routes as route-level cases on billing's H-38 fixture (real `add_teachers` / `remove_teachers`, wallet funded **after** the removal).
- **Stated (v2's note):** there is no cap on `retry_count`. A PROVIDER_FAILURE item can be retried indefinitely, and each attempt charges only on success.

### A regression the gates caught, and its fix
- **The regression:** 7fd064d put the reachability **join** into the claim's queryset. With a join, Django compiles the UPDATE with every condition inside an `id IN (SELECT …)` read from the statement's snapshot. Postgres does not re-check that on the row version it finally locks, so two racing retries both won. The first changed-module run on e9a41d9 caught it (`TwoRetriesOfOneItemAtOnce`: `[202, 202]`; `h38/1_…RACE_CAUGHT.log`).
- **The fix (`c6e54ec`):** reachability is a `pk__in` subquery beside plain column conditions on the updated row. The compiled SQL (checked with Django's update compiler, subquery elided):

```
7fd064d (both claims won):
  UPDATE t SET … WHERE t."id" IN (SELECT … reachability …)
c6e54ec (fixed):
  UPDATE t SET … WHERE (t."id" = %s AND t."id" IN (SELECT … reachability …) AND t."reason_code" = %s AND t."retry_count" = %s AND t."status" = %s)
```

- **The A-B-A test (`0ce44d6`):** 0b's mutant **M7** (status and retry_count moved into the subquery, with reason_code left outside) **survived** the race test. The claim clears `reason_code`, and `reason_code` on the outer row alone serialises two claims. `AClaimWaitingOnTheLockSeesTheRowItFinallyGets` pins the case M7 gets wrong:
  - While retry B waits on the row lock (observed in `pg_stat_activity`), the row returns to the **same** code with a new `retry_count`, i.e. it was claimed and failed again.
  - B must lose. Under M7 it claimed the row a second time (`h38/5_aba_test_under_M7.log`).

### Runs (rule 15 + addendum 2; each in 0b's slot, `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout -k 60 1800`, own test DB)
| Step | Tree | Result |
|---|---|---|
| Reproduce-first: new tests on the **pre-fix** tree (`item_retry.py` as at 923b2b8; `tasks.py` without the run-time check and its error-class branch) | c6e54ec | **15 run, 9 failures + 1 error**: every H-38 case, including both sweep routes. The error is the claim test patching `is_reachable`, which doesn't exist pre-fix. The two controls pass |
| Changed modules + **all** repo-wide guards: `students.tests_item_retry_h38`, `students.tests_item_retry`, `students.tests_batch_item_results`, `AutoGrader.tests_reason_codes`, `AutoGrader.tests_codederror_serialization`, `billing.tests.test_h38_part2_removed_teacher_routes`, `billing.tests.test_h38_teacher_removal`, the 8 guards | c6e54ec | **238 OK** |
| The A-B-A test alone | 0ce44d6 | OK; **fails under M7** |
| Mutants (below) | 0ce44d6 | **7/7 KILLED** |
| ONE regression: `assignments` + `students` (`grade_engine_async` is shared) | 0ce44d6 | **995 OK** (14 skipped). It includes the A-B-A test |

### Mutants (author's; `h38/run_gates.py.txt`, sha-checked restore)
| Mutant | Killed by |
|---|---|
| M1 no request-time check | the 404 tests (the claim then answers 409, not 404) |
| M2 no reachability in the claim | `test_a_removal_between_the_check_and_the_claim_still_refuses` |
| M3 retry-failed filters by code before reachability | `test_retry_failed_skips_every_item_without_saying_why` (with `reason_codes`) |
| M4 no assignment-less fallback | `test_an_item_with_no_assignment_is_judged_by_its_batchs_course` |
| M5 no run-time check | `test_a_run_for_a_removed_teacher_fails_before_grading` |
| M6 lost access classed SYSTEM | the same test's `error_class` assertion |
| M7 state conditions moved into the subquery | `AClaimWaitingOnTheLockSeesTheRowItFinallyGets` |

Logs: `h38/` (trimmed to test results and summaries; full logs in `GAP-evidence-logs/epic-a-s7b-h38/`).
