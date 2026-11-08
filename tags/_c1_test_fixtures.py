"""Shared fixture for the Epic C1 tests (not a test module: the leading
underscore keeps it out of discovery).

Two teachers, each with a course and assignments; a student enrolled in
teacher A's course; a school admin and a super admin. Every route in
`TAG_ROUTES` is exercised against this world.
"""

from datetime import timedelta

from django.test import override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from users.models import CustomUser, UserTypes

PASSWORD = "c1-tags-pass"  # nosec  # pragma: allowlist secret
API = "/api/v1/"

flag_on = override_settings(TAGS_ENABLED=True)
flag_off = override_settings(TAGS_ENABLED=False)

# (method, path template, URL name). {tag} and {assignment} are filled in.
TAG_ROUTES = (
    ("get", "tags", "tag-list"),
    ("post", "tags", "tag-list"),
    ("patch", "tags/{tag}", "tag-detail"),
    ("delete", "tags/{tag}", "tag-detail"),
    ("get", "tags/{tag}/impact", "tag-impact"),
    ("put", "assignments/{assignment}/tags", "assignment-tags"),
)


def data(response):
    """The payload of a successful response (users/renderers.py wraps it)."""
    return response.json()["data"]


def envelope(response):
    """The coded body of an error response (F7)."""
    return response.json()["error"]["field_errors"]


class World:
    n = 0

    @classmethod
    def uid(cls):
        cls.n += 1
        return cls.n

    def user(self, kind, first="Ada", **extra):
        n = self.uid()
        return CustomUser.objects.create_user(
            email=f"c1-{kind.lower()}-{n}@c1.test",
            password=PASSWORD,
            user_type=kind,
            first_name=first,
            last_name=f"Okon{n}",
            is_active=True,
            **extra,
        )

    def course(self, teacher, name="Biology"):
        n = self.uid()
        return Course.objects.create(
            name=f"{name} {n}",
            teacher=teacher,
            session=Session.objects.create(name=f"Term {n}", teacher=teacher),
        )

    def assignment(self, course, title="Cells"):
        return Assignment.objects.create(
            title=f"{title} {self.uid()}",
            course=course,
            total_points=10,
            due_date=timezone.now() + timedelta(days=7),
            status=AssignmentStatus.PUBLISHED,
        )

    def build(self):
        self.teacher = self.user(UserTypes.TEACHER, "Tess")
        self.other_teacher = self.user(UserTypes.TEACHER, "Oto")
        self.student = self.user(UserTypes.STUDENT, "Sade")
        self.school = School.objects.create(name=f"C1 School {self.uid()}")
        self.school_admin = self.user(UserTypes.SCHOOL_ADMIN, "Sam", school=self.school)
        self.superadmin = self.user(UserTypes.SUPER_ADMIN, "Sup", is_superuser=True)
        self.course_a = self.course(self.teacher)
        self.course_b = self.course(self.other_teacher, "Physics")
        self.a1 = self.assignment(self.course_a, "Cells")
        self.a2 = self.assignment(self.course_a, "Genetics")
        self.b1 = self.assignment(self.course_b, "Forces")
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return self


def client_for(user=None):
    client = APIClient(raise_request_exception=False)
    if user is not None:
        client.force_authenticate(user)
    return client


def fire(client, method, path, body=None):
    url = API + path
    if method in ("get", "delete"):
        return getattr(client, method)(url)
    return getattr(client, method)(url, body or {}, format="json")
