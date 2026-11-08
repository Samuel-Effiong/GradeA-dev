# Beta into staging, 2026-11-08 (Lane 2, SM brief)

Standing rule (owner, 2026-11-08): staging always holds every bug fix on
beta plus the Phase 2 work; beta takes bug fixes only; staging is never
merged into beta.

## The merge

- `git log origin/staging..origin/beta` (after fetch): one commit,
  a3736b83c H-298: deleting a teacher whose courses hold assignments no
  longer fails.
- Branch task/beta-into-staging-2026-11-08 off origin/staging
  240567975a53d0f8ba4992829466d440ef2bd4cf (contains c25519939).
- `git merge --no-ff origin/beta`: no conflicts. Merge commit
  756b518bb63e2f9d285e6431b975402700c7e1c8 (parents 240567975, a3736b83c).
- Files from beta: assignments/signals.py, classrooms/signals.py,
  assignments/tests_h298_teacher_delete.py, docs/evidence/h298/*.
  `related_or_none` (used by both signal changes) exists on staging in
  AutoGrader/cache_generation.py:210.
- `makemigrations --check --dry-run`: No changes detected.

## Near run 01 (declared before the run)

Labels (23): the 20 guards outside classrooms and users, plus classrooms,
assignments, users (these hold classrooms.tests_e1_grade_guard,
users.tests_roads_that_sign_in_or_activate and
assignments.tests_h298_teacher_delete, so they are not listed twice).
List: near_t1_labels below.

    AutoGrader.tests_credscan
    AutoGrader.tests_h271_static_files
    AutoGrader.tests_network_guard
    AutoGrader.tests_student_classmates_guard
    AutoGrader.tests_student_feedback_guard
    AutoGrader.tests_submission_audience_guard
    ai_processor.tests_call_record_static
    ai_processor.tests_h2_grading_benchmark_guard
    ai_processor.tests_ssrf_guard
    audit.tests_history_guard
    audit.tests_route_coverage
    audit.tests_h273_query_filters
    billing.tests.test_license_admin_user_guard
    billing.tests.test_qa_console_guardrail
    billing.tests.test_qa_time_travel_guardrail
    billing.tests.test_renewal_guards
    students.tests_reading_label_static
    students.tests_app_boundaries
    students.tests_h279_regrade_pending_approval
    students.tests_student_data_map
    classrooms
    assignments
    users

Count from build_suite: 2996 (classrooms 813, assignments 824, users 982,
the 20 guards 377 = 395 - 8 - 10; H-298 tests 2, inside assignments).

Predicted: **Ran 2996, OK (skipped=18)**. Skips: 14 by decorator (13 in
assignments: course_ownership_scale 1, real_extraction 3, tests_load 8,
real_upload_billing 1; ai_processor ssrf peer introspection 1) and 4 at run
time in users (LiveGoogleEndpointContractTests 2, CI_REQUIRE_NETWORK guard
1, CI_REQUIRE_REDIS guard 1; Redis answers PONG locally, so the other
Redis tests run). Any failure or error is a STOP.

Near run 01 result (`01_near.log.gz`, under flock, `--parallel 2`, 290 s):
**Ran 2996, FAILED (failures=1, skipped=18)**. **Prediction wrong**: the
count and the skips matched; the failure was not predicted.

The failure: `classrooms.tests_teacher_access_sweep.TeacherAccessSweepTests.
test_no_unlisted_direct_owner_scoping` (H-38 guard: no direct owner scoping
outside its ALLOWED list) flags two lines:

    students/data_erase.py   Course.objects.filter(teacher=caller).values_list("id", flat=True)
    students/data_export.py  Course.objects.filter(teacher=caller).values_list("id", flat=True)

Not from this merge: `git diff origin/staging HEAD` on the guard and both
files is empty, so staging 240567975 carries this red. The lines are Lane 2's
NFR-CMP-04 teacher scope (e813dc61c); its near runs did not include
classrooms, and this guard is not in the 22-guard list. It is a real H-38
issue, not only a listing one: a teacher removed from the school keeps
export/erase reach over students of their old courses. STOP: no fix made;
reported to the SM.

## SM ruling and the H-38 fix (2026-11-08)

SM: fix the H-38 red on this branch, one push with the merge. Done on
NFR-CMP-04's side (its NOTES, "Second post-push miss"): tests first
9c3d705fac5ffc11f75134084477eb9b95529111 (red 48 Ran 2 failures=2), fix
5d82e4b07901bbe0c7aba01d3c2f75aca46c570e (green 49 Ran 11 OK; plant H38
killed). No allow-list change.

## Near run 02 (declared before the run)

Labels: near 01's 23 plus students.tests_data_export and
students.tests_data_erase (25). Count from build_suite: **3088** (2996 + 46
+ 46). The two added modules have no skips.
Predicted: **Ran 3088, OK (skipped=18)**, the same 18 as run 01. Any
failure is a STOP. Log: `02_near.log`.

Near run 02 result (`02_near.log.gz`, under flock, `--parallel 2`, 312 s): **Ran 3088, OK (skipped=18)**. **As predicted.** Ready for the SM to push; no push from Lane 2.
