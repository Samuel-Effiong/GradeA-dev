# Verification: Epic A S7b, H-38 fix round (1a). Re-check after v2's REJECTED at 923b2b8 (H1)

- **Branch:** task/epic-a-s7b at **1420b84**:
  - code 0ce44d6;
  - fixes 7fd064d / 09de32f / 2e17531, then c6e54ec (race regression) and 0ce44d6 (A-B-A test);
  - base update e9a41d9 = phase2/epic-a c393d17 (brings in S6d).
- **Verifier:** v2 (independent), 2026-09-30.
- **Verdict:** **VERIFIED**

## Base update e9a41d9 (S7b × S6d)
- `git show --remerge-diff`: the only conflict is assignments/tasks.py (2 hunks):
  - The import list keeps both `CourseNotReachableError` and `RubricMissingError`.
  - `_grading_failure_error_class` takes S6d's reason-code taxonomy (coded failures and the two AI refusals carry their own class). The uncoded `CourseNotReachableError → USER` check comes after it, before the infra/SYSTEM fallback. CourseNotReachableError is a plain Exception (`reason_of` returns None), so the order is correct.
- **Added-line survival** (`vf_merge_survival.py e9a41d9`): every non-blank line added on either side is present in the merge, except 4 docstring lines rewritten by the resolution. No code lost.

## Fix review (static)
- **Request-time retry:** `retry_item` → `is_reachable` → Http404 before `refusal_for`, so an unreachable upload item is 404, never "replace_file". The course is the assignment's course, or the batch session's when the item has no assignment.
- **The claim (c6e54ec):** the state conditions (pk, status, reason_code, retry_count) are on the UPDATE's own row, with reachability as `pk__in` (subquery). The 7fd064d join form compiled every condition into `id IN (SELECT …)`, read from the snapshot, which is how both racing claims won. 1a's compiled SQL before/after in EVIDENCE matches.
- **retry-failed:** reachability is checked before the `reason_codes` filter. An unreachable item is skipped with `reason_code: null`.
- **grade_engine_async:** a run-time `reachable_courses(user)` check before any provider call. It raises `CourseNotReachableError` (uncoded, "This course wasn't found.", class USER).

## What was run
All runs used rule 13 (6G scope) and rule 12 (timeout 1800), in the scratch worktree detached at 1420b84 with DB test_vf2_s1. The assignments+students regression was not repeated (rule 15; 1a's 995 OK stands).

| Run | Result | Log |
|---|---|---|
| v2 probe `tests_vf2_s7b_probe` (H1, H2, H3, T5, R1) + students.tests_item_retry + tests_item_retry_h38 + guards (below) | **131 OK** | runs/s7b_1420b84.log |
| W1: item_retry.py as at e9a41d9 (c6e54ec reverted, the join claim) against tests_item_retry + tests_item_retry_h38 | **killed** by `AClaimWaitingOnTheLockSeesTheRowItFinallyGets` (the deterministic A-B-A test). Restored and hash-checked. | runs/s7b_1420b84_W1.log |

**Guards run (addendum 2, epic set):**
- AutoGrader.tests_no_wildcard_invalidation
- AutoGrader.tests_cache_invalidation_coverage
- AutoGrader.tests_reason_codes
- AutoGrader.tests_migration_rollback_defaults
- audit.tests_route_coverage
- audit.tests_history_guard
- classrooms.tests_teacher_access_sweep
- classrooms.tests_course_roster_scope_sweep

## Probe results (v2's H1 is closed)
- **H1** (a removed teacher retries a school-course item): **404**, nothing launched, item still FAILURE, retry_count 0. It was 202 at 923b2b8.
- **H2** (retry-failed after removal): the item is skipped with `reason_code: None`, retried = [], nothing launched.
- **H3** (a run launched before the removal, executed after it): the provider is **not called**; the item is FAILURE with "This course wasn't found."
- **T5** (the auto-grade beat after removal, run through to grade_engine_async): the provider is not called.
- **R1** (control, before removal): 202, same item id, retry_count 1, `reference` = the request's X-Request-ID.

1a's M1–M7 (in EVIDENCE) cover the request check, the claim's reachability, retry-failed ordering, the no-assignment fallback, the run-time check, the error class, and M7 (state moved into the subquery). W1 adds the regression 1a actually shipped, and shows the A-B-A test catches it deterministically.

## Notes (none blocks)
1. **Reachability in the claim is read from the statement snapshot** (a subquery; Postgres doesn't re-run it on the locked row). A removal that commits while a claim waits on the row lock isn't seen by the claim. The run-time check in grade_engine_async (H3) stops that run before any provider call, so nothing is graded or billed. The claim condition narrows the window; it isn't the last line.
2. **The race test vs the A-B-A test.** Under W1, `TwoRetriesOfOneItemAtOnce` passed in this run, while 1a's first changed-module run caught it as `[202, 202]`. It is timing-dependent. The A-B-A test is the deterministic guard, and should stay.
3. **retry-failed answers 202 with `retried: []`** when every item is skipped. Pre-existing S7b behaviour; mentioned for the frontend.
