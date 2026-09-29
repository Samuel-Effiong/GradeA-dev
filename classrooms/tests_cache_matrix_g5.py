"""H-1 Stage 3, gap G5: a course edit does not reach its enrolled students.

`clear_course_cache` (classrooms/signals.py `_course_scopes`) used to bump
`SCOPE_COURSE`, the teacher's `SCOPE_USER` and `SCOPE_SCHOOL` -- but no
enrolled student's `SCOPE_USER`. `UserCacheMixin` keys a course list/
retrieve only by the REQUESTING user's own generation, so an enrolled
student's cached copy of a renamed course never refreshed under the
generation-counter mechanism alone.

FIXED (H-1 Stage 3): `_course_scopes` now also bumps `usr` for every
student enrolled in the course. This suite proves the enrolled student's
retrieve refreshes while a student outside the course - who has no access
either way (404 before and after) - stays UNAFFECTED.

Fixtures follow the Stage 3 rule (plan §0): rows are created directly, with
exactly the fields production sets; the mutation under test runs through
the real `PATCH course/<pk>` endpoint.

Real Redis + real Postgres.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework.test import APIClient

from AutoGrader.tests_cache_matrix_support import UNAFFECTED, FreshnessMatrixMixin, Read
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()


def make_active_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


class CourseEditFreshnessTests(FreshnessMatrixMixin, TransactionTestCase):
    """G5: renaming a course must refresh its enrolled students too."""

    reset_sequences = True

    def setUp(self):
        cache.clear()

        self.teacher = make_active_user("g5-teacher@x.test", UserTypes.TEACHER, "G5T")
        self.enrolled_student = make_active_user(
            "g5-enrolled@x.test", UserTypes.STUDENT, "G5Enrolled"
        )
        self.outside_student = make_active_user(
            "g5-outside@x.test", UserTypes.STUDENT, "G5Outside"
        )

        self.session = Session.objects.create(name="G5 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="G5 course", teacher=self.teacher, session=self.session
        )
        StudentCourse.objects.create(
            student=self.enrolled_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        self.retrieve_url = reverse("course-detail", args=[self.course.pk])

    def reads(self):
        return [
            Read(
                "enrolled student's course retrieve",
                self.enrolled_student,
                self.retrieve_url,
            ),
            Read(
                "outside student's course retrieve",
                self.outside_student,
                self.retrieve_url,
            ),
        ]

    def rename_course(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        response = client.patch(self.retrieve_url, {"name": "G5 course renamed"})
        self.assertEqual(response.status_code, 200, response.content)

    def test_course_rename_now_refreshes_the_enrolled_student(self):
        result = self.run_matrix(
            "rename course (G5 fixed)",
            self.reads(),
            self.rename_course,
        )
        self.assert_no_stale(
            result, expect_changed=["enrolled student's course retrieve"]
        )
        verdicts = {o.label: o.verdict for o in result.outcomes}
        # The outside student has no access either way (404 both times);
        # the read never moves, so it stays UNAFFECTED.
        self.assertEqual(
            verdicts["outside student's course retrieve"], UNAFFECTED, result.table()
        )
