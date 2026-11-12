"""
End-to-end coverage for the blind second opinion and the teacher review
queue: persistence through grade_engine, the ?needs_review filter, and both
teacher resolution paths (mark-reviewed = "AI grade confirmed",
update-grade = "overridden") - each a labeled data point for the eval loop.

R1 (owner, 2026-11-12): the second opinion still runs and is stored in
feedback['second_opinion'], but it no longer puts a submission in the queue
and no teacher payload carries it. The queue tests below flag a paper with
an answer the reader could not find instead (students/review_state.py).

Run with:
    python manage.py test students.tests_second_opinion_queue
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.models import Assignment
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.review_state import review_sort_key
from users.models import CustomUser, UserTypes

A_MODEL = "primary-model"
B_MODEL = "second-model"

SECOND_OPINION_SETTINGS = {
    "GRADING_SECOND_OPINION_ENABLED": True,
    "GRADING_SECOND_OPINION_MODELS": [B_MODEL],
    "GRADING_SECOND_OPINION_MIN_CONFIDENCE": 80,
    "GRADING_SECOND_OPINION_HIGH_POINTS": 15,
    "GRADING_SECOND_OPINION_SAMPLE_RATE": 0,
}


def _ai_response(payload, model=A_MODEL):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps(payload)
    response.usage.total_tokens = 100
    response.model = model
    return response


def _evaluation(score):
    return {
        "question_number": 1,
        "question_text": "Discuss.",
        "score_awarded": score,
        "max_points": 20,
        "level_achieved": "good",
        "evaluation_rationale": "Because.",
        "evidence_quotes": ["my essay answer"],
    }


def _responder(a_score=15, b_score=15, confidence=50):
    """Grader A single-pass response + grader B batch response."""

    def respond(**kwargs):
        if kwargs.get("override_model") == B_MODEL:
            return _ai_response(
                {"question_evaluations": [_evaluation(b_score)]}, model=B_MODEL
            )
        return _ai_response(
            {
                "question_evaluations": [_evaluation(a_score)],
                "grading_summary": {},
                "grading_confidence": confidence,
                "overall_performance_analysis": "ok",
                "recommendations": [],
            }
        )

    return respond


@override_settings(**SECOND_OPINION_SETTINGS)
class SecondOpinionQueueTest(APITestCase):
    def setUp(self):
        stamp = timezone.now().timestamp()
        self.teacher = CustomUser.objects.create_user(
            email=f"queue-teacher-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        student = CustomUser.objects.create_user(
            email=f"queue-student-{stamp}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        # A single 20-point essay: points >= high-points threshold, so the
        # second opinion triggers deterministically (sample rate is 0).
        assignment = Assignment.objects.create(
            title="Essay assignment",
            course=course,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Discuss.",
                    "question_type": "ESSAY",
                    "points": 20,
                    "options": [],
                    # Four levels, not two: every score this file's tests use
                    # (20, 15, 10, 0) must be an exact rubric-level value, or
                    # AIProcessor._finalize_grading_result's rubric-level
                    # snapping (score corrected to the NEAREST level) would
                    # silently round a fixture score to the closest of only
                    # two levels — breaking these deliberately-crafted
                    # gap-fraction assertions (e.g. 15 vs 0 = 0.75).
                    "rubric": [
                        {"level": "excellent", "description": "Great", "points": 20},
                        {"level": "good", "description": "Good", "points": 15},
                        {"level": "fair", "description": "Fair", "points": 10},
                        {"level": "poor", "description": "Poor", "points": 0},
                    ],
                    "model_answer": "A model essay.",
                }
            ],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "<p>my essay answer</p>"}],
        )

    def _grade(self, responder):
        from students.services import grade_engine

        with patch.object(AIProcessor, "execute_graded_task", side_effect=responder):
            grade_engine(self.teacher, self.submission)
        self.submission.refresh_from_db()

    def _flag_missing_answer(self, submission=None):
        """Put a graded paper in the queue the way grading does for an
        answer the reader could not find: critical."""
        submission = submission or self.submission
        StudentSubmission.objects.filter(pk=submission.pk).update(
            needs_review=True,
            review_reasons=[{"type": "answer_not_found", "question_number": 1}],
            review_severity=review_sort_key("critical", 1.0),
            review_tier="critical",
        )
        submission.refresh_from_db()

    def _flag_off_topic(self, submission):
        """...and for an off-topic answer: moderate."""
        StudentSubmission.objects.filter(pk=submission.pk).update(
            needs_review=True,
            review_reasons=[{"type": "extraction_flag", "kind": "OFF_TOPIC"}],
            review_severity=review_sort_key("moderate", None),
            review_tier="moderate",
        )
        submission.refresh_from_db()

    # ── Persistence through grade_engine ──────────────────────────────

    def test_disagreement_is_stored_and_does_not_flag(self):
        self._grade(_responder(a_score=15, b_score=0))

        self.assertFalse(self.submission.needs_review)
        self.assertIsNone(self.submission.review_reasons)
        self.assertIsNone(self.submission.review_severity)
        self.assertIsNone(self.submission.review_tier)
        # Grader A's score is the stored grade regardless of B.
        self.assertEqual(float(self.submission.score), 15.0)
        # Both rationales and the tier are stored, for our own checks.
        [stored] = self.submission.feedback["second_opinion"]["disagreements"]
        self.assertEqual(stored["severity"]["tier"], "critical")
        self.assertEqual(stored["severity"]["gap_fraction"], 0.75)

    def test_agreement_does_not_flag(self):
        self._grade(_responder(a_score=15, b_score=15))
        self.assertFalse(self.submission.needs_review)
        self.assertIsNone(self.submission.review_reasons)
        self.assertEqual(self.submission.feedback["second_opinion"]["agreements"], [1])

    def test_regrade_clears_a_stale_disagreement_flag(self):
        # Ledger #7: a row flagged by a disagreement before R1 is cleared by
        # its next grading.
        self._grade(_responder(a_score=15, b_score=0))
        StudentSubmission.objects.filter(pk=self.submission.pk).update(
            needs_review=True,
            review_reasons=[{"type": "grader_disagreement", "tier": "critical"}],
            review_tier="critical",
        )
        self._grade(_responder(a_score=15, b_score=0))
        self.assertFalse(self.submission.needs_review)
        self.assertIsNone(self.submission.review_reasons)

    # ── Teacher surfaces ──────────────────────────────────────────────

    def test_needs_review_filter_lists_flagged_submissions(self):
        self._grade(_responder(a_score=15, b_score=0))
        self._flag_missing_answer()
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(
            reverse("student-submission-list"), {"needs_review": "true"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row["id"] for row in response.data["results"]]
        self.assertIn(str(self.submission.id), ids)

        response = self.client.get(
            reverse("student-submission-list"), {"needs_review": "false"}
        )
        ids = [row["id"] for row in response.data["results"]]
        self.assertNotIn(str(self.submission.id), ids)

    def test_detail_carries_no_second_opinion_block(self):
        self._grade(_responder(a_score=15, b_score=0))
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["needs_review"])
        self.assertNotIn("second_opinion", response.data)

    def test_detail_exposes_question_breakdown_always(self):
        # The full per-question breakdown is present regardless of
        # needs_review, so a teacher can inspect any submission in full.
        self._grade(_responder(a_score=15, b_score=15))  # agreement, no flag
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["needs_review"])
        [entry] = response.data["question_breakdown"]
        self.assertEqual(str(entry["question_number"]), "1")
        self.assertEqual(entry["score_awarded"], 15)
        self.assertEqual(entry["question_text"], "Discuss.")

    def test_mark_reviewed_confirms_and_is_idempotent(self):
        # Ledger #15.
        self._grade(_responder(a_score=15, b_score=0))
        self._flag_missing_answer()
        self.client.force_authenticate(user=self.teacher)
        url = reverse(
            "student-submission-mark-reviewed", kwargs={"pk": self.submission.pk}
        )

        first = self.client.post(url)
        second = self.client.post(url)

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(second.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.needs_review)
        resolutions = [
            entry
            for entry in self.submission.review_reasons
            if entry.get("resolved") == "confirmed"
        ]
        # Exactly one resolution entry despite the double POST.
        self.assertEqual(len(resolutions), 1)
        # The original entry is preserved (label data).
        self.assertTrue(
            any(
                entry.get("type") == "answer_not_found"
                for entry in self.submission.review_reasons
            )
        )

    def test_update_grade_resolves_as_overridden(self):
        # Ledger #16.
        self._grade(_responder(a_score=15, b_score=0))
        self._flag_missing_answer()
        self.client.force_authenticate(user=self.teacher)

        response = self.client.patch(
            reverse(
                "student-submission-update-grade", kwargs={"pk": self.submission.pk}
            ),
            data={"score": 10},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.submission.refresh_from_db()
        self.assertFalse(self.submission.needs_review)
        self.assertTrue(
            any(
                entry.get("resolved") == "overridden"
                for entry in self.submission.review_reasons
            )
        )
        self.assertEqual(float(self.submission.score), 10.0)

    # ── Severity (triage) ─────────────────────────────────────────────

    def _second_submission(self, tag):
        other_student = CustomUser.objects.create_user(
            email=f"{tag}-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        return StudentSubmission.objects.create(
            assignment=self.submission.assignment,
            student=other_student,
            answers=[{"question_number": 1, "answer_html": "<p>my essay answer</p>"}],
        )

    def test_queue_orders_by_severity(self):
        # Ledger #9: critical reasons surface before moderate ones.
        self._flag_missing_answer()
        self._flag_off_topic(self._second_submission("mild"))

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            reverse("student-submission-list"),
            {"needs_review": "true", "ordering": "-review_severity"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        severities = [row["review_severity"] for row in response.data["results"]]
        self.assertEqual(severities, sorted(severities, reverse=True))
        tiers = [row["review_tier"] for row in response.data["results"]]
        self.assertEqual(tiers, ["critical", "moderate"])

    def test_critical_outranks_moderate_even_with_a_smaller_gap(self):
        # The case raw gap_fraction ordering got WRONG: tier must win.
        self.assertGreater(
            review_sort_key("critical", 0.5), review_sort_key("moderate", 0.9)
        )
        self.assertGreater(
            review_sort_key("moderate", 0.5), review_sort_key("borderline", 0.9)
        )

    def test_review_tier_filter_returns_only_criticals(self):
        # Ledger #10.
        self._flag_missing_answer()
        moderate = self._second_submission("tierfilter")
        self._flag_off_topic(moderate)

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            reverse("student-submission-list"),
            {"needs_review": "true", "review_tier": "critical"},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = [row["id"] for row in response.data["results"]]
        self.assertIn(str(self.submission.id), ids)
        self.assertNotIn(str(moderate.id), ids)

    def test_severity_ordering_puts_unflagged_submissions_last(self):
        # Postgres sorts NULLs FIRST on DESC, so without the nulls_last
        # override an unqualified ?ordering=-review_severity listed every
        # un-flagged submission ahead of the actual review queue.
        self._flag_missing_answer()
        self._second_submission("agreed")

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            reverse("student-submission-list"), {"ordering": "-review_severity"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        severities = [row["review_severity"] for row in response.data["results"]]
        self.assertIsNotNone(severities[0])
        self.assertIsNone(severities[-1])
