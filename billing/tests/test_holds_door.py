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
from students.models import BackgroundProcessingTask

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
    def test_a_granted_hold_is_taken_on_the_row_and_the_task_settles_on_it(self):
        teacher, student, _, assignment, submission = classroom(
            "d2", 10 * estimate_for()
        )
        submission.delete()
        with fixed_estimate(), patch("students.views.launch_processing_task") as launch:
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

    def test_an_assignment_file_is_held_on_its_row(self):
        teacher, _, course, _, _ = classroom("d6", 10 * estimate_for())
        self.client.force_authenticate(user=teacher)
        with fixed_estimate(), patch(
            "assignments.views.launch_processing_task"
        ) as launch:
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
    def late_door_refusal(self, tag):
        """A swept door row comes back; its new UPLOAD job cannot hold."""
        teacher, _, _, _, _ = classroom(tag, estimate_for() + 1_000)
        wallet = wallet_of(teacher)
        old = make_job(wallet, "UPLOAD", total_items=1, status="RUNNING")
        row = make_item(old, file_name="paper-7.png", status="FAILURE")
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
            teacher = self.late_door_refusal("d8")
        send.assert_called_once()
        kwargs = send.call_args.kwargs
        self.assertEqual(kwargs["recipient_list"], [teacher.email])
        self.assertIn("paper-7.png", kwargs["message"])

    def test_the_texts_are_one_pending_constant_and_both_switches_are_off(self):
        texts = holds().LATE_DOOR_EMAIL_TEXTS
        self.assertEqual(set(texts), {"subject", "individual", "license"})
        self.assertIs(holds().LATE_DOOR_EMAIL_APPROVED, False)
        self.assertIs(holds().LATE_DOOR_STUDENT_NOTICE_APPROVED, False)
