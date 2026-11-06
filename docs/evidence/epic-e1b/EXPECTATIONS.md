# Epic E1b: expectations (written before any change)

Plan: docs/phase2/architecture/09_epic_e1_implementation_plan.md
(task/plan-e1-c1 7c7e5c028), §2 "E1b", §0.4, §0.5, §3.4, §5.1, §5.2 M9-M12.
Branch `task/epic-e1b`, written on top of task/epic-e1a 14d910785, to be
**rebased onto the new staging (refresh 19) before any red run**. No code
changes yet. No push.

## The three changes

1. **Re-apply H-5 by hand** (§0.4). The old fix is 1c0742f8a on
   task/h5-full-clean-hot-path (baseline tests 41b281ff3, verified
   dd9d875f). Not merged: that branch is 2,500+ commits behind and every
   commit on it carries a Co-Authored-By trailer. The change re-applied:
   `StudentCourse.save()` validates only the written fields when every field
   in `update_fields` is in `{final_grade, ai_summary,
   ai_summary_generated_at}`; any other save, including a mixed field set,
   still runs `full_clean()`.
2. **The grade-field skip** (§3.4). The post_save receiver returns early
   when `update_fields` is given and names no field the grade reads.
3. **Correct the comment** above `_recalculate_final_grade`: Grade all
   grades one assignment, one submission per student, so a run recalculates
   each student once; the lock matters only when runs on different
   assignments of one course overlap (§0.5).

## Premises re-checked on current code (2f00c078 + E1a)

| Premise | Checked | Holds |
|---|---|---|
| `StudentCourse.clean()` reads none of the hot-path fields | classrooms/models.py `clean()` reads student, course, pk only | yes |
| The only constraint is unique (student, course) | `Meta.constraints` | yes |
| Real hot-path callers | classrooms/signals.py `_recalculate_final_grade` (`["final_grade"]`); classrooms/tasks.py:77 (`["ai_summary", "ai_summary_generated_at"]`) | yes |
| The name-clash wording changed since the old branch (H-296) | the old tests match "already enrolled", still in the new text | tests still apply |
| `enroll_student_by_email` moved | now classrooms/services/enrollment.py, still exported by `classrooms.services` | import unchanged |

## Findings that change plan 09 (for the SM)

1. **Publishing never reaches the signal.** Both the single publish
   (students/views.py) and Publish All (assignments/views.py) write
   `is_published` through `history.record_bulk`, a queryset UPDATE, which
   fires no post_save. So Publish All already takes no lock and runs no
   recalculation today. Plan §6 Gate 6's "Publish All should take no lock
   at all after the skip" is true before E1b too; it is not something the
   skip delivers. The publish-only test below still pins the skip for any
   future publish path that saves the instance.
2. **What the skip actually saves**, the submission saves that name no grade
   field: scheduling (students/views.py:1068, assignments/tasks.py:673:
   `scheduled_grading_at`, `grading_task_name`), the label alone
   (assignments/tasks.py:975, :1255: `formatted_grade`) and a regrade held
   behind a released grade (students/services.py:480: `HELD_SAVE_FIELDS`).
   The grading engine (`GRADING_RESULT_FIELDS`, includes score) and the
   teacher's score edit (includes score) still recalculate.
3. **Column names in `update_fields`.** Django accepts
   `update_fields=["assignment_id"]` and passes it to the receiver as
   written. Plan §3.4's `GRADE_INPUT_FIELDS` lists `assignment` and
   `student` only, so `["assignment_id"]` would be skipped wrongly. The skip
   must also treat `assignment_id` and `student_id` as grade inputs; a test
   pins it.
4. Unchanged and still for E2 (§3.4): an assignment's `total_points` change
   recalculates nothing.

## The tests (written, not yet run: first run is the red run after rebase)

| File | Test | Expected on the base |
|---|---|---|
| classrooms/tests_h5_full_clean_hot_path.py | the old battery: 8 name-rule entry points (create, plain save, invite service, reactivate, status update, mixed fields, out-of-range grade) | green, except `test_a_final_grade_write_skips_the_name_lookup`: **red** |
| (same) | HotPathCostTests: save query count and lock-hold timing (measurement; threads now through `run_concurrently`) | green, prints H5_MEASURE |
| classrooms/tests_e1_hot_path.py | publish-only, scheduling, formatted-grade and held-regrade saves do not recalculate | **red** (4) |
| (same) | each grade input (`score`, `max_points`, `graded_at`, `assignment`, `student`) recalculates; also by column name (`assignment_id`, `student_id`); mixed with other fields; the grading engine's save; a save without `update_fields`; a delete; the grade follows a score change | green, stays green |
| (same) | idempotence: second recalculation returns None and issues no UPDATE | green, stays green |
| (same) | lock cost: a changed grade issues only SELECT FOR UPDATE, the aggregate and the UPDATE (at most 3 statements, nothing on users_customuser) | **red** |
| (same) | convergence: 8 barrier-released threads, real Postgres, each grading one student's submission on a different assignment of one course; stored grade equals `course_grade`; 100 rounds | green, stays green |

Also re-run at every commit: the E1a gate (parity, snapshots, guard, query
budget, parity report). The query budget can only go down.

## Mutation plan (§5.2 plus one for finding 3)

| Id | Mutation | Must fail |
|---|---|---|
| M9 | H-5 hot-path set gains `enrollment_status` | test_status_update_keeps_the_conflict_check |
| M9b | subset check becomes intersection (old mutant b) | test_mixed_update_fields_keep_the_conflict_check |
| M9c | written fields not validated (old mutant c) | test_an_out_of_range_final_grade_is_still_rejected |
| M9d | else branch skips full_clean (old mutant d) | the entry-point tests |
| M10 | `score` dropped from the grade inputs | test_each_grade_input_field_recalculates |
| M11 | skip applied when `update_fields` is None | test_a_save_without_update_fields_recalculates (+ existing final-grade tests) |
| M12 | skip also applied in post_delete | test_a_delete_recalculates |
| X5 | column names (`assignment_id`, `student_id`) not treated as grade inputs | test_a_grade_input_field_named_by_its_column_recalculates |

Each planted, run, logged, and restored from a saved copy with a sha256
check.

## Measurements for the evidence (Gate 6)

Before and after, on the same machine: H5_MEASURE (save queries, lock-hold
p50/p95 under 8 workers), E1B_MEASURE (statements inside the lock), and a
Grade all over 30 submissions: queries and total lock time.
