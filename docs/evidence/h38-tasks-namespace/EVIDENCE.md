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
    FAILURE with "This course is no longer available to you." Nothing is
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
| Reproduce-first on abeda10 (routes + tasks reverted) | see log | `prefix_abeda10_failing.txt` |
| Changed modules and all repo-wide guards present on beta | see log | `changed_modules.txt` |
| Mutation (M1–M8) | see log | `mutation_log.txt`, `mutation_results.json` |
| ONE owning-app regression: billing | see log | `regression_billing.txt` |
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
