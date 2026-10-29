# H-38: the tasks/ namespace and the grading dispatches

Branch `task/h38-tasks-namespace` off beta `abeda10`. Author: ed (Security).
Verifier: 1a. Finding: v2, `GAP-v2-handover/FINDING_h38_tasks_namespace_v2.md`.

## The hole

H-38 made course access go through `teacher_course_access_q`, but background
work is scoped on its *own* owner, and ownership never stops being true:

| # | Entry point | Scoped on | After removal, the removed teacher could |
|---|---|---|---|
| T1 | `GET tasks/session-results/<id>` | `session.teacher` | read the school's students' names and grades |
| T2 | `GET tasks/status/<celery id>` | `task.requested_by` | read the task's result and student name |
| T3 | `POST tasks/cancel/<celery id>` | `task.requested_by` | cancel the school's grading |
| T4 | `POST tasks/cancel-session/<id>` | `session.teacher` | cancel a whole grading batch |
| T5 | `auto_grade_due_assignment` (beat) | `course.teacher` | have the school's students graded, and billed, in their name |

## The fix

- **One rule, one place:** `students/task_access.py`, `teacher_may_reach(user, work)`.
  It finds the course of a task, batch session or submission. When the user
  is that course's own teacher, it answers with `teacher_can_reach_course`.
  In every other case it defers to the caller's existing ownership check,
  which is unchanged. 1a's S7b retry fix will import the same helper.
- **Routes T1–T4:** each `TaskViewSet` action checks the helper after its
  ownership lookup. An unreachable task or session raises the same `NotFound`
  / `Http404`, with the same message, as a missing one. The answer therefore
  cannot confirm that the work exists.
- **Grading:**
  - `auto_grade_due_assignment` skips a course its teacher can no longer
    reach. The log records ids only. Nothing is dispatched and nothing is
    charged.
  - `grade_engine_async` is the chokepoint for every dispatch that runs as
    the course's teacher (grade-all, scheduled grading, the beat, batch items).
    It refuses before grading when the helper says no. The task is marked
    FAILURE with "This course wasn't found." (round 2 wording; see below). Nothing is
    graded and nothing is charged. This also covers work queued *before*
    the removal.
  - `grade_batch_async` refuses at the top as well.
- **Sweep:** `classrooms.tests_teacher_access_sweep` gains
  `TasksNamespaceSweepTests` (AST). Every `@action` on `TaskViewSet` and all
  three grading tasks must call the rule, so the namespace cannot be dropped
  again. The helper's one direct `teacher_id != user.id` line is in ALLOWED
  with its reason.

## Not covered (recorded, not changed)

- `grade_all_submissions` in `assignments/tasks.py` calls `grade_engine`
  directly. Nothing dispatches it (a repo-wide grep finds no `.delay` or
  `.apply_async` of it), so it is dead code on beta. If it is ever
  re-wired, it should go through `grade_engine_async`.

## Tests: `students/tests_h38_tasks_namespace.py` (TeacherRemovalBase)

The fixtures follow v2's probe: a sentinel student name, an auto-grade
assignment that is already due, a submission, a GRADE batch session and a
STARTED grading task.

- Controls: before removal, the owner still reaches T1 and T2, and the beat
  still dispatches.
- After removal:
  - T1–T4 answer 404. Neither the sentinel nor the submission id appears in
    the response, and the task stays STARTED.
  - An unreachable task answers exactly like a missing one (same status and
    message).
  - T5: the beat dispatches nothing and creates no new session.
  - Queued `grade_engine_async` is refused. `grade_engine` is not called, the
    CreditLedger is unchanged, the task is FAILURE and `graded_at` stays None.
  - `grade_batch_async` is refused and dispatches nothing.

## Gates

| Gate | Result | Log |
|---|---|---|
| Reproduce-first on abeda10 (routes + tasks reverted) | 14 FAIL + 2 ERROR of 14 tests, as expected. T2's 200 body carries the sentinel student name; T3 left the task CANCELLED; the beat dispatched. Both ERRORs are `ImportError: COURSE_NOT_REACHABLE`, the new constant. The two controls pass on the prefix. | `prefix_abeda10_failing.txt` |
| Changed modules and all repo-wide guards present on beta | 126 tests OK | `changed_modules.txt` |
| Mutation (M1–M8) | 8/8 killed, 0 survivors, source clean after | `mutation_log.txt`, `mutation_results.json` |
| ONE owning-app regression: `users assignments` in one run (0b's call: the changed code lives there; billing's H-38 modules are in the changed set) | 1281 tests OK (17 skipped), 336 s | `regression_users_assignments.txt` (trimmed; full log in GAP-evidence-logs) |
| `pre-commit run mypy --all-files`, `makemigrations --check` | pass (at commit) | n/a |

The changed set follows rule 15 addendum 2. It includes every repo-wide guard
present on beta:
- `AutoGrader.tests_no_wildcard_invalidation`
- `AutoGrader.tests_cache_invalidation_coverage`
- `AutoGrader.tests_migration_rollback_defaults`
- `classrooms.tests_teacher_access_sweep`
- `classrooms.tests_course_roster_scope_sweep`

`tests_reason_codes` does not exist on beta.

### Mutants

| Mutant | Change |
|---|---|
| M1 | the helper always answers True |
| M2–M5 | the status, cancel, cancel-session and session-results guards removed |
| M6 | the `grade_engine_async` chokepoint off |
| M7 | the auto-grade beat guard off |
| M8 | the `grade_batch_async` guard off |

## Round 2: 1a's N1 and the SM's N2 ruling

1a verified round 1 as VERIFIED-WITH-NOTES
(`VERIFICATION_h38_tasks_namespace.md`). The SM asked for N1 to be fixed
before any fold-in, and ruled on the wording (N2).

- **N1:** `teacher_may_reach` deferred to the caller's ownership check
  whenever the user was not the course's current teacher. After a removal,
  if a super admin reassigned the course to a colleague, the ex-owner
  regained T1/T2 (the student named) and T3/T4, and queued grading ran and
  billed as them (1a's Q7).
  - **Fix:** for a TEACHER user, the helper now always answers with
    `teacher_can_reach_course`, which requires the current owner AND H-38
    reachability. This matches `teacher_course_access_q` (own & reachable)
    on every course route.
  - The deferral is kept only for non-teacher users: an admin's own task
    is still judged by its ownership check.
  - The helper's `course.teacher_id != user.id` line is gone, so its
    sweep ALLOWED entry is removed.
- **N2:** the SM's ruling:
  - A 404 body stays byte-identical to a nonexistent resource's, as before.
  - Every stored or shown refusal (the task record, the grading result,
    `grade_batch_async`'s result and the auto-grade skip) now reads
    `COURSE_NOT_FOUND = "This course wasn't found."`, S7b's wording. It
    replaces "This course is no longer available to you." and the
    auto-grade skip's own sentence.
- **Tests added:**
  - 1a's Q1: all four routes' 404 is byte-identical to a missing id's.
    This replaces round 1's T2-only message check.
  - 1a's Q6: every logger at every level; no student name or email.
  - 1a's Q7 as `ReassignedCourseTests`:
    - the ex-owner gets 404 on T1–T4, and the task stays STARTED;
    - queued grading does not run and nothing is charged;
    - control: the beat now grades as the new owner.
  - `AdminsOwnTasksTests`: a super admin's own task on the school course
    still answers 200 after the teacher's removal.
  - The round-1 tests now sit on a `TasksFixture` base with no tests of
    its own.
- **Mutants added:**
  - M9: the non-owner deferral restored (round 1's helper).
  - M10: the rule applied to non-teachers.
  - M11: session-results' 404 says more than a missing id's (1a's V2).

### Round 2 gates

The SM's scope is the touched modules plus the guards, the prefix and
mutation. There is no regression (round 1's users + assignments regression
stands).

| Gate | Result | Log |
|---|---|---|
| Changed modules and all repo-wide guards on beta | 131 tests OK | `r2_changed_modules.txt` |
| Reproduce-first: 0fbac49's helper, the rest as now | 6 FAIL of 12, exactly N1: ReassignedCourseTests (T1–T4 answer non-404, and grading ran as the ex-owner). Every other test, including the admin and the new-owner controls, passes on round 1's helper. | `r2_prefix_0fbac49_failing.txt` |
| Mutation M1–M11 | 11/11 killed, 0 survivors, source clean after. M9 is killed by the Q7 tests and by the sweep (the old helper line is unlisted again); M10 by the admin control; M11 by Q1 | `r2_mutation_log.txt`, `r2_mutation_results.json` |
