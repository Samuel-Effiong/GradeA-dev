"""Hotfix regression: `avg_grade` must never be `null` in a school admin
dashboard response.

Both `dashboard/course-performance` and `dashboard/course-overview-chart`
run `Avg("...final_grade"...)` over a course's active enrollments. SQL's
AVG() over an empty set is NULL, and that NULL used to reach the client
verbatim as JSON `null`. The frontend calls `.toFixed()` on it without a
null guard, so a brand-new course (no active enrollment, no graded work
yet) crashed the school admin dashboard with "Cannot read properties of
null (reading 'toFixed')".

Fix: both endpoints now Coalesce() the average to 0.0 at the DB layer, so
a course with no gradeable active enrollment reports avg_grade=0, not
null. "0" here means "nothing graded yet", distinguishable from a real
0% by checking `students.total` (course-performance) / course enrollment
data - it is not claiming every student scored zero.
"""

from django.test import TestCase
from rest_framework import status
from rest_framework.test import APIClient

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


class AvgGradeNeverNullTest(TestCase):
    """A course with zero active (enrolled/completed) enrollments must
    report avg_grade=0, never null, on both dashboard endpoints."""

    def setUp(self):
        self.school = School.objects.create(name="Avg Grade Null School")
        self.admin = make_school_admin("avg-grade-null-admin@example.com", self.school)
        self.teacher = make_teacher("avg-grade-null-teacher@example.com", self.school)
        self.session = Session.objects.create(
            name="Term", school=self.school, owner_type=SessionOwnerType.SCHOOL
        )
        self.empty_course = Course.objects.create(
            name="Brand New Empty Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)

    def test_course_performance_reports_zero_not_null_for_an_empty_course(self):
        response = self.client.get("/api/v1/school-admin/dashboard/course-performance")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        results = response.data["results"]
        by_name = {row["name"]: row for row in results}
        row = by_name[self.empty_course.name]
        self.assertEqual(row["students"]["total"], 0)
        self.assertEqual(row["avg_grade"], 0)
        self.assertIsNotNone(row["avg_grade"])

    def test_course_overview_chart_reports_zero_not_null_for_an_empty_course(self):
        response = self.client.get(
            "/api/v1/school-admin/dashboard/course-overview-chart"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        by_name = {row["name"]: row for row in response.data["courses"]}
        row = by_name[self.empty_course.name]
        self.assertEqual(row["avg_grade"], 0)
        self.assertIsNotNone(row["avg_grade"])

    def test_course_performance_still_averages_real_grades_correctly(self):
        # A course WITH active enrollments and final grades must still
        # report the real average, not be swallowed by the Coalesce.
        graded_course = Course.objects.create(
            name="Graded Course",
            teacher=self.teacher,
            session=self.session,
        )
        s1 = CustomUser.objects.create_user(
            email="avg-grade-null-s1@example.com",
            password=TEST_PASSWORD,
        )
        s1.user_type = UserTypes.STUDENT
        s1.is_active = True
        s1.save()
        s2 = CustomUser.objects.create_user(
            email="avg-grade-null-s2@example.com",
            password=TEST_PASSWORD,
        )
        s2.user_type = UserTypes.STUDENT
        s2.is_active = True
        s2.save()
        StudentCourse.objects.create(
            student=s1,
            course=graded_course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
            final_grade=80,
        )
        StudentCourse.objects.create(
            student=s2,
            course=graded_course,
            enrollment_status=EnrollmentStatusType.COMPLETED,
            final_grade=90,
        )
        response = self.client.get("/api/v1/school-admin/dashboard/course-performance")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        by_name = {row["name"]: row for row in response.data["results"]}
        row = by_name[graded_course.name]
        self.assertEqual(row["avg_grade"], 85.0)
