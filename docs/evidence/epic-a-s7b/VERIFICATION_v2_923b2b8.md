# Verification: Epic A S7b @ 923b2b8

**Verifier:** Verification Engineer 2 (v2). **Author:** 1a. **Date:** 2026-09-30.
**Branch:** task/epic-a-s7b @ **923b2b8** (on S7a, with 039bbc8 merged as add3b1e). Design: 08a §4.4, F2/F3/F4. `POST tasks/session/{sid}/items/{iid}/retry` and `POST tasks/session/{sid}/retry-failed`.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a scratch worktree detached at 923b2b8, with every repo-wide guard (rule 15 addendum 2).

**Verdict: REJECTED** on one defect (H1, an H-38 access hole). Everything else holds.

## H1 (defect): a teacher removed from a school can still retry, and so re-grade, that school's items
`retry_item` / `retry_failed` authorise through `_own_session`: `BatchUploadSession(id=…, teacher=request.user)`, i.e. **ownership only**. The H-38 rule, that a teacher removed from a school can no longer reach that school's courses (`teacher_course_access_q`, enforced on the grade routes), is **not applied**. The relaunched `grade_engine_async` does no access check either: it loads the user by id and grades.

Probe (`tests_vf2_s7b_probe.py`, on the H-38 fixture: the teacher joins School A through the real licence `add_teachers`, creates a course in the school session, enrols a student through the real route; a FAILED `PROVIDER_FAILURE` grade item is in the teacher's own batch session; the wallet is funded **after** removal so billing's 402 can't mask the check):

| | Status | Effect |
|---|---|---|
| R1 control, teacher still in School A | 202 | same item_id, retry_count 1, `reference == X-Request-ID`, 1 launch |
| **H1, after `remove_teachers`** | **202** | **item → PENDING, retry_count 1, 1 grading task launched** for a School A student's submission, billed to the removed teacher and writing to that student's record |

(A first run showed 402, but that was the credit gate: `remove_teachers` expires the teacher's buckets.)

The H-38 access sweep (`classrooms.tests_teacher_access_sweep`) passes because the retry routes live under `tasks/` and aren't in its route set.

**Required:**
1. Refuse (404, not naming the course) a retry, and skip the item in `retry-failed`, unless the item's course is reachable: `Course.objects.filter(teacher_course_access_q(request.user), pk=item.assignment.course_id).exists()`, or build it into `_own_session` / the item lookup.
2. A test on the H-38 fixture: removed teacher → refused, nothing launched, retry_count unchanged; plus the `retry-failed` variant.
3. A mutant dropping the check.
4. Add both retry routes to the H-38 teacher-access sweep, so the next tasks/ route can't miss it.

## Everything else (holds)
| Check | Result |
|---|---|
| v2 probes + `students.tests_item_retry` + `students.tests_batch_item_results` + every repo-wide guard | 128 run; 2 FAIL: **H1** (above) and `tests_no_wildcard_invalidation` on `audit/bench_volume.py:70`, **inherited** (S7b's base predates ed's bench move 80b33a8; not in S7b's diff) |
| R1 | a successful retry keeps the same `item_id`, `retry_count` 1, a refreshed trace: `reference` = the retry's `X-Request-ID` |
| Static | only FAILED grade items with a catalogue-retryable code are retried; an upload item is always 409 with `params.resolution = "replace_file"`; everything else is 409 NOT_RETRYABLE with nothing launched; the conditional-UPDATE claim (status + code + retry_count) decides races (1a's stale-copy test); GRADING_REQUESTED is emitted after the launch |

**Note (for the fix round):** there is no cap on `retry_count` (a PROVIDER_FAILURE item can be retried indefinitely, each attempt charging only on success). Fine for now; state it.

Logs: `runs/s7b_run1.log`, `runs/s7b_run1b.log`.
