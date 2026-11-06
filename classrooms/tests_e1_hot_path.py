"""Epic E1b: a lighter grading hot path, with no behavior change.

Plan 09 §0.5, §3.4, §5.1. Three things are pinned here:

* The grade-field skip. The final-grade receiver recalculates on every
  submission save today, including saves that touch no field the grade
  reads (scheduling, the formatted label, a held regrade). When
  `update_fields` names none of score, max_points, graded_at, assignment or
  student, the result would be identical, so the recalculation is skipped.
  A save without `update_fields`, and every delete, still recalculates.
* Idempotence (FR-E-07): recalculating twice changes nothing the second
  time and writes once.
* Less work inside the row lock (NFR-PRF-07, H-5), and convergence when
  overlapping Grade all runs finish one student's work in one course at
  the same moment (NFR-REL-09).

Expectations, written before the change. On the base: the "does not
recalculate" tests and the lock-cost test are red; everything else is
green and must stay green.
"""

from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.testing.concurrency import run_concurrently
from classrooms import grade_service, signals
from classrooms.models import Course, Session, StudentCourse
from students.models import StudentSubmission
from students.services import GRADING_RESULT_FIELDS, HELD_SAVE_FIELDS
from users.models import CustomUser, UserTypes

PASSWORD = "e1b-hot-path-pass"  # pragma: allowlist secret


class _Statements:
    def __init__(self):
        self.sql = []

    def __call__(self, execute, sql, params, many, context):
        self.sql.append(sql)
        return execute(sql, params, many, context)


class _Builder:
    n = 0

    @classmethod
    def uid(cls):
        cls.n += 1
        return cls.n

    def make_user(self, kind, first="Kemi"):
        n = self.uid()
        return CustomUser.objects.create_user(
            email=f"e1b-{kind.lower()}-{n}@e1b.test",
            password=PASSWORD,
            user_type=kind,
            first_name=first,
            last_name=f"Ade{n}",
            is_active=True,
        )

    def make_course(self, teacher):
        n = self.uid()
        return Course.objects.create(
            name=f"E1b {n}",
            teacher=teacher,
            session=Session.objects.create(name=f"Term {n}", teacher=teacher),
        )

    def make_assignment(self, course, points=10):
        return Assignment.objects.create(
            title=f"A{self.uid()}",
            course=course,
            total_points=points,
            due_date=timezone.now() + timedelta(days=7),
            status=AssignmentStatus.PUBLISHED,
        )

    def grade(self, student, assignment, score, **extra):
        return StudentSubmission.objects.create(
            student=student,
            assignment=assignment,
            answers={},
            score=Decimal(score),
            max_points=assignment.total_points,
            graded_at=timezone.now(),
            **extra,
        )


class GradeFieldSkipTest(_Builder, TestCase):
    """Which saves recalculate the final grade."""

    def setUp(self):
        self.teacher = self.make_user(UserTypes.TEACHER, "Tess")
        self.course = self.make_course(self.teacher)
        self.student = self.make_user(UserTypes.STUDENT)
        StudentCourse.objects.create(student=self.student, course=self.course)
        self.assignment = self.make_assignment(self.course)
        self.submission = self.grade(self.student, self.assignment, "7")

    def recalculations(self, action):
        with mock.patch.object(
            signals,
            "_recalculate_final_grade",
            wraps=signals._recalculate_final_grade,
        ) as spy:
            action()
        return spy.call_count

    def save_fields(self, fields):
        return self.recalculations(lambda: self.submission.save(update_fields=fields))

    # -- saves that cannot change the grade: skipped after E1b (red now) --

    def test_a_publish_only_save_does_not_recalculate(self):
        self.submission.is_published = True
        self.assertEqual(self.save_fields(["is_published"]), 0)

    def test_a_scheduling_save_does_not_recalculate(self):
        # students/views.py and assignments/tasks.py schedule grading so.
        self.assertEqual(
            self.save_fields(["scheduled_grading_at", "grading_task_name"]), 0
        )

    def test_a_formatted_grade_save_does_not_recalculate(self):
        # assignments/tasks.py writes the label alone after grading.
        self.assertEqual(self.save_fields(["formatted_grade"]), 0)

    def test_a_held_regrade_save_does_not_recalculate(self):
        # students/services.py: a regrade held behind a released grade
        # changes no released column, so the grade cannot move.
        self.assertEqual(self.save_fields(list(HELD_SAVE_FIELDS)), 0)

    # -- saves that can change the grade: still recalculate (green now) --

    def test_each_grade_input_field_recalculates(self):
        for field in ("score", "max_points", "graded_at", "assignment", "student"):
            with self.subTest(field=field):
                self.assertEqual(self.save_fields([field]), 1)

    def test_a_grade_input_field_named_by_its_column_recalculates(self):
        # Django accepts update_fields=["assignment_id"] and passes it to the
        # receiver as written: the skip must not read it as "no grade field".
        for field in ("assignment_id", "student_id"):
            with self.subTest(field=field):
                self.assertEqual(self.save_fields([field]), 1)

    def test_a_grade_input_field_among_others_recalculates(self):
        self.assertEqual(self.save_fields(["is_published", "score"]), 1)

    def test_the_grading_engine_save_recalculates(self):
        self.assertEqual(self.save_fields(list(GRADING_RESULT_FIELDS)), 1)

    def test_a_save_without_update_fields_recalculates(self):
        self.assertEqual(self.recalculations(self.submission.save), 1)

    def test_a_delete_recalculates(self):
        self.assertEqual(self.recalculations(self.submission.delete), 1)

    def test_the_grade_still_follows_a_score_change(self):
        self.submission.score = Decimal("3")
        self.submission.save(update_fields=["score"])
        enrollment = StudentCourse.objects.get(student=self.student, course=self.course)
        self.assertEqual(enrollment.final_grade, Decimal("30.00"))


class IdempotenceTest(_Builder, TestCase):
    """FR-E-07: the same data gives the same value, written once."""

    def setUp(self):
        teacher = self.make_user(UserTypes.TEACHER, "Tess")
        self.course = self.make_course(teacher)
        self.student = self.make_user(UserTypes.STUDENT)
        self.enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        self.grade(self.student, self.make_assignment(self.course), "7")
        StudentCourse.objects.filter(pk=self.enrollment.pk).update(final_grade=None)

    def test_recalculating_twice_writes_once(self):
        first = signals._recalculate_final_grade(self.student.id, self.course.id)
        self.assertEqual(first, (None, Decimal("70.00"), True))
        statements = _Statements()
        with connection.execute_wrapper(statements):
            second = signals._recalculate_final_grade(self.student.id, self.course.id)
        self.assertIsNone(second)
        self.assertFalse(
            [s for s in statements.sql if s.lstrip().upper().startswith("UPDATE")],
            statements.sql,
        )
        self.enrollment.refresh_from_db()
        self.assertEqual(
            self.enrollment.final_grade,
            grade_service.course_grade(self.student.id, self.course.id),
        )


class LockCostTest(_Builder, TestCase):
    """NFR-PRF-07 / H-5: the work done while the enrollment row is locked."""

    def setUp(self):
        teacher = self.make_user(UserTypes.TEACHER, "Tess")
        self.course = self.make_course(teacher)
        self.student = self.make_user(UserTypes.STUDENT)
        self.enrollment = StudentCourse.objects.create(
            student=self.student, course=self.course
        )
        self.grade(self.student, self.make_assignment(self.course), "7")
        StudentCourse.objects.filter(pk=self.enrollment.pk).update(final_grade=None)

    def test_a_changed_grade_costs_lock_aggregate_and_update_only(self):
        statements = _Statements()
        with connection.execute_wrapper(statements):
            result = signals._recalculate_final_grade(self.student.id, self.course.id)
        self.assertEqual(result, (None, Decimal("70.00"), True))
        work = [
            s
            for s in statements.sql
            if not s.lstrip().upper().startswith(("SAVEPOINT", "RELEASE SAVEPOINT"))
        ]
        print(f"\nE1B_MEASURE lock_statements={len(work)}")
        for sql in work:
            print("E1B_MEASURE   ", sql[:110].replace("\n", " "))
        # SELECT ... FOR UPDATE, the aggregate, the UPDATE. Nothing else:
        # no name-conflict lookup, no FK or unique validation (H-5).
        self.assertLessEqual(len(work), 3, work)
        self.assertFalse(any("users_customuser" in s for s in work), work)


class ConvergenceTest(_Builder, TransactionTestCase):
    """NFR-REL-09: overlapping Grade all runs on different assignments of
    one course finish the same student's work at once; the stored grade
    must equal the grade the work implies. 8 real threads, real Postgres,
    released together, repeated 100 times."""

    WORKERS = 8
    ROUNDS = 100

    def test_the_stored_grade_converges(self):
        teacher = self.make_user(UserTypes.TEACHER, "Tess")
        course = self.make_course(teacher)
        assignments = [
            self.make_assignment(course, points=5 + i) for i in range(self.WORKERS)
        ]
        for round_number in range(self.ROUNDS):
            student = self.make_user(UserTypes.STUDENT, f"R{round_number}")
            StudentCourse.objects.create(student=student, course=course)

            def worker(i, student=student, round_number=round_number):
                self.grade(student, assignments[i], str((i + round_number) % 5 + 1))

            _, errors = run_concurrently(worker, self.WORKERS, test=self)
            self.assertEqual(errors, [], f"round {round_number}")
            stored = StudentCourse.objects.get(student=student, course=course)
            expected = grade_service.course_grade(student.id, course.id)
            self.assertIsNotNone(expected)
            self.assertEqual(stored.final_grade, expected, f"round {round_number}")
