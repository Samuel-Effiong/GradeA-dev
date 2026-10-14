"""H-223: a teacher's batch starts, runs the files that fit, and parks the rest.

The user's decision (2026-10-09 20:26): on a teacher's BATCH upload the H-180
door no longer refuses the whole batch because one file costs more than the
balance. The door walks the files in upload order with a running balance: a
file whose estimate fits is queued and its estimate taken off what is left; a
file that does not fit is "parked": its own item, a FAILURE coded
INSUFFICIENT_CREDITS with no task, which the teacher uploads again after a
top-up (SM ruling 2026-10-14; automatic restart comes with Epic B). A later,
cheaper file can still fit. When NO file fits, the batch is refused as before
(402, no session). The door does not stamp the session's
credits_exhausted_at: the queued items read that stamp before they run, so a
stamp at the door would stop the very files chosen to run (the existing
mid-batch path still stamps it).

Both batch-shaped teacher routes: the answer-paper batch
(students .../batch-upload) and the assignment upload (assignments
upload-async). The student's single upload keeps its door
(students.tests_upload_credit_door).

Only the estimate (the one method the door asks) and the dispatch are
replaced: the estimates are set per file in upload order, so which file fits
is decided by the test, not by the size of a PNG.
"""

import io
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from billing.errors import INSUFFICIENT_CREDITS_MESSAGE
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, Session
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
)
from users.models import CustomUser, UserTypes

BALANCE = 50_000
FITS = 20_000
HALF = 30_000
DEAR = 60_000


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


class Base(APITestCase):
    #: Set by each route's class below.
    TASK_TYPE: str

    def post(self, count):
        raise NotImplementedError

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="h223-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Tess",
            last_name="Teacher",
            is_active=True,
            email_verified_at=timezone.now(),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=BALANCE,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        self.client.force_authenticate(user=self.teacher)
        self.queued = []
        for name in (
            "students.views.launch_processing_task",
            "assignments.views.launch_processing_task",
        ):
            launch = patch(name, side_effect=self._launch)
            launch.start()
            self.addCleanup(launch.stop)

    def _launch(self, task, processing_task, *args, **kwargs):
        # A fast worker: the item is STARTED before the route's loop reaches
        # the next file (launch_processing_task dispatches at once, not on
        # commit). A file parked after it must still be a plain pre-flight
        # park, not a mid-batch stop (Verifier's F1 on 3cb3846c).
        self.queued.append(processing_task.file_name)
        BackgroundProcessingTask.objects.filter(pk=processing_task.pk).update(
            status=BackgroundTaskStatus.STARTED
        )
        return SimpleNamespace(id=f"celery-{len(self.queued)}")

    def estimates(self, *values):
        """The door's estimate for each file, in upload order."""
        return patch.object(
            AIProcessor, "estimate_messages_cost", side_effect=list(values)
        )

    def files(self, count):
        return [
            SimpleUploadedFile(f"s{n}.png", PNG, content_type="image/png")
            for n in range(count)
        ]

    def assertParked(self, entry, file_name, item_index, task_type):
        self.assertIsNone(entry["task_id"])
        item = BackgroundProcessingTask.objects.get(pk=entry["item_id"])
        self.assertEqual(item.file_name, file_name)
        self.assertEqual(item.item_index, item_index)
        self.assertEqual(item.task_type, task_type)
        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "INSUFFICIENT_CREDITS")
        self.assertIsNone(item.celery_task_id)

    def assertQueued(self, entry):
        self.assertTrue(entry["task_id"])
        item = BackgroundProcessingTask.objects.get(pk=entry["item_id"])
        self.assertNotEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "")

    def assertRefusedWhole(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_402_PAYMENT_REQUIRED, response.data
        )
        self.assertEqual(response.data["code"], "insufficient_credits")
        self.assertEqual(response.data["error"], INSUFFICIENT_CREDITS_MESSAGE)
        self.assertEqual(self.queued, [])
        self.assertEqual(BatchUploadSession.objects.count(), 0)
        self.assertEqual(BackgroundProcessingTask.objects.count(), 0)


class AnswerBatch(Base):
    TASK_TYPE = BackgroundTaskType.BATCH_ANSWER_UPLOAD

    def post(self, count):
        return self.client.post(
            reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": self.assignment.pk},
            ),
            {"answers": self.files(count)},
            format="multipart",
        )


class AssignmentBatch(Base):
    TASK_TYPE = BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD

    def post(self, count):
        return self.client.post(
            reverse("assignment-upload-async"),
            {"course": str(self.course.id), "assignments": self.files(count)},
            format="multipart",
        )


if TYPE_CHECKING:
    _Shared = Base
else:
    # A plain mixin at run time, so the loader never collects it on its own.
    _Shared = object


class TheBatchRunsWhatFits(_Shared):
    """The same tests on both routes (mixed into AnswerBatch and
    AssignmentBatch below)."""

    def test_t1_a_dear_file_in_the_middle_is_parked_and_the_others_run(self):
        with self.estimates(FITS, DEAR, FITS) as estimate:
            response = self.post(3)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        self.assertEqual(estimate.call_count, 3)
        self.assertEqual(self.queued, ["s0.png", "s2.png"])
        tasks = response.data["tasks"]
        self.assertEqual(
            [t["file_name"] for t in tasks], ["s0.png", "s1.png", "s2.png"]
        )
        self.assertQueued(tasks[0])
        self.assertParked(tasks[1], "s1.png", 2, self.TASK_TYPE)
        self.assertQueued(tasks[2])
        session = BatchUploadSession.objects.get()
        self.assertEqual(session.total_files, 3)

    def test_t2_the_balance_runs_down_as_files_are_queued(self):
        # Each file fits alone (30,000 <= 50,000); together they do not.
        with self.estimates(HALF, HALF):
            response = self.post(2)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        self.assertEqual(self.queued, ["s0.png"])
        tasks = response.data["tasks"]
        self.assertQueued(tasks[0])
        self.assertParked(tasks[1], "s1.png", 2, self.TASK_TYPE)

    def test_t3_a_cheap_file_after_a_dear_one_still_runs(self):
        with self.estimates(DEAR, FITS):
            response = self.post(2)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        self.assertEqual(self.queued, ["s1.png"])
        tasks = response.data["tasks"]
        self.assertParked(tasks[0], "s0.png", 1, self.TASK_TYPE)
        self.assertQueued(tasks[1])

    def test_t4_when_no_file_fits_the_batch_is_refused_whole(self):
        """Green on the old code too: nothing can start, nothing is made."""
        with self.estimates(DEAR, DEAR):
            response = self.post(2)

        self.assertRefusedWhole(response)

    def test_t5_the_door_does_not_stamp_the_session(self):
        with self.estimates(FITS, DEAR):
            response = self.post(2)

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED, response.data)
        session = BatchUploadSession.objects.get()
        self.assertIsNone(session.credits_exhausted_at)
        self.assertEqual(self.queued, ["s0.png"])

    def test_t6_one_dear_file_alone_is_refused_as_before(self):
        """Green on the old code too: a one-file batch that does not fit."""
        with self.estimates(DEAR):
            response = self.post(1)

        self.assertRefusedWhole(response)


class TheAnswerBatchRunsWhatFits(TheBatchRunsWhatFits, AnswerBatch):
    pass


class TheAssignmentUploadRunsWhatFits(TheBatchRunsWhatFits, AssignmentBatch):
    pass
