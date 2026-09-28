# H-38 part 2: the 7 sweep hits from the rebase onto beta `4b902fc`

2026-09-28. Branch `task/teacher-removal`. Starting HEAD was `e4984d6`. The
code fix is `85763e5` and the tests are `27e15fd`.

After the rebase, `classrooms.tests_teacher_access_sweep
.test_no_unlisted_direct_owner_scoping` flagged 7 lines from beta that scope
on the course owner directly. `course.teacher == user` never stops being
true, so each line still matched a removed teacher's old School A course.

## Why the old probes could not see these

Each site sits inside an outer queryset that H-38 already scopes, such as
the my-students `Exists(active_enrollment)` or `CustomUserViewSet
.get_queryset`. The existing fixture gives the removed teacher only one
course, the School A course. The outer queryset therefore drops the pupil,
and the inner owner-scoping is never consulted. The existing
`test_my_students` passes for exactly that reason.

The reproduction needs a pupil whom the removed teacher can still
legitimately see. The new `SharedStudentBase` fixture sets this up as
follows:

- The teacher keeps an INDIVIDUAL course, "Own Chemistry", and the School A
  pupil is enrolled there as well. This is the shared-student shape from
  H-22, now across a removal.
- The School A enrollment has a grade (91.50), a description, an assignment
  and a graded submission.
- A School A colleague's course holds the same pupil, which is H-22's own
  boundary.

## Per site

The table records reproduction on the branch before the fix, at unmodified
`e4984d6` code with the new tests (`scratchpad/repro_before_fix.log`: 23
run, 9 failed, and all 9 failures were leaks).

| # | Site | Introduced by | Reachable | Leak reproduced before the fix | Disposition |
|---|---|---|---|---|---|
| 1 | `classrooms/views.py` my_students `Prefetch("enrollments", StudentCourse.objects.filter(course__teacher=user))` | H-22 `b0644ad` | Yes. `GET /student-course/my-students` | 200. The shared pupil's row showed `enrolled_courses: ["School A Biology", "Own Chemistry"]`. The prefetch cache held the School A enrollment. | Fixed: `teacher_course_access_q(user, prefix="course__")` |
| 2 | `classrooms/views.py` my_students `Prefetch("submissions", ...filter(assignment__course__teacher=user))` | H-22 `b0644ad` | Yes, same route. Not visible in the payload while #1 is scoped, because the serializer counts only the submissions of the row's "relevant course". | The prefetch cache held the School A submission. | Fixed: `teacher_course_access_q(user, prefix="assignment__course__")` |
| 3 | `classrooms/filters.py` `MyStudentsFilter._through_own_enrollments` `course__teacher=self.request.user` | H-22 `b0644ad` | Yes. `my-students?enrollments__course=` / `?enrollments__course__session=` | `count: 1` for the School A course and for the School A session. Together with #1, the row's subject became the School A course: `course_description "School A syllabus"`, teacher name, and `grade 91.5 / A-`. | Fixed: helper inside the same `filter()` call. This is a narrowing filter, but here the narrowing itself is the oracle, so it was not allowlisted. |
| 4 | `users/filters.py` `visible_enrollments` `return Q(course__teacher=user)` | H-22 `d40de69` | Yes. `GET /users/<pupil>?enrollments__course=` / `__session=` | 200 for the School A course and session; 404 for an unknown id. This is the yes/no oracle H-22 closed for other tenants. | Fixed: `teacher_course_access_q(user, prefix="course__")`, which matches `CustomUserViewSet.get_queryset`'s teacher branch |
| 5 | `assignments/serializers.py` `AssignmentTextSerializer.validate_course` `if value.teacher_id != user.id:` | **H-18** `25613d3` (not H-22) | Yes. `POST /assignments`, `POST /assignments/create-async`, `PATCH /assignments/<own>` | 202 with the row created in School A for both create routes. PATCH answered 200 and moved the teacher's own assignment into the School A course. | Fixed: `teacher_can_reach_course(user, value)`, the same rule `TopicSerializer.validate_course` already uses |
| 6 | `classrooms/scale_my_students.py` `course__teacher=self.measured_teacher` | H-22 Gate 6 harness `5ed650a` | **No** | n/a | Allowlisted with a reason (see below) |
| 7 | `classrooms/scale_my_students.py` `User.objects.filter(enrollments__course__teacher=self.measured_teacher)` | same | **No** | n/a | Allowlisted with a reason |

### Why #6 and #7 are unreachable

`classrooms/scale_my_students.py` is a `TransactionTestCase`. Its name does
not match `test*.py`, so discovery never collects it, and its docstring says
it runs only as `manage.py test classrooms.scale_my_students`. A repo-wide
grep finds no import of it anywhere outside `docs/`, and no `urls.py`
references it.

The two lines do not make access decisions. One seeds fixtures: it calls
`_recalculate_final_grade` over the measured teacher's enrollments. The
other computes the expected roster size for the report. The measured
teacher is an active school teacher, and for an active teacher owner
scoping and the H-38 rule select the same rows. The sweep's `ALLOWED` block
now has both lines, each with its reason.

### H-22 and H-18 semantics

Both helpers are `own & reachable`. `teacher_course_access_q` builds
`Q(teacher=user) & (...)`, and `teacher_can_reach_course` returns False
unless `course.teacher_id == user.id`. Every rewrite is therefore a subset
of the line it replaced. It can drop a course the teacher was removed from.
It can never add another teacher's course.

The positive controls check this on the same fixture. The active teacher
does not see the colleague's course in the row. The colleague course filter
returns 0 rows on my-students and 404 on `/users`. Create-async into the
colleague's course is refused with 400.

None of the five sites takes a row lock. The `select_for_update` outer-join
trap described in `select_for_update_outer_join_regression.md` does not
apply here.

## Tests added (`billing/tests/test_h38_part2_removed_teacher_routes.py`)

`RemovedTeacherSharedStudentTests` has 9 tests. Every assertion is strict,
and every HTTP helper also checks `assertLess(status, 500)`.

- **my-students row:** 200, `enrolled_courses == ["Own Chemistry"]`, and no
  School A name or description in the body.
- **Prefetch caches, read directly:** enrollments contain only the teacher's
  own course, and submissions are empty. This uses the same technique as
  H-22's `test_prefetch_caches_hold_only_the_teachers_own_rows`. It is the
  only test that can catch #2 on its own.
- **my-students course filter and session filter:** each answers 200 with
  `count == 0`.
- **`/users/<pupil>` course and session filters:** the unfiltered request
  answers 200, which proves the pupil is reachable. The filtered request
  then answers 404, and its status and body are identical to the
  unknown-id control.
- **create, create-async and PATCH into the School A course:** each answers
  400 with `course` in the body. No row is written, and neither the
  extraction nor `launch_processing_task` is called.

`ActiveTeacherSharedStudentTests` has 11 tests: the same fixture without
the removal, as a positive control.

- The my-students row lists both of the teacher's courses.
- The prefetch caches hold the School A enrollment and the School A
  submission.
- The School A course filter returns 1 row with `course_description`,
  `grade 91.50` and `total_assignments_submitted 1`. The session filter
  returns 1 row.
- `/users` answers 200 for both filters.
- create-async into the School A course answers 202, and the row lands in
  that course. PATCH into it answers 200, and the assignment moves.
- The three H-22 boundary checks on the colleague's course hold.

## Problem 2: the three credit-gated tests

`test_upload_assignment_async`, `test_grade_all` and `test_grade_paid_ai`
asserted 400 but got 402. The removal expires the teacher's buckets, and
`HasCreditBalance` refuses before any H-38 check runs (beta `f7cd15e`
changed that refusal from 400 to 402). As written, the tests proved the
billing gate and nothing about H-38.

The fix is a module-level `fund_wallet(user)`. It is the 500k MONTHLY
bucket the custom-ai-prompt positive control already used, and that control
now calls it too. Each of the three tests funds the removed teacher first.
With the wallet funded, all three reach the H-38 guard and get its own
**404**. Each asserts `== 404`, `< 500` and that nothing was written: no
`BatchUploadSession`, and the score unchanged. `test_grade_paid_ai` also
stubs the provider and asserts it was not called.

| Test | H-38 guard reached | Mutation | Result under mutation |
|---|---|---|---|
| `test_upload_assignment_async` | A2b `get_object_or_404(reachable_courses(request.user), id=course_id)` (assignments/views.py:966) | M6: reverted to `get_object_or_404(Course, id=course_id, teacher=request.user)` | **KILLED**: 400 "No files were uploaded" != 404 |
| `test_grade_all` | A1 `AssignmentViewSet.get_queryset` `teacher_course_access_q(user, prefix="course__")` via `get_object()` | M7: `.filter(course__teacher=user)` | **KILLED**: 400 "No ungraded submissons" != 404 (plus 6 other A1 route tests) |
| `test_grade_paid_ai` | S2 `StudentSubmissionViewSet.get_queryset` teacher branch via `get_object()` | M8: `.filter(assignment__course__teacher=user)` | **KILLED**: 403 `ai_feature_not_available` != 404 (plus 4 other S2 route tests) |

One observation from M8. With the H-38 guard reverted, the funded removed
teacher reached `grade_engine` and was stopped only by the AI subscription
gate ("No active subscription"). A removed teacher who holds an individual
subscription of their own would pass that gate. S2 is the real barrier on
this route.

## Mutations on the fixed sites

Each mutation reverted one site to its beta form. The run covered
`billing.tests.test_h38_part2_removed_teacher_routes` plus
`classrooms.tests_teacher_access_sweep`, 63 tests. After each run the file
was restored with `git checkout -- <file>`, and `git status` was confirmed
clean.

| Mutant | Behavioural tests that failed | Sweep |
|---|---|---|
| M1 #1 enrollments prefetch | `test_my_students_row_names_only_the_own_course`, `test_my_students_prefetch_caches_hold_no_school_rows` | failed |
| M2 #2 submissions prefetch | `test_my_students_prefetch_caches_hold_no_school_rows` | failed |
| M3 #3 MyStudentsFilter | course-filter and session-filter tests | failed |
| M4 #4 visible_enrollments | both `/users` oracle tests | failed |
| M5 #5 validate_course | create, create-async and PATCH tests | failed |

All 5 were killed by behavioural route tests and not only by the sweep.
Combined with M6 to M8 above, the result is 8 of 8 killed.

## Regression (branch at `27e15fd`, run one invocation at a time under `nice -n 10`)

| Modules | Result |
|---|---|
| `billing.tests.test_h38_part2_removed_teacher_routes billing.tests.test_h38_teacher_removal classrooms.tests_teacher_access_sweep` | **80 tests, OK**, including the sweep. Before this change the same modules had 60 tests with 4 failures. |
| H-22 and H-18: `classrooms.tests_my_students_course_scope users.tests_user_enrollment_filter_oracle classrooms.tests_my_students_concurrency classrooms.tests_query_budget classrooms.tests_security_penetration assignments.tests_course_ownership_idor assignments.tests_security` | **203 tests, OK** |
| Dashboard and refusal modules from `custom_ai_prompt_500_triage.md` | **117 tests, OK** (2 skipped, the opt-in real-AI tests) |
| Other modules that touch these sites: `assignments.tests_course_ownership_scale assignments.tests_course_ownership_concurrency assignments.tests_course_ownership_failure classrooms.tests_fail_closed_ownership_validators classrooms.tests_tenancy_and_roster` | **44 tests, OK** (1 skipped) |

## Verification follow-ups (2026-09-28): the last three credit-gated probes, and roster import

Raised by the Verification Engineer (part A note 1) and the Security Engineer's
core review (notes 1 and 4). The SM ruled both in scope for this landing.

### The last three removed-teacher probes now reach the H-38 guard

These accepted `(402, 403, 404)` and did not fund the teacher, so billing could
refuse before the guard ran. Each now funds the removed teacher with
`fund_wallet()`, asserts the guard's exact code plus `< 500`, asserts nothing was
written, and has an active-teacher positive control in the new class
`ActiveTeacherCourseGuardRouteTests`.

| Test | Guard | Asserts | Positive control | Mutant (sweep excluded) |
|---|---|---|---|---|
| `test_upload_assignment` (now sends a real PNG; provider stubbed) | A2a | 404, <500, no Assignment, model not called | `test_upload_assignment_succeeds_for_active_teacher` → 201 | killed: 400 ≠ 404 |
| `test_generate_assignment_from_prompt` | A2c | 404, <500, no generation session or message, model not called | `test_generate_assignment_from_prompt_succeeds_for_active_teacher` → 201 | killed: 403 ≠ 404 |
| `test_batch_upload_answers_to_the_school_assignment` (was `test_submission_upload_to_the_school_assignment`) | S1 | 404, <500, no BatchUploadSession or task, launch not called | `test_batch_upload_answers_succeeds_for_active_teacher` → 202 | killed: 202 ≠ 404 (the reverted guard queued the upload) |

**`batch-upload` now has a dynamic probe.** The old S1 probe posted to
`submissions/{id}/upload` (`upload_answers`, IsStudent-only), which never calls
`_assignment_taught_by`; it is now aimed at `submissions/{id}/batch-upload`, the
guard's only caller.

Two findings from this: upload and generate carry no `HasCreditBalance`, so on
those two routes the guard was already reached and only the loose assertion
hid it; and the generate positive control needs `AI_PROMPT_ASSIGNMENT_CREATION`
on the plan, because generation is a gated premium feature.

### Roster import: a real removed-teacher leak, fixed in this landing

The sweep allowed `classrooms/services/roster_import.py`
`_find_existing_student_by_name`, which matched a no-email roster row against
students in any course where `enrollments__course__teacher=course.teacher`. That
includes SCHOOL courses the teacher has since been removed from, and
`_import_row_without_email` enrols the match with no cross-school gate of its own.

- **Reproduced first**, before any code change: a teacher removed from School A
  imports a no-email "Ada,Lovelace" row. School A's Ada was attached both to the
  teacher's own individual course and to a School B course.
- **Fix** (the one production line changed): the lookup uses
  `teacher_course_access_q(course.teacher, prefix="enrollments__course__")` in
  the same single `filter()` call, so the access rule and the name match bind
  to one enrollment row. The sweep's allowlist entry is removed, so the sweep
  now polices this line.
- **Tests**: `RemovedTeacherRosterNameMatchTests` (import into an individual
  course, and into a School B course: each creates a new student and does not
  attach School A's) and the positive control
  `ActiveTeacherRosterNameMatchTests` (an active teacher's import into a second
  School A course still attaches the existing student).
- **Mutant** (sweep excluded; restored from a saved copy, sha256 verified):
  reverting to `enrollments__course__teacher=course.teacher` fails both
  removed-teacher probes (`True is not false`: the School A student was
  attached); the positive control still passes. Killed.

### Regression (one invocation, `nice -n 10`)

`billing.tests.test_h38_part2_removed_teacher_routes billing.tests.test_h38_teacher_removal classrooms.tests_teacher_access_sweep classrooms.tests_tenancy_and_roster classrooms.tests_cross_school_enrollment classrooms.tests_concurrency_and_resilience`:
**173 tests, OK.** After black's reformat of the test file, the route module
alone: 64 OK. All pre-commit hooks pass on the three changed files.
