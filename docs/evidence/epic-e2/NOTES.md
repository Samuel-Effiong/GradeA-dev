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

## Runs

(filled in at the end)
