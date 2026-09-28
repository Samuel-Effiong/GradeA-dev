# H-38 part 2 — mutation testing, remaining 16 mutants

Continuation of the mutation run recorded in `RESUME.md`. That run got 4/20
mutants done before crashing on a stale string match in the mutation
harness (a tooling bug, not a code bug): `A1_assignments_list_qs`,
`A2a_upload_course`, `A2b_upload_async_course`, `A2c_generate_course` — all
4 confirmed KILLED. The original harness (`mutate2.py`) lived in a
different session's scratchpad and no longer exists. This file covers the
remaining 16 mutants, reconstructed by hand from the `56099ce`/`ad93df2`
diffs and the two H-38 test files.

Method for every mutant: (1) confirm `git status --short` clean on tracked
files; (2) edit the one call site to revert it to its pre-H-38 form (plain
`.filter(...teacher=user)` / `teacher_id != user.id` instead of the shared
`teacher_course_access_q` / `reachable_courses` / `teacher_can_reach_course`
helper); (3) run the specific test module/class that should catch it via
`python manage.py test <target> --settings=settings_worktree --noinput`;
(4) record KILLED (test failed, as expected) or SURVIVED (suite still
passed — a real gap); (5) `git checkout -- <file>` and reconfirm clean
before the next mutant. Test files are never mutated.

## Site-name interpretation notes

Read from `git show 56099ce -- <file>` and `git show ad93df2 -- <file>`,
cross-referenced against the two test files' test names:

- `A2b_upload_async_course` (already KILLED, prior run) = the **view**
  action `upload_assignment_async` in `assignments/views.py`
  (`reachable_courses(request.user)` at the `course_id` lookup inside the
  action that creates the `BatchUploadSession`).
- `T1_upload_task` (this run) = the **Celery task**
  `upload_assignment_async` in `assignments/tasks.py`
  (`reachable_courses(user).get(id=course_id)`) — the task-level re-fetch of
  the course, distinct from the view-level one above.
- `S3_my_students_qs` = `students/views.py` `StudentViewSet.get_queryset`
  (`CustomUser.objects.filter(teacher_course_access_q(user,
  prefix="enrollments__course__"))`) — a plain queryset filter.
- `C2_my_students_exists` = `classrooms/views.py`
  `StudentCourseViewSet.get_queryset`, the `my_students` action's
  `active_enrollment` `Exists()`/`OuterRef` subquery filter — same "my
  students" concept as S3, but the `Exists`-subquery implementation in the
  classrooms app rather than the plain-queryset one in the students app.
  Hence "_exists" vs "_qs" in the two names.
- `D1_dash_course` = `TeacherAdminDashboardView.courses` action
  (`dashboard/courses/{course_id}`).
- `D3_dash_course_teacher` = `TeacherAdminDashboardView.students` action
  (`dashboard/students/{course_id}`) — named `_teacher` because that method
  binds `teacher = request.user` explicitly before the `reachable_courses`
  call, distinguishing it from D1's `request.user` inline.
- `D2_dash_assignment` = the assignment-analytics action's
  `Assignment.objects.filter(teacher_course_access_q(..., prefix="course__"))`
  lookup by `assignment_id`.
- `D4_ai_context` = `dashboard/services.py` `TeacherAIContextService`'s
  `reachable_courses(teacher)` course list.

All 16 interpretations above are my best-reasoned mapping from the diff and
test names; where a mutant's kill test is ambiguous I say so at that
mutant's entry.

---

## Results

### A3_draft_save
- **File:line(s):** `assignments/views.py:1470-1475` (inside `save_generated_assignment_draft`,
  the AI-draft-save action `generated-drafts/{message_id}/save`).
- **Weakened:** replaced
  `.filter(teacher_course_access_q(request.user, prefix="session__course__"), id=message_id, session__user=request.user, role=...)`
  with the pre-H-38 form
  `.filter(id=message_id, session__user=request.user, session__course__teacher=request.user, role=...)`.
- **Result: KILLED — but only by the static sweep guard, not by any behavioral route test.**
  `classrooms.tests_teacher_access_sweep.TeacherAccessSweepTests.test_no_unlisted_direct_owner_scoping`
  FAILED (flagged the reverted `session__course__teacher=request.user,` line).
  All 10 tests in `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherAssignmentRouteTests`
  PASSED unchanged — none of them exercises `save_generated_assignment_draft` /
  `generated-drafts/<id>/save` at all. A repo-wide grep confirms no test file
  anywhere references `save_generated_assignment_draft` or the
  `generated-drafts` URL path.
- **Finding:** the AI-draft-save endpoint has no dynamic/behavioral H-38 probe.
  Its only protection against a future regression that keeps the same *shape*
  (e.g. someone reverts to `session__course__teacher=`) is the static sweep
  scanner. A rewrite that broke the same protection in a way the sweep's regex
  doesn't match (e.g. a helper that silently ignores `prefix`, or a `Q()`
  no-op) would slip through both the sweep and the dynamic suite undetected.
  Worth a dedicated `test_save_generated_assignment_draft` probe in
  `RemovedTeacherAssignmentRouteTests`.

### A4_pdf_teacher_view
- **File:line:** `assignments/views.py:1788` (`download_pdf` action,
  `?view=teacher` branch of `AssignmentViewSet`).
- **Weakened:** replaced `if not teacher_can_reach_course(request.user,
  assignment.course):` with the pre-H-38 `if assignment.course.teacher !=
  request.user:`.
- **Result: KILLED — again only by the static sweep guard.** Ran
  `classrooms.tests_teacher_access_sweep`, `assignments.tests_security.TeacherViewLeakTest`,
  and the full `billing.tests.test_h38_part2_removed_teacher_routes` (44 tests
  total). Only `test_no_unlisted_direct_owner_scoping` failed (flagged
  `if assignment.course.teacher != request.user:`). All 43 other tests,
  including every `TeacherViewLeakTest` case (which tests a *different*
  teacher, not a *removed* teacher) and every H-38 part-2 route probe,
  passed unchanged.
- **Finding:** same structural gap as A3 — `download-pdf?view=teacher` has no
  dynamic H-38 probe for the removed-teacher scenario specifically (the
  existing `TeacherViewLeakTest` cases only prove cross-tenant isolation
  between two *different* teachers, which the old `!=` check already handled
  correctly; they say nothing about a teacher who WAS `course.teacher` and
  lost school membership). Protection here is real (confirmed by the sweep)
  but only text-pattern-verified, not behavior-verified.

### T1_upload_task
- **File:line:** `assignments/tasks.py:930` (Celery task `upload_assignment_async`,
  the task-level re-fetch of the course — distinct from the view-level
  `reachable_courses` call at `assignments/views.py:964`, which is
  `A2b_upload_async_course`, already KILLED in the prior run).
- **Weakened:** replaced `course = reachable_courses(user).get(id=course_id)`
  with `course = Course.objects.get(id=course_id, teacher=user)`, and
  reverted the `classrooms.models` import line accordingly (`Course` instead
  of `reachable_courses`) since `Course` is otherwise unused/unimported here.
- **Result: KILLED — only by the static sweep guard.** Ran
  `classrooms.tests_teacher_access_sweep`,
  `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherAssignmentRouteTests`,
  `assignments.tests_upload_pipeline`, and
  `assignments.tests_upload_task_retry_policy` (25 tests). Only the sweep's
  `test_no_unlisted_direct_owner_scoping` failed. `test_upload_assignment_async`
  in the H-38 route tests passed unchanged because the **view-level** guard
  (A2b, still intact) already returns before the Celery task ever runs for a
  removed teacher's synchronous request; the two direct-task-invocation test
  files (`tests_upload_pipeline`, `tests_upload_task_retry_policy`) call the
  task directly but never simulate a removed-teacher course.
- **Finding — the most concrete gap of the three "sweep-only" mutants.** T1
  is exactly the defense-in-depth layer for the realistic async race: a
  teacher's upload-async request is accepted (course check passes at
  request time), gets queued, and the teacher is removed from the school
  *before the Celery task executes*. No test simulates that ordering. Under
  the current suite, if T1's guard were silently broken in a way the sweep's
  regex doesn't match, a removed teacher's already-queued async upload could
  still write into a school's course with nothing but a linter-style check
  standing behind it. Recommend a dedicated test that queues the task,
  removes the teacher, *then* runs `upload_assignment_async.apply(...)`
  directly and asserts it refuses.

### S1_assignment_taught_by
- **File:line:** `students/views.py:153-158`, the `_assignment_taught_by(assignment_id, teacher)`
  helper (only call site: `batch_upload` action, `submissions/{assignment_id}/batch-upload`).
- **Weakened:** replaced
  `get_object_or_404(Assignment.objects.filter(teacher_course_access_q(teacher, prefix="course__")), id=assignment_id)`
  with the pre-H-38 `get_object_or_404(Assignment, id=assignment_id, course__teacher=teacher)`.
- **Result: KILLED — only by the static sweep guard.** Ran the sweep plus
  the full `billing.tests.test_h38_part2_removed_teacher_routes` (38 tests).
  Only `test_no_unlisted_direct_owner_scoping` failed. The test whose `note()`
  label points at this line (`test_submission_upload_to_the_school_assignment`,
  labelled "students/views.py:158 upload") actually posts to
  `submissions/{assignment.id}/upload`, which is `upload_answers` — a
  different, `IsStudent`-only action that never calls `_assignment_taught_by`
  — not `batch-upload`. So that test's label is stale/misleading: it does not
  exercise this helper.
- **Finding:** `batch_upload` (`submissions/{id}/batch-upload`, teacher
  batch-grading upload) has no dynamic H-38 probe at all. The existing
  `test_submission_upload_to_the_school_assignment` probe should either be
  redirected at `batch-upload`, or a new test should be added for it
  specifically — its current label suggests coverage that isn't there.

### S2_submissions_qs
- **File:line:** `students/views.py:356-358`, `StudentSubmissionViewSet.get_queryset`
  teacher branch.
- **Weakened:** replaced
  `StudentSubmission.objects.filter(teacher_course_access_q(user, prefix="assignment__course__"))`
  with `StudentSubmission.objects.filter(assignment__course__teacher=user)`.
- **Result: KILLED — by real behavioral tests, plus the sweep.** 13 tests
  run, 5 failed: `test_no_unlisted_direct_owner_scoping` (sweep) AND four
  dynamic route probes in
  `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherSubmissionRouteTests`:
  `test_list_submissions`, `test_retrieve_submission`, `test_delete_submission`,
  `test_publish_grade`. This site has genuine, working dynamic H-38 coverage.

### S3_my_students_qs
- **File:line:** `students/views.py:1383-1387`, `StudentViewSet.get_queryset`.
- **Weakened:** replaced
  `CustomUser.objects.filter(teacher_course_access_q(user, prefix="enrollments__course__")).distinct()`
  with the pre-H-38 `CustomUser.objects.filter(enrollments__course__teacher=user).distinct()`.
- **Result: KILLED — by the sweep guard only, and this is dead code.**
  Confirmed by grepping the entire repo: `StudentViewSet` (students/views.py:1368)
  is defined but **never registered in any router** — `students/urls.py` only
  registers `StudentSubmissionViewSet` under `submissions`; no `urls.py`
  anywhere imports or routes `StudentViewSet`. No test file references it
  either. This class is unreachable from any HTTP request, so no dynamic test
  could ever exercise it regardless of what the queryset does.
- **Finding:** not a security gap in the live app (the view can't be hit),
  but worth flagging to the team: either delete `StudentViewSet` as dead code,
  or wire it up if it was meant to be a real endpoint. As written it adds
  confusion — a reader could mistake it for the live "my students" surface
  (that's actually `StudentCourseViewSet.my_students`, see C2 below).

### C1_remove_student
- **File:line:** `classrooms/views.py:1448`, `CourseViewSet.remove_student` action.
- **Weakened:** replaced `if not teacher_can_reach_course(request.user, course):`
  with the pre-H-38 `if request.user != course.teacher:`.
- **Result: KILLED — only by the static sweep guard.** Ran the sweep plus
  the full `billing.tests.test_h38_part2_removed_teacher_routes` and
  `billing.tests.test_h38_teacher_removal` (55 tests). Only
  `test_no_unlisted_direct_owner_scoping` failed.
- **Finding:** `remove_student` (`course/{id}/students/{student_id}`, DELETE)
  has no dynamic H-38 probe in either test file. Note `remove_student` gets
  to this permission check via `self.get_object()` on `CourseViewSet`, whose
  `get_queryset` teacher branch already filters through
  `teacher_course_access_q` (confirmed dynamically covered by
  `test_h38_teacher_removal`'s course-access tests) — so `get_object()` would
  itself 404 before this line for a truly removed teacher in the common
  case. This `teacher_can_reach_course` check is a second, redundant
  belt-and-suspenders guard at the object level; like T1, its specific
  removal is unverified by any dynamic test, only by the sweep.

### C2_my_students_exists
- **File:line:** `classrooms/views.py:2038-2041`, `StudentCourseViewSet.get_queryset`,
  `my_students` action's `active_enrollment` `Exists()`/`OuterRef` subquery.
- **Weakened:** replaced
  `StudentCourse.objects.filter(teacher_course_access_q(user, prefix="course__"), student=OuterRef("pk"))`
  with `StudentCourse.objects.filter(course__teacher=user, student=OuterRef("pk"))`.
- **Result: KILLED — by a real behavioral test, plus the sweep.** 14 tests
  run, 2 failed: `test_my_students` (dynamic, in
  `RemovedTeacherClassroomRouteTests`) and the sweep's
  `test_no_unlisted_direct_owner_scoping`. Genuine, working dynamic H-38
  coverage for `student-course/my-students`.

### C3_submissions_prefetch
- **File:line:** `classrooms/views.py:2069-2073`, `StudentCourseViewSet.get_queryset`,
  the `submissions_qs` used in a `Prefetch("student__submissions", ...)` for
  the non-`my_students` list/retrieve path.
- **Weakened:** replaced
  `submissions_qs.filter(teacher_course_access_q(user, prefix="assignment__course__"))`
  with `submissions_qs.filter(assignment__course__teacher=user)`.
- **Result: KILLED — only by the static sweep guard.** 14 tests, 1 failure
  (the sweep). None of `test_student_course_list`, `test_student_course_patch`,
  or `test_student_course_delete` in `RemovedTeacherClassroomRouteTests`
  failed.
- **Finding:** this prefetch only affects which submissions are embedded
  inside a `StudentCourse` row's serialized payload — it doesn't gate
  whether the row itself is visible (that's C4's job, `queryset.filter(...)`
  a few lines below, which independently blocks removed-teacher access to
  the row entirely). Because C4 already 404s/empty-lists everything for a
  removed teacher before this prefetch matters, none of the three dynamic
  `student_course_*` tests can reach a state where C3 alone is exposed. A
  gap remains for the case where a teacher can still reach the
  `StudentCourse` row through some other path (e.g. is still validly viewing
  their OWN course but a submission belongs to an assignment in a course
  they no longer control) — no test constructs that scenario.

### C4_studentcourse_qs
- **File:line:** `classrooms/views.py:2089-2092`, `StudentCourseViewSet.get_queryset`,
  the main list/retrieve/patch/delete queryset for the teacher branch.
- **Weakened:** replaced
  `queryset.filter(teacher_course_access_q(user, prefix="course__")).distinct()`
  with `queryset.filter(course__teacher=user).distinct()`.
- **Result: KILLED — by real behavioral tests, plus the sweep.** 14 tests,
  4 failed: `test_student_course_list`, `test_student_course_patch`,
  `test_student_course_delete` (dynamic) and the sweep. Genuine, working
  dynamic H-38 coverage — this is the primary gate C3 depends on (see C3's
  finding above).

### C5_topic_qs
- **File:line:** `classrooms/views.py:2386-2388`, `TopicViewSet.get_queryset`
  teacher branch.
- **Weakened:** replaced `Topic.objects.filter(teacher_course_access_q(user, prefix="course__"))`
  with `Topic.objects.filter(course__teacher=user)`.
- **Result: KILLED — by real behavioral tests, plus the sweep.** 14 tests,
  4 failed: `test_list_topics`, `test_patch_topic`, `test_delete_topic`
  (dynamic) and the sweep. Genuine, working dynamic H-38 coverage.

### C6_topic_create_validator
- **File:line:** `classrooms/serializers.py:117`, `TopicSerializer.validate_course`.
- **Weakened:** replaced `if not teacher_can_reach_course(user, value):`
  with the pre-H-38 `if value.teacher_id != user.id:`.
- **Result: KILLED — by a real behavioral test, plus the sweep.** 14 tests,
  2 failed: `test_create_topic_in_the_school_course` (dynamic, POST to
  `/topics` going through `TopicSerializer`) and the sweep.
  `test_create_topic_through_the_course_action` did NOT fail — that route
  (`course/{id}/topics`) doesn't go through this serializer validator at
  all, it's already scoped by the `CourseViewSet.get_object()` queryset
  filter (dynamically covered separately). This site has genuine, working
  dynamic H-38 coverage.

### D1_dash_course
- **File:line:** `dashboard/views.py:3150`, `TeacherAdminDashboardView.courses`
  action (`teacher-admin/dashboard/courses/{course_id}`).
- **Weakened:** replaced `get_object_or_404(reachable_courses(request.user), id=course_id)`
  with `get_object_or_404(Course, id=course_id, teacher=request.user)`.
- **Result: KILLED — by a real behavioral test, plus the sweep.** 10 tests,
  2 failed: `test_dashboard_course` (dynamic) and the sweep. Genuine,
  working dynamic H-38 coverage.

### D2_dash_assignment
- **File:line:** `dashboard/views.py:3284-3286`, the assignment-analytics
  action's assignment lookup by `assignment_id`.
- **Weakened:** replaced
  `get_object_or_404(Assignment.objects.filter(teacher_course_access_q(self.request.user, prefix="course__")), id=assignment_id)`
  with `get_object_or_404(Assignment, id=assignment_id, course__teacher=self.request.user)`.
- **Result: KILLED — by a real behavioral test, plus the sweep.** 10 tests,
  2 failed: `test_dashboard_assignment` (dynamic) and the sweep. Genuine,
  working dynamic H-38 coverage.

### D3_dash_course_teacher
- **File:line:** `dashboard/views.py:3388`, `TeacherAdminDashboardView.students`
  action (`teacher-admin/dashboard/students/{course_id}`).
- **Weakened:** replaced `get_object_or_404(reachable_courses(teacher), id=course_id)`
  with `get_object_or_404(Course, id=course_id, teacher=teacher)`.
- **Result: KILLED — by a real behavioral test, plus the sweep.** 10 tests,
  2 failed: `test_dashboard_students` (dynamic) and the sweep. Genuine,
  working dynamic H-38 coverage.

### D4_ai_context
- **File:line:** `dashboard/services.py:1136`, `TeacherAIContextService.build`
  (feeds the `custom-ai-prompt` teacher-admin dashboard action).
- **Weakened:** replaced `reachable_courses(teacher)` with
  `Course.objects.filter(teacher=teacher)`.
- **Result: KILLED — only by the static sweep guard.** 10 tests, 1 failure
  (the sweep). `test_custom_ai_prompt_on_the_school_course` did not fail —
  it's blocked earlier by the paid-AI credit gate (403/402, confirmed in the
  run log) before `TeacherAIContextService.build` is ever reached, the same
  pattern as A3/A4/T1's paid-AI or otherwise-gated routes.
- **Finding:** no dynamic H-38 test can currently reach this service without
  first satisfying the credit/subscription gate. A unit test that calls
  `TeacherAIContextService().build(teacher)` directly (bypassing the view
  and its billing gate) would close this gap cheaply.
- **Update (2026-09-28): now caught dynamically.** `e4984d6` tightened
  `test_custom_ai_prompt_on_the_school_course` with a context spy, which reads
  the AI context before the billing refusal. The Verification Engineer's
  mutant M9 (this exact revert) is now killed by that test, with the sweep
  excluded from the run (`VERIFICATION.md`). D4 is no longer sweep-only.

---

## Summary

**16/16 KILLED, 0/16 SURVIVED** — every one of the 16 mutations made the
test suite fail (`classrooms.tests_teacher_access_sweep` alone, at minimum).
Combined with the 4 mutants confirmed in the prior session
(`A1_assignments_list_qs`, `A2a_upload_course`, `A2b_upload_async_course`,
`A2c_generate_course`, all KILLED), that's **20/20 KILLED** across the full
H-38 part 2 mutation set. No pure survivor was found: H-38's protection
exists and is enforced everywhere it was added.

That said, the "which test kills it" breakdown surfaces a real, actionable
gap in *dynamic* coverage:

- **8 of 16 sites are caught only by the static sweep guard**
  (`classrooms.tests_teacher_access_sweep.TeacherAccessSweepTests.test_no_unlisted_direct_owner_scoping`),
  not by any HTTP-level removed-teacher probe:
  `A3_draft_save`, `A4_pdf_teacher_view`, `T1_upload_task`,
  `S1_assignment_taught_by`, `S3_my_students_qs`, `C1_remove_student`,
  `C3_submissions_prefetch`, `D4_ai_context`. The sweep is a real,
  committed regex-based test that genuinely fails on any revert to the old
  `course__teacher=`/`.teacher_id ==`/etc. shape — so these sites are not
  unprotected — but the sweep only catches *that specific shape* of
  regression. A rewrite that broke the same protection differently (a
  helper that silently no-ops, a `Q()` that matches everything, a prefix
  bug) would slip past the sweep and, for these 8 sites, past every other
  test too.
  - `S3_my_students_qs` is additionally dead code (`StudentViewSet` is
    never routed anywhere) — not a live security gap, but worth cleaning up
    or wiring up.
  - `T1_upload_task` and `C1_remove_student` are defense-in-depth layers
    behind an outer guard that dynamic tests already exercise (A2b for T1;
    `CourseViewSet.get_queryset` for C1) — real but currently unproven in
    isolation, which matters most for T1's async race-condition scenario.
  - `A3_draft_save` and `A4_pdf_teacher_view` have literally no test
    (H-38-specific or otherwise) touching their endpoints for a
    removed-teacher scenario.
  - `D4_ai_context` and (structurally) `A3`/`A4` are blocked from dynamic
    reach by an earlier gate (credit/subscription or missing route probe)
    unrelated to H-38 itself.
- **8 of 16 sites have genuine, working dynamic H-38 route coverage** in
  addition to the sweep: `S2_submissions_qs`, `C2_my_students_exists`,
  `C4_studentcourse_qs`, `C5_topic_qs`, `C6_topic_create_validator`,
  `D1_dash_course`, `D2_dash_assignment`, `D3_dash_course_teacher`.

Recommended follow-ups (not implemented here, evidence-only run): add direct
route/unit probes for `save_generated_assignment_draft`, `download_pdf`
(`view=teacher`, removed-teacher case), `batch_upload`, `remove_student`,
the Celery `upload_assignment_async` task called after removal, and
`TeacherAIContextService.build`; delete or route `StudentViewSet`.
