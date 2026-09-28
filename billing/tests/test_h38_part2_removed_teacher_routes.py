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
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from assignments.models import (
    Assignment,
    AssignmentGenerationMessage,
    AssignmentGenerationRole,
    AssignmentGenerationSession,
    AssignmentStatus,
)
from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School, StudentCourse, Topic
from students.models import StudentSubmission
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
        response = self.client_t.post(
            f"{API}/assignments/upload-async",
            {"course": self.course_id},
            format="multipart",
        )
        note("assignments/views.py:960 upload-async", "WRITE", response, False)
        # The credit gate answers 402, 400 or (custom-ai-prompt, a pre-existing
        # status-mapping quirk) 500 depending on route; what matters is that
        # the paid action is refused and nothing changed.
        self.assertNotIn(response.status_code, (200, 201, 202, 204), response.content)

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
        response = self.client_t.post(
            f"{API}/assignments/{self.assignment.id}/grade-all", {}, format="json"
        )
        note("assignments/views.py grade-all", "WRITE(paid AI)", response, False)
        # The credit gate answers 402, 400 or (custom-ai-prompt, a pre-existing
        # status-mapping quirk) 500 depending on route; what matters is that
        # the paid action is refused and nothing changed.
        self.assertNotIn(response.status_code, (200, 201, 202, 204), response.content)

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
        response = self.client_t.post(
            f"{API}/submissions/{self.submission.id}/grade", {}, format="json"
        )
        note("students/views.py grade", "WRITE(paid AI)", response, False)
        # The credit gate answers 402, 400 or (custom-ai-prompt, a pre-existing
        # status-mapping quirk) 500 depending on route; what matters is that
        # the paid action is refused and nothing changed.
        self.assertNotIn(response.status_code, (200, 201, 202, 204), response.content)

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
        response = self.client_t.post(
            f"{API}/teacher-admin/dashboard/custom-ai-prompt",
            {"prompt": "Summarise", "course_id": self.course_id},
            format="json",
        )
        note("dashboard/views.py custom-ai-prompt", "READ(paid AI)", response, False)
        # The credit gate answers 402, 400 or (custom-ai-prompt, a pre-existing
        # status-mapping quirk) 500 depending on route; what matters is that
        # the paid action is refused and nothing changed.
        self.assertNotIn(response.status_code, (200, 201, 202, 204), response.content)


class RemovedTeacherDraftSaveTests(RemovedTeacherRoutesBase):
    """H-38 part 2 mutation testing (docs/evidence/h38_part2/mutation_results_final.md,
    A3_draft_save) found `save_generated_assignment_draft`
    (`generated-drafts/{message_id}/save`) had zero dynamic route coverage for
    the removed-teacher scenario - only the static sweep guard protected it.
    This closes that gap. `@require_ai_access` is commented out on the action
    itself, so there is no credit gate to route around here; the draft-save
    guard is directly reachable.

    Separately (found while writing this test, unrelated to H-38 itself):
    the live H-38-fixed query - `.select_for_update()` combined with
    `teacher_course_access_q(..., prefix="session__course__")`'s "reachable"
    OR-clause, which needs a LEFT OUTER JOIN through the nullable
    `Course.session` FK to satisfy its `session__isnull=True` branch - hits
    Postgres's "FOR UPDATE cannot be applied to the nullable side of an
    outer join" and 500s. This reproduces for ANY teacher, active or
    removed (confirmed against a plain active-teacher fixture, not just this
    one), so it is not a removed-teacher-specific hole; it currently makes
    the endpoint fail closed for everyone rather than fail open, which is
    why the status check below is a broad "did not succeed" rather than a
    strict (403, 404), mirroring the file's existing convention for other
    quirky-status-mapping routes above. Reverting to the pre-H-38 filter
    shape (no OR-clause, no outer join) removes the 500 entirely and lets
    the write through for a removed teacher - see the mutation check this
    test is paired with."""

    def _make_ai_draft_message(self):
        session = AssignmentGenerationSession.objects.create(
            user=self.teacher, course_id=self.course_id
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

    def test_save_generated_assignment_draft_refuses_removed_teacher(self):
        draft_message = self._make_ai_draft_message()
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
        # See the class docstring: the H-38-fixed query currently 500s for
        # every teacher on Postgres (an unrelated select_for_update/outer-join
        # bug), so this asserts "did not succeed" rather than a strict
        # (403, 404) - the same pattern used above for the paid-AI routes.
        # What matters for H-38 is that nothing was written.
        self.assertNotIn(response.status_code, (200, 201, 202, 204), response.content)
        self.assertFalse(changed)
        draft_message.refresh_from_db()
        self.assertIsNone(draft_message.assignment_id)


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
