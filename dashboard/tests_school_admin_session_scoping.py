"""Regression: school-admin dashboard endpoints accept an optional
`?session_id=` to scope their data to one of the school's own sessions,
mirroring the teacher's existing per-session dashboard
(`dashboard/overview/<session_id>`). Omitted, behaviour is unchanged - the
whole school history, as before this parameter existed (2026-09-22 review
decision)."""

from django.test import TestCase
from rest_framework import status as http_status
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    SessionOwnerType,
    StudentCourse,
)
from users.models import CustomUser, UserTypes

TEST_PASSWORD = "password123"  # pragma: allowlist secret


def make_school_admin(email, school):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.SCHOOL_ADMIN
    user.is_active = True
    user.school = school
    user.save()
    return user


def make_teacher(email, school):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.TEACHER
    user.is_active = True
    user.school = school
    user.save()
    return user


def make_student(email):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.STUDENT
    user.is_active = True
    user.save()
    return user


def make_school_session(school, name):
    return Session.objects.create(
        name=name, owner_type=SessionOwnerType.SCHOOL, school=school
    )


class SessionScopingResolverTest(TestCase):
    """Validation behaviour of the shared `session_id` resolver, exercised
    through `dashboard/course-performance` (the simplest consumer)."""

    def setUp(self):
        self.school = School.objects.create(name="Resolver School")
        self.other_school = School.objects.create(name="Other School")
        self.admin = make_school_admin("resolver-admin@example.com", self.school)
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_omitted_session_id_returns_all_history(self):
        response = self.client.get("/api/v1/school-admin/dashboard/course-performance")
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )

    def test_unknown_session_id_is_rejected(self):
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-performance",
            {"session_id": "00000000-0000-0000-0000-000000000000"},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_400_BAD_REQUEST, response.content
        )

    def test_malformed_session_id_is_rejected_not_500(self):
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-performance",
            {"session_id": "not-a-uuid"},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_400_BAD_REQUEST, response.content
        )

    def test_another_schools_session_is_rejected(self):
        foreign_session = make_school_session(self.other_school, "Foreign Term")
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-performance",
            {"session_id": str(foreign_session.id)},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_400_BAD_REQUEST, response.content
        )

    def test_individual_owner_type_session_is_rejected(self):
        teacher = make_teacher("resolver-teacher@example.com", self.school)
        individual_session = Session.objects.create(
            name="My Own Term",
            owner_type=SessionOwnerType.INDIVIDUAL,
            teacher=teacher,
        )
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-performance",
            {"session_id": str(individual_session.id)},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_400_BAD_REQUEST, response.content
        )


class CoursePerformanceSessionScopingTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Course Scoping School")
        self.admin = make_school_admin("course-scope-admin@example.com", self.school)
        self.teacher = make_teacher("course-scope-teacher@example.com", self.school)
        self.session_a = make_school_session(self.school, "Term A")
        self.session_b = make_school_session(self.school, "Term B")
        self.course_a = Course.objects.create(
            name="Course A", teacher=self.teacher, session=self.session_a
        )
        self.course_b = Course.objects.create(
            name="Course B", teacher=self.teacher, session=self.session_b
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_session_id_filters_to_only_that_sessions_courses(self):
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-performance",
            {"session_id": str(self.session_a.id)},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Course A"})

    def test_omitted_session_id_returns_every_session(self):
        response = self.client.get("/api/v1/school-admin/dashboard/course-performance")
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"Course A", "Course B"})


class SummarySessionScopingTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Summary Scoping School")
        self.admin = make_school_admin("summary-scope-admin@example.com", self.school)
        self.teacher = make_teacher("summary-scope-teacher@example.com", self.school)
        self.session_a = make_school_session(self.school, "Term A")
        self.session_b = make_school_session(self.school, "Term B")
        self.course_a = Course.objects.create(
            name="Course A", teacher=self.teacher, session=self.session_a
        )
        self.course_b = Course.objects.create(
            name="Course B", teacher=self.teacher, session=self.session_b
        )
        Assignment.objects.create(
            course=self.course_a, title="A1", status=AssignmentStatus.PUBLISHED
        )
        Assignment.objects.create(
            course=self.course_b, title="B1", status=AssignmentStatus.PUBLISHED
        )
        student = make_student("summary-scope-student@example.com")
        StudentCourse.objects.create(
            student=student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_session_id_scopes_assignments_created(self):
        response = self.client.get(
            "/api/v1/school-admin/dashboard/summary",
            {"session_id": str(self.session_a.id)},
        )
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        self.assertEqual(response.data["assignments_created"], 1)

    def test_omitted_session_id_counts_every_session(self):
        response = self.client.get("/api/v1/school-admin/dashboard/summary")
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        self.assertEqual(response.data["assignments_created"], 2)
