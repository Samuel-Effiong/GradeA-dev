"""H-5: `StudentCourse.save()` runs `full_clean()` on the grading hot path.

`_recalculate_final_grade` holds a `select_for_update()` row lock on the
enrollment while it aggregates and then calls
`enrollment.save(update_fields=["final_grade"])`. That save runs full model
validation - foreign-key existence checks, the unique-constraint lookup and
the exact-name conflict lookup - inside the lock, none of which can change
when only `final_grade` is written.

Two halves:

* `NameConflictEntryPointTests` pin the roster-integrity rule at every entry
  point that can violate it. They must pass before and after the change, and
  must fail when the validation is removed (see the mutation battery).
* `HotPathCostTests` measure the queries and lock-hold time of the write.

Epic E1b re-applies H-5 by hand (plan 09 §0.4). This file is the old
branch's battery (task/h5-full-clean-hot-path, 41b281ff3 + 1c0742f8a,
verified dd9d875f), carried over unchanged except that the lock-hold
measurement now runs its threads through the shared harness
(AutoGrader.testing.concurrency.run_concurrently: barrier, one deadline,
connections closed, no leaked backends) instead of raw joins.
"""

import time
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.testing.concurrency import run_concurrently
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from classrooms.services import enroll_student_by_email
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()


def make_user(email, user_type, first="Test", last=None, middle=""):
    user = User.objects.create_user(email=email, password="password123")  # nosec
    user.user_type = user_type
    user.is_active = True
    user.first_name = first
    user.last_name = last or user_type.title()
    user.middle_name = middle
    user.save()
    return user


def make_course(teacher, name="English 7"):
    return Course.objects.create(
        name=name,
        teacher=teacher,
        session=Session.objects.create(name=f"Term {name}", teacher=teacher),
    )


class NameConflictEntryPointTests(TestCase):
    """The exact-name rule is enforced wherever an enrollment is written."""

    def setUp(self):
        self.teacher = make_user("h5-t@x.test", UserTypes.TEACHER)
        self.course = make_course(self.teacher)
        self.ada = make_user("ada1@x.test", UserTypes.STUDENT, "Ada", "Obi")
        self.ada2 = make_user("ada2@x.test", UserTypes.STUDENT, "ada", "obi")
        StudentCourse.objects.create(student=self.ada, course=self.course)

    def test_create_of_a_same_name_student_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "already enrolled"):
            StudentCourse.objects.create(student=self.ada2, course=self.course)
        self.assertEqual(self.course.enrollments.count(), 1)

    def test_plain_save_of_a_new_same_name_enrollment_is_rejected(self):
        with self.assertRaisesMessage(ValidationError, "already enrolled"):
            StudentCourse(student=self.ada2, course=self.course).save()

    def test_invite_service_rejects_a_same_name_existing_student(self):
        self.ada2.must_change_password = False
        self.ada2.save(update_fields=["must_change_password"])
        with self.assertRaises(ValidationError):
            enroll_student_by_email(course=self.course, email="ada2@x.test")
        self.assertEqual(self.course.enrollments.count(), 1)

    def test_reactivate_is_rejected_when_a_same_name_student_is_now_enrolled(self):
        other_course = make_course(self.teacher, "Maths 7")
        enrollment = StudentCourse.objects.create(
            student=self.ada2, course=other_course
        )
        enrollment.withdrawn()
        # A same-name student got in while withdrawn (unvalidated insert).
        StudentCourse.objects.bulk_create(
            [StudentCourse(student=self.ada, course=other_course)]
        )
        with self.assertRaisesMessage(ValidationError, "already enrolled"):
            enrollment.reactivate()
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.WITHDRAWN)

    def test_status_update_keeps_the_conflict_check(self):
        """`update_fields=["enrollment_status"]` is not a hot-path write."""
        other_course = make_course(self.teacher, "Maths 7")
        StudentCourse.objects.create(student=self.ada, course=other_course)
        # A same-name duplicate that got in without validation (bulk_create).
        StudentCourse.objects.bulk_create(
            [StudentCourse(student=self.ada2, course=other_course)]
        )
        enrollment = StudentCourse.objects.get(student=self.ada2, course=other_course)
        enrollment.enrollment_status = EnrollmentStatusType.ENROLLED
        with self.assertRaisesMessage(ValidationError, "already enrolled"):
            enrollment.save(update_fields=["enrollment_status"])

    def test_mixed_update_fields_keep_the_conflict_check(self):
        """A hot-path field alongside a status field is not a hot-path write."""
        other_course = make_course(self.teacher, "Maths 7")
        StudentCourse.objects.create(student=self.ada, course=other_course)
        StudentCourse.objects.bulk_create(
            [StudentCourse(student=self.ada2, course=other_course)]
        )
        enrollment = StudentCourse.objects.get(student=self.ada2, course=other_course)
        enrollment.final_grade = Decimal("50.00")
        with self.assertRaisesMessage(ValidationError, "already enrolled"):
            enrollment.save(update_fields=["final_grade", "enrollment_status"])

    def test_a_final_grade_write_skips_the_name_lookup(self):
        """The point of H-5: the hot path no longer runs the conflict query."""
        enrollment = StudentCourse.objects.get(student=self.ada, course=self.course)
        enrollment.final_grade = Decimal("12.34")
        counter = _QueryCounter()
        with connection.execute_wrapper(counter):
            enrollment.save(update_fields=["final_grade"])
        self.assertLessEqual(len(counter.statements), 3, counter.statements)
        self.assertFalse(
            any(
                "users_customuser" in sql and "iexact" in sql.lower()
                for sql in counter.statements
            )
        )
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.final_grade, Decimal("12.34"))

    def test_an_out_of_range_final_grade_is_still_rejected(self):
        enrollment = StudentCourse.objects.get(student=self.ada, course=self.course)
        enrollment.final_grade = Decimal("1000.00")
        with self.assertRaises(ValidationError):
            enrollment.save(update_fields=["final_grade"])
        enrollment.refresh_from_db()
        self.assertIsNone(enrollment.final_grade)


class _QueryCounter:
    def __init__(self):
        self.statements = []

    def __call__(self, execute, sql, params, many, context):
        self.statements.append(sql)
        return execute(sql, params, many, context)


class HotPathCostTests(TransactionTestCase):
    """Cost of the locked `final_grade` write, on real Postgres."""

    WORKERS = 8
    ASSIGNMENTS = 16

    def setUp(self):
        self.teacher = make_user("h5-cost-t@x.test", UserTypes.TEACHER)
        self.course = make_course(self.teacher)
        self.student = make_user("h5-cost-s@x.test", UserTypes.STUDENT, "Kemi", "Ade")
        self.enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        self.assignments = [
            Assignment.objects.create(
                title=f"A{i}",
                course=self.course,
                total_points=10,
                due_date=timezone.now() + timedelta(days=7),
                status=AssignmentStatus.PUBLISHED,
            )
            for i in range(self.ASSIGNMENTS)
        ]

    def _grade(self, assignment, score):
        return StudentSubmission.objects.create(
            student=self.student,
            assignment=assignment,
            score=Decimal(score),
            answers={},
            graded_at=timezone.now(),
        )

    def test_queries_issued_by_the_final_grade_save(self):
        self._grade(self.assignments[0], 7)
        enrollment = StudentCourse.objects.get(pk=self.enrollment.pk)
        enrollment.final_grade = Decimal("12.34")
        counter = _QueryCounter()
        with connection.execute_wrapper(counter):
            enrollment.save(update_fields=["final_grade"])
        print(f"\nH5_MEASURE save_queries={len(counter.statements)}")
        for sql in counter.statements:
            print("H5_MEASURE   ", sql[:110].replace("\n", " "))

    def test_lock_hold_under_concurrent_grading(self):
        durations = []
        real_save = StudentCourse.save

        def timed_save(instance, *args, **kwargs):
            started = time.perf_counter()
            try:
                return real_save(instance, *args, **kwargs)
            finally:
                if kwargs.get("update_fields") == ["final_grade"]:
                    durations.append(time.perf_counter() - started)

        def worker(index):
            for position in range(index, self.ASSIGNMENTS, self.WORKERS):
                self._grade(self.assignments[position], 1 + position % 9)

        with mock.patch.object(StudentCourse, "save", timed_save):
            started = time.perf_counter()
            _, errors = run_concurrently(worker, self.WORKERS, test=self)
            wall = time.perf_counter() - started

        self.assertEqual(errors, [])
        durations.sort()
        n = len(durations)
        self.enrollment.refresh_from_db()
        total = sum(1 + i % 9 for i in range(self.ASSIGNMENTS))
        expected = (Decimal(total) / Decimal(10 * self.ASSIGNMENTS) * 100).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.assertEqual(self.enrollment.final_grade, expected)
        print(
            f"\nH5_MEASURE writes={n} wall_s={wall:.3f} "
            f"save_p50_ms={durations[n // 2] * 1000:.2f} "
            f"save_p95_ms={durations[int(n * 0.95) - 1] * 1000:.2f} "
            f"save_total_ms={sum(durations) * 1000:.1f} "
            f"load1={open('/proc/loadavg').read().split()[0]}"
        )
