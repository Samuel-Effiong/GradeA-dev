"""Epic B1a step 3b: the metered unit at the five refund-scope sites and its
entry rule through the task row (plan 08 §3.4; SM rulings 2026-11-06).

Entry rule, with the flag on, for a unit opened with a processing_task_id:
  * a task row with no job: the first call opens a SINGLE_ITEM job and
    attaches the row to it (the row is the holder, key (task, retry));
  * a row whose hold is HELD: the start claim (R4-1), then the row holds;
  * a row already settled for its retry_count: exits (CreditHoldReturnedError);
  * a returned hold on a finished row: a late message. A grading row exits;
    a door UPLOAD row runs under a new UPLOAD job keyed on the row;
  * a returned hold on a row being retried (PENDING/STARTED): a new
    SINGLE_ITEM job keyed (task, retry_count) (E-2, SM ruling).
A unit never finishes a job it did not open (a shared batch or door job).
"""

import ast
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, CreditUsageLog, CreditWallet, SettlementRecord
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    fresh,
    holds,
    make_item,
    make_job,
)
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import BackgroundProcessingTask, StudentSubmission
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"
GRADING = {
    "grading_summary": {"total_score": 8, "max_total_points": 10, "percentage": 80.0},
    "grading_confidence": 90,
    "question_evaluations": [],
}
EXTRACTED = {
    "answers": [{"question_number": 1, "answer_html": "<p>an answer</p>"}],
    "extraction_confidence": 77,
}


def classroom(tag, credits):
    teacher = ai_ready_teacher(f"{tag}-t@example.com", credits)
    student = CustomUser.objects.create_user(
        email=f"{tag}-s@example.com",
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
        first_name="Stu",
        last_name="Dent",
        is_active=True,
    )
    session = Session.objects.create(name="S", teacher=teacher)
    course = Course.objects.create(name="C", teacher=teacher, session=session)
    StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )
    assignment = Assignment.objects.create(
        title="A",
        course=course,
        status=AssignmentStatus.PUBLISHED,
        questions=[
            {
                "question_number": 1,
                "question_text": "Q1?",
                "points": 10,
                "model_answer": "An answer.",
            }
        ],
    )
    submission = StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers=[{"question_number": 1, "answer_html": "x"}],
        attempt_count=1,
        is_published=False,
    )
    return teacher, student, course, assignment, submission


def billed(teacher, calls, result):
    """A fake AI step: `calls` real billed calls, then `result`."""

    def fake(self, *args, **kwargs):
        for _ in range(calls):
            call_ai(teacher)
        return result

    return fake


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def task_row(user, **over):
    fields = {"requested_by": user, "task_type": "answer_extraction"}
    fields.update(over)
    return BackgroundProcessingTask.objects.create(**fields)


@override_settings(BILLING_HOLDS_ENABLED=True)
class EntryRuleTests(TestCase):
    """ER-1…ER-8: metered_unit(processing_task_id=…) on real task rows."""

    def setUp(self):
        self.teacher = ai_ready_teacher("er-t@example.com", 10 * estimate_for())
        self.wallet = wallet_of(self.teacher)

    def run_unit(self, row, calls=2, tokens=100):
        with patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(tokens)
        ) as provider:
            with holds().metered_unit(processing_task_id=row.id, reason="t"):
                for _ in range(calls):
                    call_ai(self.teacher)
        return provider

    def test_er1_a_row_with_no_job_is_attached_to_a_new_single_item_job(self):
        row = task_row(self.teacher)
        self.run_unit(row)
        row = fresh(row)
        self.assertIsNotNone(row.job_id)
        job = row.job
        self.assertEqual((job.job_type, job.status), ("SINGLE_ITEM", "SETTLED"))
        record = SettlementRecord.objects.get(job=job)
        self.assertEqual((record.item_id, record.retry_count), (row.id, 0))
        self.assertEqual(job.consumed_credits, 200)
        self.assertEqual(row.hold_state, "RETURNED")
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 0)

    def test_er2_a_held_row_on_a_shared_job_is_claimed_and_the_job_stays_open(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=2, status="RUNNING")
        row = make_item(job)
        holds().reserve(self.wallet, estimate_for(), job, item=row)
        self.run_unit(row, calls=1)
        row, job = fresh(row), fresh(job)
        self.assertIsNotNone(row.started_at)
        self.assertEqual(job.status, "RUNNING")
        self.assertEqual(job.completed_items, 1)
        self.assertEqual(SettlementRecord.objects.get(job=job).item_id, row.id)

    def test_er3_a_late_grading_message_exits_before_any_call(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=1, status="RUNNING")
        row = make_item(job, status="FAILURE", reason_code="PROVIDER_FAILURE")
        holds().reserve(self.wallet, 50, job, item=row)
        holds().fail_item(job, item=row)
        with patch.object(AIProcessor, PROVIDER) as provider:
            with self.assertRaises(holds().CreditHoldReturnedError):
                with holds().metered_unit(processing_task_id=row.id, reason="t"):
                    call_ai(self.teacher)
        provider.assert_not_called()
        self.assertEqual(AIJob.objects.count(), 1)

    def test_er4_a_row_already_settled_exits_before_any_call(self):
        job = make_job(self.wallet, "UPLOAD", total_items=1, status="RUNNING")
        row = make_item(job, status="SUCCESS")
        holds().reserve(self.wallet, 50, job, item=row)
        holds().settle(job, 10, item=row)
        with patch.object(AIProcessor, PROVIDER) as provider:
            with self.assertRaises(holds().CreditHoldReturnedError):
                with holds().metered_unit(processing_task_id=row.id, reason="t"):
                    call_ai(self.teacher)
        provider.assert_not_called()

    def test_er5_a_retried_row_runs_under_a_new_single_item_job(self):
        old = make_job(self.wallet, "BATCH_GRADING", total_items=1, status="RUNNING")
        row = make_item(old)
        holds().reserve(self.wallet, 50, old, item=row)
        holds().fail_item(old, item=row)
        BackgroundProcessingTask.objects.filter(pk=row.pk).update(
            status="PENDING", retry_count=1
        )
        before = fresh(old)
        self.run_unit(fresh(row), calls=1)
        retry = AIJob.objects.exclude(pk=old.pk).get()
        self.assertEqual((retry.job_type, retry.status), ("SINGLE_ITEM", "SETTLED"))
        self.assertEqual(retry.trace_id, old.trace_id)
        record = SettlementRecord.objects.get(job=retry)
        self.assertEqual((record.item_id, record.retry_count), (row.id, 1))
        after = fresh(old)
        self.assertEqual(
            (after.completed_items, after.failed_items, after.released_credits),
            (before.completed_items, before.failed_items, before.released_credits),
        )
        self.assertEqual(fresh(row).job_id, old.pk)

    def test_er6_a_late_door_row_runs_under_a_new_upload_job(self):
        old = make_job(self.wallet, "UPLOAD", total_items=1, status="RUNNING")
        started = timezone.now() - holds().item_sweep_window() - timedelta(minutes=1)
        row = make_item(old, started_at=started)
        holds().reserve(self.wallet, 50, old, item=row)
        holds().sweep_stale_holds()  # FAILURE, hold RETURNED
        self.run_unit(fresh(row), calls=1)
        new = AIJob.objects.exclude(pk=old.pk).get()
        self.assertEqual((new.job_type, new.status), ("UPLOAD", "SETTLED"))
        record = SettlementRecord.objects.get(job=new)
        self.assertEqual((record.item_id, record.retry_count), (row.id, 0))
        self.assertEqual(fresh(old).consumed_credits, 0)

    def test_er8_a_failed_unit_on_a_shared_job_returns_the_row_hold_only(self):
        job = make_job(self.wallet, "BATCH_GRADING", total_items=2, status="RUNNING")
        row = make_item(job)
        holds().reserve(self.wallet, estimate_for(), job, item=row)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(100)):
            with self.assertRaises(ValueError):
                with holds().metered_unit(processing_task_id=row.id, reason="t"):
                    call_ai(self.teacher)
                    raise ValueError("failed")
        job = fresh(job)
        self.assertEqual((job.status, job.failed_items), ("RUNNING", 1))
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertEqual(wallet_of(self.teacher).reserved_credits, 0)


@override_settings(BILLING_HOLDS_ENABLED=True)
class EntryPointTests(TestCase):
    """E-1, E-4, E-5, E-6, E-8 through the real entry functions; the AI step
    is a fake that makes real billed calls. E-2 is ER-5's mechanism; E-3's
    batch job and E-7's door are steps 5 and 3c."""

    def granted(self, tag):
        return classroom(tag, 10 * estimate_for())

    def refused(self, tag):
        return classroom(tag, estimate_for() - 1)

    def test_e1_a_single_paper_grade_settles_once_on_its_task_row(self):
        from students.services import grade_engine

        teacher, _, _, _, submission = self.granted("e1")
        row = task_row(teacher, task_type="grading", submission=submission)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(300)):
            with patch.object(
                AIProcessor, "extract_grade_with_retry", billed(teacher, 2, GRADING)
            ):
                grade_engine(teacher, submission, processing_task_id=row.id)
        record = SettlementRecord.objects.get()
        self.assertEqual((record.item_id, record.retry_count), (row.id, 0))
        self.assertEqual(record.job.consumed_credits, 600)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)

    def test_e1_refused_before_the_provider(self):
        from students.services import grade_engine

        teacher, _, _, _, submission = self.refused("e1r")
        row = task_row(teacher, task_type="grading", submission=submission)
        with patch.object(AIProcessor, PROVIDER) as provider:
            with patch.object(
                AIProcessor, "extract_grade_with_retry", billed(teacher, 1, GRADING)
            ):
                with self.assertRaises(InsufficientCreditsError):
                    grade_engine(teacher, submission, processing_task_id=row.id)
        provider.assert_not_called()
        self.assertFalse(CreditUsageLog.objects.exists())

    def test_e4_a_sync_upload_settles_once_on_its_own_job(self):
        from students.services import upload_answers_engine

        teacher, student, _, assignment, submission = self.granted("e4")
        submission.delete()
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(250)):
            with patch.object(
                AIProcessor, "extract_answer_with_retry", billed(teacher, 2, EXTRACTED)
            ):
                upload_answers_engine(assignment, [{"type": "text"}], student)
        record = SettlementRecord.objects.get()
        self.assertEqual((record.item_id, record.retry_count), (record.job_id, 0))
        self.assertEqual(record.job.consumed_credits, 500)

    def test_e5_a_sync_edit_settles_once_on_its_own_job(self):
        from students.services import update_submission_from_raw_text

        teacher, student, _, _, submission = self.granted("e5")
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(250)):
            with patch.object(
                AIProcessor, "extract_answer_with_retry", billed(teacher, 2, EXTRACTED)
            ):
                update_submission_from_raw_text(student, submission, "Question 1: a")
        self.assertEqual(SettlementRecord.objects.get().job.consumed_credits, 500)

    def test_e5_refused_before_the_provider(self):
        from students.services import update_submission_from_raw_text

        teacher, student, _, _, submission = self.refused("e5r")
        with patch.object(AIProcessor, PROVIDER) as provider:
            with patch.object(
                AIProcessor, "extract_answer_with_retry", billed(teacher, 1, EXTRACTED)
            ):
                with self.assertRaises(InsufficientCreditsError):
                    update_submission_from_raw_text(student, submission, "Q1: a")
        provider.assert_not_called()

    def test_e6_an_async_edit_settles_once_on_its_task_row(self):
        from assignments.tasks import extract_answer_background_task

        teacher, student, _, _, submission = self.granted("e6")
        row = task_row(student, submission=submission)
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(250)):
            with patch.object(
                AIProcessor, "extract_answer_with_retry", billed(teacher, 2, EXTRACTED)
            ):
                extract_answer_background_task.apply(
                    args=(str(submission.id), "Question 1: a", str(student.id)),
                    kwargs={"processing_task_id": str(row.id)},
                )
        record = SettlementRecord.objects.get()
        self.assertEqual((record.item_id, record.retry_count), (row.id, 0))
        self.assertEqual(record.job.consumed_credits, 500)

    def test_e8_a_failed_assignment_upload_charges_nothing(self):
        from assignments.file_uploads import upload_assignment_file
        from assignments.services import AssignmentProcessingService

        teacher, _, course, _, _ = self.granted("e8")
        upload = SimpleUploadedFile("a.txt", b"Q1. What?", content_type="text/plain")
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(250)):
            with patch.object(
                AssignmentProcessingService, "prepare_ai_content", return_value=[{}]
            ), patch.object(
                AssignmentProcessingService,
                "extract_assignment_data",
                classmethod(billed(teacher, 2, {"not": "an assignment"})),
            ):
                with self.assertRaises(ValidationError):
                    upload_assignment_file(
                        teacher, upload, "p", course=course, sha256="e8" * 32
                    )
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(AIJob.objects.get().status, "FAILED")


class WiringTests(TestCase):
    """No production code opens billing_refund_scope any more; the five sites
    open metered_unit with their processing_task_id."""

    SITES = {
        "ai_processor/services.py": 1,
        "students/services.py": 3,
        "assignments/file_uploads.py": 1,
    }

    def calls_in(self, rel, name):
        tree = ast.parse((Path(settings.BASE_DIR) / rel).read_text())
        return [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", None)) == name
        ]

    def test_the_five_sites_open_a_metered_unit_with_their_task_id(self):
        for rel, count in self.SITES.items():
            with self.subTest(site=rel):
                units = self.calls_in(rel, "metered_unit")
                self.assertEqual(len(units), count)
                for call in units:
                    self.assertIn("processing_task_id", {k.arg for k in call.keywords})

    def test_no_production_code_opens_a_refund_scope(self):
        for rel in self.SITES:
            with self.subTest(site=rel):
                self.assertEqual(self.calls_in(rel, "billing_refund_scope"), [])
