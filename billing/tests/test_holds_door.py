"""Epic B1a step 3c: the hold at the upload door (plan 08 §3.2, §3.5, E-7;
SM rulings 2026-11-06).

Flag on, at the three upload-async routes:
  * the door reads SPENDABLE credit (total less what other jobs hold);
  * each queued file's door estimate is reserved on its task row under one
    UPLOAD job per request, inside the request's transaction; a refused hold
    rolls the row back and nothing is launched;
  * the task's unit then claims the row and settles on it; the shared door
    job stays open (its terminal transition is step 5).
A late door file that cannot be held gets an email, but the texts are
PENDING OWNER APPROVAL and nothing is sent until they are approved.
"""

import io
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from PIL import Image
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, CreditWallet
from billing.testing.holds import (
    ai_response,
    call_ai,
    estimate_for,
    fresh,
    holds,
    make_item,
    make_job,
)
from billing.tests.test_holds_entry import classroom
from students.exceptions import STUDENT_UPLOAD_NOT_PROCESSED
from students.models import BackgroundProcessingTask
from students.task_tracking import failure_reason_code

PROVIDER = "_AIProcessor__ai_model"
DOOR_ESTIMATE = 5_000


def png_file(name="a.png"):
    from django.core.files.uploadedfile import SimpleUploadedFile

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def fixed_estimate():
    return patch("assignments.upload_door._estimate_for", return_value=DOOR_ESTIMATE)


def door_check_passes():
    """Another request takes the free balance between the door's check and
    the hold: the check sees plenty, the real reserve refuses."""
    return patch("assignments.upload_door._balance", return_value=10**9)


class StudentDoorTests(APITestCase):
    def upload(self, student, assignment):
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return self.client.post(url, {"answer": png_file()}, format="multipart")

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_a_refused_hold_rolls_the_request_back_and_launches_nothing(self):
        teacher, student, _, assignment, submission = classroom("d1", 6_000)
        submission.delete()
        holds().reserve(wallet_of(teacher), 2_000, make_job(wallet_of(teacher)))
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 402)
        launch.assert_not_called()
        self.assertFalse(BackgroundProcessingTask.objects.exists())
        self.assertFalse(AIJob.objects.filter(job_type="UPLOAD").exists())

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_d11_a_hold_refused_after_the_door_check_rolls_the_row_back(self):
        # D-11 (NOTES §15h-2): the race. D-1 only pins the door's own check.
        teacher, student, _, assignment, submission = classroom("d11", 6_000)
        submission.delete()
        holds().reserve(wallet_of(teacher), 2_000, make_job(wallet_of(teacher)))
        with fixed_estimate(), door_check_passes(), patch(
            "students.views.launch_processing_task"
        ) as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(
            response.data,
            {"error": STUDENT_UPLOAD_NOT_PROCESSED, "code": "insufficient_credits"},
        )
        self.assertFalse(BackgroundProcessingTask.objects.exists())
        self.assertFalse(AIJob.objects.filter(job_type="UPLOAD").exists())
        self.assertEqual(callbacks, [])
        launch.assert_not_called()

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_a_granted_hold_is_taken_on_the_row_and_the_task_settles_on_it(self):
        teacher, student, _, assignment, submission = classroom(
            "d2", 10 * estimate_for()
        )
        submission.delete()
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 200)
        launch.assert_called_once()
        job = AIJob.objects.get(job_type="UPLOAD")
        row = BackgroundProcessingTask.objects.get()
        self.assertEqual(row.job_id, job.pk)
        self.assertEqual((row.hold_state, row.hold_credits), ("HELD", DOOR_ESTIMATE))
        self.assertEqual(wallet_of(teacher).reserved_credits, DOOR_ESTIMATE)

        # The task's side: the unit claims the row and settles on it.
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(300)):
            with holds().metered_unit(processing_task_id=row.id, reason="t"):
                call_ai(teacher)
        row, job = fresh(row), fresh(job)
        self.assertEqual(row.hold_state, "RETURNED")
        self.assertEqual(job.consumed_credits, 300)
        self.assertNotIn(job.status, ("SETTLED", "FAILED"))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)

    @override_settings(BILLING_HOLDS_ENABLED=False)
    def test_with_the_flag_off_the_door_reads_the_total_as_before(self):
        teacher, student, _, assignment, submission = classroom("d4", 6_000)
        submission.delete()
        holds().reserve(wallet_of(teacher), 2_000, make_job(wallet_of(teacher)))
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            response = self.upload(student, assignment)
        self.assertEqual(response.status_code, 200)
        launch.assert_called_once()
        self.assertFalse(AIJob.objects.filter(job_type="UPLOAD").exists())


@override_settings(BILLING_HOLDS_ENABLED=True)
class TeacherDoorTests(APITestCase):
    def test_a_batch_holds_the_files_that_fit_and_parks_the_rest(self):
        teacher, _, _, assignment, _ = classroom("d5", 7_000)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202)  # Accepted (batch)
        self.assertEqual(launch.call_count, 1)
        job = AIJob.objects.get(job_type="UPLOAD")
        self.assertEqual(job.reserved_credits, DOOR_ESTIMATE)
        self.assertEqual(
            BackgroundProcessingTask.objects.filter(job=job, hold_state="HELD").count(),
            1,
        )
        self.assertEqual(wallet_of(teacher).reserved_credits, DOOR_ESTIMATE)

    def test_d10_the_door_parks_by_spendable_not_by_total(self):
        # D-10 (NOTES §15h): another job holds 3,000 of 12,000, so the door's
        # spendable 9,000 fits one 5,000 file; the total would fit both.
        teacher, _, _, assignment, _ = classroom("d10", 12_000)
        holds().reserve(wallet_of(teacher), 3_000, make_job(wallet_of(teacher)))
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202)
        job = AIJob.objects.get(job_type="UPLOAD")
        self.assertEqual(job.total_items, 1)
        self.assertEqual(
            BackgroundProcessingTask.objects.filter(job=job, hold_state="HELD").count(),
            1,
        )

    def test_d12_a_batch_file_whose_hold_loses_the_race_is_failed_not_launched(self):
        # D-12 (NOTES §15h-2): no transaction here; the refused file's row is
        # its own FAILED item with the credit refusal, and is not launched.
        teacher, _, _, assignment, _ = classroom("d12", 12_000)
        holds().reserve(wallet_of(teacher), 3_000, make_job(wallet_of(teacher)))
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), door_check_passes(), patch(
            "students.views.launch_processing_task"
        ) as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(launch.call_count, 1)
        by_name = {t["file_name"]: t for t in response.data["tasks"]}
        self.assertIsNone(by_name["s2.png"]["task_id"])
        refused = BackgroundProcessingTask.objects.get(file_name="s2.png")
        self.assertEqual(refused.status, "FAILURE")
        self.assertEqual(
            refused.reason_code, failure_reason_code(InsufficientCreditsError())
        )
        self.assertEqual(AIJob.objects.get(job_type="UPLOAD").reserved_credits, 5_000)

    def test_an_assignment_file_is_held_on_its_row(self):
        teacher, _, course, _, _ = classroom("d6", 10 * estimate_for())
        self.client.force_authenticate(user=teacher)
        with fixed_estimate(), patch(
            "assignments.views.launch_processing_task"
        ) as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("assignment-upload-async"),
                    {"course": str(course.id), "assignments": [png_file("paper.png")]},
                    format="multipart",
                )
        self.assertEqual(response.status_code, 202)  # Accepted (batch)
        launch.assert_called_once()
        job = AIJob.objects.get(job_type="UPLOAD")
        row = BackgroundProcessingTask.objects.get(job=job)
        self.assertEqual((row.hold_state, row.hold_credits), ("HELD", DOOR_ESTIMATE))


@override_settings(BILLING_HOLDS_ENABLED=True)
class LateDoorNoticeTests(APITestCase):
    def late_door_refusal(self, tag, task_type="answer_extraction"):
        """A swept door row comes back; its new UPLOAD job cannot hold."""
        teacher, _, _, _, _ = classroom(tag, estimate_for() + 1_000)
        wallet = wallet_of(teacher)
        old = make_job(wallet, "UPLOAD", total_items=1, status="RUNNING")
        row = make_item(
            old, file_name="paper-7.png", status="FAILURE", task_type=task_type
        )
        holds().reserve(wallet, 50, old, item=row)
        holds().fail_item(old, item=row)
        holds().reserve(wallet, estimate_for(), make_job(wallet))  # spend it
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(100)):
            with self.assertRaises(InsufficientCreditsError):
                with holds().metered_unit(processing_task_id=row.id, reason="t"):
                    call_ai(teacher)
        return teacher

    def test_no_email_is_sent_while_the_texts_await_approval(self):
        with patch("AutoGrader.tasks.send_email_task.delay") as send:
            self.late_door_refusal("d7")
        send.assert_not_called()

    def test_once_approved_the_teacher_is_emailed_naming_the_file(self):
        with patch("AutoGrader.tasks.send_email_task.delay") as send, patch.object(
            holds(), "LATE_DOOR_EMAIL_APPROVED", True
        ):
            # B1b-8 (SM Q8-3): the teacher is emailed for a batch row only.
            teacher = self.late_door_refusal("d8", task_type="batch_answer_upload")
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [teacher.email])
        self.assertIn("paper-7.png", kwargs["message"])

    def test_the_texts_are_one_pending_constant_the_email_switch_is_off_and_the_student_notice_is_on(
        self,
    ):
        texts = holds().LATE_DOOR_EMAIL_TEXTS
        self.assertEqual(set(texts), {"subject", "individual", "license"})
        # B1b-8: text 3 is owner-approved and switched on.
        self.assertIs(holds().LATE_DOOR_EMAIL_APPROVED, True)
        self.assertIs(holds().LATE_DOOR_STUDENT_NOTICE_APPROVED, True)
