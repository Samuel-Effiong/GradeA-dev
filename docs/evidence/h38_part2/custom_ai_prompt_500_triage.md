# H-38 part 2: triage of the removed-teacher 500 on `custom-ai-prompt`

2026-09-28. Branch `task/teacher-removal`, rebased onto beta `4b902fc`
(branch HEAD `874362a` before this change). Route:
`POST /api/v1/teacher-admin/dashboard/custom-ai-prompt`
(`TeacherAdminDashboardView.custom_ai_prompt`, dashboard/views.py).

The earlier note in `select_for_update_outer_join_regression.md` said the
500 was pre-existing and came from "building superadmin analytics context for
a non-superadmin user". **The cause was different.** The 500 was an AI-access
refusal answered as a server error, and beta's `f7cd15e` has already fixed it.

## Measured status codes

These are identical test code on every side: the H-38 part 2 test file plus a
throwaway probe (never committed), copied unchanged into a detached throwaway
worktree with its own test database. The provider call is stubbed at
`AIProcessor.__ai_model`, so the real access gate and credit metering run.
The active teacher's wallet is funded with a 500k bucket, because the 20k
license allocation alone is under the estimator's ~22k requirement and
answers 402.

| Tree | Removed teacher | Active teacher | Removed teacher's AI context contains School A's course |
|---|---|---|---|
| pre-rebase branch `b8577ac` (base `fba1294`, before `f7cd15e`) | **500** | 200 | no |
| beta `4b902fc` | 403 `ai_feature_not_available` | 200 | **yes (H-38 leak)** |
| rebased branch `874362a` | 403 `ai_feature_not_available` | 200 | no |

Removed-teacher body on beta and on the branch:
`{"success":false,"message":"AI access denied: No active subscription","error":{"field_errors":{"error":"AI access denied: No active subscription","code":"ai_feature_not_available"}}}`

## Root cause of the old 500

- `remove_teachers` deactivates the teacher's license allocation. Then
  `can_user_access_ai` resolves `kind="none"` and returns "No active
  subscription".
- `ai_processor/services.py:4420` (`execute_graded_task`, in `b8577ac`)
  raises `AIFeatureNotAvailableError("AI access denied: No active subscription")`.
- `custom_ai_prompt` and `custom_ai_prompt_retry` re-raise it untouched.
- `dashboard/views.py` `run_dashboard_ai_chat` in `b8577ac` has no refusal
  clause. The error lands in `except Exception` (line 200), which returns an
  explicit `Response(..., status=HTTP_500_INTERNAL_SERVER_ERROR)` (line 215).

Beta `f7cd15e` ("Surface AI refusals cleanly") added
`except PERMANENT_AI_REFUSALS: return refusal_response(e)` (now
`dashboard/views.py:204-206`), which maps the refusal to 403. H-38 did not
cause the 500 and did not change it. The route never used `select_for_update`
or `teacher_course_access_q`. No code change was needed on this branch.

## What H-38 changes on this route

The route ignores `course_id`. It builds its context from
`TeacherAIContextService.build(request.user)` (dashboard/services.py:1155,
now `reachable_courses(teacher)`), and it does so **before** the billing
gate runs. On beta the removed teacher's context still includes School A's
course. Only the missing subscription stopped it reaching the model, so a
removed teacher with an individual subscription of their own would have had
School A's data sent to the model. The branch drops the course.

## Test changes (`billing/tests/test_h38_part2_removed_teacher_routes.py`)

- `test_custom_ai_prompt_on_the_school_course` is now strict:
  `assertEqual(403)`, `assertLess(500)`, code `ai_feature_not_available`,
  and the model is never called. A spy on `custom_ai_prompt_retry` asserts
  that the context sent does not contain "School A Biology".
- New `ActiveTeacherCustomAIPromptTests` is the positive control. The same
  teacher, still active and with a funded wallet, gets 200 with the stubbed
  reply, the model is called, and the context does contain the course. This
  proves the spy assertion cannot pass vacuously.
- On beta `4b902fc` the same file fails the strict test on the context
  assertion (403, but the context leaks). The positive control passes there.

## Mutations (both reverted and confirmed by `git diff`)

1. `dashboard/services.py:1155`: `reachable_courses(teacher)` changed back to
   `Course.objects.filter(teacher=teacher)`. **KILLED**: strict test fails
   `True is not false` on the context-leak assertion. The positive control
   still passes.
2. `dashboard/views.py:204-206`: `except PERMANENT_AI_REFUSALS` clause
   deleted. **KILLED**: `500 != 403`, which reproduces the historical 500.

## Regression (rebased branch plus this change)

- `billing.tests.test_h38_part2_removed_teacher_routes billing.tests.test_h38_teacher_removal classrooms.tests_teacher_access_sweep`:
  60 tests, **4 failures**. Unmodified `874362a` has the **same 4 failures**
  (59 tests), so they are rebase fallout and this change did not cause them:
  - `test_upload_assignment_async`, `test_grade_all`, `test_grade_paid_ai`
    expect 400 but get **402** `insufficient_credits`. Beta `f7cd15e` moved
    the credit gate from 400 to 402.
  - `test_no_unlisted_direct_owner_scoping` finds 7 direct-owner scoping lines
    that beta brought in and that are not routed through the H-38 helper:
    `assignments/serializers.py` `if value.teacher_id != user.id:`,
    `classrooms/filters.py` `course__teacher=self.request.user,`,
    `classrooms/scale_my_students.py` (2 lines),
    `classrooms/views.py` `assignment__course__teacher=user` and
    `StudentCourse.objects.filter(course__teacher=user)`,
    `users/filters.py` `return Q(course__teacher=user)`.
- `dashboard.tests_custom_ai_prompt dashboard.tests_dashboard_remediation dashboard.tests_real_ai_chat ai_processor.tests_dashboard_custom_ai_prompt billing.tests.test_refusal_handling billing.tests.test_refusal_handling_gates`:
  117 tests, OK (2 skipped: the opt-in real-AI tests).
