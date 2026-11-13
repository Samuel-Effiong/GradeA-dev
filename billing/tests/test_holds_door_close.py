"""B1b fix (SM brief 2026-11-12, found by the Fixer's hands-on test): an
UPLOAD door job closes when its last item ends.

Before: the door job stayed RESERVED after every item had settled or failed
(holds' last-item helper acted for BATCH_GRADING only), so
`billing_holds_drain` never reported DRAINED.

The rule is the batch job's (decide_after_item, SM ruling C4): when no item
is held or waiting and every held item has ended, the job ends SETTLED if
anything settled, else FAILED, and its remaining hold is returned. A door
job closes only once its request has attached every file it will hold
(the teacher batch and assignment doors launch each file as it is held, so
an early file can end before the last one is attached); the backstop closes
a stalled door job whatever its request reached.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.upload_door import DoorHold
from billing.models import AIJob, CreditWallet
from billing.testing.holds import ai_response, call_ai, fresh, holds
from billing.tests.test_holds_door import (
    DOOR_ESTIMATE,
    PROVIDER,
    door_check_passes,
    fixed_estimate,
    png_file,
)
from billing.tests.test_holds_entry import classroom
from students.models import BackgroundProcessingTask

TERMINAL = ("SETTLED", "FAILED")


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def left_on(job):
    job = fresh(job)
    return job.reserved_credits - job.hold_drawn - job.released_credits


def settle_row(teacher, row, tokens=300):
    with patch.object(AIProcessor, PROVIDER, return_value=ai_response(tokens)):
        with holds().metered_unit(processing_task_id=row.id, reason="t"):
            call_ai(teacher)


def new_row(teacher):
    """A queued file's row before the door attaches it."""
    return BackgroundProcessingTask.objects.create(
        requested_by=teacher, task_type="answer_extraction"
    )


def settle_direct(row, credits=300):
    row = fresh(row)
    holds().settle(row.job, credits, item=row)


def fail_row(row):
    row = fresh(row)
    holds().fail_item(row.job, item=row)


@override_settings(BILLING_HOLDS_ENABLED=True)
class DoorJobClosesAtItsLastItemTests(APITestCase):
    def assert_closed(self, job, teacher, status):
        job = fresh(job)
        self.assertEqual(job.status, status)
        self.assertEqual(left_on(job), 0)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(), [])

    def student_upload(self, tag):
        teacher, student, _, assignment, submission = classroom(tag, 100_000)
        submission.delete()
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    url, {"answer": png_file()}, format="multipart"
                )
        self.assertEqual(response.status_code, 200)
        return teacher, AIJob.objects.get(job_type="UPLOAD")

    def teacher_batch(self, tag, n=2, credits=100_000):
        teacher, _, _, assignment, _ = classroom(tag, credits)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file(f"s{i}.png") for i in range(n)]
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202)
        job = AIJob.objects.get(job_type="UPLOAD")
        rows = list(
            BackgroundProcessingTask.objects.filter(job=job).order_by("item_index")
        )
        return teacher, assignment, job, rows

    def test_student_upload_settles_when_its_one_file_ends(self):
        teacher, job = self.student_upload("dc1")
        settle_row(teacher, BackgroundProcessingTask.objects.get(job=job))
        self.assert_closed(job, teacher, "SETTLED")

    def test_teacher_batch_stays_open_until_the_last_file_then_settles(self):
        teacher, _, job, (first, second) = self.teacher_batch("dc2")
        settle_row(teacher, first)
        self.assertNotIn(fresh(job).status, TERMINAL)
        self.assertEqual(wallet_of(teacher).reserved_credits, DOOR_ESTIMATE)
        fail_row(second)
        self.assert_closed(job, teacher, "SETTLED")

    def test_teacher_batch_where_every_file_failed_ends_failed(self):
        teacher, _, job, rows = self.teacher_batch("dc3")
        for row in rows:
            fail_row(row)
        self.assert_closed(job, teacher, "FAILED")

    def test_assignment_upload_settles_after_both_papers(self):
        teacher, _, course, _, _ = classroom("dc4", 100_000)
        self.client.force_authenticate(user=teacher)
        with fixed_estimate(), patch("assignments.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("assignment-upload-async"),
                    {
                        "course": str(course.id),
                        "assignments": [png_file("p1.png"), png_file("p2.png")],
                    },
                    format="multipart",
                )
        self.assertEqual(response.status_code, 202)
        job = AIJob.objects.get(job_type="UPLOAD")
        rows = list(BackgroundProcessingTask.objects.filter(job=job))
        self.assertEqual(len(rows), 2)
        settle_row(teacher, rows[0])
        self.assertNotIn(fresh(job).status, TERMINAL)
        settle_row(teacher, rows[1])
        self.assert_closed(job, teacher, "SETTLED")

    def test_a_file_that_ends_before_the_next_is_attached_does_not_close_the_job(
        self,
    ):
        teacher, _, _, assignment, _ = classroom("dc5", 100_000)
        door = DoorHold(teacher, assignment, 2)
        first = new_row(teacher)
        self.assertTrue(door.hold(first, DOOR_ESTIMATE))
        fail_row(first)
        self.assertNotIn(fresh(door.job).status, TERMINAL)
        second = new_row(teacher)
        self.assertTrue(door.hold(second, DOOR_ESTIMATE))
        fail_row(second)
        self.assert_closed(door.job, teacher, "FAILED")

    def test_a_door_that_held_fewer_files_than_planned_closes_once_sealed(self):
        teacher, _, _, assignment, _ = classroom("dc6", 100_000)
        door = DoorHold(teacher, assignment, 3)
        only = new_row(teacher)
        self.assertTrue(door.hold(only, DOOR_ESTIMATE))
        fail_row(only)
        self.assertNotIn(fresh(door.job).status, TERMINAL)
        door.seal()
        self.assert_closed(door.job, teacher, "FAILED")

    def test_the_batch_view_seals_so_a_file_that_lost_its_hold_race_does_not_block(
        self,
    ):
        # D-12: the second file's hold loses the race, so the door holds one
        # file of two; the request seals the job and the one file closes it.
        teacher, _, _, assignment, _ = classroom("dc7", 12_000)
        holds().reserve(
            wallet_of(teacher),
            3_000,
            holds().create_job(
                wallet=wallet_of(teacher),
                requested_by=teacher,
                job_type="SINGLE_CALL",
            ),
        )
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), door_check_passes(), patch(
            "students.views.launch_processing_task"
        ):
            with self.captureOnCommitCallbacks(execute=True):
                self.client.post(url, {"answers": files}, format="multipart")
        job = AIJob.objects.get(job_type="UPLOAD")
        self.assertEqual(BackgroundProcessingTask.objects.filter(job=job).count(), 1)
        settle_direct(BackgroundProcessingTask.objects.get(job=job))
        job = fresh(job)
        self.assertEqual(job.status, "SETTLED")
        self.assertEqual(left_on(job), 0)


@override_settings(BILLING_HOLDS_ENABLED=True)
class BackstopClosesStalledDoorJobsTests(APITestCase):
    def stall(self, job):
        """Nothing about the job started or settled for 40 hours."""
        from billing.models import SettlementRecord

        long_ago = timezone.now() - timedelta(hours=40)
        AIJob.objects.filter(pk=job.pk).update(reservation_claimed_at=long_ago)
        SettlementRecord.objects.filter(job_id=job.pk).update(settled_at=long_ago)

    def test_an_unsealed_door_job_with_an_unstarted_file_is_failed_and_closed(self):
        from billing.batch_queue import backstop_unstarted

        teacher, _, _, assignment, _ = classroom("dc8", 100_000)
        door = DoorHold(teacher, assignment, 2)
        row = new_row(teacher)
        self.assertTrue(door.hold(row, DOOR_ESTIMATE))
        self.stall(door.job)
        self.assertEqual(backstop_unstarted(), 1)
        job = fresh(door.job)
        self.assertEqual(job.status, "FAILED")
        self.assertEqual(left_on(job), 0)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)

    def test_a_door_job_left_open_after_all_its_files_ended_is_closed(self):
        # The Fixer's shape: every row RETURNED, completed_items counted,
        # wallet reserved 0, the job still RESERVED (left by the old code).
        from billing.batch_queue import backstop_unstarted

        teacher, _, _, assignment, _ = classroom("dc9", 100_000)
        door = DoorHold(teacher, assignment, 2)
        rows = [new_row(teacher) for _ in range(2)]
        for row in rows:
            self.assertTrue(door.hold(row, DOOR_ESTIMATE))
        with patch("billing.holds._after_batch_item"):
            settle_direct(rows[0])
            settle_direct(rows[1])
        self.assertNotIn(fresh(door.job).status, TERMINAL)
        self.stall(door.job)
        backstop_unstarted()
        job = fresh(door.job)
        self.assertEqual(job.status, "SETTLED")
        self.assertEqual(left_on(job), 0)
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
