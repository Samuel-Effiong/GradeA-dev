"""Epic B1a 2(b): H-279 held re-grades keep today's billing, pinned under
credit holds (owner ruling relayed by the SM 2026-11-06; NOTES §16).

1. A held re-grade gets exactly one grading charge, at the AI call.
2. Approval adds no grading charge and exactly one student-summary call,
   held and settled to the approving (course) teacher.
3. A newer re-grade replacing a held one: the replaced one is not
   refunded, and the new one is charged.

Charges are counted in settlement rows (the usage rows), never in
AICallRecord.
"""

import copy
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from ai_processor.services import AIProcessor
from billing.models import CreditLedger, CreditLedgerType, SettlementRecord
from billing.testing.holds import ai_response, estimate_for, fresh, remaining
from billing.tests.test_holds_entry import (
    GRADING,
    PROVIDER,
    billed,
    classroom,
    task_row,
    wallet_of,
)
from classrooms.tasks import student_summary_async
from students.services import grade_engine, promote_pending

GRADING_TOKENS = 300
SUMMARY_TOKENS = 120
RELEASED_SCORE = Decimal("5")


def grading(score):
    summary = {"total_score": score, "max_total_points": 10, "percentage": score * 10.0}
    return {**copy.deepcopy(GRADING), "grading_summary": summary}


@override_settings(BILLING_HOLDS_ENABLED=True)
class HeldRegradeBillingTests(TestCase):
    def setUp(self):
        (
            self.teacher,
            self.student,
            _,
            _,
            self.submission,
        ) = classroom("rp", 10 * estimate_for())
        type(self.submission).objects.filter(pk=self.submission.pk).update(
            is_published=True,
            graded_at=timezone.now(),
            score=RELEASED_SCORE,
            max_points=Decimal("10"),
        )
        self.start = remaining(wallet_of(self.teacher))
        launch = patch("students.services.launch_processing_task")
        self.launch = launch.start()
        self.addCleanup(launch.stop)
        summary = patch("students.services.student_summary_async")
        self.summary = summary.start()
        self.addCleanup(summary.stop)

    def regrade(self, score):
        row = task_row(self.teacher, task_type="grading", submission=self.submission)
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(GRADING_TOKENS)
        ), patch.object(
            AIProcessor,
            "extract_grade_with_retry",
            billed(self.teacher, 1, grading(score)),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                grade_engine(
                    self.teacher, fresh(self.submission), processing_task_id=row.id
                )
        return row

    def approve(self):
        self.summary.delay.side_effect = lambda *args: student_summary_async.apply(
            args=args
        )
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(SUMMARY_TOKENS)
        ):
            with self.captureOnCommitCallbacks(execute=True):
                promoted = promote_pending(self.submission.pk, actor=self.teacher)
        self.assertIsNotNone(promoted)

    def spent(self):
        return self.start - remaining(wallet_of(self.teacher))

    def test_r1_a_held_regrade_gets_exactly_one_grading_charge(self):
        row = self.regrade(8)
        submission = fresh(self.submission)
        self.assertIsNotNone(submission.pending_result)
        self.assertEqual(submission.score, RELEASED_SCORE)
        record = SettlementRecord.objects.get()
        self.assertEqual((record.item_id, record.retry_count), (row.id, 0))
        self.assertEqual(record.credits_charged, GRADING_TOKENS)
        self.assertEqual(record.job.job_type, "SINGLE_ITEM")
        self.assertEqual(record.job.wallet_id, wallet_of(self.teacher).pk)
        self.assertEqual(self.spent(), GRADING_TOKENS)
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 0)
        self.summary.delay.assert_not_called()
        self.assertEqual(self.launch.call_count, 1)

    def test_r2_approval_adds_one_summary_call_settled_to_the_teacher_and_no_grading_charge(
        self,
    ):
        self.regrade(8)
        grading_record = SettlementRecord.objects.get()
        self.approve()
        submission = fresh(self.submission)
        self.assertIsNone(submission.pending_result)
        self.assertEqual(submission.score, Decimal("8"))
        self.assertEqual(self.summary.delay.call_count, 1)
        self.assertEqual(self.summary.delay.call_args.args[1], str(self.teacher.id))
        self.assertEqual(SettlementRecord.objects.count(), 2)
        self.assertEqual(fresh(grading_record).credits_charged, GRADING_TOKENS)
        summary_record = SettlementRecord.objects.exclude(pk=grading_record.pk).get()
        self.assertEqual(summary_record.credits_charged, SUMMARY_TOKENS)
        job = summary_record.job
        self.assertEqual(job.job_type, "SINGLE_CALL")
        self.assertEqual(job.requested_by_id, self.teacher.id)
        self.assertEqual(job.wallet_id, wallet_of(self.teacher).pk)
        self.assertEqual(self.launch.call_count, 1)
        self.assertEqual(self.spent(), GRADING_TOKENS + SUMMARY_TOKENS)
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 0)

    def test_r3_a_newer_regrade_replaces_the_held_one_without_a_refund(self):
        first_row = self.regrade(8)
        first = SettlementRecord.objects.get()
        first_consumed = first.job.consumed_credits
        second_row = self.regrade(9)
        records = SettlementRecord.objects.order_by("settled_at")
        self.assertEqual(
            [(r.item_id, r.credits_charged) for r in records],
            [(first_row.id, GRADING_TOKENS), (second_row.id, GRADING_TOKENS)],
        )
        self.assertEqual(fresh(first).credits_charged, GRADING_TOKENS)
        self.assertEqual(fresh(first.job).consumed_credits, first_consumed)
        self.assertEqual(first_consumed, GRADING_TOKENS)
        self.assertFalse(
            CreditLedger.objects.filter(
                user_id=self.teacher.id, ledger_type=CreditLedgerType.REFUND
            ).exists()
        )
        self.assertEqual(self.spent(), 2 * GRADING_TOKENS)
        submission = fresh(self.submission)
        self.assertEqual(submission.score, RELEASED_SCORE)
        self.assertEqual(Decimal(str(submission.pending_result["score"])), Decimal("9"))
