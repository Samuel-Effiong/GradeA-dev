"""FR-A-01 for the grading call sites: exactly one well-formed AuditEvent
per branch of grading request (students/views.py grade_async,
assignments/views.py grade_all_submission) and grading outcome
(assignments/tasks.py grade_engine_async's success and failure paths).
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import GRADING_ASSIGNMENT_PROMPT
from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import grade_engine_async
from audit.enums import AuditAction, AuditOutcome, ErrorClass
from audit.models import AuditEvent
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from billing.refusals import PERMANENT_AI_REFUSALS
from classrooms.models import Course, Session
from students.models import BackgroundProcessingTask, StudentSubmission
from users.models import CustomUser, UserTypes


def make_classroom(prefix):
    teacher = CustomUser.objects.create_user(
        email=f"{prefix}-teacher@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
    )
    student = CustomUser.objects.create_user(
        email=f"{prefix}-student@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
    )
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[
            {
                "question_number": 1,
                "question_text": "Q1?",
                "points": 10,
                "model_answer": "4",
            }
        ],
    )
    wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
    CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=CreditBucketType.MONTHLY,
        total_credits=100_000,
        used_credits=0,
        expires_at=timezone.now() + timedelta(days=30),
    )
    return teacher, student, course, assignment


def make_submission(assignment, student):
    return StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers=[{"question_number": 1, "answer_html": "An answer."}],
    )


def valid_grading_result(score=8, max_points=10):
    return {
        "grading_summary": {
            "total_score": score,
            "max_total_points": max_points,
            "percentage": round(score / max_points * 100, 2),
        },
        "grading_confidence": 90,
        "question_evaluations": [],
    }


class GradeAsyncRequestedAuditEventTest(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "grade-async-requested"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.url = reverse(
            "student-submission-grade-async", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.views.grade_engine_async")
    def test_a_successful_dispatch_emits_exactly_one_requested_event(self, mock_task):
        mock_task.delay.return_value.id = "fake-celery-task-id"

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.target_type, "StudentSubmission")
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))
        self.assertEqual(event.metadata["submission_id"], str(self.submission.id))

    @patch("students.views.grade_engine_async")
    def test_a_broker_outage_emits_no_requested_event(self, mock_task):
        """launch_processing_task raises before the emit call is reached -
        H-40 tracks this gap (no audit trail for a dispatch that never
        happened), so this locks the CURRENT behaviour, not the ideal one."""
        mock_task.delay.side_effect = ConnectionError("broker unreachable")

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(
            AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED).count(), 0
        )


class GradeAllRequestedAuditEventTest(APITestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "grade-all-requested"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.url = reverse("assignment-grade-all", kwargs={"pk": self.assignment.pk})
        self.client.force_authenticate(user=self.teacher)

    @patch("assignments.views.grade_engine_async")
    def test_batch_grading_emits_exactly_one_requested_event_per_submission(
        self, mock_task
    ):
        mock_task.delay.return_value.id = "fake-celery-task-id"

        response = self.client.post(self.url)

        self.assertIn(
            response.status_code, (status.HTTP_200_OK, status.HTTP_202_ACCEPTED)
        )
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))


class GradeEngineAsyncOutcomeAuditEventTest(TestCase):
    """Runs grade_engine_async synchronously (.apply()) to lock its
    GRADING_COMPLETED/GRADING_FAILED emission, independent of Celery/broker
    concerns already covered above."""

    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = make_classroom(
            "grade-engine-outcome"
        )
        self.submission = make_submission(self.assignment, self.student)
        self.processing_task = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            assignment=self.assignment,
            submission=self.submission,
            task_type="submission_grading",
        )

    def run_task(self):
        return grade_engine_async.apply(
            args=(str(self.teacher.id), str(self.submission.id)),
            kwargs={"processing_task_id": str(self.processing_task.id)},
        )

    @patch("assignments.tasks.grade_engine")
    def test_a_successful_grade_emits_exactly_one_completed_event(self, mock_grade):
        # BE-A-09 #2 needs the model that actually served this run on the
        # returned submission, exactly as ai_processor/services.py leaves
        # it (json_data["grading_model"] -> submission.feedback).
        self.submission.feedback = {"grading_model": "x-ai/grok-4.3"}
        mock_grade.return_value = self.submission

        outcome = self.run_task()

        self.assertTrue(outcome.successful())
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_COMPLETED)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertIsNone(event.error_class)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.target_type, "StudentSubmission")
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(event.metadata["assignment_id"], str(self.assignment.id))
        self.assertEqual(event.metadata["model"], "x-ai/grok-4.3")
        # S5 (NFR-OBS-04): the exact grading prompt behind this grade.
        self.assertEqual(
            event.metadata["prompt_version"], GRADING_ASSIGNMENT_PROMPT.version
        )

    @patch("assignments.tasks.grade_engine")
    def test_a_successful_grade_with_no_captured_model_has_no_model_metadata(
        self, mock_grade
    ):
        # feedback absent (e.g. a mock return with no .feedback set at all)
        # must not raise AttributeError trying to read a model off it.
        self.submission.feedback = None
        mock_grade.return_value = self.submission

        outcome = self.run_task()

        self.assertTrue(outcome.successful())
        event = AuditEvent.objects.get(action=AuditAction.GRADING_COMPLETED)
        self.assertIsNone(event.metadata.get("model"))

    @patch("assignments.tasks.grade_engine")
    def test_a_system_error_emits_exactly_one_failed_event_classed_system(
        self, mock_grade
    ):
        mock_grade.side_effect = RuntimeError("unexpected grading failure")

        outcome = self.run_task()

        self.assertTrue(outcome.failed())
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_FAILED)
        self.assertEqual(events.count(), 1)
        event = events.get()
        self.assertEqual(event.outcome, AuditOutcome.FAILURE)
        self.assertEqual(event.error_class, ErrorClass.SYSTEM)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.target_id, self.submission.id)
        self.assertEqual(
            event.metadata["prompt_version"], GRADING_ASSIGNMENT_PROMPT.version
        )

    @patch("assignments.tasks.classify_infra_error")
    @patch("assignments.tasks.grade_engine")
    def test_a_provider_error_emits_exactly_one_failed_event_classed_provider(
        self, mock_grade, mock_classify
    ):
        mock_grade.side_effect = ConnectionError("lost connection to grading service")
        mock_classify.return_value = "We lost connection to the grading service."

        outcome = self.run_task()

        self.assertTrue(outcome.failed())
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_FAILED)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().error_class, ErrorClass.PROVIDER)

    @patch("assignments.tasks.grade_engine")
    def test_a_model_refusal_emits_exactly_one_failed_event_classed_model(
        self, mock_grade
    ):
        refusal_type = next(iter(PERMANENT_AI_REFUSALS))
        mock_grade.side_effect = refusal_type("content policy refusal")

        outcome = self.run_task()

        self.assertTrue(outcome.failed())
        events = AuditEvent.objects.filter(action=AuditAction.GRADING_FAILED)
        self.assertEqual(events.count(), 1)
        self.assertEqual(events.get().error_class, ErrorClass.MODEL)
