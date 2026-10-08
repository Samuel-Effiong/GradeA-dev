"""
R-2: ai_grading_completed_at was a DateField, and the superadmin AI
performance dashboard reports `ai_grading_completed_at - ai_graded_at` as
the average grading duration. A date minus a timestamp is midnight minus
the real time, so every value it ever reported was negative.

Proven here on real PostgreSQL:

* Migration 0027 changes the column to TIMESTAMPTZ (checked against
  information_schema, not the model).
* Migration 0028 backfills historical rows from graded_at, which the same
  save sets milliseconds later, and nulls the date-only value on rows that
  were never successfully graded. Verified by migrating BACK to 0026,
  inserting rows through the historical model exactly as the old code
  would have left them, and migrating forward again.
* The reverse of 0027 is applied and re-applied cleanly.
* The dashboard endpoint then reports a positive duration equal to the
  mean of the real per-row durations, for a backfilled historical row and
  for a row graded by the pipeline after the migration.

Run with:
    python manage.py test students.tests_grading_duration_migration
"""

from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone
from django.utils.dateparse import parse_duration
from rest_framework.test import APIClient

from assignments.models import Assignment
from classrooms.models import Course, School, Session
from students.models import StudentSubmission
from students.services import grade_engine
from users.models import CustomUser, UserTypes

BEFORE = [("students", "0026_alter_backgroundprocessingtask_task_type")]
AFTER = [("students", "0028_backfill_ai_grading_completed_at")]


def _with_other_apps_at_latest(targets):
    """Only `students` moves; every other app stays at its leaf, so the
    historical models used to insert fixture rows match the columns the
    database actually has (e.g. users.registration_method, NOT NULL)."""
    executor = MigrationExecutor(connection)
    others = [
        node for node in executor.loader.graph.leaf_nodes() if node[0] != "students"
    ]
    return list(targets) + others


AI_PERF = "/api/v1/super-admin/dashboard/ai_performance"


def _column_type(table, column):
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_name = %s AND column_name = %s",
            [table, column],
        )
        return cursor.fetchone()[0]


class GradingCompletedAtMigrationTest(TransactionTestCase):
    """Runs the real migrations backwards and forwards on the test
    database. Leaves the schema at the latest state for every test that
    follows."""

    def _migrate(self, targets):
        targets = _with_other_apps_at_latest(targets)
        executor = MigrationExecutor(connection)
        executor.migrate(targets)
        executor.loader.build_graph()
        return executor.loader.project_state(targets).apps

    def tearDown(self):
        # Never leave the database behind the current schema.
        executor = MigrationExecutor(connection)
        executor.migrate(
            [
                node
                for node in executor.loader.graph.leaf_nodes()
                if node[0] == "students"
            ]
        )
        super().tearDown()

    def test_backfill_recovers_historical_rows_and_nulls_never_graded_ones(self):
        old_apps = self._migrate(BEFORE)
        self.assertEqual(
            _column_type("students_studentsubmission", "ai_grading_completed_at"),
            "date",
        )

        User = old_apps.get_model("users", "CustomUser")
        OldSession = old_apps.get_model("classrooms", "Session")
        OldCourse = old_apps.get_model("classrooms", "Course")
        OldAssignment = old_apps.get_model("assignments", "Assignment")
        OldSubmission = old_apps.get_model("students", "StudentSubmission")

        teacher = User.objects.create(
            email="mig-teacher@example.com",
            user_type=UserTypes.TEACHER,
            registration_method="EMAIL",
        )
        session = OldSession.objects.create(name="S", teacher=teacher)
        course = OldCourse.objects.create(name="C", teacher=teacher, session=session)
        assignment = OldAssignment.objects.create(
            title="A", course=course, questions=[]
        )

        started = timezone.now() - timedelta(hours=3)
        graded_at = started + timedelta(seconds=42)

        def make(email, **fields):
            student = User.objects.create(
                email=email, user_type=UserTypes.STUDENT, registration_method="EMAIL"
            )
            return OldSubmission.objects.create(
                assignment=assignment, student=student, answers=[], **fields
            ).pk

        # Exactly what the pre-migration code wrote: a DATE in the
        # completion column (Django truncated the datetime), a precise
        # graded_at on the same save.
        graded_pk = make(
            "mig-graded@example.com",
            ai_graded_at=started,
            ai_grading_completed_at=started.date(),
            graded_at=graded_at,
        )
        # A run that set the completion date but never finished the save
        # that sets graded_at (there is no such path today, but the data
        # can exist from older code).
        never_pk = make(
            "mig-never@example.com",
            ai_graded_at=started,
            ai_grading_completed_at=started.date(),
            graded_at=None,
        )
        # Never graded at all.
        untouched_pk = make("mig-untouched@example.com")

        new_apps = self._migrate(AFTER)
        self.assertEqual(
            _column_type("students_studentsubmission", "ai_grading_completed_at"),
            "timestamp with time zone",
        )
        NewSubmission = new_apps.get_model("students", "StudentSubmission")

        graded = NewSubmission.objects.get(pk=graded_pk)
        self.assertEqual(graded.ai_grading_completed_at, graded_at)
        self.assertEqual(
            graded.ai_grading_completed_at - graded.ai_graded_at,
            timedelta(seconds=42),
        )
        self.assertIsNone(
            NewSubmission.objects.get(pk=never_pk).ai_grading_completed_at
        )
        self.assertIsNone(
            NewSubmission.objects.get(pk=untouched_pk).ai_grading_completed_at
        )

        # Idempotent: applying the backfill logic again changes nothing.
        from importlib import import_module

        backfill = import_module(
            "students.migrations.0028_backfill_ai_grading_completed_at"
        ).backfill_completed_at_from_graded_at
        backfill(new_apps, None)
        self.assertEqual(
            NewSubmission.objects.get(pk=graded_pk).ai_grading_completed_at, graded_at
        )

    def test_0027_reverses_and_reapplies_cleanly(self):
        self._migrate(BEFORE)
        self.assertEqual(
            _column_type("students_studentsubmission", "ai_grading_completed_at"),
            "date",
        )
        self._migrate(AFTER)
        self.assertEqual(
            _column_type("students_studentsubmission", "ai_grading_completed_at"),
            "timestamp with time zone",
        )


class GradingDurationDashboardTest(TransactionTestCase):
    """The consumer of the column: real endpoint, real Redis cache, real
    Postgres aggregation."""

    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Duration School")
        self.superadmin = CustomUser.objects.create_user(
            email="duration-sa@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="duration-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        self.assignment = Assignment.objects.create(
            title="A",
            course=course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.client = APIClient()
        self.client.force_authenticate(self.superadmin)

    def tearDown(self):
        cache.clear()

    def _student(self, tag):
        return CustomUser.objects.create_user(
            email=f"duration-{tag}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )

    @patch("students.services.ai_processor")
    def test_dashboard_reports_a_positive_mean_of_real_durations(self, mock_ai):
        # A historical row as 0028 leaves it: precise completion == graded_at.
        started = timezone.now() - timedelta(days=1)
        StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self._student("historical"),
            answers=[],
            ai_graded_at=started,
            ai_grading_completed_at=started + timedelta(seconds=30),
            graded_at=started + timedelta(seconds=30),
        )
        # A row graded by the pipeline after the migration.
        fresh = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self._student("fresh"),
            answers=[{"question_number": 1, "answer_html": "x"}],
        )
        mock_ai.extract_grade_with_retry.return_value = {
            "grading_summary": {
                "total_score": 8,
                "max_total_points": 10,
                "percentage": 80.0,
            },
            "grading_confidence": 90,
            "question_evaluations": [],
        }
        grade_engine(self.teacher, fresh)
        fresh.refresh_from_db()
        self.assertIsNotNone(fresh.ai_grading_completed_at)
        self.assertGreaterEqual(fresh.ai_grading_completed_at, fresh.ai_graded_at)

        expected = (
            timedelta(seconds=30) + (fresh.ai_grading_completed_at - fresh.ai_graded_at)
        ) / 2

        response = self.client.get(AI_PERF)
        self.assertEqual(response.status_code, 200)
        reported = parse_duration(
            response.data["risk_indicators"]["avg_grading_processing_time"]
        )
        self.assertIsNotNone(reported)
        self.assertGreater(reported, timedelta(0))
        self.assertLess(abs(reported - expected), timedelta(milliseconds=50))
