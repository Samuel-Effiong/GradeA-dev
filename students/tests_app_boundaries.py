"""
R-5: app boundaries between `students` and `assignments`.

* students.services no longer imports assignments.tasks (which imports
  students.services back). The formatted-grade follow-up is dispatched by
  registered task name. Proven in a fresh interpreter, where an import
  cycle would be visible as assignments.tasks appearing in sys.modules.
* The name dispatched actually resolves to the registered task.
* students.task_tracking no longer locks/deletes Assignment rows itself;
  it asks assignments.services, which refuses when the assignment has
  submissions.

Run with:
    python manage.py test students.tests_app_boundaries
"""

import os
import subprocess
import sys
from unittest.mock import patch

from django.conf import settings
from django.db import transaction
from django.test import TestCase

from assignments.models import Assignment
from assignments.services import lock_placeholder_assignment_for_cleanup
from AutoGrader.celery import app as celery_app
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import FORMATTED_GRADE_TASK_NAME, grade_engine
from users.models import CustomUser, UserTypes


class ImportCycleTest(TestCase):
    def test_students_services_does_not_pull_in_assignments_tasks(self):
        code = (
            "import os, sys, django;"
            f"os.environ.setdefault('DJANGO_SETTINGS_MODULE', {settings.SETTINGS_MODULE!r});"
            "django.setup();"
            "import students.services;"
            "sys.exit(1 if 'assignments.tasks' in sys.modules else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=settings.BASE_DIR,
            env={**os.environ, "DJANGO_SETTINGS_MODULE": settings.SETTINGS_MODULE},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"assignments.tasks was imported by students.services\n{result.stderr[-2000:]}",
        )

    def test_formatted_grade_task_name_resolves_to_the_registered_task(self):
        import assignments.tasks  # noqa: F401 - registers the task

        self.assertIn(FORMATTED_GRADE_TASK_NAME, celery_app.tasks)
        self.assertEqual(
            celery_app.tasks[FORMATTED_GRADE_TASK_NAME].name,
            "assignments.tasks.formatted_grade_async",
        )


class FollowupDispatchByNameTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="boundary-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email="boundary-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        assignment = Assignment.objects.create(
            title="A", course=course, questions=[{"question_number": 1, "points": 10}]
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
        )

    @patch("students.services.student_summary_async")
    @patch("students.services.launch_processing_task")
    @patch("students.services.ai_processor")
    def test_grade_engine_dispatches_the_formatter_by_name(
        self, mock_ai, mock_launch, mock_summary
    ):
        mock_ai.extract_grade_with_retry.return_value = {
            "grading_summary": {
                "total_score": 8,
                "max_total_points": 10,
                "percentage": 80.0,
            },
            "grading_confidence": 90,
            "question_evaluations": [],
        }

        with self.captureOnCommitCallbacks(execute=True):
            grade_engine(self.teacher, self.submission)

        mock_launch.assert_called_once()
        signature, processing_task, submission_id, prompt = mock_launch.call_args.args
        self.assertEqual(signature.task, FORMATTED_GRADE_TASK_NAME)
        self.assertEqual(submission_id, str(self.submission.id))
        self.assertEqual(processing_task.submission_id, self.submission.id)


class PlaceholderAssignmentCleanupServiceTest(TestCase):
    def setUp(self):
        teacher = CustomUser.objects.create_user(
            email="cleanup-boundary-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email="cleanup-boundary-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=teacher)
        self.course = Course.objects.create(name="C", teacher=teacher, session=session)

    def test_returns_locked_placeholder_when_it_has_no_submissions(self):
        assignment = Assignment.objects.create(course=self.course, title="Ghost")
        with transaction.atomic():
            locked = lock_placeholder_assignment_for_cleanup(assignment.id)
        self.assertEqual(locked.pk, assignment.pk)

    def test_refuses_an_assignment_that_has_submissions(self):
        assignment = Assignment.objects.create(course=self.course, title="Real")
        StudentSubmission.objects.create(
            assignment=assignment, student=self.student, answers=[]
        )
        with transaction.atomic():
            self.assertIsNone(lock_placeholder_assignment_for_cleanup(assignment.id))

    def test_missing_assignment_is_none(self):
        import uuid

        with transaction.atomic():
            self.assertIsNone(lock_placeholder_assignment_for_cleanup(uuid.uuid4()))
