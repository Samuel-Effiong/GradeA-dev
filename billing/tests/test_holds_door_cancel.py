"""B1b fix (SM brief 2026-11-12, found by the Fixer's hands-on test): a door
item that ends before its metered unit returns its hold at once.

Before: cancelling an UPLOAD door item that had not started left it HELD
(the row CANCELLED, the job RESERVED, the wallet's reserved credits up by
its estimate) until the 36-hour backstop. The D6-c release
(`batch_queue.release_unused_item_hold`) acted for BATCH_GRADING papers
only. The same gap held an UPLOAD item that failed or succeeded without a
metered unit.

After: the item's hold is returned when it ends (cancel from the session,
the worker seeing the cancel, a failure before the unit), it counts as
finished, and the door job closes under the last-item rule.
"""

from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from billing.models import AIJob, CreditWallet
from billing.testing.holds import fresh, holds
from billing.tests.test_holds_door import fixed_estimate, png_file
from billing.tests.test_holds_door_close import settle_direct
from billing.tests.test_holds_entry import classroom
from students.models import BackgroundProcessingTask
from students.task_tracking import (
    cancel_processing_task,
    mark_processing_task_cancelled,
    mark_processing_task_failure,
)

TERMINAL = ("SETTLED", "FAILED", "CANCELLED")


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def left_on(job):
    job = fresh(job)
    return job.reserved_credits - job.hold_drawn - job.released_credits


@override_settings(BILLING_HOLDS_ENABLED=True)
class CancelReturnsTheDoorHoldTests(APITestCase):
    def assert_returned(self, row, teacher):
        row = fresh(row)
        self.assertEqual(row.hold_state, "RETURNED")
        self.assertEqual(row.released_credits, row.hold_credits)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(), [])

    def assert_job_closed(self, job, status):
        job = fresh(job)
        self.assertEqual(job.status, status)
        self.assertEqual(left_on(job), 0)

    def teacher_batch(self, tag, n=1):
        teacher, _, _, assignment, _ = classroom(tag, 100_000)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file(f"s{i}.png") for i in range(n)]
        # The worker is stopped: nothing is launched.
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"answers": files}, format="multipart")
        job = AIJob.objects.get(job_type="UPLOAD")
        rows = list(
            BackgroundProcessingTask.objects.filter(job=job).order_by("item_index")
        )
        return teacher, job, rows

    def cancel_session(self, teacher, row):
        self.client.force_authenticate(user=teacher)
        response = self.client.post(
            reverse("task-cancel-session", kwargs={"session_id": row.batch_session_id})
        )
        self.assertEqual(response.status_code, 200)

    def test_cancelling_an_unstarted_batch_file_returns_its_hold_and_closes_the_job(
        self,
    ):
        teacher, job, (row,) = self.teacher_batch("cc1")
        self.assertEqual(wallet_of(teacher).reserved_credits, row.hold_credits)
        self.cancel_session(teacher, row)
        self.assertEqual(fresh(row).status, "CANCELLED")
        self.assert_returned(row, teacher)
        self.assert_job_closed(job, "FAILED")

    def test_the_worker_seeing_the_cancel_returns_the_hold_once(self):
        teacher, job, (row,) = self.teacher_batch("cc2")
        # The worker is running: it observes the cancel itself.
        mark_processing_task_cancelled(row.id)
        self.assert_returned(row, teacher)
        # The session cancel arriving after it changes nothing.
        cancel_processing_task(fresh(row))
        self.assert_returned(row, teacher)
        self.assertEqual(fresh(job).failed_items, 1)
        self.assert_job_closed(job, "FAILED")

    def test_cancelling_a_partly_done_door_returns_the_rest_and_settles_the_job(
        self,
    ):
        teacher, job, (done, waiting) = self.teacher_batch("cc3", n=2)
        settle_direct(done)
        BackgroundProcessingTask.objects.filter(pk=done.pk).update(status="SUCCESS")
        self.assertNotIn(fresh(job).status, TERMINAL)
        self.cancel_session(teacher, waiting)
        self.assertEqual(fresh(done).status, "SUCCESS")
        self.assert_returned(waiting, teacher)
        self.assert_job_closed(job, "SETTLED")

    def test_cancelling_a_student_upload_returns_its_hold(self):
        teacher, student, _, assignment, submission = classroom("cc4", 100_000)
        submission.delete()
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"answer": png_file()}, format="multipart")
        job = AIJob.objects.get(job_type="UPLOAD")
        row = BackgroundProcessingTask.objects.get(job=job)
        cancel_processing_task(row)
        self.assert_returned(row, teacher)
        self.assert_job_closed(job, "FAILED")

    def test_cancelling_an_assignment_upload_returns_its_hold(self):
        teacher, _, course, _, _ = classroom("cc5", 100_000)
        self.client.force_authenticate(user=teacher)
        with fixed_estimate(), patch("assignments.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(
                    reverse("assignment-upload-async"),
                    {"course": str(course.id), "assignments": [png_file("p.png")]},
                    format="multipart",
                )
        job = AIJob.objects.get(job_type="UPLOAD")
        row = BackgroundProcessingTask.objects.get(job=job)
        cancel_processing_task(row)
        self.assert_returned(row, teacher)
        self.assert_job_closed(job, "FAILED")

    def test_a_door_file_that_fails_before_its_metered_unit_returns_its_hold(self):
        # Same root cause: an unreadable file fails in the task before any
        # AI call, so no unit ever settles it.
        teacher, job, (row,) = self.teacher_batch("cc6")
        mark_processing_task_failure(row.id, ValueError("unreadable"))
        self.assert_returned(row, teacher)
        self.assert_job_closed(job, "FAILED")
