# E2 build notes: grade categories, excused and missing work, assignment copy

Branch `task/epic-e2`, rebased on staging 8c1aa325b (SM -44, 2026-11-18).
Plan: STEP0_PLAN.md (3d93150f6). Owner decisions Q1-Q9 and wording W1-W13,
A1-A4: OWNER_SHEET.md, all approved (owner, 2026-11-18).

## Decisions made during the build (SM rulings, 2026-11-18)

1. **In-category formula (plan correction).** A category grade is
   Σ(relative_weight × score) / Σ(relative_weight × points). The plan's
   Σ(rw × %)/Σ rw would have been the plain mean that Q1 removes. With every
   relative weight 1 it is today's points-weighted formula.
2. **D1.** The plain-mean figures (P1, P2, P4, P6, P7, P8 and M1-M6, M8)
   become points-weighted Σscore/Σpoints over the same rows, behind
   GRADE_CATEGORIES_ENABLED. M7 (a student's mean raw score per course) is
   a mean of scores, not of percentages, so it is unchanged.
3. **Missing work** (owner sheet wording): published, a due date that is
   set and past, no submission row, not excused. It counts as 0 out of
   `total_points` only when `Course.missing_counts_as_zero` is on, in the
   staff and the released grade alike.
   **Default on (owner 2026-11-19, option 1):** every course counts it,
   new and existing (classrooms 0019 adds the field with default and
   db_default True; 0019 had not been applied on any database, the local
   dev DB stopping at 0018). A teacher can switch it off per course; W13
   shows the line that matches the course's setting.
4. **D2 exception: the missing-work task.** In a course that counts missing
   work, the stored grade changes when a due date passes, with nothing
   saved at that moment. A one-off clocked task (`classrooms.tasks
   .recalculate_missing_work`) recomputes the course then. It exists only
   while the flag is on, the assignment is published with a due date
   ahead, and the course counts missing work. It is re-timed or deleted
   with each change (as reminders are), idempotent, runs under the course
   lock, writes audit entries only for grades that moved, and spends no
   credits. It is a clocked PeriodicTask, not a beat schedule entry, so the
   beat-lock pin does not move.
   An assignment created already published with its due date past (no
   task will fire) recomputes the course at once, under the same lock
   (SM 2026-11-19).
   **Go-live:** stored grades are only rewritten by events, and the task is
   made only for due dates still ahead while the flag is on. So right after
   GRADE_CATEGORIES_ENABLED goes on, run `manage.py recalculate_missing_work`
   (dry run) and then `--apply`: for every course that counts missing work
   it makes the tasks for due dates still ahead and recomputes the course
   under its lock (GRADE_CHANGE audit only for grades that move, source
   `missing_work_go_live`, no credits). A second run changes nothing. A
   student's released grade is computed on read, so it is right from the
   moment the flag is on.
5. **W6** applies to every door that makes an assignment: JSON create,
   create-async, PATCH (clearing or moving), both file uploads (a multipart
   `category`, checked before any charge and carried through the task and
   its retry record to the saved draft) and the AI draft save. Only a copy
   with no category-name match lands uncategorized (X-6); W5 warns.
6. **`CourseCategory.course` stays nullable** in the database (H-56: no
   NOT NULL column without a database default). classrooms 0019 deletes
   every pre-E2 row (all had no course; Q7), and code requires a course.
7. **No Hypothesis.** The property tests use seeded random corpora; no new
   dependency.
8. **Cross-course grade** (teacher's my-students list): each course's row
   rules and relative weights apply; category weights do not combine
   across courses.

## Choices inside the plan

- Recalculation audit: one GRADE_CHANGE ("Grade changed", existing label)
  per stored final grade that moved, before/after `final_grade`, target the
  enrollment, metadata `source` names the trigger. A1-A4 record the trigger
  itself (category ids and weights before/after; never names).
- ExcusedWork is erased with the student but not exported: an export file
  name is owner-approved wording. **Owner question for later:** add it to
  the export (needs a file name)?
- Copy failures: per-item reason codes `ASSIGNMENT_NOT_AVAILABLE` (W8) and
  `COURSE_NOT_REACHABLE` (W9, the existing code); a new ReasonCode would
  need a new label and remediation text.
- Migrations: classrooms 0019 (acknowledged as an expand step: the two
  constraints are added on an empty table, after the Q7 delete),
  assignments 0042 (check added NOT VALID) and 0043 (VALIDATE), students
  0044 (new table).
- **H-6:** the unrouted, broken `CourseCategoryViewSet` and its serializer
  are deleted, with their roster-sweep allow-list entry and audit label.
- Audit target label "Category template" (the words of approved A2; SM
  accepted). Excuse events target the assignment (existing label).

## Plants (one per run, each restored from a saved copy, sha256 checked)

| # | Plant | Predicted kill | Result |
|---|---|---|---|
| P1 | `course_grade` runs the E2 policy query with the flag off | E1a query budget | Killed by `tests_e1_hot_path` LockCostTest (an extra statement inside the enrollment lock). Prediction incomplete: the budget test stayed green |
| P2 | excused work counts as 0 instead of being left out | maths excuse tests | Killed: `test_excuse_and_its_removal_recompute_and_are_audited` (the cross-course excuse test drops excused rows on its own path) |
| P3 | copy accepts any existing destination course | copy tenancy tests | Killed: single 404 test and bulk W9 test |

## Known failure, not E2

`assignments.tests_schema_extension` (2 tests) fails whenever
`audit.tests_h293_error_class_filter` runs first in the same process: the
latter builds the OpenAPI schema, which turns the extension's
`target_class` string into a class. Reproduced on clean staging 8c1aa325b
with those two labels alone (Ran 16, failures=1, errors=1). Not fixed in E2
(SM).

## Runs

E2 + E1a suites: Ran 217 OK. Near run (flags unset, --parallel 1):
classrooms students dashboard assignments tags billing users Ran 7766,
failures=2 (the two known local flag tests), skipped=29; guards Ran 1163,
failures=1 (`coursecategorys` left in the cache-coverage mixin list after
H-6; entry removed, guard rerun in the last commit's message).
