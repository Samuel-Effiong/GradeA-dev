# Epic E1a: every grade-maths site on staging 2f00c078

Found by reading and by the guard scanner's first run
(`classrooms/tests_e1_grade_guard.py`, rules G1-G6). Line numbers are on
2f00c078. "Frozen by" is the test that turns red when that site's number is
changed on the base code (`coverage-probe-base.txt`: one planted edit per site,
restored by sha256).

Classes: **moved** = goes through `classrooms.grade_service` in E1a;
**allow-listed** = stays where it is, listed in the guard's `ALLOWED` with its
reason; **not grade maths** = the scanner does not match it, listed here so
nobody re-surveys it; **dead** = computed but never read.

## The weighted figure: rows, weight and formula

| Id | Site (2f00c078) | Figure | Class | Frozen by |
|---|---|---|---|---|
| R1 | classrooms/signals.py:186 `compute_final_grade` (Coalesce :211) | stored `StudentCourse.final_grade` | moved (`course_grade`, `graded_rows`) | parity live R1 test |
| R2 | classrooms/final_grade.py:45 `released_final_grade` (via classrooms/serializers.py:441) | final grade a student may see | moved (`released_course_grade`) | snapshot student_enrollments |
| R3 | students/serializers.py:984 `_cross_course_grade` (ternary :1004-1008) | one student across a teacher's courses | moved (`cross_course_grade`) | snapshot teacher_my_students |
| R4 | dashboard/views.py:3631-3644 `TeacherAdminDashboardView.assignments` | one assignment's average | moved (`assignment_grade`) | snapshot teacher_assignment_* |
| R5 | dashboard/services.py:1135 `TeacherAIContextService.build` | risk inputs (weight annotation) | moved (`risk_rows`) | snapshot svc_teacher_ai_context |
| R6 | dashboard/services.py:1518 `SchoolAdminWeeklySummaryService._at_risk_students` | risk inputs (weight annotation) | moved (`risk_rows`) | snapshot school_summary, svc_school_weekly |
| W1 | dashboard/views.py:3324-3328 `TeacherAdminDashboardView._at_risk_students` | risk row weight | moved (`risk_score_row`) | snapshot teacher_overview, teacher_courses_* |
| W2 | dashboard/views.py:3838-3842 `TeacherAdminDashboardView.students` | risk row weight | moved (`risk_score_row`) | snapshot teacher_students_* |
| W3 | dashboard/views.py:4074-4078 `TeacherAdminDashboardView.all_students.build_row` | risk row weight | moved (`risk_score_row`) | snapshot teacher_all_students |
| W4 | dashboard/services.py:392-396 `WeeklyCourseSummaryService._build_student_record` | risk row weight | moved (`risk_score_row`) | snapshot svc_course_weekly_* |
| W5 | students/serializers.py:1093-1097 `StudentListSerializer._risk_result` | risk row weight | moved (`risk_score_row`) | snapshot teacher_my_students |
| -- | dashboard/risk.py:71 `StudentRiskEvaluator.evaluate` | the formula over W1-W5/R5/R6 rows | moved (`points_weighted`) | parity live risk test |
| -- | classrooms/final_grade.py:19 `final_grade_from` | the formula | moved (`points_weighted`); module deleted | parity |

## The plain mean of percentages (D1: kept until E2)

| Id | Site (2f00c078) | Figure | Class | Frozen by |
|---|---|---|---|---|
| P1 | dashboard/views.py:2539 `SchoolAdminDashboardView.unit_performance` | average score per unit | moved (`mean_percentage`) | snapshot school_unit_performance |
| P2 | dashboard/views.py:3142 `TeacherAdminDashboardView.overview` | average grade per course | moved (`mean_percentage`) | snapshot teacher_overview |
| P3 | dashboard/views.py:3270 `TeacherAdminDashboardView._at_risk_students` `avg_grade_val` | **none: never read** | **dead** (see note 1) | nothing can be: not in any output |
| P4 | dashboard/views.py:3439, 3443, 3448 `TeacherAdminDashboardView.courses` | average score by type, by topic, lowest assignment | moved (`mean_percentage`) | snapshot teacher_courses_* |
| P5 | dashboard/views.py:3777 `TeacherAdminDashboardView.students` `avg_grade_val` | **none: never read** | **dead** (see note 1) | nothing can be: not in any output |
| P6 | dashboard/views.py:4331 `StudentAdminDashboardView.summary` | a student's own average | moved (`mean_percentage`) | snapshot student_summary_* |
| P7 | dashboard/services.py:1103 `TeacherAIContextService.build` | average score per assignment | moved (`mean_percentage`) | snapshot svc_teacher_ai_context |
| P8 | dashboard/rigor.py:270 `_submission_rollup` | average percentage, rigor | moved (`mean_percentage`) | snapshots school_teachers, school_teacher_detail, svc_school_weekly, svc_teacher_stats_rigor |
| M1 | dashboard/services.py:241 `StudentWeeklySummaryService._average_score` | weekly student email average | moved (`mean_of_values`) | snapshot svc_student_weekly_* |
| M2 | dashboard/services.py:483 `WeeklyCourseSummaryService._build_overall_metrics` | course average | moved (`mean_of_values`) | snapshot svc_course_weekly_* |
| M3 | dashboard/services.py:513 (same) | recent-window average | moved (`mean_of_values`) | snapshot svc_course_weekly_* |
| M4 | dashboard/services.py:518 (same) | previous-window average | moved (`mean_of_values`) | snapshot svc_course_weekly_algebra |
| M5 | dashboard/services.py:1245 `TeacherAIContextService._assemble` | course average score | moved (`mean_of_values`) | snapshot svc_teacher_ai_context |
| M6 | dashboard/views.py:4578-4580 `StudentAdminDashboardView.overview` `avg_pct` | per-course percentage, UNROUNDED | moved (`mean_of_values(ndigits=None)`) | snapshot student_overview_* |
| M7 | dashboard/views.py:4581-4583 (same) `avg_score` | per-course score, UNROUNDED, divides by every row | moved (`mean_of_values(ndigits=None, count=...)`) | snapshot student_overview_* |
| M8 | dashboard/views.py:4617-4619 (same) `overall_percentage` | mean of course percentages | moved (`mean_of_values`) | snapshot student_overview_* |

## Means of the stored final grade

| Id | Site (2f00c078) | Class | Frozen by |
|---|---|---|---|
| F1 | dashboard/views.py:856 `SuperAdminDashboardView.schools` | moved (`mean_of_final_grades`) | snapshot super_schools |
| F2 | dashboard/views.py:960 `SuperAdminDashboardView.teachers` | moved | snapshot super_teachers |
| F3 | dashboard/views.py:1046 `SuperAdminDashboardView.students` | moved | snapshot super_students |
| F4 | dashboard/views.py:2356 `SchoolAdminDashboardView.course_peformance` (`enrollments__final_grade`, filtered) | moved | snapshot school_course_performance |
| F5 | dashboard/views.py:2636 `SchoolAdminDashboardView.students` | moved | snapshot school_students |
| F6 | dashboard/views.py:2907 `SchoolAdminDashboardView.course_overview_chart` (`enrollments__final_grade`, filtered) | moved | snapshot school_course_overview_chart |

## Allow-listed (the guard matches them; they stay)

| Site (2f00c078) | Rule | Reason |
|---|---|---|
| ai_processor/services.py:5970 `generate_student_summary` | G5 | **Lane 1, reclassify when the Fixes Worker's B1a lands.** Plain mean (round 1) written into AI prompt text, not a shown figure |
| ai_processor/services.py:3363 `_finalize_grading_result` | G4 | Lane 1. One submission's own percentage at grading time: the input, not an aggregate |
| students/views.py:1248 `update_grade` | G4 | One submission's own percentage when a teacher edits its score |
| ai_processor/management/commands/grading_eval.py:61 `_score_band` | G4 | AI evaluation tool, not a product figure |
| assignments/serializers.py:575 `get_student_submissions` | G3 | Display maximum, not the weight rule (see below) |

## Not grade maths (the guard does not match them)

| Site (2f00c078) | Why it is not the weight rule / a grade figure |
|---|---|
| students/serializers.py:215 `max_points_shown` | `max_points or total_points`: a display maximum. Differs from the weight rule when `max_points == 0` (the weight rule keeps 0 and skips the row; this shows total_points) |
| assignments/serializers.py:575 | `max_points if submission else total_points`: keyed on whether a submission exists, not on NULL. Display only |
| students/views.py:1220 | `float(max_points or 0)`: bounds check on a teacher's score edit |
| dashboard/views.py:4624 `StudentAdminDashboardView.overview` | mean of course GPAs, not a grade formula |
| dashboard/views.py:743 | `Avg("conf_score")`: confidence, not a grade field |
| dashboard/services.py:1339, 1342 | means of extraction and grading confidence |
| students/confidence.py:20 | `Avg(field_name)` over confidence fields only |

## Notes

1. **P3 and P5 are dead annotations.** `avg_grade_val` is annotated at
   dashboard/views.py:3270 and :3777 and read nowhere in the repository (the
   one other mention is a comment in dashboard/tests.py:118 about an old
   bug). The "average grade" those two endpoints show is
   `StudentRiskEvaluator.average_grade`, which is **points-weighted** (W1/W2
   rows through `points_weighted`). So plan 09 §0.2's statement that P3/P5
   show a plain-mean grade is not right on 2f00c078: those two figures
   already agree with the final grade's formula. P6 (the student's own
   summary) is a real plain mean. Proposed handling in E1a: delete the two
   annotations (fewer joins, no output change) rather than route dead code
   through the service. Awaiting SM.
2. The plan's "four `Avg("final_grade")` sites" are six: F4 and F6 average
   `enrollments__final_grade` with a filter.
3. Coverage: 33 of 35 planted edits turn a test red on the base code. The two
   that cannot are P3 and P5 (note 1).
