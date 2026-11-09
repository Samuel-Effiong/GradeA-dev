"""
Coverage for the refund boundary around grade persistence.

billing_refund_scope originally wrapped only the AI call
(ai_processor/services.py::grade_student_submission). It closed the moment
a grading result existed — but _run_grading_pipeline then still had to run
the grading_summary shape guard, _coerce_confidence, the HTML/ProseMirror
conversion, and the final save(). A failure in any of those charged the
teacher in full for a grade that was never persisted, and since FAILED is
a re-claimable state, every retry charged again.

students/services.py::_run_grading_pipeline now opens an outer
billing_refund_scope around the whole grade-and-persist block. The inner
scope re-parents its committed task_ids up to the outer one on success
(billing/refunds.py lines 73-75), so a persistence failure reclaims the
AI call's charge too.

Run with:
    python manage.py test billing.tests.test_grading_refund_scope
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from ai_processor.services import AIProcessor
from assignments.models import Assignment
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from billing.testing.holds import ai_response, assert_hold_ledger, call_ai
from classrooms.models import Course, Session
from students.models import GradingState, StudentSubmission
from users.models import CustomUser, UserTypes


class GradingRefundScopeTest(TestCase):
    def setUp(self):
        stamp = timezone.now().timestamp()
        self.teacher = CustomUser.objects.create_user(
            email=f"refundscope-teacher-{stamp}@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        student = CustomUser.objects.create_user(
            email=f"refundscope-student-{stamp}@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        assignment = Assignment.objects.create(
            title="A",
            course=course,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Discuss.",
                    "question_type": "ESSAY",
                    "points": 10,
                    "options": [],
                    "rubric": [
                        {"level": "excellent", "description": "Great", "points": 10},
                        {"level": "poor", "description": "Poor", "points": 0},
                    ],
                    "model_answer": "A model essay.",
                }
            ],
        )
        self.submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=[{"question_number": 1, "answer_html": "<p>my answer</p>"}],
        )

    def _used_credits(self):
        return sum(bucket.used_credits for bucket in self.wallet.buckets.all())

    def _grading_result(self):
        return {
            "question_evaluations": [
                {
                    "question_number": 1,
                    "score_awarded": 10,
                    "max_points": 10,
                    "level_achieved": "excellent",
                    "evidence_quotes": ["my answer"],
                }
            ],
            "grading_summary": {
                "total_score": 10,
                "max_total_points": 10,
                "percentage": 100.0,
            },
            "grading_confidence": 95,
        }

    def _run_grading(self, charge=1_000, persist_error=None):
        """
        Simulate one grading run: the AI call commits a real credit charge
        inside ai_processor's own refund scope, then persistence
        optionally blows up.
        """
        from billing.refunds import record_billing_task_id
        from students.services import grade_engine

        def fake_extract(*args, **kwargs):
            # Mirror execute_graded_task: charge, then register the charge
            # with whatever refund scope is currently open.
            self.wallet.consume_credits(
                amount=charge,
                feature="Grading Assignment",
                task_type="grade_assignment",
                task_id="refundscope-task-1",
            )
            record_billing_task_id("refundscope-task-1")
            return self._grading_result()

        patches = [
            patch(
                "students.services.ai_processor.extract_grade_with_retry",
                side_effect=fake_extract,
            )
        ]
        if persist_error is not None:
            patches.append(
                patch(
                    "students.services.AssignmentProcessingService."
                    "html_to_prosemirror_text",
                    side_effect=persist_error,
                )
            )

        for p in patches:
            p.start()
        try:
            return grade_engine(self.teacher, self.submission)
        finally:
            for p in patches:
                p.stop()

    def test_successful_run_keeps_the_charge(self):
        self._run_grading()

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.grading_state, GradingState.DONE)
        self.assertEqual(float(self.submission.score), 10.0)
        # The grade landed — the teacher is correctly charged.
        self.assertEqual(self._used_credits(), 1_000)

    # B1b-2 (NOTES §2.3 B3): the flag-off refund scope, pinned; its flag-on
    # twin is in GradingRefundScopeWithHoldsTest below.
    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_failure_during_persistence_refunds_the_ai_charge(self):
        # The load-bearing assertion: the AI call succeeded and committed
        # its charge, then persistence died. Without the outer scope the
        # teacher paid 1,000 credits for a grade that was never saved.
        with self.assertRaises(RuntimeError):
            self._run_grading(persist_error=RuntimeError("prosemirror exploded"))

        self.submission.refresh_from_db()
        self.assertEqual(self.submission.grading_state, GradingState.FAILED)
        self.assertIsNone(self.submission.graded_at)
        self.assertEqual(self._used_credits(), 0)

    # B1b-2 (NOTES §2.3 B3): the flag-off refund scope, pinned; its flag-on
    # twin is in GradingRefundScopeWithHoldsTest below.
    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_malformed_summary_also_refunds(self):
        # The grading_summary shape guard raises inside the same block, and
        # must refund for the same reason.
        from billing.refunds import record_billing_task_id
        from students.services import grade_engine

        def fake_extract(*args, **kwargs):
            self.wallet.consume_credits(
                amount=500,
                feature="Grading Assignment",
                task_type="grade_assignment",
                task_id="refundscope-task-2",
            )
            record_billing_task_id("refundscope-task-2")
            return {"question_evaluations": []}  # no grading_summary

        with patch(
            "students.services.ai_processor.extract_grade_with_retry",
            side_effect=fake_extract,
        ):
            with self.assertRaises(ValueError):
                grade_engine(self.teacher, self.submission)

        self.assertEqual(self._used_credits(), 0)


@override_settings(BILLING_HOLDS_ENABLED=True)
class GradingRefundScopeWithHoldsTest(TestCase):
    """B1b-2 (NOTES §2.10) B3 twins: the same two failures with holds on.
    The AI step makes one real billed call inside grading's metered unit,
    then persistence fails: nothing is charged, no refund row is written,
    and the call's hold is returned."""

    def setUp(self):
        GradingRefundScopeTest.setUp(self)
        for patcher in (
            patch(
                "ai_processor.services.can_user_access_ai", return_value=(True, None)
            ),
            patch.object(
                AIProcessor, "_AIProcessor__ai_model", return_value=ai_response(1_000)
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _used_credits(self):
        return GradingRefundScopeTest._used_credits(self)

    def _run_grading(self, *, result, persist_error=None):
        from students.services import grade_engine

        def fake_extract(*args, **kwargs):
            call_ai(self.teacher)  # real, billed, under the run's unit
            return result

        patches = [
            patch(
                "students.services.ai_processor.extract_grade_with_retry",
                side_effect=fake_extract,
            )
        ]
        if persist_error is not None:
            patches.append(
                patch(
                    "students.services.AssignmentProcessingService."
                    "html_to_prosemirror_text",
                    side_effect=persist_error,
                )
            )
        for p in patches:
            p.start()
        try:
            return grade_engine(self.teacher, self.submission)
        finally:
            for p in patches:
                p.stop()

    def test_failure_during_persistence_charges_nothing_and_returns_the_hold(self):
        with self.assertRaises(RuntimeError):
            self._run_grading(
                result=GradingRefundScopeTest._grading_result(self),
                persist_error=RuntimeError("prosemirror exploded"),
            )
        self.submission.refresh_from_db()
        self.assertEqual(self.submission.grading_state, GradingState.FAILED)
        self.assertIsNone(self.submission.graded_at)
        self.assertEqual(self._used_credits(), 0)
        assert_hold_ledger(self, self.teacher, kept=[])

    def test_malformed_summary_charges_nothing_and_returns_the_hold(self):
        with self.assertRaises(ValueError):
            self._run_grading(result={"question_evaluations": []})
        self.assertEqual(self._used_credits(), 0)
        assert_hold_ledger(self, self.teacher, kept=[])
