"""Query-count budgets for the classrooms list endpoints.

Each test measures the same request at two page sizes and asserts the query
count did not grow. That is the property that matters: an absolute budget
drifts and gets bumped, but "flat as the page grows" is exactly what an N+1
violates, so these fail loudly the moment one is reintroduced.

Measured under load before these were fixed (3-row page):
    my-students     425 queries      course list     137 queries
    my-courses      345 queries      enrollment list   7 queries
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
    Topic,
)
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM)
class QueryBudgetBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Budget School")
        self.teacher = User.objects.create_user(
            email="budget-teacher@x.test",
            password="password123",  # nosec  # pragma: allowlist secret
        )
        self.teacher.user_type = UserTypes.TEACHER
        self.teacher.is_active = True
        self.teacher.school = self.school
        self.teacher.first_name, self.teacher.last_name = "Budget", "Teacher"
        self.teacher.save()
        self.session = Session.objects.create(name="S", teacher=self.teacher)
        self._seq = 0

    def build(self, *, courses, students_per_course, assignments_per_course):
        """Create a self-contained slice of realistic data."""
        made = []
        for _ in range(courses):
            self._seq += 1
            course = Course.objects.create(
                name=f"Course {self._seq}", teacher=self.teacher, session=self.session
            )
            Topic.objects.create(name=f"Topic {self._seq}", course=course)
            assignments = [
                Assignment.objects.create(
                    title=f"A{self._seq}-{i}",
                    course=course,
                    teacher=self.teacher,
                    status=AssignmentStatus.PUBLISHED,
                )
                for i in range(assignments_per_course)
            ]
            for j in range(students_per_course):
                self._seq += 1
                student = User.objects.create_user(
                    email=f"budget-s{self._seq}@x.test", password="p"  # nosec
                )
                student.user_type = UserTypes.STUDENT
                student.is_active = True
                student.first_name, student.last_name = f"S{self._seq}", "T"
                student.save()
                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )
                for assignment in assignments[: max(1, j % 3)]:
                    StudentSubmission.objects.create(
                        student=student, assignment=assignment, answers={}
                    )
            made.append(course)
        return made

    def count_queries(self, url, params=None, user=None):
        cache.clear()
        self.client.force_authenticate(user or self.teacher)
        with CaptureQueriesContext(connection) as captured:
            response = self.client.get(url, params or {})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return len(captured), response

    def assert_flat(self, url, small, large, *, user=None, params=None):
        """The same endpoint, a small page then a larger one."""
        self.build(**small)
        small_n, _ = self.count_queries(url, params, user)

        self.build(**large)
        large_n, response = self.count_queries(url, params, user)

        self.assertGreater(
            len(response.data.get("results", response.data)),
            0,
            "the larger page returned no rows - the budget would be vacuous",
        )
        self.assertLessEqual(
            large_n,
            small_n,
            f"query count grew {small_n} -> {large_n} as the page got bigger; "
            "an N+1 has been reintroduced",
        )


class ListEndpointQueryBudgets(QueryBudgetBase):
    def test_course_list_is_flat(self):
        self.assert_flat(
            reverse("course-list"),
            {"courses": 1, "students_per_course": 3, "assignments_per_course": 2},
            {"courses": 4, "students_per_course": 3, "assignments_per_course": 2},
        )

    def test_my_students_is_flat(self):
        self.assert_flat(
            reverse("student-course-my-students"),
            {"courses": 1, "students_per_course": 2, "assignments_per_course": 2},
            {"courses": 3, "students_per_course": 4, "assignments_per_course": 3},
        )

    def test_enrollment_list_is_flat(self):
        self.assert_flat(
            reverse("student-course-list"),
            {"courses": 1, "students_per_course": 2, "assignments_per_course": 2},
            {"courses": 3, "students_per_course": 4, "assignments_per_course": 3},
        )

    def test_topic_list_is_flat(self):
        self.assert_flat(
            reverse("topic-list"),
            {"courses": 1, "students_per_course": 1, "assignments_per_course": 1},
            {"courses": 5, "students_per_course": 1, "assignments_per_course": 1},
        )

    def test_session_list_is_flat(self):
        self.assert_flat(
            reverse("session-list"),
            {"courses": 1, "students_per_course": 2, "assignments_per_course": 1},
            {"courses": 4, "students_per_course": 2, "assignments_per_course": 1},
        )


class StudentFacingQueryBudgets(QueryBudgetBase):
    """my-courses is unpaginated, so its cost scales with enrollments."""

    def _student_in(self, courses):
        student = User.objects.create_user(
            email=f"budget-viewer{self._seq}@x.test", password="p"  # nosec
        )
        self._seq += 1
        student.user_type = UserTypes.STUDENT
        student.is_active = True
        student.save()
        for course in courses:
            StudentCourse.objects.create(
                student=student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
        return student

    def test_my_courses_is_flat_as_enrollments_grow(self):
        one = self.build(courses=1, students_per_course=2, assignments_per_course=2)
        viewer = self._student_in(one)
        small_n, _ = self.count_queries(reverse("course-my-courses"), user=viewer)

        more = self.build(courses=4, students_per_course=2, assignments_per_course=2)
        for course in more:
            StudentCourse.objects.create(
                student=viewer,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
        large_n, response = self.count_queries(
            reverse("course-my-courses"), user=viewer
        )

        self.assertEqual(len(response.data), 5)
        self.assertLessEqual(
            large_n,
            small_n,
            f"my-courses grew {small_n} -> {large_n} queries between 1 and 5 "
            "enrolled courses",
        )

    def test_student_course_list_is_flat_for_a_student(self):
        one = self.build(courses=1, students_per_course=1, assignments_per_course=2)
        viewer = self._student_in(one)
        small_n, _ = self.count_queries(reverse("student-course-list"), user=viewer)

        more = self.build(courses=4, students_per_course=1, assignments_per_course=2)
        for course in more:
            StudentCourse.objects.create(
                student=viewer,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
        large_n, _ = self.count_queries(reverse("student-course-list"), user=viewer)

        self.assertLessEqual(large_n, small_n)


class SerializerCorrectnessUnderPrefetch(QueryBudgetBase):
    """The prefetch rewrites must not change the numbers they report."""

    def test_my_students_counts_survive_the_prefetch_rewrite(self):
        course = self.build(courses=1, students_per_course=1, assignments_per_course=3)[
            0
        ]
        student = StudentCourse.objects.get(course=course).student
        # one submission exists from build(); add a second
        remaining = Assignment.objects.filter(course=course).exclude(
            submissions__student=student
        )
        StudentSubmission.objects.create(
            student=student, assignment=remaining.first(), answers={}
        )

        self.client.force_authenticate(self.teacher)
        row = self.client.get(reverse("student-course-my-students")).data["results"][0]

        self.assertEqual(row["total_assignments_in_course"], 3)
        self.assertEqual(row["total_assignments_submitted"], 2)
        self.assertAlmostEqual(row["percentage_of_submission"], 66.67, places=1)
        self.assertEqual(row["teacher"], self.teacher.get_full_name())
        self.assertEqual(len(row["enrolled_courses"]), 1)

    def test_course_students_report_their_enrollment_status(self):
        course = self.build(courses=1, students_per_course=2, assignments_per_course=1)[
            0
        ]
        self.client.force_authenticate(self.teacher)
        payload = self.client.get(
            reverse("course-detail", kwargs={"pk": course.id})
        ).data

        self.assertEqual(payload["student_count"], 2)
        self.assertEqual(payload["assignment_count"], 1)
        self.assertEqual(len(payload["students"]), 2)
        for student in payload["students"]:
            self.assertEqual(
                student["enrollment_status"], EnrollmentStatusType.ENROLLED
            )

    def test_withdrawn_students_are_excluded_from_the_course_payload(self):
        course = self.build(courses=1, students_per_course=2, assignments_per_course=1)[
            0
        ]
        enrollment = StudentCourse.objects.filter(course=course).first()
        enrollment.withdrawn()

        self.client.force_authenticate(self.teacher)
        payload = self.client.get(
            reverse("course-detail", kwargs={"pk": course.id})
        ).data

        self.assertEqual(payload["student_count"], 1)
        self.assertEqual(len(payload["students"]), 1)
