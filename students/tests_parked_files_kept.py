"""Parked files are kept, so a teacher retries them by hand (owner, 2026-11-18).

H-223 parks a file of a teacher's batch that does not fit the balance at the
door: its own item, a FAILURE coded INSUFFICIENT_CREDITS. Until now no file
was kept for it, so a retry answered "upload the file again". Now:

* each parked file is kept like an accepted one (a KeptUpload row with the
  usual kept_until and relaunch data), written with its item, before any
  file launches, with no hold and no charge; both teacher batch routes;
* storage down: the usual fallback (not kept, kept_upload_fallback counted),
  the upload still answers 202, and a retry says NOT_SAVED;
* a retry while still short is refused for credits in the worker and the
  file survives; after a top-up the retry runs from the kept file, with one
  hold and one charge;
* retry-failed picks the parked items up;
* the lifetime purge removes the file; deleting the batch removes it;
* session-results' `retryable` (and so `resumable`) gives item_retry's own
  answer for upload items, with one kept-file query for the whole session.

    python manage.py test students.tests_parked_files_kept
"""

import io
import uuid
from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from PIL import Image
from rest_framework.test import APIClient

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditUsageLog, CreditWallet
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    assert_hold_ledger,
    estimate_for,
    fund,
    holds,
    real_billed_call,
)
from classrooms.models import Course, Session, Topic
from students import kept_files
from students.item_retry import NOT_SAVED, deleted_after_days
from students.kept_uploads import purge_due
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    KeptUpload,
)
from students.task_tracking import mark_processing_task_cancelled

PROVIDER = "_AIProcessor__ai_model"
ANSWERS = BackgroundTaskType.BATCH_ANSWER_UPLOAD
ASSIGNMENTS = BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD


def _png():
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buffer, format="PNG")
    return buffer.getvalue()


PNG = _png()


if TYPE_CHECKING:
    _Base = TestCase
else:
    # A plain mixin at run time, so the loader never collects it on its own.
    _Base = object


class ParkedFixture(_Base):
    """A teacher with `balance` credits, a course and an assignment; the
    door's estimates are set per file; launches are recorded."""

    def build(self, balance, tag="parked"):
        self.teacher = ai_ready_teacher(f"{tag}-teacher@example.com", balance)
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
        self.api = APIClient()
        self.api.force_authenticate(user=self.teacher)
        self.door_launched = []
        for name in (
            "students.views.launch_processing_task",
            "assignments.views.launch_processing_task",
        ):
            launch = patch(name, side_effect=self._door_launch)
            launch.start()
            self.addCleanup(launch.stop)

    def _door_launch(self, task, processing_task, *args, **kwargs):
        self.door_launched.append(processing_task.file_name)
        return SimpleNamespace(id=f"celery-{len(self.door_launched)}")

    def files(self, count):
        return [
            SimpleUploadedFile(f"s{n}.png", PNG, content_type="image/png")
            for n in range(count)
        ]

    def upload(self, *estimates, route=ANSWERS, topic=None):
        """POST a batch of len(estimates) files; the door's estimate for
        each file is the given value, in upload order."""
        if route == ANSWERS:
            url = reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": self.assignment.pk},
            )
            data = {"answers": self.files(len(estimates))}
        else:
            url = reverse("assignment-upload-async")
            data = {
                "course": str(self.course.id),
                "assignments": self.files(len(estimates)),
            }
            if topic is not None:
                data["topic"] = str(topic.id)
        with patch.object(
            AIProcessor, "estimate_messages_cost", side_effect=list(estimates)
        ), self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(url, data, format="multipart")
        self.assertEqual(response.status_code, 202, response.content[:400])
        return response.json()["data"]

    def items(self, data):
        return [
            BackgroundProcessingTask.objects.get(pk=entry["item_id"])
            for entry in data["tasks"]
        ]

    def retry(self, item):
        return self.api.post(
            reverse(
                "task-retry-item",
                kwargs={
                    "session_id": str(item.batch_session_id),
                    "item_id": str(item.id),
                },
            )
        )

    def session_results(self, session_id):
        response = self.api.get(
            reverse("task-session-results", kwargs={"session_id": str(session_id)})
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()["data"]

    def assertKeptParked(self, item):
        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "INSUFFICIENT_CREDITS")
        self.assertIsNone(item.celery_task_id)
        self.assertIsNone(item.job_id, "a parked file takes no hold")
        self.assertIsNone(item.hold_state)
        row = KeptUpload.objects.get(processing_task=item)
        self.assertIsNone(row.purged_at)
        self.assertEqual(row.purge_reason, "")
        expected = timezone.now() + timedelta(days=30)
        self.assertLess(abs((row.kept_until - expected).total_seconds()), 120)
        self.assertEqual(row.original_name, item.file_name)
        self.assertEqual(kept_files.load(row.storage_key), PNG)
        return row


class _ParkedFilesAreKept(ParkedFixture):
    """Run with holds on and with holds off (the classes below)."""

    def setUp(self):
        self.build(10_000)

    def test_a_batch_of_three_where_one_fits_keeps_the_two_parked_files(self):
        data = self.upload(1_000, 20_000, 20_000)

        queued, *parked = self.items(data)
        self.assertEqual(self.door_launched, ["s0.png"])
        self.assertNotEqual(queued.status, BackgroundTaskStatus.FAILURE)
        for item in parked:
            self.assertKeptParked(item)
            self.assertEqual(KeptUpload.objects.get(processing_task=item).relaunch, {})
        self.assertEqual(KeptUpload.objects.count(), 3)
        self.assertFalse(CreditUsageLog.objects.exists(), "nothing is charged")

    def test_the_assignment_upload_keeps_its_parked_files_with_the_topic(self):
        topic = Topic.objects.create(name="Fractions", course=self.course)
        data = self.upload(1_000, 20_000, route=ASSIGNMENTS, topic=topic)

        _, parked = self.items(data)
        row = self.assertKeptParked(parked)
        self.assertEqual(row.relaunch, {"topic_id": str(topic.id)})


@override_settings(BILLING_HOLDS_ENABLED=True)
class ParkedFilesAreKeptHoldsOn(_ParkedFilesAreKept, TestCase):
    def test_only_the_queued_file_holds_credits(self):
        self.upload(1_000, 20_000, 20_000)
        wallet = CreditWallet.objects.get(user=self.teacher)
        self.assertEqual(wallet.reserved_credits, 1_000)


@override_settings(BILLING_HOLDS_ENABLED=False)
class ParkedFilesAreKeptHoldsOff(_ParkedFilesAreKept, TestCase):
    pass


class _RetryByHand(ParkedFixture):
    """A parked item retried while still short, then after a top-up. The
    retry's launch runs the real worker at once; the extraction is one real
    billed call inside the worker's metered unit."""

    def setUp(self):
        self.estimate = estimate_for()
        # One credit short of the worker's own call once s0's hold is gone.
        self.build(self.estimate - 1, tag="retry")
        data = self.upload(1, self.estimate, self.estimate)
        self.queued, self.parked, self.other = self.items(data)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(self.queued.id)
        self.engine_users = []
        launch = patch(
            "students.item_retry.launch_processing_task", side_effect=self._run_now
        )
        launch.start()
        self.addCleanup(launch.stop)

    def _engine(self, *, request_user, processing_task_id, **kwargs):
        self.engine_users.append(request_user.pk)
        with holds().metered_unit(processing_task_id=processing_task_id, reason="t"):
            real_billed_call(request_user)
        return SimpleNamespace(id=uuid.uuid4())

    def _run_now(self, task_callable, processing_task, *args, **kwargs):
        celery_id = str(uuid.uuid4())
        BackgroundProcessingTask.objects.filter(pk=processing_task.pk).update(
            celery_task_id=celery_id
        )
        with patch(
            "assignments.tasks.upload_answers_engine", side_effect=self._engine
        ), patch.object(
            AIProcessor, PROVIDER, return_value=ai_response(100)
        ), self.captureOnCommitCallbacks(
            execute=True
        ):
            task_callable.apply(
                args=args, kwargs={**kwargs, "processing_task_id": processing_task.id}
            )
        return SimpleNamespace(id=celery_id)

    def test_still_short_is_refused_for_credits_and_the_file_survives(self):
        row = self.assertKeptParked(self.parked)

        response = self.retry(self.parked)

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.parked.refresh_from_db()
        self.assertEqual(self.parked.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(self.parked.reason_code, "INSUFFICIENT_CREDITS")
        self.assertEqual(self.parked.retry_count, 1)
        row.refresh_from_db()
        self.assertIsNone(row.purged_at, "the file survives the refusal")
        self.assertEqual(kept_files.load(row.storage_key), PNG)
        self.assertFalse(CreditUsageLog.objects.exists())
        self.assertEqual(
            CreditWallet.objects.get(user=self.teacher).reserved_credits, 0
        )

    def test_after_a_top_up_the_retry_runs_from_the_kept_file(self):
        row = self.assertKeptParked(self.parked)
        self.retry(self.parked)  # still short: refused, file kept
        fund(self.teacher, 100_000)

        response = self.retry(self.parked)

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.parked.refresh_from_db()
        self.assertEqual(self.parked.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(self.parked.retry_count, 2)
        self.assertEqual(self.engine_users, [self.teacher.pk, self.teacher.pk])
        charges = CreditUsageLog.objects.filter(
            wallet__user=self.teacher, is_refunded=False
        )
        self.assertEqual(charges.count(), 1, "one charge, for the one call")
        self.assertEqual(
            CreditWallet.objects.get(user=self.teacher).reserved_credits, 0
        )
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at, "success discards the file")


@override_settings(BILLING_HOLDS_ENABLED=True)
class RetryByHandHoldsOn(_RetryByHand, TestCase):
    def test_the_successful_retry_ran_under_its_own_hold(self):
        fund(self.teacher, 100_000)
        self.retry(self.parked)
        self.parked.refresh_from_db()
        self.assertEqual(self.parked.status, BackgroundTaskStatus.SUCCESS)
        # A hold was taken and settled; one charge; nothing left reserved.
        assert_hold_ledger(self, self.teacher, kept_count=1)


@override_settings(BILLING_HOLDS_ENABLED=False)
class RetryByHandHoldsOff(_RetryByHand, TestCase):
    pass


class RetryFailedPicksUpParkedItems(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="retry-failed")
        self.launched = []
        launch = patch(
            "students.item_retry.launch_processing_task", side_effect=self._record
        )
        launch.start()
        self.addCleanup(launch.stop)

    def _record(self, task_callable, processing_task, *args, **kwargs):
        self.launched.append((processing_task.id, args[1]))
        return SimpleNamespace(id=str(uuid.uuid4()))

    def test_retry_failed_retries_both_parked_items_from_their_files(self):
        data = self.upload(1_000, 20_000, 20_000)
        _, first, second = self.items(data)

        response = self.api.post(
            reverse("task-retry-failed", kwargs={"session_id": data["session_id"]}),
            {"reason_codes": ["INSUFFICIENT_CREDITS"]},
            format="json",
        )

        self.assertEqual(response.status_code, 202, response.content[:400])
        body = response.json()["data"]
        self.assertEqual(body["retried"], [str(first.id), str(second.id)])
        self.assertEqual(
            self.launched,
            [
                (
                    item.id,
                    {
                        "kept_upload_id": str(
                            KeptUpload.objects.get(processing_task=item).id
                        )
                    },
                )
                for item in (first, second)
            ],
        )


class StorageDownFallsBack(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="storage-down")

    def test_not_kept_still_202_and_a_retry_says_not_saved(self):
        with patch(
            "students.kept_files.store",
            side_effect=kept_files.KeptFileUnavailable("down"),
        ), patch("students.kept_uploads.audit_metrics.count") as metric:
            data = self.upload(1_000, 20_000, 20_000)

        _, *parked = self.items(data)
        self.assertFalse(KeptUpload.objects.exists())
        self.assertEqual(
            [c.args[0] for c in metric.call_args_list].count("kept_upload_fallback"),
            3,
        )
        for item in parked:
            self.assertEqual(item.reason_code, "INSUFFICIENT_CREDITS")
            response = self.retry(item)
            self.assertEqual(response.status_code, 409, response.content[:400])
            self.assertIn(NOT_SAVED, response.content.decode())


class ParkedFilesGo(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="parked-go")
        data = self.upload(1_000, 20_000)
        self.session_id = data["session_id"]
        self.parked = self.items(data)[1]
        self.row = KeptUpload.objects.get(processing_task=self.parked)

    def test_the_lifetime_purge_removes_it_after_thirty_days(self):
        self.assertEqual(
            purge_due(now=timezone.now() + timedelta(days=29))["purged"], 0
        )
        with self.captureOnCommitCallbacks(execute=True):
            purge_due(now=timezone.now() + timedelta(days=30, minutes=1))
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.purged_at)
        self.assertEqual(self.row.purge_reason, KeptUpload.PurgeReason.LIFETIME)
        response = self.retry(self.parked)
        self.assertEqual(response.status_code, 409)
        self.assertIn(deleted_after_days(), response.content.decode())

    def test_deleting_the_batch_removes_the_row_and_the_file(self):
        with self.captureOnCommitCallbacks(execute=True):
            BatchUploadSession.objects.filter(pk=self.session_id).delete()
        self.assertFalse(KeptUpload.objects.filter(pk=self.row.pk).exists())
        with self.assertRaises(kept_files.KeptFileMissing):
            kept_files.load(self.row.storage_key)


class SessionResultsSaysRetryable(ParkedFixture, TestCase):
    """GET tasks/session-results: an upload item's `retryable` is
    item_retry's own answer, and `resumable` follows from it."""

    def setUp(self):
        self.build(10_000, tag="results")

    def entry(self, data, item):
        return next(e for e in data["failure_list"] if e["item_id"] == str(item.id))

    def test_a_parked_item_with_its_file_is_retryable_and_resumable(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        results = self.session_results(data["session_id"])
        self.assertIs(self.entry(results, parked)["retryable"], True)
        self.assertIs(results["resumable"], True)

    def test_after_the_lifetime_purge_it_is_not(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        KeptUpload.objects.filter(processing_task=parked).update(
            purged_at=timezone.now(), purge_reason=KeptUpload.PurgeReason.LIFETIME
        )
        results = self.session_results(data["session_id"])
        self.assertIs(self.entry(results, parked)["retryable"], False)
        self.assertIs(results["resumable"], False)

    def test_a_parked_item_not_kept_is_not(self):
        with patch(
            "students.kept_files.store",
            side_effect=kept_files.KeptFileUnavailable("down"),
        ):
            data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        results = self.session_results(data["session_id"])
        self.assertIs(self.entry(results, parked)["retryable"], False)
        self.assertIs(results["resumable"], False)

    def test_grading_items_are_unchanged(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher, course=self.course, task_type=BatchUploadType.GRADE
        )
        rows = {
            code: BackgroundProcessingTask.objects.create(
                requested_by=self.teacher,
                task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
                batch_session=session,
                assignment=self.assignment,
                status=BackgroundTaskStatus.FAILURE,
                reason_code=code,
                item_index=index,
            )
            for index, code in enumerate(
                ("PROVIDER_FAILURE", "INSUFFICIENT_CREDITS"), start=1
            )
        }
        results = self.session_results(session.id)
        self.assertIs(self.entry(results, rows["PROVIDER_FAILURE"])["retryable"], True)
        self.assertIs(
            self.entry(results, rows["INSUFFICIENT_CREDITS"])["retryable"], False
        )

    def test_the_kept_files_are_read_in_one_query_for_the_session(self):
        data = self.upload(1_000, 20_000, 20_000, 20_000, 20_000)
        with CaptureQueriesContext(connection) as queries:
            results = self.session_results(data["session_id"])
        self.assertEqual(len(results["failure_list"]), 4)
        kept_reads = [
            q["sql"]
            for q in queries.captured_queries
            if '"students_keptupload"' in q["sql"]
        ]
        self.assertEqual(len(kept_reads), 1, kept_reads)
