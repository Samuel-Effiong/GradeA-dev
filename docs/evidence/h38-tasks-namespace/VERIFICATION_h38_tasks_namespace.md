# Verification: H-38 for the tasks/ namespace and the grading dispatches @ 0fbac49

**Verifier:** 1a. **Author:** ed. **Date:** 2026-09-30.
**Branch:** `task/h38-tasks-namespace` @ **0fbac49**, off beta `abeda10`. The code is `7ebb046`; the evidence is `cb365f3` and `0fbac49` (`docs/evidence/h38-tasks-namespace/`). The finding is v2's `FINDING_h38_tasks_namespace_v2.md`.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from a detached scratch checkout at 0fbac49 with its own test DB (`test_vf_h38t`). Under rule 15, ed's users + assignments regression (1281 OK) is cited, not repeated.

**Verdict: VERIFIED-WITH-NOTES.** All five of v2's rows (T1–T5) are closed on beta, with nothing over-blocked and nothing leaked. **N1 is substantive:** the shared helper answers "reachable" for a teacher who is not the course's current owner. So a super-admin course reassignment after a removal reopens T1, T2 and grading for the ex-owner. I recommend fixing it before S7b adopts this helper (a small follow-up; I can re-check it quickly). It does not block this fix, which is strictly better than beta and whose precondition is an admin action outside the product. **Whether N1 blocks is the SM's call.**

## Static
- `students/task_access.py`:
  - `course_of` finds the course of a task, a batch session or a submission.
  - `teacher_may_reach(user, work)` defers to `teacher_can_reach_course` **only when `user` is the course's teacher**. Otherwise it returns True, leaving the caller's ownership check in charge (see N1).
  - `ensure_reachable` raises Http404.
- **`TaskViewSet`:** status and cancel raise the same `NotFound` as a missing task. cancel-session and session-results raise the same `Http404` message as `get_object_or_404`. There are only four actions on beta, and all four are covered.
- **Grading:**
  - `grade_engine_async` refuses before `grade_engine`. The task is marked FAILURE, the log carries ids only, and there's no charge.
  - `grade_batch_async` refuses at the top.
  - `auto_grade_due_assignment` skips, and a course with no teacher is skipped too.
- **Other views:** no other view scopes a task or a batch session by owner. The other `requested_by=request.user` sites are creates.
- **Hooks:** `pre-commit run --from-ref abeda10 --to-ref 0fbac49` passes, and so does each of the 3 commits on its own.

## Evidence
My probes are in `h38_tasks_probe_tests_vf1a_h38t_probe.py`. They use billing's `TeacherRemovalBase` (real licence add and remove, real JWT). The harness is `h38_tasks_harness_vf_h38t_run.py`. v2's probe was run unchanged as a second oracle.

| Check | Result |
|---|---|
| **Baseline** @ 0fbac49: my 8 probes + ed's 7 tests + v2's 2-test probe | **16/17 OK**. The 1 is v2's documenting probe: it appends 5 rows (T2 twice) but asserts 4, so it fails on every tree. Its printed rows are all secure: T1–T4 **404**, no student name, task still STARTED; T5 **0 dispatches** |
| **Reproduce-first:** abeda10's `users/views.py` + `assignments/tasks.py` | Every removal probe **fails** (11 failures). v2's rows reproduce the live hole: T1 **200** with the student's name, T2 **200** with the name, T3/T4 **200** and the task CANCELLED, T5 **1 dispatch as the removed teacher**. My controls pass |
| Q1: an unreachable task/session vs a missing one, all four routes | **Byte-identical** 404 bodies. ed's test compares only T2's message |
| Q2: controls, cancel and cancel-session for a member | 200, task CANCELLED. ed's control covers only T1/T2 |
| Q3: the scheduled grading's shape (`[user, submission]`, **no** `processing_task_id`) after removal | Refused. `grade_engine` not called, no `CreditLedger` row, submission ungraded |
| Q4: teacher removed and then **added to School B** by B's admin (real route) | School A's work is still 404 on all four routes, and grading is refused |
| Q5: over-blocking: the teacher's own INDIVIDUAL-session course, after removal | T1/T2 **200**; grading **runs** |
| Q6: all four routes + the refused run + the beat, with **every logger at every level** captured | No student name, no student email, no teacher email. The submission id is present |
| Q7: **a course reassigned** after the removal (super admin, via Django admin) | **T1 200, T2 200, both naming the student; grading RAN as the ex-owner** (N1) |
| **Mutants (mine), 4/4 KILLED**, sha-checked restore | V1: a task's course paths dropped (killed by Q3, Q4 and ed's queued-grading test). V2: session-results' 404 says more than a missing one's (killed **only by Q1**). V3: over-block every course-owning teacher (killed by Q2, Q5 and ed's control). V4: the refused run logs the student's name (killed **only by Q6**) |

## Notes
- **N1 (substantive: the helper's non-owner case).** The problem:
  - `teacher_may_reach` returns True whenever `user` isn't the course's current teacher.
  - After a removal, if a super admin reassigns the school course to a colleague (only possible in Django admin today, but a plausible support request when a teacher leaves), the removed ex-owner can again:
    - read T1/T2, which name the school's students;
    - cancel T3/T4 (by the same path);
    - have queued or scheduled grading run as them, billed to them. Grading **ran** in Q7.

  This diverges from H-38 part 2: `teacher_course_access_q` requires `own & reachable`, so a reassigned course is invisible to the ex-owner on every course route.

  Suggested fix: for a **TEACHER** user, answer with `teacher_can_reach_course` whether or not they own the course (a non-owner teacher is then refused), keeping the current deferral for non-teacher users (admins' own tasks). Add Q7 as the test. My S7b fix uses `reachable_courses(user)` (own & reachable), so it would inherit this gap when it switches to this helper at bundle 5, unless it's fixed first.
- **N2 (consistency).** The refusal text "This course is no longer available to you." tells the reader they lost access. For S7b's run-time check the SM ruled a plain "not found" ("This course wasn't found."). One wording should be picked before bundle 5, where the two lines meet in `grade_engine_async`.
- **N3 (audit, low).** The refusal *returns* instead of raising, so no `GRADING_FAILED` event is emitted. An auditor sees `GRADING_REQUESTED` with no terminal event for the refused run. Worth one event with error_class USER (ids only).
- **N4 (scope, low).** The run-time check covers grading only. Answer extraction and assignment uploads queued before a removal still run and bill. The window is short (request-time checks exist from H-38 part 2), and there is no scheduled form.
- **N5 (for v2).** `tests_vf2_tasks_h38_probe.py` asserts `len(results) == 4` but appends 5 rows.

## Bundle 4 (F6)
The fix is off `abeda10`, the same base as the add_teachers fix. `git merge-tree --write-tree 8de3078 0fbac49` (bundle 4's tip) is **clean**. If the founder picks the fold-in, it needs one strict full re-run and my Gate 1 refresh.

Logs: `runs/h38_tasks_{baseline_0fbac49,prefix_abeda10,mutant_V1..V4}.log`.

---

## Re-check (N1 + N2, narrow) @ **970c010**, 2026-09-30: **VERIFIED**
- **a6d7e64 (N1):** for a **TEACHER** user, `teacher_may_reach` now always answers with `teacher_can_reach_course` (current owner **and** reachable). Non-teachers keep the deferral. The sweep's ALLOWED entry for the old `course.teacher_id != user.id` line is gone.
- **a6d7e64 (N2):** every stored or shown refusal (grading, batch, auto-grade skip) is `COURSE_NOT_FOUND = "This course wasn't found."`, per the SM's ruling. The 404 bodies are unchanged.
- **4e2d870:** my round-1 record, committed verbatim.

The run was in 0b's slot at 6G, from my detached checkout moved to 970c010. My probe's Q7 was turned from a documenting print into assertions, and it gained a never-owner case.

| Check | Result |
|---|---|
| **Baseline** @ 970c010: my 9 probes + ed's module + `classrooms.tests_teacher_access_sweep` | **28 OK** |
| Q7: the ex-owner of a **reassigned** course (after removal) | T1–T4 **404**, no student name, task still STARTED; grading **not run**, no `CreditLedger` row; stored error **"This course wasn't found."** |
| Q7b: a same-school teacher who never owned the course, holding a task on it | T2 **404** |
| Q1–Q6 (round 1) | all still hold: byte-identical 404s, member cancels 200, clocked grading refused uncharged, moved-to-school-B refused, own individual course untouched, no log leak |
| **Mutants (mine), 5/5 KILLED**, sha-checked restore | V1 (a task's course never found), V2 (session-results' 404 wording), V3 (over-block), V4 (the refused run logs the student), and **V5, the round-1 deferral restored**. V5 is killed by my Q7/Q7b, ed's `ReassignedCourseTests` and the sweep's direct-owner scan |
| Hooks | `pre-commit run --from-ref 0fbac49 --to-ref 970c010` passes, and each of the 3 commits passes |
| Bundle 4 | `git merge-tree --write-tree 8de3078 970c010` is **clean** |

N3 (no GRADING_FAILED event on a refusal) and N4 (no run-time check on extraction) stand, for the SM.

Logs: `runs/h38_tasks_r2_{baseline_970c010,mutant_V1..V5}.log`. Probe and harness: `h38_tasks_probe_r2_*`, `h38_tasks_harness_r2_*`.
