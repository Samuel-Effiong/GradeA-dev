"""H-196: the lists of enrolments have ONE defined order.

The course detail and list (`students`), the teacher's my-students list
(`enrolled_courses`, and which course a row is about) and the serializers'
fallbacks listed enrolments in no order: whatever the database's plan
returned. beta's CI went red once because the cached answer and the fresh
answer listed the same students in a different order. The order is now the
enrolment's created_at, oldest first, then its id.

How each test makes the order observable: the rows are made in one order and
then given created_at values (or, for the tie-break, one shared created_at and
chosen ids) so that the DEFINED order is the REVERSE of the order the rows were
inserted in. A plan that returns rows in insertion order, which is what the
database does for a small table, is then wrong in a way the test sees.
Negative forms are decided by comparing complete lists whose length is
asserted first.

Not tested, on purpose: `CourseSerializer._own_entry` (a student's own entry):
it holds at most one row (unique student/course), so no order can be seen.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

import classrooms.models as classrooms_models
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from classrooms.serializers import CourseSerializer
from students.serializers import StudentListSerializer
from users.models import UserTypes

User = get_user_model()
N = 6


def make_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
        last_login=timezone.now(),
    )


class Fixture(TestCase):
    def setUp(self):
        self.teacher = make_user("h196-t@x.test", UserTypes.TEACHER, "Teacher")
        self.term = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Course", teacher=self.teacher, session=self.term
        )
        self.students = [
            make_user(f"h196-s{i}@x.test", UserTypes.STUDENT, f"Student{i}")
            for i in range(N)
        ]

    def enrol_in_reverse_created_order(self):
        """Rows inserted 0..N-1; created_at makes N-1..0 the defined order."""
        base = timezone.now() - timedelta(days=1)
        rows = []
        for i, student in enumerate(self.students):
            row = StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentCourse.objects.filter(pk=row.pk).update(
                created_at=base - timedelta(minutes=i)
            )
            rows.append(row)
        # Insertion order is 0..N-1; created_at, oldest first, is N-1..0.
        return [row.student_id for row in reversed(rows)]

    def enrol_tied_with_descending_ids(self):
        """Rows inserted with ids N..1 and ONE shared created_at; the defined
        order is by id, ascending: the reverse of insertion."""
        shared = timezone.now() - timedelta(days=1)
        inserted = []
        for i, student in enumerate(self.students):
            row = StudentCourse.objects.create(
                id=uuid.UUID(int=N - i),
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            inserted.append(row)
        StudentCourse.objects.filter(course=self.course).update(created_at=shared)
        by_id = sorted(inserted, key=lambda r: r.id)
        return [row.student_id for row in by_id]

    def get(self, url, user=None):
        client = APIClient()
        client.force_authenticate(user or self.teacher)
        # a fresh query every time: no cached answer can stand in for the order
        with patch.object(cache, "get", lambda *a, **k: None), patch.object(
            cache, "set", lambda *a, **k: True
        ):
            response = client.get(url)
        self.assertEqual(response.status_code, 200)
        return response.data

    def ids(self, students):
        return [str(s["id"]) for s in students]


class CourseDetailOrderTests(Fixture):
    def detail_ids(self):
        data = self.get(reverse("course-detail", args=[self.course.pk]))
        return self.ids(data["students"])

    def test_detail_lists_students_by_enrolment_created_at_oldest_first(self):
        expected = [str(i) for i in self.enrol_in_reverse_created_order()]
        got = self.detail_ids()
        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)

    def test_detail_breaks_a_created_at_tie_by_enrolment_id(self):
        expected = [str(i) for i in self.enrol_tied_with_descending_ids()]
        got = self.detail_ids()
        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)

    def test_list_route_lists_students_in_the_same_order(self):
        expected = [str(i) for i in self.enrol_in_reverse_created_order()]
        data = self.get(reverse("course-list"))
        items = data["results"] if "results" in data else data
        item = next(c for c in items if str(c["id"]) == str(self.course.pk))
        got = self.ids(item["students"])
        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)


class SerializerFallbackOrderTests(Fixture):
    """CourseSerializer on a Course that was NOT loaded by the view (no
    prefetched `active_enrollments`): the fallback query must order too."""

    def fallback_ids(self):
        course = Course.objects.get(pk=self.course.pk)
        self.assertFalse(hasattr(course, "active_enrollments"))
        return self.ids(CourseSerializer(course, context={}).data["students"])

    def test_fallback_lists_students_by_created_at_oldest_first(self):
        expected = [str(i) for i in self.enrol_in_reverse_created_order()]
        got = self.fallback_ids()
        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)

    def test_fallback_breaks_a_created_at_tie_by_enrolment_id(self):
        expected = [str(i) for i in self.enrol_tied_with_descending_ids()]
        got = self.fallback_ids()
        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)


class StudentCoursesOrderTests(Fixture):
    """One student in several of the teacher's courses: `enrolled_courses`
    (and which course the row is about) follow the defined order."""

    def setUp(self):
        super().setUp()
        self.student = self.students[0]
        base = timezone.now() - timedelta(days=1)
        self.courses = []
        for i in range(N):
            course = Course.objects.create(
                name=f"Course {i}", teacher=self.teacher, session=self.term
            )
            row = StudentCourse.objects.create(
                student=self.student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentCourse.objects.filter(pk=row.pk).update(
                created_at=base - timedelta(minutes=i)
            )
            self.courses.append(course)
        # inserted 0..N-1; created_at oldest first is N-1..0
        self.expected_names = [c.name for c in reversed(self.courses)]

    def test_my_students_lists_enrolled_courses_oldest_enrolment_first(self):
        data = self.get(reverse("student-course-my-students"))
        row = next(r for r in data["results"] if str(r["id"]) == str(self.student.id))
        self.assertEqual(len(row["enrolled_courses"]), N)
        self.assertEqual(row["enrolled_courses"], self.expected_names)

    def test_unprefetched_serializer_lists_enrolled_courses_in_the_same_order(self):
        student = User.objects.get(pk=self.student.pk)
        self.assertFalse(hasattr(student, "_prefetched_objects_cache"))
        data = StudentListSerializer(student).data
        self.assertEqual(len(data["enrolled_courses"]), N)
        self.assertEqual(data["enrolled_courses"], self.expected_names)


# Named by string: these exist only after the change, and the tests above must
# be able to run (and fail) on the code before it.
ORDER_NAME = "ENROLLMENT_LIST_ORDER"
METHOD_NAME = "in_list_order"


class OneDefinitionTests(TestCase):
    def test_the_order_is_defined_once_and_the_queryset_uses_it(self):
        order = getattr(classrooms_models, ORDER_NAME)

        self.assertEqual(tuple(order), ("created_at", "id"))
        sql = str(getattr(StudentCourse.objects, METHOD_NAME)().query)
        self.assertIn("ORDER BY", sql)
        clause = sql.split("ORDER BY")[1]
        self.assertIn('"created_at"', clause)
        self.assertIn('"id"', clause)
        self.assertLess(clause.index('"created_at"'), clause.index('"id"'))
