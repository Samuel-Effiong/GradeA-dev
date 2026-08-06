"""
Regression coverage for H2: if a batch call (or the single-pass call, on
the small-assignment path) returned fewer question_evaluations than
questions it was asked to grade, the missing questions were never
reconciled. They still counted toward max_total_points but contributed
nothing to total_score — silently deflating the grade, with no error, no
flag, and nothing to alert a teacher that anything had gone wrong.

The fix is AIProcessor._missing_question_numbers, called right after
parsing the model's response in both _grade_question_batch (per batch)
and the single-pass branch of _grade_student_submission_impl (against the
full rubric). An incomplete response is now treated as a retryable
failure — exactly like an empty or unparseable response already was —
rather than silently accepted.

Run with:
    python manage.py test ai_processor.tests_grading_completeness
"""

import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TransactionTestCase
from django.utils import timezone

from ai_processor.services import GRADING_QUESTIONS_PER_CHUNK, AIProcessor, ai_processor
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import CustomUser, UserTypes


def make_ai_response(tokens=100, content='{"result": "ok"}'):
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = tokens
    return response


class MissingQuestionNumbersTest(SimpleTestCase):
    """Unit-level coverage of the reconciliation check itself."""

    def setUp(self):
        self.processor = AIProcessor()

    def test_no_missing_when_all_present(self):
        questions = [{"question_number": 1}, {"question_number": 2}]
        evaluations = [
            {"question_number": 1, "score_awarded": 5},
            {"question_number": 2, "score_awarded": 5},
        ]
        self.assertEqual(
            self.processor._missing_question_numbers(evaluations, questions), []
        )

    def test_detects_a_dropped_question(self):
        questions = [{"question_number": 1}, {"question_number": 2}]
        evaluations = [{"question_number": 1, "score_awarded": 5}]
        missing = self.processor._missing_question_numbers(evaluations, questions)
        self.assertEqual(missing, [{"question_number": 2}])

    def test_empty_evaluations_means_everything_is_missing(self):
        questions = [{"question_number": 1}, {"question_number": 2}]
        missing = self.processor._missing_question_numbers([], questions)
        self.assertEqual(len(missing), 2)

    def test_int_vs_string_question_number_is_not_a_false_positive(self):
        # The rubric stores question_number as an int; the model quotes it
        # as a string. This must still count as present, not missing.
        questions = [{"question_number": 1}]
        evaluations = [{"question_number": "1", "score_awarded": 5}]
        self.assertEqual(
            self.processor._missing_question_numbers(evaluations, questions), []
        )


class GradingCompletenessPipelineTestBase(TransactionTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def _make_teacher_with_credits(self, credits=500_000):
        teacher = CustomUser.objects.create_user(
            email=f"completeness-{timezone.now().timestamp()}@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        plan = SubscriptionPlan.objects.create(
            name=f"plan-{teacher.id}",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.PRO,
            interval=BillingInterval.MONTHLY,
            monthly_credits=20_000,
            carry_over_percent=25,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=teacher,
            plan=plan,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + timedelta(days=30),
            is_trial=False,
            auto_renew=True,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=credits,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        return teacher


class BatchPathCompletenessTest(GradingCompletenessPipelineTestBase):
    """
    Uses a 10-question rubric (> GRADING_QUESTIONS_PER_CHUNK=5) so this
    genuinely exercises the batched path's own per-batch retry loop in
    _grade_question_batch — not the single-pass path's outer-wrapper
    retry, which is covered separately by SinglePassCompletenessTest.
    Batch 1 covers questions 1-5, batch 2 covers 6-10.
    """

    def test_incomplete_batch_is_retried_then_succeeds(self):
        rubric = json.dumps([{"question_number": i, "points": 5} for i in range(1, 11)])
        answers = json.dumps(
            [{"question_number": i, "answer_html": "x"} for i in range(1, 11)]
        )
        call_count = {"n": 0}

        def fake_model(*args, **kwargs):
            call_count["n"] += 1
            n = call_count["n"]
            if n == 1:
                # Batch 1, attempt 1: drops question 5.
                content = json.dumps(
                    {
                        "question_evaluations": [
                            {"question_number": i, "score_awarded": 4}
                            for i in range(1, 5)
                        ]
                    }
                )
            elif n == 2:
                # Batch 1, attempt 2 (retry): complete.
                content = json.dumps(
                    {
                        "question_evaluations": [
                            {"question_number": i, "score_awarded": 4}
                            for i in range(1, 6)
                        ]
                    }
                )
            elif n == 3:
                # Batch 2: complete on its first attempt.
                content = json.dumps(
                    {
                        "question_evaluations": [
                            {"question_number": i, "score_awarded": 4}
                            for i in range(6, 11)
                        ]
                    }
                )
            else:
                # Overall summary call — its content is irrelevant here,
                # since _build_overall_grading_summary overwrites
                # grading_summary itself regardless of what's returned.
                content = json.dumps({})
            return make_ai_response(tokens=100, content=content)

        teacher = self._make_teacher_with_credits()
        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            result = self.processor._grade_student_submission_impl(
                user=teacher, rubric_json=rubric, answer_json=answers
            )

        self.assertEqual(
            call_count["n"], 4, "expected batch1 x2 + batch2 x1 + summary x1"
        )
        self.assertEqual(result["grading_summary"]["total_score"], 40)
        self.assertEqual(result["grading_summary"]["max_total_points"], 50)

    def test_persistently_incomplete_batch_fails_loudly_not_silently(self):
        rubric = json.dumps([{"question_number": i, "points": 5} for i in range(1, 11)])
        answers = json.dumps(
            [{"question_number": i, "answer_html": "x"} for i in range(1, 11)]
        )

        def always_incomplete(*args, **kwargs):
            # Batch 1 always drops question 5, on every one of its 3
            # internal retry attempts.
            content = json.dumps(
                {
                    "question_evaluations": [
                        {"question_number": i, "score_awarded": 4} for i in range(1, 5)
                    ]
                }
            )
            return make_ai_response(tokens=100, content=content)

        teacher = self._make_teacher_with_credits()
        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=always_incomplete
        ):
            # The load-bearing assertion: this must raise (and, upstream,
            # get refunded) rather than silently return a deflated grade
            # that's missing question 5's points from every batch.
            with self.assertRaises(Exception):
                self.processor._grade_student_submission_impl(
                    user=teacher, rubric_json=rubric, answer_json=answers
                )


class SinglePassCompletenessTest(GradingCompletenessPipelineTestBase):
    def test_incomplete_single_pass_response_is_retried_by_outer_wrapper(self):
        rubric = json.dumps(
            [
                {"question_number": 1, "points": 10},
                {"question_number": 2, "points": 10},
            ]
        )
        answers = json.dumps(
            [
                {"question_number": 1, "answer_html": "x"},
                {"question_number": 2, "answer_html": "y"},
            ]
        )
        call_count = {"n": 0}

        def fake_model(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Drops question 2 entirely.
                content = json.dumps(
                    {
                        "question_evaluations": [
                            {"question_number": 1, "score_awarded": 8}
                        ]
                    }
                )
            else:
                content = json.dumps(
                    {
                        "question_evaluations": [
                            {"question_number": 1, "score_awarded": 8},
                            {"question_number": 2, "score_awarded": 9},
                        ]
                    }
                )
            return make_ai_response(tokens=100, content=content)

        teacher = self._make_teacher_with_credits()
        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            result = ai_processor.extract_grade_with_retry(
                teacher, rubric, answers, max_retries=3
            )

        # extract_grade_with_retry (the outer wrapper) is what catches
        # this, since the single-pass branch has no retry loop of its own.
        self.assertEqual(call_count["n"], 2)
        self.assertEqual(result["grading_summary"]["total_score"], 17)
