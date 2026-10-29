"""H-220: the cached assignment detail drops a student who is withdrawn.

Before H-220 the `student_submissions` list did not depend on the enrolment
status, so withdrawing a student could never make a cached detail stale. Now it
does. These tests use the REAL cache (as AutoGrader.tests_cache_bespoke_1114
does): the teacher reads the detail, a student without a submission is
withdrawn through `StudentCourse.withdrawn()` (a save, which bumps the
teacher's scope), and the next read must not list that student.

F2 is the control that makes F1 mean something: a silent bulk `.update()`
(which sends no signal) is NOT seen through the cached read, so the cache is
really in use and F1's fresh read comes from the bump, not from a missing cache.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from assignments.models import Assignment
from AutoGrader.test_cache import real_redis_caches
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()
REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/4")


def make_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
    )


@override_settings(CACHES=REDIS_CACHE)
class TheCachedDetailFollowsAWithdrawal(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.teacher = make_user("h220f-t@x.test", UserTypes.TEACHER, "Teacher")
        term = Session.objects.create(name="Term", teacher=self.teacher)
        course = Course.objects.create(
            name="Course", teacher=self.teacher, session=term
        )
        self.assignment = Assignment.objects.create(
            title="Essay",
            course=course,
            questions=[{"question_number": 1, "points": 10}],
        )
        self.rows = []
        for i in range(2):
            student = make_user(f"h220f-s{i}@x.test", UserTypes.STUDENT, f"Student{i}")
            self.rows.append(
                StudentCourse.objects.create(
                    student=student,
                    course=course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )
            )
        self.teacher_client = APIClient()
        self.teacher_client.force_authenticate(self.teacher)

    def names(self):
        response = self.teacher_client.get(
            reverse("assignment-detail", args=[self.assignment.pk])
        )
        self.assertEqual(response.status_code, 200)
        rows = response.data["student_submissions"]  # type: ignore[attr-defined]
        return sorted(row["name"] for row in rows)

    def full_name(self, row):
        return row.student.get_full_name()

    def test_f1_a_withdrawal_reaches_the_cached_detail(self):
        both = sorted(self.full_name(row) for row in self.rows)
        self.assertEqual(self.names(), both)
        self.assertEqual(self.names(), both)

        self.rows[0].withdrawn()

        self.assertEqual(self.names(), [self.full_name(self.rows[1])])

    def test_f2_control_a_silent_bulk_update_is_not_seen_through_the_cache(self):
        both = sorted(self.full_name(row) for row in self.rows)
        self.assertEqual(self.names(), both)

        StudentCourse.objects.filter(pk=self.rows[0].pk).update(
            enrollment_status=EnrollmentStatusType.WITHDRAWN
        )

        self.assertEqual(self.names(), both)
