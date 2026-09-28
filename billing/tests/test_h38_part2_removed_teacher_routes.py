"""H-38 part 2: what a removed teacher can still do through routes that scope
by `course.teacher == user` alone.

`remove_teachers` deactivates the allocation and expires the credit buckets but
leaves `course.teacher` pointing at the removed teacher, so every endpoint that
filters by `teacher=user` keeps serving School A's course to them. Part 1
(course, enrol, /users) is on task/teacher-removal. These probe the rest.

Every request is a real route with a real JWT. Each test asserts the secure
behaviour, so it FAILS on a beta where the hole is open, and it prints one
`H38P2 <site> <VERB> status=<n> changed=<bool>` line that the REPRODUCE.md
table is built from. "changed" is measured on the row, never inferred from the
status code. Re-run unchanged against the fixed branch.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient, APIRequestFactory
from rest_framework_simplejwt.tokens import RefreshToken

import dashboard.views as dashboard_views
from assignments.models import (
    Assignment,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
    AssignmentStatus,
)
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
    Topic,
)
from classrooms.views import StudentCourseViewSet
from students.models import BatchUploadSession, StudentSubmission
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-h38-password!"  # pragma: allowlist secret
API = "/api/v1"


def jwt_client(email):
    # A real signed access token, checked by the real JWT authenticator on
    # every request. Minted directly rather than through /auth/login because
    # the login throttle trips after a few logins in one test process.
    token = RefreshToken.for_user(CustomUser.objects.get(email=email)).access_token
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def make_user(email, user_type, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password=PASSWORD,
        first_name=email.split("@")[0].title(),
        last_name="H38",
        user_type=user_type,
        school=school,
        is_active=True,
    )


class TeacherRemovalBase(TestCase):
    def setUp(self):
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H38 License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.school = School.objects.create(name="School A H38")
        self.admin = make_user("admin-a@h38.test", UserTypes.SCHOOL_ADMIN, self.school)
        self.license = self._license(self.school, self.admin)

        self.other_school = School.objects.create(name="School B H38")
        self.other_admin = make_user(
            "admin-b@h38.test", UserTypes.SCHOOL_ADMIN, self.other_school
        )
        self.other_license = self._license(self.other_school, self.other_admin)

        # An individual-track teacher who already has an account, joined to
        # School A by its admin - the path that sets user.school.
        self.teacher = make_user("teacher@h38.test", UserTypes.TEACHER)
        self.admin_client = jwt_client(self.admin.email)
        response = self.admin_client.post(
            f"{API}/license-subscriptions/{self.license.id}/add_teachers",
            {"teacher_emails": [self.teacher.email]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.data["successful"] == 1, response.content
        self.teacher.refresh_from_db()
        assert self.teacher.school_id == self.school.id
        assert self.teacher.is_under_license()

        response = self.admin_client.post(
            f"{API}/sessions", {"name": "2026 Term 1"}, format="json"
        )
        assert response.status_code == 201, response.content
        self.session_id = response.data["id"]

        teacher_client = jwt_client(self.teacher.email)
        response = teacher_client.post(
            f"{API}/course",
            {"name": "School A Biology", "session": self.session_id},
            format="json",
        )
        assert response.status_code == 201, response.content
        self.course_id = response.data["id"]

        response = teacher_client.post(
            f"{API}/course/{self.course_id}/students",
            {"email": "pupil@h38.test"},
            format="json",
        )
        assert response.status_code == 200, response.content
        self.student = CustomUser.objects.get(email="pupil@h38.test")

    def _license(self, school, admin):
        return LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )

    def remove_teacher(self):
        response = self.admin_client.post(
            f"{API}/license-subscriptions/{self.license.id}/remove_teachers",
            {"teacher_ids": [str(self.teacher.id)]},
            format="json",
        )
        assert response.status_code == 200, response.content
        assert response.data["successful"] == 1, response.content
        self.teacher.refresh_from_db()


def note(site, verb, response, changed):
    print(f"H38P2 {site} {verb} status={response.status_code} changed={changed}")


def fund_wallet(user):
    """A large, live MONTHLY bucket in `user`'s wallet.

    `remove_teachers` expires the removed teacher's credit buckets, so on a
    credit-gated route the billing gate (HasCreditBalance -> 402
    insufficient_credits, beta f7cd15e) refuses before any H-38 access check
    runs, and a test that stops there proves billing, not H-38. Funding the
    wallet gets the request past billing to the course-access guard. It is
    also the positive controls' fixture: 500k clears the AI estimator's ~20k
    baseline that the 20k license allocation alone does not."""
    wallet, _ = CreditWallet.objects.get_or_create(user=user)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=500_000,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=25),
    )


class RemovedTeacherRoutesBase(TeacherRemovalBase):
    """The base scaffold, plus an assignment, a graded submission and a topic
    inside School A's course, and then the teacher's removal."""

    def setUp(self):
        super().setUp()
        self.assignment = Assignment.objects.create(
            title="School A quiz",
            course_id=self.course_id,
            total_points=10,
            due_date=timezone.now() + timedelta(days=7),
            status=AssignmentStatus.PUBLISHED,
        )
        self.submission = StudentSubmission.objects.create(
            student=self.student,
            assignment=self.assignment,
            answers={},
            score=Decimal("7.00"),
            graded_at=timezone.now(),
            feedback={
                "grading_summary": {
                    "total_score": 7,
                    "max_total_points": 10,
                    "percentage": 70,
                },
                "grading_confidence": 0.9,
            },
        )
        self.topic = Topic.objects.create(name="Cells", course_id=self.course_id)
        self.remove_teacher()
        self.client_t = jwt_client(self.teacher.email)

    def reload(self, obj):
        obj.refresh_from_db()
        return obj


class RemovedTeacherAssignmentRouteTests(RemovedTeacherRoutesBase):
    def test_list_assignments(self):
        response = self.client_t.get(f"{API}/assignments", {"page_size": 100})
        leaked = str(self.assignment.id) in response.content.decode()
        note("assignments/views.py:310 list", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_retrieve_assignment(self):
        response = self.client_t.get(f"{API}/assignments/{self.assignment.id}")
        leaked = response.status_code == 200
        note("assignments/views.py:310 retrieve", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_patch_assignment(self):
        response = self.client_t.patch(
            f"{API}/assignments/{self.assignment.id}",
            {"title": "Hijacked"},
            format="json",
        )
        changed = self.reload(self.assignment).title != "School A quiz"
        note("assignments/views.py:310 patch", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_delete_assignment(self):
        response = self.client_t.delete(f"{API}/assignments/{self.assignment.id}")
        gone = not Assignment.objects.filter(pk=self.assignment.pk).exists()
        note("assignments/views.py:310 delete", "DELETE", response, gone)
        self.assertFalse(gone)

    def test_upload_assignment(self):
        before = Assignment.objects.count()
        response = self.client_t.post(
            f"{API}/assignments/upload", {"course": self.course_id}, format="multipart"
        )
        note(
            "assignments/views.py:776 upload",
            "WRITE",
            response,
            Assignment.objects.count() != before,
        )
        self.assertIn(response.status_code, (402, 403, 404), response.content)

    def test_upload_assignment_async(self):
        # Funded, so HasCreditBalance passes and the request reaches the
        # H-38 guard (A2b: get_object_or_404(reachable_courses(user), ...)).
        # Unfunded, the 402 from the billing gate is all this could prove.
        fund_wallet(self.teacher)
        before = BatchUploadSession.objects.count()
        response = self.client_t.post(
            f"{API}/assignments/upload-async",
            {"course": self.course_id},
            format="multipart",
        )
        note(
            "assignments/views.py:966 upload-async",
            "WRITE",
            response,
            BatchUploadSession.objects.count() != before,
        )
        # Strict: the guard's own 404. With A2b reverted the request gets
        # past it and stops at "No files were uploaded" (400) instead.
        self.assertEqual(response.status_code, 404, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertEqual(BatchUploadSession.objects.count(), before)

    def test_generate_assignment_from_prompt(self):
        response = self.client_t.post(
            f"{API}/assignments/generate/{self.course_id}",
            {"prompt": "Make a quiz on cells"},
            format="json",
        )
        note("assignments/views.py:1232 generate", "WRITE(paid AI)", response, False)
        self.assertIn(response.status_code, (402, 403, 404), response.content)

    def test_associate_topic(self):
        response = self.client_t.patch(
            f"{API}/assignments/{self.assignment.id}/associate-topic"
            f"?topic_id={self.topic.id}",
            format="json",
        )
        changed = self.reload(self.assignment).topic_id is not None
        note("assignments/views.py associate-topic", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_grade_all(self):
        # Funded past HasCreditBalance, so the refusal is the H-38 guard:
        # get_object() over AssignmentViewSet.get_queryset's
        # teacher_course_access_q filter (A1).
        fund_wallet(self.teacher)
        before = BatchUploadSession.objects.count()
        response = self.client_t.post(
            f"{API}/assignments/{self.assignment.id}/grade-all", {}, format="json"
        )
        note(
            "assignments/views.py grade-all",
            "WRITE(paid AI)",
            response,
            BatchUploadSession.objects.count() != before,
        )
        # Strict: the guard's 404. With A1 reverted, get_object() returns the
        # assignment and the view answers 400 "No ungraded submissons".
        self.assertEqual(response.status_code, 404, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertEqual(BatchUploadSession.objects.count(), before)

    def test_publish_all_grades(self):
        response = self.client_t.post(
            f"{API}/assignments/{self.assignment.id}/publish-all-grades",
            {},
            format="json",
        )
        changed = self.reload(self.submission).is_published
        note("assignments/views.py publish-all-grades", "WRITE", response, changed)
        self.assertFalse(changed)


class RemovedTeacherSubmissionRouteTests(RemovedTeacherRoutesBase):
    def test_list_submissions(self):
        response = self.client_t.get(f"{API}/submissions", {"page_size": 100})
        leaked = str(self.submission.id) in response.content.decode()
        note("students/views.py:358 list", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_retrieve_submission(self):
        response = self.client_t.get(f"{API}/submissions/{self.submission.id}")
        leaked = response.status_code == 200
        note("students/views.py:358 retrieve", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_update_grade(self):
        response = self.client_t.patch(
            f"{API}/submissions/{self.submission.id}/update-grade",
            {"score": 1},
            format="json",
        )
        changed = self.reload(self.submission).score != Decimal("7.00")
        note("students/views.py update-grade", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_teacher_feedback(self):
        response = self.client_t.get(
            f"{API}/submissions/{self.submission.id}/teacher_feedback"
        )
        leaked = response.status_code == 200
        note("students/views.py teacher_feedback", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_publish_grade(self):
        response = self.client_t.post(
            f"{API}/submissions/{self.submission.id}/publish", {}, format="json"
        )
        published = self.reload(self.submission).is_published
        note("students/views.py publish", "WRITE", response, published)
        self.assertFalse(published)

    def test_delete_submission(self):
        response = self.client_t.delete(f"{API}/submissions/{self.submission.id}")
        gone = not StudentSubmission.objects.filter(pk=self.submission.pk).exists()
        note("students/views.py:358 delete", "DELETE", response, gone)
        self.assertFalse(gone)

    def test_grade_paid_ai(self):
        # Funded past HasCreditBalance, so the refusal is the H-38 guard:
        # get_object() over StudentSubmissionViewSet.get_queryset's
        # teacher_course_access_q filter (S2). The provider is stubbed so a
        # regressed guard can never reach a real model.
        fund_wallet(self.teacher)
        with patch(
            "ai_processor.services.AIProcessor._AIProcessor__ai_model",
            return_value=fake_model_response(),
        ) as model:
            response = self.client_t.post(
                f"{API}/submissions/{self.submission.id}/grade", {}, format="json"
            )
        changed = self.reload(self.submission).score != Decimal("7.00")
        note("students/views.py grade", "WRITE(paid AI)", response, changed)
        self.assertEqual(response.status_code, 404, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertFalse(model.called)
        self.assertFalse(changed)

    def test_submission_upload_to_the_school_assignment(self):
        before = StudentSubmission.objects.count()
        response = self.client_t.post(
            f"{API}/submissions/{self.assignment.id}/upload", {}, format="multipart"
        )
        note(
            "students/views.py:158 upload",
            "WRITE",
            response,
            StudentSubmission.objects.count() != before,
        )
        self.assertIn(response.status_code, (402, 403, 404), response.content)


class RemovedTeacherClassroomRouteTests(RemovedTeacherRoutesBase):
    def test_list_topics(self):
        response = self.client_t.get(f"{API}/topics", {"page_size": 100})
        leaked = str(self.topic.id) in response.content.decode()
        note("classrooms/views.py:2392 topics list", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_create_topic_in_the_school_course(self):
        before = Topic.objects.filter(course_id=self.course_id).count()
        response = self.client_t.post(
            f"{API}/topics",
            {"name": "Injected", "course": self.course_id},
            format="json",
        )
        changed = Topic.objects.filter(course_id=self.course_id).count() != before
        note("classrooms/views.py topics create", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_patch_topic(self):
        response = self.client_t.patch(
            f"{API}/topics/{self.topic.id}", {"name": "Hijacked"}, format="json"
        )
        changed = self.reload(self.topic).name != "Cells"
        note("classrooms/views.py:2392 topic patch", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_delete_topic(self):
        response = self.client_t.delete(f"{API}/topics/{self.topic.id}")
        gone = not Topic.objects.filter(pk=self.topic.pk).exists()
        note("classrooms/views.py:2392 topic delete", "DELETE", response, gone)
        self.assertFalse(gone)

    def test_create_topic_through_the_course_action(self):
        before = Topic.objects.filter(course_id=self.course_id).count()
        response = self.client_t.post(
            f"{API}/course/{self.course_id}/topics",
            ["Injected via course"],
            format="json",
        )
        changed = Topic.objects.filter(course_id=self.course_id).count() != before
        note("classrooms/views.py:1723 course topics", "WRITE", response, changed)
        self.assertFalse(changed)

    def test_student_course_list(self):
        response = self.client_t.get(f"{API}/student-course", {"page_size": 100})
        leaked = str(self.student.id) in response.content.decode()
        note("classrooms/views.py:2099 student-course list", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_my_students(self):
        response = self.client_t.get(f"{API}/student-course/my-students")
        leaked = self.student.email in response.content.decode()
        note("classrooms/views.py:2041-2082 my-students", "READ", response, leaked)
        self.assertFalse(leaked)

    def test_student_course_delete(self):
        enrollment = StudentCourse.objects.get(
            student=self.student, course_id=self.course_id
        )
        response = self.client_t.delete(f"{API}/student-course/{enrollment.id}")
        gone = not StudentCourse.objects.filter(pk=enrollment.pk).exists()
        note("classrooms/views.py:2099 student-course delete", "DELETE", response, gone)
        self.assertFalse(gone)

    def test_student_course_patch(self):
        enrollment = StudentCourse.objects.get(
            student=self.student, course_id=self.course_id
        )
        original_status = enrollment.enrollment_status
        response = self.client_t.patch(
            f"{API}/student-course/{enrollment.id}",
            {"enrollment_status": "WITHDRAWN"},
            format="json",
        )
        changed = self.reload(enrollment).enrollment_status != original_status
        note(
            "classrooms/views.py:2099 student-course patch", "WRITE", response, changed
        )
        self.assertFalse(changed)


class RemovedTeacherDashboardRouteTests(RemovedTeacherRoutesBase):
    def _read(self, site, path, marker):
        response = self.client_t.get(f"{API}{path}")
        leaked = response.status_code == 200 and (
            marker is None or marker in response.content.decode()
        )
        note(site, "READ", response, leaked)
        self.assertFalse(leaked)

    def test_dashboard_course(self):
        self._read(
            "dashboard/views.py teacher courses/{id}",
            f"/teacher-admin/dashboard/courses/{self.course_id}",
            None,
        )

    def test_dashboard_students(self):
        self._read(
            "dashboard/views.py teacher students/{course}",
            f"/teacher-admin/dashboard/students/{self.course_id}",
            str(self.student.id),
        )

    def test_dashboard_assignment(self):
        self._read(
            "dashboard/views.py teacher assignments/{id}",
            f"/teacher-admin/dashboard/assignments/{self.assignment.id}",
            "School A quiz",
        )

    def test_dashboard_overview(self):
        self._read(
            "dashboard/views.py teacher overview/{session}",
            f"/teacher-admin/dashboard/overview/{self.session_id}",
            "School A Biology",
        )

    def test_custom_ai_prompt_on_the_school_course(self):
        spy = CustomAIPromptContextSpy()
        with patch(
            "ai_processor.services.AIProcessor._AIProcessor__ai_model",
            return_value=fake_model_response(),
        ) as model, patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry", side_effect=spy
        ):
            response = self.client_t.post(
                CUSTOM_AI_PROMPT_URL,
                {"prompt": "Summarise", "course_id": self.course_id},
                format="json",
            )
        leaked = any("School A Biology" in context for context in spy.contexts)
        note("dashboard/views.py custom-ai-prompt", "READ(paid AI)", response, leaked)
        # Strict. The removal deactivated the teacher's license allocation,
        # so the AI access gate (billing/access_control.py
        # can_user_access_ai -> "No active subscription") refuses with
        # AIFeatureNotAvailableError, which run_dashboard_ai_chat maps to
        # 403 "ai_feature_not_available" (billing/refusals.py, beta
        # f7cd15e). Before f7cd15e that same refusal fell into the view's
        # `except Exception` and answered an explicit 500 - the 500 this
        # test used to tolerate with a loose "did not succeed" check. See
        # docs/evidence/h38_part2/custom_ai_prompt_500_triage.md.
        self.assertEqual(response.status_code, 403, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["code"],
            "ai_feature_not_available",
            response.content,
        )
        self.assertFalse(model.called)
        # The H-38 part of this route. The route ignores `course_id`; what
        # it reads is TeacherAIContextService.build(request.user), which is
        # built BEFORE the billing gate runs. On beta that context still
        # carried School A's course for the removed teacher (so one with an
        # individual subscription of their own would have had it sent to
        # the model); reachable_courses() now drops it. The positive
        # control below proves this spy does see the course when it is
        # reachable, so the assertion cannot pass vacuously.
        self.assertEqual(len(spy.contexts), 1)
        self.assertFalse(leaked, spy.contexts[0])


CUSTOM_AI_PROMPT_URL = f"{API}/teacher-admin/dashboard/custom-ai-prompt"


def fake_model_response(content="Stub dashboard answer."):
    """The provider response shape execute_graded_task reads: the reply at
    choices[0].message.content and the metered size at usage.total_tokens.
    Patched in at the provider boundary (AIProcessor.__ai_model), so the
    real access gate and credit metering still run - no network call."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = 100
    return response


class CustomAIPromptContextSpy:
    """Records the context the teacher custom-AI-prompt view hands the AI
    layer, then calls the real custom_ai_prompt_retry."""

    def __init__(self):
        self.contexts = []
        # The exact object the view calls, captured before it is patched.
        self._real = dashboard_views.ai_processor.custom_ai_prompt_retry

    def __call__(self, user, context, question, role, **kwargs):
        self.contexts.append(context)
        return self._real(user, context, question, role, **kwargs)


class ActiveTeacherCustomAIPromptTests(TeacherRemovalBase):
    """Positive control for RemovedTeacherDashboardRouteTests
    .test_custom_ai_prompt_on_the_school_course. An ACTIVE teacher on the
    same School A course, through the same real access gate and credit
    metering (only the provider call is stubbed), gets a normal 200 whose
    context contains the course - so the removed teacher's 403 is the gate
    refusing them, not the route being broken for everyone, and the
    removed-teacher context assertion is not vacuous."""

    def setUp(self):
        super().setUp()
        self.client_t = jwt_client(self.teacher.email)
        # The license allocation alone (20,000) is below the estimator's
        # fixed ~20k baseline plus the prompt, which answers 402 - the same
        # funded-wallet fixture dashboard/tests_real_ai_chat.py uses.
        fund_wallet(self.teacher)

    def test_custom_ai_prompt_succeeds_for_active_teacher(self):
        spy = CustomAIPromptContextSpy()
        with patch(
            "ai_processor.services.AIProcessor._AIProcessor__ai_model",
            return_value=fake_model_response(),
        ) as model, patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry", side_effect=spy
        ):
            response = self.client_t.post(
                CUSTOM_AI_PROMPT_URL,
                {"prompt": "Summarise", "course_id": self.course_id},
                format="json",
            )

        note(
            "dashboard/views.py custom-ai-prompt (positive control - active teacher)",
            "READ(paid AI)",
            response,
            False,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()["data"]["response"], "Stub dashboard answer.")
        self.assertTrue(model.called)
        self.assertEqual(len(spy.contexts), 1)
        self.assertIn("School A Biology", spy.contexts[0])


def make_ai_draft_message(teacher, course_id):
    session = AssignmentGenerationSession.objects.create(
        user=teacher, course_id=course_id
    )
    return AssignmentGenerationMessage.objects.create(
        session=session,
        role=AssignmentGenerationRole.ASSISTANT,
        content="Here is a draft assignment on cells.",
        assignment_snapshot={
            "title": "Cell Biology Quiz",
            "instructions": "Answer every question.",
            "total_points": 10,
            "question_count": 1,
            "assignment_type": "OBJECTIVE",
            "questions": [
                {
                    "question_number": 1,
                    "question_text": "What is the powerhouse of the cell?",
                    "question_type": "OBJECTIVE",
                    "question_image": "",
                    "points": 10,
                    "blooms_level": "Remember",
                    "options": ["Mitochondria", "Nucleus", "Ribosome"],
                    "rubric": [],
                    "model_answer": "Mitochondria",
                }
            ],
            "potential_issues": [],
            "self_assessment": "A focused objective check.",
            "extraction_confidence": 95,
        },
        metadata={"draft_status": "AI_DRAFT"},
    )


class RemovedTeacherDraftSaveTests(RemovedTeacherRoutesBase):
    """H-38 part 2 mutation testing (docs/evidence/h38_part2/mutation_results_final.md,
    A3_draft_save) found `save_generated_assignment_draft`
    (`generated-drafts/{message_id}/save`) had zero dynamic route coverage for
    the removed-teacher scenario - only the static sweep guard protected it.
    This closes that gap. `@require_ai_access` is commented out on the action
    itself, so there is no credit gate to route around here; the draft-save
    guard is directly reachable.

    Separately (found while writing this test, unrelated to H-38 itself, and
    now fixed): the live H-38 query combined `.select_for_update()` with
    `teacher_course_access_q(..., prefix="session__course__")`'s "reachable"
    OR-clause, which needs a LEFT OUTER JOIN through the nullable
    `Course.session` FK to satisfy its `session__isnull=True` branch. Postgres
    rejects "FOR UPDATE" on the nullable side of an outer join, so the
    endpoint 500'd for EVERY teacher, active or removed - not a
    removed-teacher-specific hole, but a regression the H-38 fix itself
    introduced. A loose "did not succeed" assertion here initially masked
    that 500 as if it were correct removed-teacher-refusal behaviour. The fix
    (assignments/views.py `save_generated_assignment_draft`) now runs the
    access check as its own unlocked query first, then takes the lock with a
    plain by-primary-key `.get()` (never an outer join). The assertion below
    is now strict (404) with an explicit sub-500 guard so this class of bug
    cannot hide behind a loose assertion again; see
    `ActiveTeacherDraftSaveTests` below for the positive control that would
    have caught the regression immediately."""

    def test_save_generated_assignment_draft_refuses_removed_teacher(self):
        draft_message = make_ai_draft_message(self.teacher, self.course_id)
        before = Assignment.objects.count()

        response = self.client_t.post(
            f"{API}/assignments/generated-drafts/{draft_message.id}/save",
            {},
            format="json",
        )

        changed = Assignment.objects.count() != before
        note(
            "assignments/views.py:1461 generated-drafts save",
            "WRITE",
            response,
            changed,
        )
        # Strict: get_object_or_404's Http404 maps to DRF's NotFound, i.e.
        # 404, for this lookup shape (no PermissionDenied is raised here).
        # The explicit sub-500 guard is what would have caught the
        # select_for_update/outer-join regression documented in the class
        # docstring - it was previously masked by an assertNotIn(...,
        # (200, 201, 202, 204)) check that treated a 500 as "refused".
        self.assertEqual(response.status_code, 404, response.content)
        self.assertLess(response.status_code, 500, response.content)
        self.assertFalse(changed)
        draft_message.refresh_from_db()
        self.assertIsNone(draft_message.assignment_id)


class ActiveTeacherDraftSaveTests(TeacherRemovalBase):
    """Positive control for A3_draft_save (see RemovedTeacherDraftSaveTests
    above). An ACTIVE teacher - never removed - must still be able to save
    their own AI draft. This is the test that would have caught the
    select_for_update/outer-join regression immediately: an active teacher
    being unable to save is obviously wrong, unlike the removed-teacher case
    above where a non-2xx status was plausible for either "correctly
    refused" or "broken for everyone"."""

    def setUp(self):
        super().setUp()
        self.client_t = jwt_client(self.teacher.email)

    def test_save_generated_assignment_draft_succeeds_for_active_teacher(self):
        draft_message = make_ai_draft_message(self.teacher, self.course_id)
        before = Assignment.objects.count()

        response = self.client_t.post(
            f"{API}/assignments/generated-drafts/{draft_message.id}/save",
            {},
            format="json",
        )

        note(
            "assignments/views.py:1461 generated-drafts save "
            "(positive control - active teacher)",
            "WRITE",
            response,
            Assignment.objects.count() != before,
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Assignment.objects.count(), before + 1)
        draft_message.refresh_from_db()
        self.assertIsNotNone(draft_message.assignment_id)


class RemovedTeacherPdfTeacherViewTests(RemovedTeacherRoutesBase):
    """H-38 part 2 mutation testing (mutation_results_final.md,
    A4_pdf_teacher_view) found `download_pdf`'s `?view=teacher` branch had
    zero dynamic route coverage for a removed teacher - the existing
    `assignments.tests_security.TeacherViewLeakTest` only proves cross-tenant
    isolation between two *different* teachers, never a teacher who WAS
    `course.teacher` and lost school membership. This closes that gap."""

    def _give_the_assignment_questions(self):
        self.assignment.questions = [
            {
                "question_number": 1,
                "question_text": "What is the powerhouse of the cell?",
                "question_type": "OBJECTIVE",
                "question_image": "",
                "points": 10,
                "blooms_level": "Remember",
                "options": ["Mitochondria", "Nucleus", "Ribosome"],
                "rubric": [],
                "model_answer": "Mitochondria",
            }
        ]
        self.assignment.save(update_fields=["questions"])

    def test_download_pdf_teacher_view_refuses_removed_teacher(self):
        self._give_the_assignment_questions()

        response = self.client_t.get(
            f"{API}/assignments/{self.assignment.id}/download-pdf",
            {"view": "teacher"},
        )

        leaked = response.status_code == 200
        note(
            "assignments/views.py:1788 download-pdf teacher view",
            "READ",
            response,
            leaked,
        )
        # A success here is a FileResponse (streaming), which has no
        # `.content` - use status_code, not the body, as the assertion
        # message so a leaked PDF fails cleanly instead of erroring on
        # attribute access.
        self.assertIn(response.status_code, (403, 404), response.status_code)

    def test_download_pdf_teacher_view_refuses_removed_teacher_past_the_object_filter(
        self,
    ):
        """`AssignmentViewSet.get_queryset`'s teacher branch already filters
        by `teacher_course_access_q(..., prefix="course__")` - the exact Q
        twin of the `teacher_can_reach_course` check this action makes at
        assignments/views.py:1788 - so for a removed teacher requesting
        their OWN former course's assignment, `self.get_object()` 404s
        before that line is ever reached (confirmed: the test above still
        passes even with that line reverted to the pre-H-38
        `assignment.course.teacher != request.user`, because the outer
        queryset filter already refuses first). That makes line 1788 a
        redundant, defense-in-depth guard with no HTTP path that exercises
        it in isolation today.

        This test patches `get_queryset` open (as a stand-in for "the outer
        filter has a bug/regresses and lets the row through") so the
        request reaches `download_pdf`'s body with a real assignment
        object, and checks that the `?view=teacher` guard still refuses on
        its own merit rather than relying entirely on the outer filter."""
        self._give_the_assignment_questions()
        unfiltered = Assignment.objects.select_related("course__teacher")

        with patch(
            "assignments.views.AssignmentViewSet.get_queryset",
            return_value=unfiltered,
        ):
            response = self.client_t.get(
                f"{API}/assignments/{self.assignment.id}/download-pdf",
                {"view": "teacher"},
            )

        leaked = response.status_code == 200
        note(
            "assignments/views.py:1788 download-pdf teacher view "
            "(object-level guard in isolation)",
            "READ",
            response,
            leaked,
        )
        self.assertIn(response.status_code, (403, 404), response.status_code)


class RemovedTeacherSchoolAdminSurfaceTests(RemovedTeacherRoutesBase):
    """Negative controls: a removed teacher was never a school admin."""

    def test_school_admin_dashboard_is_closed(self):
        for path in (
            "/school-admin/dashboard/assignment-activity-over-time",
            "/school-admin/dashboard/teachers",
            "/school-admin/dashboard/students",
        ):
            response = self.client_t.get(f"{API}{path}")
            note(f"dashboard/views.py SchoolAdmin {path}", "READ", response, False)
            self.assertIn(response.status_code, (403, 404), path)


# ---------------------------------------------------------------------------
# Sites beta brought in after H-38 part 2 was written (rebase onto 4b902fc):
# H-22's my-students prefetches and filters (b0644ad), H-22's /users
# enrollment filters (d40de69) and H-18's assignment course validator
# (25613d3). Each scoped on `course__teacher=user` / `teacher_id == user.id`,
# which stays true for a removed teacher's old school course. See
# docs/evidence/h38_part2/rebase_sweep_hits.md.
#
# All of them need a student the removed teacher can still LEGITIMATELY see:
# otherwise the outer, already-H-38-scoped queryset drops the student and
# the inner scoping is never consulted. So the teacher also keeps an
# INDIVIDUAL course of their own, and the School A pupil is enrolled in it
# too - the shared-student shape H-22 was about, now across a removal.
# ---------------------------------------------------------------------------

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
UNKNOWN = "00000000-0000-4000-8000-000000000000"
MY_STUDENTS_URL = f"{API}/student-course/my-students"
FAKE_TASK = MagicMock(id="00000000-0000-0000-0000-000000000001")


def _passthrough_extraction(user, assignment, content, **kwargs):
    """Stands in for the billed AI extraction on POST /assignments."""
    return assignment


@override_settings(CACHES=LOCMEM)
class SharedStudentBase(TeacherRemovalBase):
    """School A course with the pupil's grade, description, assignment and
    submission; the teacher's own INDIVIDUAL course with the same pupil; a
    School A colleague's course with the same pupil (H-22's boundary). Then
    the teacher is removed, unless `remove` is False (positive controls)."""

    remove = True

    def setUp(self):
        # /users/<id> caches retrieve per (requester, pk) and ignores the
        # query string, so every filtered probe must run cold. LocMem, so
        # clearing it cannot touch a shared Redis.
        cache.clear()
        self.addCleanup(cache.clear)
        super().setUp()
        Course.objects.filter(pk=self.course_id).update(description="School A syllabus")
        self.school_assignment = Assignment.objects.create(
            title="School A quiz",
            course_id=self.course_id,
            total_points=10,
            status=AssignmentStatus.PUBLISHED,
        )
        self.school_submission = StudentSubmission.objects.create(
            student=self.student,
            assignment=self.school_assignment,
            answers={},
            score=Decimal("9.00"),
            graded_at=timezone.now(),
        )
        # After the submission, whose receiver recalculates final_grade.
        StudentCourse.objects.filter(
            student=self.student, course_id=self.course_id
        ).update(final_grade=Decimal("91.50"))

        own_session = Session.objects.create(
            name="Own term",
            owner_type=SessionOwnerType.INDIVIDUAL,
            teacher=self.teacher,
        )
        self.own_course = Course.objects.create(
            name="Own Chemistry",
            description="Own notes",
            teacher=self.teacher,
            session=own_session,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.own_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.own_assignment = Assignment.objects.create(
            title="Own quiz",
            course=self.own_course,
            total_points=10,
            status=AssignmentStatus.DRAFT,
        )

        colleague = make_user("colleague@h38.test", UserTypes.TEACHER, self.school)
        self.colleague_course = Course.objects.create(
            name="Colleague Physics",
            description="Colleague syllabus",
            teacher=colleague,
            session_id=self.session_id,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.colleague_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        if self.remove:
            self.remove_teacher()
        self.client_t = jwt_client(self.teacher.email)

    # -- my-students ---------------------------------------------------------

    def my_students(self, **params):
        response = self.client_t.get(MY_STUDENTS_URL, params)
        self.assertLess(response.status_code, 500, response.content)
        return response

    def student_row(self, response):
        rows = [
            r for r in response.data["results"] if str(r["id"]) == str(self.student.id)
        ]
        self.assertEqual(len(rows), 1, response.content)
        return rows[0]

    def prefetched(self):
        """The prefetch caches `StudentListSerializer` is handed, read
        directly: the serializer only counts submissions of the row's
        "relevant course", so the submissions prefetch is not observable in
        the payload while the enrollments prefetch is scoped (the same
        technique as H-22's test_prefetch_caches_hold_only_the_teachers_own_rows)."""
        request = APIRequestFactory().get(MY_STUDENTS_URL)
        request.user = self.teacher
        view = StudentCourseViewSet(action="my_students", request=request)
        student = next(s for s in view.get_queryset() if s.pk == self.student.pk)
        return (
            {e.course_id for e in student.enrollments.all()},
            {s.pk for s in student.submissions.all()},
        )

    # -- /users/<id> ---------------------------------------------------------

    def user_detail(self, **params):
        cache.clear()
        response = self.client_t.get(
            reverse("user-detail", kwargs={"pk": self.student.pk}), params
        )
        self.assertLess(response.status_code, 500, response.content)
        return response

    # -- assignment course validator ----------------------------------------

    def create_payload(self, title):
        return {
            "course": str(self.course_id),
            "raw_input": f"Q1. {title}",
            "title": title,
            "status": "PUBLISHED",
        }


class RemovedTeacherSharedStudentTests(SharedStudentBase):
    # classrooms/views.py my_students: enrollments prefetch
    def test_my_students_row_names_only_the_own_course(self):
        response = self.my_students()
        body = response.content.decode()
        leaked = "School A Biology" in body or "School A syllabus" in body
        note(
            "classrooms/views.py my-students enrollments prefetch",
            "READ",
            response,
            leaked,
        )
        self.assertEqual(response.status_code, 200, response.content)
        row = self.student_row(response)
        self.assertEqual(row["enrolled_courses"], ["Own Chemistry"])
        self.assertFalse(leaked, body)

    # classrooms/views.py my_students: both prefetches, below the serializer
    def test_my_students_prefetch_caches_hold_no_school_rows(self):
        courses, submissions = self.prefetched()
        self.assertEqual(courses, {self.own_course.id})
        self.assertEqual(submissions, set())

    # classrooms/filters.py MyStudentsFilter (course)
    def test_my_students_course_filter_naming_the_school_course_returns_no_rows(self):
        response = self.my_students(enrollments__course=self.course_id)
        body = response.content.decode()
        leaked = response.status_code == 200 and response.data["count"] != 0
        note(
            "classrooms/filters.py my-students ?enrollments__course",
            "READ",
            response,
            leaked,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 0, body)
        for marker in ("School A Biology", "School A syllabus", "91.50"):
            self.assertNotIn(marker, body)

    # classrooms/filters.py MyStudentsFilter (session)
    def test_my_students_session_filter_naming_the_school_session_returns_no_rows(self):
        response = self.my_students(enrollments__course__session=self.session_id)
        leaked = response.status_code == 200 and response.data["count"] != 0
        note(
            "classrooms/filters.py my-students ?enrollments__course__session",
            "READ",
            response,
            leaked,
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 0, response.content)

    # users/filters.py visible_enrollments (course)
    def test_user_detail_course_filter_is_not_an_oracle_on_the_school_course(self):
        # The student IS reachable (via the own course), so a 404 below is
        # the filter refusing, not the target being invisible.
        self.assertEqual(self.user_detail().status_code, 200)
        probe = self.user_detail(enrollments__course=self.course_id)
        control = self.user_detail(enrollments__course=UNKNOWN)
        leaked = probe.status_code == 200
        note("users/filters.py /users/<id>?enrollments__course", "READ", probe, leaked)
        self.assertEqual(probe.status_code, 404, probe.content)
        self.assertEqual(
            (probe.status_code, probe.data), (control.status_code, control.data)
        )

    # users/filters.py visible_enrollments (session)
    def test_user_detail_session_filter_is_not_an_oracle_on_the_school_session(self):
        self.assertEqual(self.user_detail().status_code, 200)
        probe = self.user_detail(enrollments__course__session=self.session_id)
        control = self.user_detail(enrollments__course__session=UNKNOWN)
        leaked = probe.status_code == 200
        note(
            "users/filters.py /users/<id>?enrollments__course__session",
            "READ",
            probe,
            leaked,
        )
        self.assertEqual(probe.status_code, 404, probe.content)
        self.assertEqual(
            (probe.status_code, probe.data), (control.status_code, control.data)
        )

    # assignments/serializers.py AssignmentTextSerializer.validate_course
    @patch(
        "assignments.views.AssignmentProcessingService.update_assignment_from_extraction",
        side_effect=_passthrough_extraction,
    )
    def test_create_assignment_in_the_school_course(self, mock_extract):
        response = self.client_t.post(
            f"{API}/assignments", self.create_payload("PLANTED"), format="json"
        )
        changed = Assignment.objects.filter(title="PLANTED").exists()
        note(
            "assignments/serializers.py validate_course create",
            "WRITE",
            response,
            changed,
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("course", response.content.decode())
        self.assertFalse(changed)
        mock_extract.assert_not_called()

    def test_create_async_assignment_in_the_school_course(self):
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client_t.post(
                reverse("assignment-create-async"),
                self.create_payload("PLANTED ASYNC"),
                format="json",
            )
        changed = Assignment.objects.filter(title="PLANTED ASYNC").exists()
        note(
            "assignments/serializers.py validate_course create-async",
            "WRITE",
            response,
            changed,
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertIn("course", response.content.decode())
        self.assertFalse(changed)
        mock_launch.assert_not_called()

    def test_patch_own_assignment_into_the_school_course(self):
        response = self.client_t.patch(
            f"{API}/assignments/{self.own_assignment.id}",
            {"course": str(self.course_id)},
            format="json",
        )
        changed = self.reload_course_id(self.own_assignment) != self.own_course.id
        note(
            "assignments/serializers.py validate_course patch",
            "WRITE",
            response,
            changed,
        )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(changed)

    def reload_course_id(self, obj):
        obj.refresh_from_db()
        return obj.course_id


class ActiveTeacherSharedStudentTests(SharedStudentBase):
    """Positive controls: the same fixture without the removal. The active
    teacher still sees School A data through every rewritten site, and still
    never the colleague's course (H-22's boundary)."""

    remove = False

    def test_my_students_row_names_both_own_courses_but_not_the_colleagues(self):
        response = self.my_students()
        self.assertEqual(response.status_code, 200, response.content)
        row = self.student_row(response)
        self.assertCountEqual(
            row["enrolled_courses"], ["School A Biology", "Own Chemistry"]
        )
        self.assertNotIn("Colleague", response.content.decode())

    def test_my_students_prefetch_caches_hold_the_school_rows(self):
        courses, submissions = self.prefetched()
        self.assertEqual(courses, {self.course_id_uuid(), self.own_course.id})
        self.assertEqual(submissions, {self.school_submission.pk})

    def test_my_students_course_filter_selects_the_school_course(self):
        response = self.my_students(enrollments__course=self.course_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 1, response.content)
        row = self.student_row(response)
        self.assertEqual(row["course_description"], "School A syllabus")
        self.assertEqual(Decimal(str(row["grade"]["percentage"])), Decimal("91.50"))
        self.assertEqual(row["total_assignments_submitted"], 1)

    def test_my_students_session_filter_selects_the_student(self):
        response = self.my_students(enrollments__course__session=self.session_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 1, response.content)

    def test_my_students_colleague_course_filter_still_returns_no_rows(self):
        response = self.my_students(enrollments__course=self.colleague_course.id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["count"], 0, response.content)

    def test_user_detail_course_filter_finds_the_student(self):
        response = self.user_detail(enrollments__course=self.course_id)
        self.assertEqual(response.status_code, 200, response.content)

    def test_user_detail_session_filter_finds_the_student(self):
        response = self.user_detail(enrollments__course__session=self.session_id)
        self.assertEqual(response.status_code, 200, response.content)

    def test_user_detail_colleague_course_filter_is_still_404(self):
        response = self.user_detail(enrollments__course=self.colleague_course.id)
        self.assertEqual(response.status_code, 404, response.content)

    def test_create_async_assignment_in_the_school_course(self):
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client_t.post(
                reverse("assignment-create-async"),
                self.create_payload("Legit async"),
                format="json",
            )
        self.assertEqual(response.status_code, 202, response.content)
        created = Assignment.objects.get(title="Legit async")
        self.assertEqual(str(created.course_id), str(self.course_id))
        mock_launch.assert_called_once()

    def test_patch_own_assignment_into_the_school_course(self):
        response = self.client_t.patch(
            f"{API}/assignments/{self.own_assignment.id}",
            {"course": str(self.course_id)},
            format="json",
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.own_assignment.refresh_from_db()
        self.assertEqual(str(self.own_assignment.course_id), str(self.course_id))

    def test_create_async_in_the_colleagues_course_is_still_refused(self):
        payload = self.create_payload("Colleague plant")
        payload["course"] = str(self.colleague_course.id)
        with patch("assignments.views.launch_processing_task", return_value=FAKE_TASK):
            response = self.client_t.post(
                reverse("assignment-create-async"), payload, format="json"
            )
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(Assignment.objects.filter(title="Colleague plant").exists())

    def course_id_uuid(self):
        return Course.objects.get(pk=self.course_id).pk
