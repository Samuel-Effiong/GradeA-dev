# Bundle 4 → phase2/epic-a merge-down: ed's part of the resolution

Merge: beta 67a0681 into phase2/epic-a ba165b4, in 0b's worktree `Grade-Automator-Plus-epic-a-merge-down-b4` (`task/epic-a-merge-down-b4`). The files here use repo paths, ready for 0b to apply.

## users/views.py (conflicted: 2 import hunks + the SM's semantic rule)

- **Imports:** the union of both sides. From the epic: `F, Q`, `item_retry`, `item_results`, `BackgroundProcessingTask`. From beta: `Http404` and `students.task_access.teacher_may_reach`.
- **What F6.2 returns** (beta, confirmed by 1a at 67a0681): `session_results` and `cancel_session` do `get_object_or_404(BatchUploadSession, id=…, teacher=user)`, then `if not teacher_may_reach(request.user, session): raise Http404("No BatchUploadSession matches the given query.")`. That is byte-for-byte the same message as a missing session.
- **SM ruling:** the retry routes follow the same session-level rule. `retry_item` and `retry_failed` get those same two lines right after `_own_session`.
  - A removed teacher's retry and retry-failed now answer that same 404. retry-failed used to answer 202 with an empty `retried`.
  - The lines sit in the actions themselves, not in the `_own_session` helper, so beta's strict ast sweep (`TasksNamespaceSweepTests`: every TaskViewSet action's own source names `teacher_may_reach`) passes with no exemption.
- **Defence in depth, kept (1a's list):** S7b's per-item checks in `students/item_retry.py`:
  - the item's own course, falling back to the batch session's course;
  - the in-claim `pk__in=reachable_items_q` recheck for a removal that lands mid-request;
  - retry_failed's skip-without-code.

## classrooms/tests_teacher_access_sweep.py (conflicted)

- Both classes are kept: the epic's `TasksNamespaceRoutesFollowTheRule` and beta's `TasksNamespaceSweepTests` (+ `_functions`).
- `test_retry_failed_retries_nothing_for_a_removed_teacher` (a 202 with an empty result) becomes `test_retry_failed_is_not_found_for_a_removed_teacher`: 404, with the same message as a random-uuid session's 404, and nothing launched.

## students/tests_item_retry_h38.py (auto-merged; edited for the rule)

The route tests now stop at the session gate. So the item-level checks are pinned at the SERVICE, to stop the gate masking them (1a's masking risk):
- `test_the_service_refuses_the_item_by_its_own_course` (new): `item_retry.retry_item(grade_item, removed_teacher)` raises Http404, and nothing is launched.
- `test_an_item_with_no_assignment_is_judged_by_its_batchs_course`: plus `item_retry.retry_item(orphan, removed_teacher)` raises Http404, which covers the batch-course fallback.
- `test_retry_failed_skips_every_item_without_saying_why` is split:
  - the route: 404 for both bodies;
  - `test_the_service_skips_every_item_without_saying_why`: `item_retry.retry_failed(...)` skips every item with `reason_code: None`, and nothing is launched.
- `test_a_removal_between_the_check_and_the_claim_still_refuses`: it now lets BOTH request-time checks pass (`users.views.teacher_may_reach` and `item_retry.is_reachable`, each patched with a real function), so the claim's recheck is still what refuses (409).

## After gate step 1 at bfcf6e1 went red (SM ruling, 2026-10-01): two follow-up commits

Step 1 ran 304 tests: failures=3, errors=1, all in beta's `students.tests_h38_tasks_namespace`, and all 11 guards passed. `assignments/tasks.py` auto-merged with no conflict. The cause is two pieces of Epic A behaviour that beta's tests were never written against. This was not a resolution error.

1. **`grade_engine_async` intentionally diverges: beta keeps H-38's soft return, epic keeps the S7b coded refusal.** Future merge-downs keep the epic side there.
   - The merge put both run-time checks in a row. S7b's (7fd064d, VERIFIED: `reachable_courses` → `raise CourseNotReachableError()`) came first, so beta's (`teacher_may_reach` → a warning, `mark_processing_task_failure`, `return {"message": COURSE_NOT_FOUND}`) could never run.
   - The epic now has one check, S7b's, and logs beta's ids-only "Grading refused (H-38): submission %s, user %s …" warning before raising. Beta's dead block is dropped.
   - Beta's `test_queued_grading_is_refused_and_never_charged_after_removal` is adapted on the epic side. It asserts a `CourseNotReachableError` result whose text is COURSE_NOT_FOUND, `task.error == COURSE_NOT_FOUND`, FAILURE, grade_engine not called, the ledger unchanged, and nothing graded.
   - Consumer grep, done first at the SM's request: nothing in production reads `grade_engine_async`'s return value. There is no synchronous, chained or `.apply` caller. The only production `AsyncResult` reads `.state` (task_tracking), task_status's AsyncResult fallback was removed earlier, and status comes from the tracking row. COURSE_NOT_FOUND's other production uses (`grade_batch_async`, `auto_grade_due_assignment`) are unchanged.
2. **The fixture's question gets a one-level marking guide**, so S6d's rubric gate (epic only) lets the auto-grade beat dispatch. The two `delay` controls had seen 0 calls. This is test-only and epic-only (SM amendment: no beta hunk, since the module diverges anyway).

Re-gate on the new tip: step 1 + mutants (6G), then ONE combined students/classrooms/users/assignments regression (12G); assignments is added because production changed there. v2's remerge-diff review covers this resolution explicitly; I authored it, so I don't verify it.

## Gates on 0b's merged tip (mine, when 0b gives the sha)

- My modules + ALL guards + ONE students/classrooms/users regression.
- **Plus S7b's H-38 mutants M1–M7 re-run on the merged tree** (`docs/evidence/epic-a-s7b/h38/run_gates.py.txt`, incl. the A-B-A M7). Each must still be killed, now by the service-level or race tests. A survivor gets its own service-level test.

## Noted, not changed

- `item_retry` raises a bare `Http404()` ("Not found."), while the session gate says "No BatchUploadSession matches the given query.". The difference shows only on the race path (a removal mid-request). Harmless; I'm leaving it for a later tidy-up.
- `billing/services.py` is d5's (a comment), not in this folder.
