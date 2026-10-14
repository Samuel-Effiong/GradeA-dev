"""H-231: a filter number that is not a number is the caller's 400, not a 500.

my-students' `min_grade` and `max_grade` were cast with a bare float():
`?min_grade=abc`, or an empty `?min_grade=` beside another filter, raised
ValueError, which the exception handler answers with a bare 500 (Verifier's
M3 on 913f248f). `nan` passed float() and silently matched nothing. They are
now read as dashboard's `_percentage_param` reads its thresholds: absent or
empty means not given; anything else must be a number from 0 to 100, or the
answer is a 400 naming the parameter (DRF's ValidationError, as this file's
`_validate_uuid_query_param` answers a bad id).

The sibling with the same pattern, `months` on monthly-token-usage (a bare
int()), gets the same: absent or empty is the default 12, a value that is
not a whole number is a 400; a whole number out of range still falls back
to 12, as before.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MY_STUDENTS = reverse("student-course-my-students")
MONTHLY = reverse("school-monthly-token-usage")
PASSWORD = "remediation-pass-123"  # pragma: allowlist secret


def _not_a_grade(value):
    return [f"{value!r} is not a number between 0 and 100."]


@override_settings(CACHES=LOCMEM)
class ABadGradeFilterIsA400Test(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = CustomUser.objects.create_user(
            email="h231-teacher@audit.test",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        course = Course.objects.create(
            name="Course", teacher=self.teacher, session=session
        )
        self.student = CustomUser.objects.create_user(
            email="h231-student@audit.test",
            password=PASSWORD,
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        # One due assignment, submitted and graded at 70%: not at risk.
        assignment = Assignment.objects.create(
            title="Work",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            total_points=100,
            due_date=timezone.now() - timedelta(days=1),
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            answers={},
            score=70,
            score_percentage=70,
            submission_date=timezone.now(),
            graded_at=timezone.now(),
        )
        self.client.force_authenticate(self.teacher)

    def _get(self, **params):
        cache.clear()
        return self.client.get(MY_STUDENTS, params)

    def assertRefused(self, response, name, value):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.data
        )
        self.assertEqual(response.data, {name: _not_a_grade(value)})

    def test_g1_min_grade_that_is_not_a_number(self):
        self.assertRefused(self._get(min_grade="abc"), "min_grade", "abc")

    def test_g2_max_grade_that_is_not_a_number(self):
        self.assertRefused(self._get(max_grade="abc"), "max_grade", "abc")

    def test_g3_nan_is_not_a_number_here(self):
        self.assertRefused(self._get(min_grade="nan"), "min_grade", "nan")

    def test_g4_above_100(self):
        self.assertRefused(self._get(max_grade="150"), "max_grade", "150")

    def test_g5_below_0(self):
        self.assertRefused(self._get(min_grade="-5"), "min_grade", "-5")

    def test_g6_an_empty_grade_beside_another_filter_is_not_given(self):
        response = self._get(min_grade="", at_risk="false")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(ids, [str(self.student.id)])

    def test_g7_control_the_edges_are_grades(self):
        """Green before and after: 0 and 100 are in range, and a grade
        equal to min_grade is kept."""
        response = self._get(min_grade="70", max_grade="100")

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(ids, [str(self.student.id)])
        response = self._get(min_grade="0", max_grade="69.99")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["results"], [])


class BadMonthsIsA400Test(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        school = School.objects.create(name="H231 School")
        admin = CustomUser.objects.create_user(
            email="h231-admin@audit.test",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.client.force_authenticate(admin)

    def test_m1_months_that_is_not_a_whole_number(self):
        response = self.client.get(MONTHLY, {"months": "abc"})

        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.data
        )
        self.assertEqual(response.data, {"months": ["'abc' is not a whole number."]})

    def test_m2_empty_months_is_the_default(self):
        response = self.client.get(MONTHLY, {"months": ""})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 12)

    def test_m3_control_out_of_range_still_falls_back_to_12(self):
        """Green before and after."""
        response = self.client.get(MONTHLY, {"months": "99"})

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(len(response.data), 12)
