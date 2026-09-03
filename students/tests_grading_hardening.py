"""
Regression coverage for the persistence-side grading hardening:

- update_grade (the teacher's manual score override) validates the score
  against the assignment's maximum. Before this fix a teacher could PATCH
  500 on a 10-point assignment: the score was stored as-is, and any
  percentage >= 1000 crashed at save time on the 5-digit decimal column.

- The API's max_points fields serve the grading-time denominator
  (submission.max_points) when one is stored, so the displayed score and
  its denominator can never disagree; assignment.total_points remains the
  fallback for ungraded rows.

- upload_answers_engine persists the extractor's extraction_confidence.
  Before this fix the field was silently dropped on every path, so
  StudentSubmission.extraction_confidence stayed 0 forever while the
  dashboard threshold-flagged on it.

- _reconcile_formatted_grade_numbers forces the student-facing formatted
  grade's numbers to match the authoritative stored grade — the formatter
  prompt forbids changing numbers, but nothing else verified the LLM's
  restatement.

Run with:
    python manage.py test students.tests_grading_hardening
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from assignments.tasks import _reconcile_formatted_grade_numbers
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.serializers import StudentSubmissionListSerializer
from students.services import upload_answers_engine
from users.models import CustomUser, UserTypes


def _make_classroom(teacher):
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        total_points=10,
        questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
    )
    return course, assignment


class UpdateGradeClampTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="clamp-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email="clamp-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        _, assignment = _make_classroom(self.teacher)
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "x"}],
            score=8,
            max_points=10,
            score_percentage=80,
            feedback={
                "grading_summary": {
                    "total_score": 8,
                    "max_total_points": 10,
                    "percentage": 80.0,
                },
                "question_evaluations": [],
            },
        )

        from billing.models import CreditBucket, CreditBucketType, CreditWallet

        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )
        self.client.force_authenticate(user=self.teacher)

    @patch("students.views.formatted_grade_async")
    def test_score_above_max_is_rejected_with_400(self, mock_formatted):
        response = self.client.patch(self.url, {"score": 500}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.submission.refresh_from_db()
        # Nothing was persisted from the rejected request.
        self.assertEqual(float(self.submission.score), 8)
        self.assertEqual(float(self.submission.score_percentage), 80)
        mock_formatted.delay.assert_not_called()

    @patch("students.views.formatted_grade_async")
    def test_negative_score_is_rejected_with_400(self, mock_formatted):
        response = self.client.patch(self.url, {"score": -3}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("students.views.formatted_grade_async")
    def test_valid_override_is_stored_with_consistent_percentage(self, mock_formatted):
        response = self.client.patch(self.url, {"score": 7}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 7)
        self.assertEqual(float(self.submission.score_percentage), 70)
        self.assertTrue(self.submission.was_regraded)
        # The stored AI JSON was kept in sync with the override.
        self.assertEqual(self.submission.feedback["grading_summary"]["total_score"], 7)
        self.assertEqual(self.submission.feedback["grading_summary"]["percentage"], 70)
        mock_formatted.delay.assert_called_once()

    @patch("students.views.formatted_grade_async")
    def test_missing_grading_summary_falls_back_to_stored_max_points(
        self, mock_formatted
    ):
        # A malformed feedback dict must not 500 (the old code KeyError'd);
        # the stored max_points column is the fallback denominator.
        self.submission.feedback = {"question_evaluations": []}
        self.submission.save(update_fields=["feedback"])

        response = self.client.patch(self.url, {"score": 5}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 5)
        self.assertEqual(float(self.submission.score_percentage), 50)


class SerializerMaxPointsTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="maxpoints-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.student = CustomUser.objects.create_user(
            email="maxpoints-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        _, self.assignment = _make_classroom(self.teacher)

    def test_graded_submission_serves_its_grading_time_denominator(self):
        # The assignment was edited to 50 points AFTER this submission was
        # graded out of 10 — the API must keep serving the denominator the
        # stored score was actually computed against.
        submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[],
            score=8,
            max_points=10,
            score_percentage=80,
        )
        self.assignment.total_points = 50
        self.assignment.save(update_fields=["total_points"])

        data = StudentSubmissionListSerializer(submission).data
        self.assertEqual(data["max_points"], 10)

    def test_ungraded_submission_falls_back_to_assignment_total(self):
        submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers=[],
        )
        data = StudentSubmissionListSerializer(submission).data
        self.assertEqual(data["max_points"], self.assignment.total_points)


class ExtractionConfidencePersistenceTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="confidence-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        _, self.assignment = _make_classroom(self.teacher)

    @patch("students.services.ai_processor")
    def test_upload_persists_extraction_confidence(self, mock_ai):
        mock_ai.extract_answer_with_retry.return_value = {
            "student_name": "",
            "student_id": "",
            "answers": [{"question_number": 1, "answer_html": "an answer"}],
            "extraction_confidence": 55,
            "feedback": "",
        }

        submission = upload_answers_engine(
            self.assignment, content=[], request_user=self.teacher
        )

        submission.refresh_from_db()
        self.assertEqual(submission.extraction_confidence, 55)

    @patch("students.services.ai_processor")
    def test_junk_confidence_is_coerced_not_crashed(self, mock_ai):
        mock_ai.extract_answer_with_retry.return_value = {
            "student_name": "",
            "student_id": "",
            "answers": [{"question_number": 1, "answer_html": "an answer"}],
            "extraction_confidence": "very high",
        }

        submission = upload_answers_engine(
            self.assignment, content=[], request_user=self.teacher
        )

        submission.refresh_from_db()
        self.assertEqual(submission.extraction_confidence, 0)


class ReconcileFormattedGradeNumbersTest(TestCase):
    def _submission(self):
        teacher = CustomUser.objects.create_user(
            email=f"fmt-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"fmt-student-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        _, assignment = _make_classroom(teacher)
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[],
            score=Decimal("8.00"),
            max_points=10,
            score_percentage=Decimal("80.00"),
            feedback={
                "question_evaluations": [
                    {"question_number": 1, "score_awarded": 8, "max_points": 10}
                ]
            },
        )

    def test_hallucinated_numbers_are_overwritten_from_stored_grade(self):
        submission = self._submission()
        formatted = {
            "overall_performance_summary": {
                # The formatter hallucinated a different total.
                "score_statement": "You scored 95 out of 100 points (95%).",
            },
            "question_by_question_breakdown": [
                {
                    "question_number": 1,
                    "max_score": 999,
                    "score_awarded": 999,
                    "narrative": "…",
                }
            ],
        }

        result = _reconcile_formatted_grade_numbers(formatted, submission)

        statement = result["overall_performance_summary"]["score_statement"]
        self.assertIn("8 out of 10", statement)
        self.assertIn("80.00%", statement)
        self.assertNotIn("95", statement)

        breakdown = result["question_by_question_breakdown"][0]
        self.assertEqual(breakdown["max_score"], 10)
        self.assertEqual(breakdown["score_awarded"], 8)

    def test_string_question_number_still_matches_for_correction(self):
        submission = self._submission()
        formatted = {
            "question_by_question_breakdown": [
                {"question_number": "1", "max_score": 999}
            ]
        }

        result = _reconcile_formatted_grade_numbers(formatted, submission)
        self.assertEqual(result["question_by_question_breakdown"][0]["max_score"], 10)

    def test_unexpected_shapes_are_left_untouched(self):
        submission = self._submission()
        self.assertEqual(
            _reconcile_formatted_grade_numbers("plain text", submission),
            "plain text",
        )
        self.assertEqual(_reconcile_formatted_grade_numbers(None, submission), None)
        # A dict without the known keys passes through unchanged.
        self.assertEqual(
            _reconcile_formatted_grade_numbers({"foo": 1}, submission), {"foo": 1}
        )
