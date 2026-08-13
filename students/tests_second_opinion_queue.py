"""
End-to-end coverage for the teacher review queue fed by the blind second
opinion: persistence through grade_engine, the ?needs_review filter, and
both teacher resolution paths (mark-reviewed = "AI grade confirmed",
update-grade = "overridden") — each of which is a labeled data point for
the future eval loop.

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

    # ── Persistence through grade_engine ──────────────────────────────

    def test_disagreement_persists_needs_review_with_reasons(self):
        self._grade(_responder(a_score=15, b_score=0))

        self.assertTrue(self.submission.needs_review)
        [reason] = self.submission.review_reasons
        self.assertEqual(reason["type"], "grader_disagreement")
        self.assertEqual(reason["a_score"], 15)
        self.assertEqual(reason["b_score"], 0)
        # Grader A's score is the stored grade regardless of B.
        self.assertEqual(float(self.submission.score), 15.0)
        # Both rationales are stored for the side-by-side display.
        disagreements = self.submission.feedback["second_opinion"]["disagreements"]
        self.assertEqual(len(disagreements), 1)

    def test_agreement_does_not_flag(self):
        self._grade(_responder(a_score=15, b_score=15))
        self.assertFalse(self.submission.needs_review)
        self.assertIsNone(self.submission.review_reasons)
        self.assertEqual(self.submission.feedback["second_opinion"]["agreements"], [1])

    def test_regrade_that_now_agrees_clears_stale_flag(self):
        # Ledger #7.
        self._grade(_responder(a_score=15, b_score=0))
        self.assertTrue(self.submission.needs_review)

        self._grade(_responder(a_score=15, b_score=15))
        self.assertFalse(self.submission.needs_review)
        self.assertIsNone(self.submission.review_reasons)

    # ── Teacher surfaces ──────────────────────────────────────────────

    def test_needs_review_filter_lists_flagged_submissions(self):
        self._grade(_responder(a_score=15, b_score=0))
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

    def test_detail_exposes_second_opinion_block(self):
        self._grade(_responder(a_score=15, b_score=0))
        self.client.force_authenticate(user=self.teacher)

        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["needs_review"])
        self.assertEqual(len(response.data["second_opinion"]["disagreements"]), 1)

    def test_mark_reviewed_confirms_and_is_idempotent(self):
        # Ledger #15.
        self._grade(_responder(a_score=15, b_score=0))
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
        # The original disagreement entry is preserved (label data).
        self.assertTrue(
            any(
                entry.get("type") == "grader_disagreement"
                for entry in self.submission.review_reasons
            )
        )

    def test_update_grade_resolves_as_overridden(self):
        # Ledger #16.
        self._grade(_responder(a_score=15, b_score=0))
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

    def test_disagreement_severity_is_persisted(self):
        # 15 vs 0 on a 20-point question: gap fraction 0.75 → critical.
        # review_severity is the TIER-WEIGHTED sort key, not the raw
        # fraction: critical's band starts at 2/3, plus 0.75/3 within it.
        self._grade(_responder(a_score=15, b_score=0))

        [reason] = self.submission.review_reasons
        self.assertEqual(reason["tier"], "critical")
        self.assertEqual(reason["gap_fraction"], 0.75)
        self.assertAlmostEqual(self.submission.review_severity, 2 / 3 + 0.75 / 3, 5)
        self.assertEqual(self.submission.review_tier, "critical")

        stored = self.submission.feedback["second_opinion"]["disagreements"][0]
        self.assertEqual(stored["severity"]["tier"], "critical")

    def test_agreement_resets_severity(self):
        self._grade(_responder(a_score=15, b_score=0))
        self.assertIsNotNone(self.submission.review_severity)
        self.assertEqual(self.submission.review_tier, "critical")

        self._grade(_responder(a_score=15, b_score=15))
        self.assertIsNone(self.submission.review_severity)
        self.assertIsNone(self.submission.review_tier)

    def _grade_second_submission(self, responder, tag):
        """Grade a second submission on the same assignment, leaving
        self.submission pointing back at the original."""
        other_student = CustomUser.objects.create_user(
            email=f"{tag}-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        other = StudentSubmission.objects.create(
            assignment=self.submission.assignment,
            student=other_student,
            answers=[{"question_number": 1, "answer_html": "<p>my essay answer</p>"}],
        )
        original = self.submission
        self.submission = other
        self._grade(responder)
        graded = self.submission
        self.submission = original
        return graded

    def test_queue_orders_by_severity(self):
        # Ledger #9: critical disagreements surface before mild ones.
        self._grade(_responder(a_score=15, b_score=0))  # critical
        # 15 vs 10 is one rubric level, gap 5/20 = 0.25 → moderate.
        self._grade_second_submission(_responder(a_score=15, b_score=10), "mild")

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

    def test_critical_outranks_moderate_even_with_a_smaller_point_gap(self):
        # Ledger #9, the case raw gap_fraction ordering got WRONG: this
        # submission's disagreement is critical only because the graders
        # are 2 rubric levels apart (20 vs 10 on the 20/15/10/0 ladder),
        # while its gap fraction (0.5) is no larger than a moderate one.
        # Tier must win the ordering regardless.
        self._grade(_responder(a_score=20, b_score=10))
        self.assertEqual(self.submission.review_tier, "critical")

        moderate = self._grade_second_submission(
            _responder(a_score=15, b_score=10), "moderate"
        )
        self.assertEqual(moderate.review_tier, "moderate")
        # The critical one sorts higher despite both being measurable gaps.
        self.assertGreater(self.submission.review_severity, moderate.review_severity)

    def test_review_tier_filter_returns_only_criticals(self):
        # Ledger #10.
        self._grade(_responder(a_score=15, b_score=0))  # critical
        moderate = self._grade_second_submission(
            _responder(a_score=15, b_score=10), "tierfilter"
        )

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
        self._grade(_responder(a_score=15, b_score=0))
        self._grade_second_submission(_responder(a_score=15, b_score=15), "agreed")

        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(
            reverse("student-submission-list"), {"ordering": "-review_severity"}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        severities = [row["review_severity"] for row in response.data["results"]]
        self.assertIsNotNone(severities[0])
        self.assertIsNone(severities[-1])
