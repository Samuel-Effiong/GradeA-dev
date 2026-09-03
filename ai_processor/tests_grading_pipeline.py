"""
Regression coverage for the C2 fix: grade_student_submission used to run
inside a single @transaction.atomic wrapping every AI call in a grading
run. Because CreditWallet.consume_credits() takes select_for_update() on
the wallet row, that held the teacher's wallet locked for the entire
(multi-minute, multi-call) run, and serialized every other grading task for
the same teacher behind it (see assignments/views.py::grade_all_submission,
which fans out one Celery task per submission for one teacher).

That transaction also had a side effect relied on elsewhere: a failed run
rolled back every credit charge it had made. Removing the transaction means
each execute_graded_task call now commits its charge independently, so an
explicit refund mechanism (billing/refunds.py::billing_refund_scope +
SubscriptionService.refund_credits) replaces the old rollback-based one.

Run with:
    python manage.py test ai_processor.tests_grading_pipeline

Uses TransactionTestCase (not TestCase) throughout: a plain TestCase wraps
every test in its own atomic block, which makes `connection.in_atomic_block`
unconditionally True regardless of the code under test — the whole point of
T3/T4 below is to observe real commit/lock behavior across connections.
"""

import json
import math
import threading
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.db import OperationalError, connection
from django.db import transaction as django_transaction
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ai_processor.services import GRADING_QUESTIONS_PER_CHUNK, AIProcessor, ai_processor
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    PlanCategory,
    PlanTier,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import CustomUser, UserTypes


def make_ai_response(tokens=100, content='{"result": "ok"}'):
    """Stand-in for the OpenAI SDK response shape execute_graded_task reads:
    response.choices[0].message.content and response.usage.total_tokens."""
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = content
    response.usage.total_tokens = tokens
    return response


# A rubric with more than GRADING_QUESTIONS_PER_CHUNK questions, to force
# grade_student_submission onto the batched path: _grade_question_batch x N
# (N = ceil(_QUESTION_COUNT / GRADING_QUESTIONS_PER_CHUNK) batches) + one
# _build_overall_grading_summary call. Derived from the live constant
# rather than hardcoded, so a future chunk-size tune doesn't silently
# desync these fixtures from reality the way a hardcoded "3 batches"
# did when GRADING_QUESTIONS_PER_CHUNK moved from 5 to 10.
_QUESTION_COUNT = 12
_NUM_BATCHES = math.ceil(_QUESTION_COUNT / GRADING_QUESTIONS_PER_CHUNK)
RUBRIC_12Q = json.dumps(
    [
        {"question_number": i, "question_text": f"Question {i}?", "points": 5}
        for i in range(1, _QUESTION_COUNT + 1)
    ]
)
ANSWERS_12Q = json.dumps(
    [
        {"question_number": i, "answer_html": f"Answer to question {i}."}
        for i in range(1, _QUESTION_COUNT + 1)
    ]
)
assert _QUESTION_COUNT > GRADING_QUESTIONS_PER_CHUNK  # sanity: batched path


def _batch_evaluations_json(batch_index, chunk_size=GRADING_QUESTIONS_PER_CHUNK):
    """
    A question_evaluations response covering exactly the question_numbers
    the given 1-based batch actually contains (batch 1 = questions 1-5,
    batch 2 = 6-10, ...). _grade_question_batch cross-checks returned
    question_numbers against the batch it sent (H2 fix —
    AIProcessor._missing_question_numbers), so a fixture that always
    claims to be grading questions 1-5 regardless of which batch is being
    requested is now correctly rejected as incomplete.
    """
    start = (batch_index - 1) * chunk_size + 1
    end = min(batch_index * chunk_size, _QUESTION_COUNT)
    return json.dumps(
        {
            "question_evaluations": [
                {
                    "question_number": i,
                    "score_awarded": 4,
                    # Evidence contract: awarded points must cite a
                    # verbatim span of the student's answer.
                    "evidence_quotes": [f"Answer to question {i}"],
                }
                for i in range(start, end + 1)
            ]
        }
    )


SUMMARY_JSON = json.dumps(
    {
        "overall_performance_analysis": "Solid work overall.",
        "grader_meta_analysis": "Consistent scoring across batches.",
        "grading_confidence": 90,
        "recommendations": ["Review question 3."],
    }
)


@override_settings(
    GRADING_SECOND_OPINION_ENABLED=False, GRADING_ANSWER_CACHE_ENABLED=False
)
class GradingPipelineTestBase(TransactionTestCase):
    def setUp(self):
        self.processor = AIProcessor()

    def _make_teacher_with_credits(self, credits=500_000):
        teacher = CustomUser.objects.create_user(
            email=f"grading-{timezone.now().timestamp()}@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        # can_user_access_ai (billing/access_control.py) requires an active
        # subscription in addition to a non-empty wallet.
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


class NoOpenTransactionAcrossAICallsTest(GradingPipelineTestBase):
    """T3 — the regression lock for C2. Fails on the pre-fix code (where
    grade_student_submission was wrapped in @transaction.atomic, making
    every one of these True) and passes after it."""

    def test_no_transaction_is_held_during_any_ai_call(self):
        teacher = self._make_teacher_with_credits()
        seen_in_atomic_block = []

        def fake_model(*args, **kwargs):
            seen_in_atomic_block.append(connection.in_atomic_block)
            call_index = len(seen_in_atomic_block)
            # _NUM_BATCHES batch calls, then the summary call.
            content = (
                _batch_evaluations_json(call_index)
                if call_index <= _NUM_BATCHES
                else SUMMARY_JSON
            )
            return make_ai_response(tokens=100, content=content)

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            ai_processor.grade_student_submission(teacher, RUBRIC_12Q, ANSWERS_12Q)

        self.assertEqual(len(seen_in_atomic_block), _NUM_BATCHES + 1)
        self.assertFalse(
            any(seen_in_atomic_block),
            "an AI call ran while a transaction was open — the pipeline is "
            "still holding a transaction (and therefore the wallet row "
            "lock) across network I/O",
        )


class WalletRowNotLockedBetweenCallsTest(GradingPipelineTestBase):
    """T4 — proves the wallet row lock from consume_credits' select_for_update
    is actually released between AI calls, not just that no transaction is
    open in the current thread (T3). Probes from a second thread, which
    gets its own DB connection."""

    def test_wallet_row_is_acquirable_from_another_connection_mid_run(self):
        teacher = self._make_teacher_with_credits()
        wallet_id = teacher.credit_wallet.pk
        probe_results = []
        call_count = {"n": 0}

        def probe():
            try:
                with django_transaction.atomic():
                    CreditWallet.objects.select_for_update(nowait=True).get(
                        pk=wallet_id
                    )
                probe_results.append("acquired")
            except OperationalError:
                probe_results.append("locked")
            finally:
                connection.close()

        def fake_model(*args, **kwargs):
            call_count["n"] += 1
            # Probe from a second connection only after at least one charge
            # has had a chance to commit, so this also proves charges are
            # landing incrementally rather than only at the very end.
            if call_count["n"] == 2:
                t = threading.Thread(target=probe)
                t.start()
                t.join(timeout=5)
            content = (
                _batch_evaluations_json(call_count["n"])
                if call_count["n"] <= 3
                else SUMMARY_JSON
            )
            return make_ai_response(tokens=100, content=content)

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            ai_processor.grade_student_submission(teacher, RUBRIC_12Q, ANSWERS_12Q)

        self.assertEqual(probe_results, ["acquired"])
        self.assertTrue(
            CreditUsageLog.objects.filter(wallet_id=wallet_id).exists(),
            "expected at least one charge to have committed mid-run",
        )


class FailedGradingRunRefundsEveryChargeTest(GradingPipelineTestBase):
    """T7 / T7b — a run that fails partway through refunds every charge it
    made; a run that succeeds keeps all of them."""

    def test_failed_run_refunds_every_charge(self):
        teacher = self._make_teacher_with_credits()
        wallet = teacher.credit_wallet
        starting_balance = wallet.total_remaining_credits()
        call_count = {"n": 0}

        def flaky(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # First batch succeeds and is charged.
                return make_ai_response(
                    tokens=1_000, content=_batch_evaluations_json(1)
                )
            # Every call after that fails outright (simulating the model /
            # network breaking mid-run) — _grade_question_batch retries 3x
            # internally before giving up and raising.
            raise RuntimeError("model exploded")

        with patch.object(AIProcessor, "_AIProcessor__ai_model", side_effect=flaky):
            # The retry-exhaustion path raises a bare Exception by design
            # (see the matching note in tests_grading_completeness.py), so
            # this matches on the wrapped failure reason rather than the
            # type.
            with self.assertRaisesRegex(Exception, "model exploded"):
                ai_processor.grade_student_submission(teacher, RUBRIC_12Q, ANSWERS_12Q)

        wallet.refresh_from_db()
        self.assertEqual(wallet.total_remaining_credits(), starting_balance)
        self.assertFalse(
            CreditUsageLog.objects.filter(wallet=wallet, is_refunded=False).exists(),
            "the one successful charge before the failure should have been "
            "refunded, not left consumed",
        )

    def test_successful_run_keeps_every_charge(self):
        teacher = self._make_teacher_with_credits()
        wallet = teacher.credit_wallet
        starting_balance = wallet.total_remaining_credits()

        def fake_model(*args, **kwargs):
            call_index = CreditUsageLog.objects.filter(wallet=wallet).count() + 1
            content = (
                _batch_evaluations_json(call_index)
                if call_index <= _NUM_BATCHES
                else SUMMARY_JSON
            )
            return make_ai_response(tokens=500, content=content)

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            ai_processor.grade_student_submission(teacher, RUBRIC_12Q, ANSWERS_12Q)

        wallet.refresh_from_db()
        self.assertLess(wallet.total_remaining_credits(), starting_balance)
        self.assertFalse(
            CreditUsageLog.objects.filter(wallet=wallet, is_refunded=True).exists(),
            "a fully successful run must not refund anything",
        )


class PerAttemptRefundSemanticsTest(GradingPipelineTestBase):
    """T8 — pins the chosen refund boundary: extract_grade_with_retry's
    attempts are independent. A failed attempt is refunded; a subsequent
    successful attempt is not touched by that refund. If the refund
    boundary is ever widened (e.g. to the whole extract_grade_with_retry
    loop instead of one grade_student_submission call), this test is the
    one that should change."""

    def test_failed_attempt_is_refunded_successful_attempt_is_kept(self):
        # A rubric small enough to take the single-pass (non-batched) path,
        # so exactly one execute_graded_task call happens per attempt.
        small_rubric = json.dumps(
            [{"question_number": 1, "question_text": "Q1?", "points": 10}]
        )
        small_answers = json.dumps(
            [{"question_number": 1, "answer_html": "An answer."}]
        )

        teacher = self._make_teacher_with_credits()
        wallet = teacher.credit_wallet
        call_count = {"n": 0}

        def fake_model(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                # Attempt 1: the AI call itself succeeds (so it gets
                # charged), but returns invalid JSON — grade_student_
                # submission's single-pass branch fails to parse it and
                # raises *after* the charge already committed. This is the
                # case that actually exercises the refund path, unlike a
                # call that fails before ever reaching consume_credits.
                return make_ai_response(tokens=200, content="not valid json")
            # Attempt 2: succeeds cleanly. grading_summary is deliberately
            # NOT trusted as-is by the single-pass path (see
            # AIProcessor._finalize_grading_result) — it's recomputed from
            # question_evaluations, so that's what must be present here.
            return make_ai_response(
                tokens=300,
                content=json.dumps(
                    {
                        "question_evaluations": [
                            {
                                "question_number": 1,
                                "score_awarded": 10,
                                "evidence_quotes": ["An answer"],
                            }
                        ],
                    }
                ),
            )

        with patch.object(
            AIProcessor, "_AIProcessor__ai_model", side_effect=fake_model
        ):
            result = ai_processor.extract_grade_with_retry(
                teacher, small_rubric, small_answers, max_retries=3
            )

        self.assertEqual(result["grading_summary"]["total_score"], 10)
        self.assertEqual(call_count["n"], 2)

        logs = list(CreditUsageLog.objects.filter(wallet=wallet).order_by("created_at"))
        self.assertEqual(len(logs), 2)
        # Attempt 1's charge (200 tokens -> refunded), attempt 2's kept.
        self.assertTrue(logs[0].is_refunded)
        self.assertFalse(logs[1].is_refunded)
