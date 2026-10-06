"""BE-I-04 slice A: a grade that existed before the label did.

A row made while `students` is at migration 0030 reads "unlabelled" in all
six label columns after 0031, its grade is untouched, and the migration
reverses. This is the plan's promise that no old grade is left blank and
that no copying job has to be run, shown on a real row going through the
real migration, not read off the column's definition.

Written by the Next-stage Checker as a probe of its own (it passed at
58326e45 in the checker's run) and handed over for adoption by the Senior
Manager's ruling of 2026-10-06. The class is as the checker wrote it; only
its name and this docstring are the builder's.
"""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

from students.grading_label import LABEL_FIELDS, UNLABELLED
from students.models import StudentSubmission
from users.models import UserTypes

TABLE = "students_studentsubmission"
AT_0030 = [("students", "0030_batch_session_credits_exhausted_at")]
AT_0031 = [("students", "0031_submission_grading_label")]


def _targets(students_target):
    executor = MigrationExecutor(connection)
    others = [n for n in executor.loader.graph.leaf_nodes() if n[0] != "students"]
    return list(students_target) + others


def _label_row(pk):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT %s FROM %s WHERE id = %%s" % (", ".join(LABEL_FIELDS), TABLE),
            [pk],
        )
        return cursor.fetchone()


def _label_columns_present():
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = ANY(%s)",
            [TABLE, list(LABEL_FIELDS)],
        )
        return cursor.fetchone()[0]


class ARowMadeBeforeTheLabelExistedTest(TransactionTestCase):
    """Runs the real migrations backwards and forwards on the test
    database, and leaves the schema at the latest state for the tests that
    follow."""

    def _migrate(self, students_target):
        targets = _targets(students_target)
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(
            [n for n in executor.loader.graph.leaf_nodes() if n[0] == "students"]
        )
        super().tearDown()

    def test_a_row_made_before_0031_reads_the_placeholder_after_it(self):
        old_apps = self._migrate(AT_0030)
        self.assertEqual(_label_columns_present(), 0)
        User = old_apps.get_model("users", "CustomUser")
        teacher = User.objects.create(
            email="p3-teacher@example.com",
            user_type=UserTypes.TEACHER,
            registration_method="EMAIL",
        )
        student = User.objects.create(
            email="p3-student@example.com",
            user_type=UserTypes.STUDENT,
            registration_method="EMAIL",
        )
        session = old_apps.get_model("classrooms", "Session").objects.create(
            name="S", teacher=teacher
        )
        course = old_apps.get_model("classrooms", "Course").objects.create(
            name="C", teacher=teacher, session=session
        )
        assignment = old_apps.get_model("assignments", "Assignment").objects.create(
            title="A", course=course, questions=[]
        )
        OldSubmission = old_apps.get_model("students", "StudentSubmission")
        graded = OldSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[],
            score=7,
            max_points=10,
            score_percentage=70,
            feedback={"grading_model": "x-ai/grok-4.3"},
        )

        self._migrate(AT_0031)
        self.assertEqual(_label_columns_present(), 6)
        self.assertEqual(_label_row(graded.pk), (UNLABELLED,) * 6)
        fresh = StudentSubmission.objects.get(pk=graded.pk)
        self.assertEqual(float(fresh.score or 0), 7.0)
        for name in LABEL_FIELDS:
            self.assertEqual(getattr(fresh, name), UNLABELLED)

        # Back again: the migration reverses, and the grade is untouched.
        self._migrate(AT_0030)
        self.assertEqual(_label_columns_present(), 0)
        with connection.cursor() as cursor:
            cursor.execute("SELECT score FROM %s WHERE id = %%s" % TABLE, [graded.pk])
            self.assertEqual(float(cursor.fetchone()[0]), 7.0)
