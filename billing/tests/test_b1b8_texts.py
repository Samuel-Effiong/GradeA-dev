"""Epic B1b-8 (08b §4, §7, §8; NOTES §8.0-§8.2): the approved texts and the
switches.

- Text 1 (INSUFFICIENT_CREDITS_MID_BATCH remediation) and text 2 (REUPLOAD)
  take {days} from QUEUE_LIFETIME_DAYS; text 3 (the late-door email) too.
- Q8-1: a non-retryable upload item says why: no kept file row -> #16b;
  purged by the lifetime purge -> text 2; anything else -> the catalogue's
  default; resolution replace_file on all.
- Q8-2: the task-status response carries kept_until for a teacher, never
  for a student.
- Q8-3: the late-door email goes to the teacher for batch rows only; a
  student's single upload stays logged as before.
- Q8-6: cancelling every waiting paper of a queued batch closes it
  (CANCELLED, no email); 0 waiting papers send no warning or expiry email;
  the queue emails' sending path, tested.
- D8-a: a cycle bucket with no expiry has no {cycle_end}: no warning.
- The switches are on.

The approved texts are copied verbatim from team/approved_wording_2026-11-08.md
(texts 1-3) and team/approved_wording_b1b_2026-11-09.md (#5-#10, #16).

    python manage.py test billing.tests.test_b1b8_texts
"""

import importlib
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.test import APIClient

from assignments.models import Assignment
from AutoGrader.reason_codes import REASON_CODES, ReasonCode
from billing.models import AIJob, AIJobStatus, CreditBucket, SchoolCreditAllocation
from billing.testing.holds import funded_wallet, make_item, make_job
from billing.tests.test_beat_lock_catch_up import make_school_licence
from classrooms.models import Course
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    KeptUpload,
)
from users.models import CustomUser, UserTypes

SEND = "AutoGrader.tasks.send_email_task.delay"
ABSENT = "absent"

TEXT_1 = (
    "Top up credits, then resume the remaining items. Your files are kept for "
    "{days} days, so you won't need to upload them again."
)
TEXT_2 = (
    "This upload can't be retried because its file was deleted after {days} "
    "days. Upload the file again."
)
TEXT_16B = (
    "This upload can't be retried because its file wasn't saved. Upload the "
    "file again."
)
DEFAULT_WHY = "This item can't be retried as it is."
TEXT_3_SUBJECT = "We couldn't process {file_name}"
TEXT_3_INDIVIDUAL = (
    "We couldn't process {file_name} because your credits ran out while it "
    "was waiting. Nothing was charged for it. Your file is kept for {days} "
    "days. Top up your credits, then retry it. You won't need to upload it "
    "again."
)
TEXT_3_LICENSE = (
    "We couldn't process {file_name} because your credits ran out while it "
    "was waiting. Nothing was charged for it. Your file is kept for {days} "
    "days. Ask your school administrator for more credits, then retry it. "
    "You won't need to upload it again."
)
QUEUE = {
    "queued": (
        "Your grading batch is waiting for credits",
        "Your credits ran out while grading {assignment}. {done} of {total} "
        "papers were graded and saved. The other {left} papers will start "
        "again on their own when you add credits. They will wait for {days} "
        "days, until {expires_on}.",
    ),
    "expiry_warning": (
        "Your waiting papers expire in 2 days",
        "{left} papers from {assignment} are still waiting for credits. If you "
        "don't add credits by {expires_on}, they will stop waiting. You can "
        "still grade them later by retrying them.",
    ),
    "restarted": (
        "Your grading batch has started again",
        "Credits arrived, so grading {assignment} has started again for "
        "{started} of the {left} waiting papers.",
    ),
    "expired": (
        "Your waiting papers have stopped waiting",
        "{left} papers from {assignment} waited {days} days for credits and "
        "have stopped waiting. Nothing was charged for them. You can grade "
        "them at any time by retrying them after you add credits.",
    ),
}
CLOSING_INDIVIDUAL = "Top up your credits to continue."
CLOSING_LICENSE = "Ask your school administrator for more credits to continue."


def module(name):
    return importlib.import_module(name)


def user(tag, user_type=UserTypes.TEACHER, **extra):
    return CustomUser.objects.create_user(
        email=f"b1b8.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=user_type,
        **extra,
    )


def under_license(teacher, tag):
    school, licence = make_school_licence(
        tag,
        billing_cycle_start=timezone.now() - timedelta(days=5),
        billing_cycle_end=timezone.now() + timedelta(days=25),
    )
    teacher.school = school
    teacher.save()
    SchoolCreditAllocation.objects.create(
        license_subscription=licence,
        user=teacher,
        monthly_allocation=100_000,
        is_active=True,
        next_credit_grant_at=timezone.now() + timedelta(days=25),
    )
    assert teacher.is_under_license(), "fixture: not under license"


def kept(item, *, until=None, purged_at=None):
    return KeptUpload.objects.create(
        processing_task=item,
        storage_key=f"b1b8/{uuid.uuid4()}",
        original_name="a.png",
        content_type="image/png",
        size_bytes=1,
        sha256="0" * 64,
        kept_until=until or timezone.now() + timedelta(days=30),
        purged_at=purged_at,
    )


# --------------------------------------------------------------------------
# Text 1
# --------------------------------------------------------------------------


class MidBatchRemediationTests(TestCase):
    def test_the_remediation_is_approved_text_1_at_the_default(self):
        self.assertEqual(settings.QUEUE_LIFETIME_DAYS, 30, "fixture: the default")
        spec = REASON_CODES[ReasonCode.INSUFFICIENT_CREDITS_MID_BATCH]
        self.assertEqual(spec.remediation, TEXT_1.format(days=30))

    def test_another_lifetime_fills_its_days(self):
        build = module("AutoGrader.reason_codes").mid_batch_remediation
        self.assertEqual(build(7), TEXT_1.format(days=7))


# --------------------------------------------------------------------------
# Text 2, #16b and the default (Q8-1)
# --------------------------------------------------------------------------


class ReuploadWhyTests(TestCase):
    def item(self):
        teacher = user(f"why.{uuid.uuid4().hex[:6]}")
        return BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
            status=BackgroundTaskStatus.FAILURE,
            reason_code=ReasonCode.PROVIDER_FAILURE.value,
        )

    def refusal(self, item):
        refusal = module("students.item_retry").refusal_for(item)
        self.assertIsNotNone(refusal, "fixture: the item must be refused")
        self.assertEqual(refusal.params.get("resolution"), "replace_file")
        return str(refusal)

    def test_no_kept_file_row_says_it_was_not_saved(self):
        self.assertEqual(self.refusal(self.item()), TEXT_16B)

    def test_a_file_purged_at_the_end_of_its_lifetime_says_deleted_after_days(self):
        item = self.item()
        until = timezone.now() - timedelta(hours=2)
        kept(item, until=until, purged_at=until + timedelta(hours=1))
        self.assertEqual(self.refusal(item), TEXT_2.format(days=30))

    @override_settings(QUEUE_LIFETIME_DAYS=7)
    def test_another_lifetime_fills_text_2s_days(self):
        item = self.item()
        until = timezone.now() - timedelta(hours=2)
        kept(item, until=until, purged_at=until)
        self.assertEqual(self.refusal(item), TEXT_2.format(days=7))

    def test_a_file_gone_early_gets_the_default(self):
        item = self.item()
        kept(item, purged_at=timezone.now())  # kept_until is 30 days ahead
        self.assertEqual(self.refusal(item), DEFAULT_WHY)


# --------------------------------------------------------------------------
# Text 3 and batch rows only (Q8-3)
# --------------------------------------------------------------------------


class LateDoorEmailTests(TestCase):
    def notify(self, task_type, *, licensed=False, tag="ld"):
        wallet = funded_wallet(f"b1b8.{tag}@example.com", 0)
        teacher = wallet.user
        if licensed:
            under_license(teacher, f"b1b8{tag}")
        job = make_job(wallet, "UPLOAD", total_items=1, status="RUNNING")
        row = make_item(job, task_type=task_type, file_name="paper-7.png")
        with patch(SEND) as send:
            module("billing.holds").notify_late_door_refusal(row, job)
        return teacher, send

    def assertSent(self, send, teacher, body, days=30):
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [teacher.email])
        self.assertEqual(
            kwargs["subject"], TEXT_3_SUBJECT.format(file_name="paper-7.png")
        )
        self.assertEqual(
            kwargs["message"], body.format(file_name="paper-7.png", days=days)
        )

    def test_a_batch_row_emails_the_individual_text(self):
        teacher, send = self.notify(BackgroundTaskType.BATCH_ANSWER_UPLOAD, tag="ld1")
        self.assertSent(send, teacher, TEXT_3_INDIVIDUAL)

    def test_a_batch_row_emails_the_license_text(self):
        teacher, send = self.notify(
            BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD, licensed=True, tag="ld2"
        )
        self.assertSent(send, teacher, TEXT_3_LICENSE)

    @override_settings(QUEUE_LIFETIME_DAYS=7)
    def test_another_lifetime_fills_text_3s_days(self):
        teacher, send = self.notify(BackgroundTaskType.BATCH_ANSWER_UPLOAD, tag="ld3")
        self.assertSent(send, teacher, TEXT_3_INDIVIDUAL, days=7)

    def test_a_single_upload_is_logged_and_the_teacher_is_not_emailed(self):
        with self.assertLogs("billing.holds", "WARNING") as logs:
            teacher, send = self.notify(BackgroundTaskType.ANSWER_EXTRACTION, tag="ld4")
        recipients = [c.kwargs.get("recipient_list") for c in send.call_args_list]
        self.assertNotIn([teacher.email], recipients)
        self.assertTrue(any("no email was sent" in line for line in logs.output))


# --------------------------------------------------------------------------
# Q8-6: a cancelled waiting batch, the 0-waiting backstop, the sending path
# --------------------------------------------------------------------------


class QueueFixture(TestCase):
    def setUp(self):
        self.wallet = funded_wallet(f"b1b8.q.{uuid.uuid4().hex[:6]}@example.com", 0)
        self.teacher = self.wallet.user
        course = Course.objects.create(name="B1b-8 C", teacher=self.teacher)
        self.assignment = Assignment.objects.create(title="Fractions", course=course)
        self.expires = timezone.now() + timedelta(days=30)
        self.job = make_job(
            self.wallet,
            "BATCH_GRADING",
            total_items=5,
            completed_items=2,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            assignment=self.assignment,
            queued_at=timezone.now(),
            expires_at=self.expires,
        )

    def waiting(self, n):
        return [
            make_item(
                self.job,
                task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                assignment=self.assignment,
            )
            for _ in range(n)
        ]

    def send(self, kind, **counts):
        with patch(SEND) as send:
            module("billing.batch_queue").notify(self.job.pk, kind, **counts)
        return send

    def body(self, kind, **values):
        values.setdefault("assignment", "Fractions")
        values.setdefault("days", 30)
        values.setdefault("expires_on", self.expires.date().isoformat())
        return QUEUE[kind][1].format(**values)

    def assertSent(self, send, kind, message):
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [self.teacher.email])
        self.assertEqual(kwargs["subject"], QUEUE[kind][0])
        self.assertEqual(kwargs["message"], message)


class CancelledQueueTests(QueueFixture):
    def test_cancelling_every_waiting_paper_closes_the_job_with_no_email(self):
        from students.task_tracking import cancel_processing_task

        rows = self.waiting(2)
        with patch(SEND) as send:
            for row in rows:
                cancel_processing_task(row)
        self.assertEqual(
            AIJob.objects.get(pk=self.job.pk).status, AIJobStatus.CANCELLED
        )
        send.assert_not_called()

    def test_cancelling_one_of_two_keeps_it_queued(self):
        from students.task_tracking import cancel_processing_task

        cancel_processing_task(self.waiting(2)[0])
        self.assertEqual(
            AIJob.objects.get(pk=self.job.pk).status,
            AIJobStatus.QUEUED_PENDING_CREDITS,
        )


class QueueSendTests(QueueFixture):
    def test_queued_individual(self):
        self.waiting(3)
        send = self.send("queued")
        message = self.body("queued", done=2, total=5, left=3)
        self.assertSent(send, "queued", f"{message}\n\n{CLOSING_INDIVIDUAL}")

    def test_queued_license(self):
        under_license(self.teacher, "b1b8q")
        self.waiting(3)
        send = self.send("queued")
        message = self.body("queued", done=2, total=5, left=3)
        self.assertSent(send, "queued", f"{message}\n\n{CLOSING_LICENSE}")

    def test_expiry_warning(self):
        self.waiting(3)
        send = self.send("expiry_warning")
        self.assertSent(send, "expiry_warning", self.body("expiry_warning", left=3))

    def test_restarted(self):
        send = self.send("restarted", started=2, left=3)
        self.assertSent(send, "restarted", self.body("restarted", started=2, left=3))

    def test_expired(self):
        send = self.send("expired", left=3)
        self.assertSent(send, "expired", self.body("expired", left=3))

    def test_no_warning_when_nothing_waits(self):
        self.send("expiry_warning").assert_not_called()

    def test_no_expiry_email_when_nothing_waited(self):
        self.send("expired", left=0).assert_not_called()


# --------------------------------------------------------------------------
# Q8-2: kept_until on the task-status response, for teachers only
# --------------------------------------------------------------------------


class KeptUntilStatusTests(TestCase):
    def status(self, who, task):
        client = APIClient()
        client.force_authenticate(user=who)
        response = client.get(f"/api/v1/tasks/status/{task.celery_task_id}")
        self.assertEqual(response.status_code, 200, response.content[:300])
        return response.data

    def task(self, who, task_type, assignment=None):
        return BackgroundProcessingTask.objects.create(
            requested_by=who,
            task_type=task_type,
            status=BackgroundTaskStatus.FAILURE,
            celery_task_id=str(uuid.uuid4()),
            assignment=assignment,
        )

    def teacher_task(self):
        teacher = user(f"ku.t.{uuid.uuid4().hex[:6]}")
        course = Course.objects.create(name="B1b-8 KU", teacher=teacher)
        assignment = Assignment.objects.create(title="KU", course=course)
        return teacher, self.task(
            teacher, BackgroundTaskType.BATCH_ANSWER_UPLOAD, assignment
        )

    def test_a_teacher_reads_the_kept_until(self):
        teacher, task = self.teacher_task()
        until = timezone.now() + timedelta(days=12)
        kept(task, until=until)
        data = self.status(teacher, task)
        self.assertEqual(parse_datetime(data.get("kept_until") or ""), until)

    def test_a_teacher_reads_null_when_no_file_is_kept(self):
        teacher, task = self.teacher_task()
        self.assertEqual(self.status(teacher, task).get("kept_until", ABSENT), None)

    def test_a_student_never_reads_it(self):
        student = user(f"ku.s.{uuid.uuid4().hex[:6]}", UserTypes.STUDENT)
        task = self.task(student, BackgroundTaskType.ANSWER_EXTRACTION)
        kept(task)
        self.assertNotIn("kept_until", self.status(student, task))


# --------------------------------------------------------------------------
# D8-a and the switches
# --------------------------------------------------------------------------


class LowBalanceNoExpiryTests(TestCase):
    def test_a_cycle_bucket_with_no_expiry_sends_no_warning(self):
        wallet = funded_wallet("b1b8.lb@example.com", 100_000)
        CreditBucket.objects.filter(wallet=wallet).update(
            used_credits=80_000, expires_at=None
        )
        with patch(SEND) as send:
            claimed = module("billing.low_balance").check_low_balance(wallet.pk)
        self.assertIsNone(claimed)
        send.assert_not_called()


class SwitchTests(TestCase):
    def test_every_b1b8_switch_is_on(self):
        export = module("students.data_export")
        switches = {
            "LATE_DOOR_EMAIL_APPROVED": module(
                "billing.holds"
            ).LATE_DOOR_EMAIL_APPROVED,
            "QUEUE_EMAIL_APPROVED": module("billing.batch_queue").QUEUE_EMAIL_APPROVED,
            "LOW_BALANCE_EMAIL_APPROVED": module(
                "billing.low_balance"
            ).LOW_BALANCE_EMAIL_APPROVED,
            "DRIFT_EMAIL_APPROVED": module("billing.estimates").DRIFT_EMAIL_APPROVED,
            "KEPT_FILE_EXPORT_NAME_APPROVED": export.KEPT_FILE_EXPORT_NAME_APPROVED,
        }
        self.assertEqual(switches, dict.fromkeys(switches, True))
