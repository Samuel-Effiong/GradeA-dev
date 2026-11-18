"""A teacher dismisses failed batch items: "don't retry" (owner, 2026-11-18).

POST tasks/session/{id}/items/{item_id}/dismiss and
POST tasks/session/{id}/dismiss-failed (optional {"reason_codes": [...]}).

* Only a FAILURE item; a second dismiss is a no-op (200); any other status is
  the existing 409 NOT_RETRYABLE. Same ownership and H-38 rules as a retry.
* The item stays FAILURE with its code and error; dismissed_at is set.
* Its kept file is discarded at once (purge_reason "dismissed"), the delete
  after commit.
* A dismissed item is not retryable anywhere (retry, retry-failed,
  session-results, tasks/sessions); session-results shows `dismissed` and
  tasks/sessions a dismissed_count.
* No credit moves; one audit event per dismiss.
* A dismissed grading paper's submission is still graded by grade-all.
* Point 8 (a check): cancel-session on a queued grading job cancels its
  waiting papers, closes the job and leaves reserved credits right.

    python manage.py test students.tests_item_dismiss
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.tests_grading_audit_events import make_classroom, make_submission
from audit.models import AuditEvent
from billing.models import (
    AIJob,
    AIJobStatus,
    CreditLedger,
    CreditUsageLog,
    CreditWallet,
)
from billing.testing.holds import fund, holds, make_item, make_job
from classrooms.models import School, Session, SessionOwnerType
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
    KeptUpload,
)
from students.tests_item_retry import GRADE, UPLOAD, RetryFixture
from students.tests_kept_uploads import keep

DISMISSED = "BATCH_ITEM_DISMISSED"


class DismissFixture(RetryFixture):
    def dismiss(self, item, api=None, session=None):
        return (api or self.api).post(
            reverse(
                "task-dismiss-item",
                kwargs={
                    "session_id": str((session or self.session).id),
                    "item_id": str(item.id),
                },
            )
        )

    def dismiss_failed(self, body=None):
        return self.api.post(
            reverse("task-dismiss-failed", kwargs={"session_id": str(self.session.id)}),
            body or {},
            format="json",
        )

    def results(self):
        response = self.api.get(
            reverse("task-session-results", kwargs={"session_id": str(self.session.id)})
        )
        self.assertEqual(response.status_code, 200, response.content[:400])
        return response.json()["data"]

    def entry(self, results, item):
        for key in ("failure_list", "success_list", "pending_list", "cancelled_list"):
            for entry in results[key]:
                if entry["item_id"] == str(item.id):
                    return entry
        raise AssertionError("item not in session-results")


class DismissOneItem(DismissFixture, TestCase):
    def setUp(self):
        self.build()

    def test_a_parked_kept_file_is_deleted_after_commit(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS")
        row = keep(item)
        with patch("students.kept_uploads.kept_files.delete") as delete:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.dismiss(item)
            self.assertEqual(response.status_code, 200, response.content[:400])
            delete.assert_not_called()
            for callback in callbacks:
                callback()
            delete.assert_called_once_with(row.storage_key)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertEqual(row.purge_reason, "dismissed")
        item.refresh_from_db()
        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "INSUFFICIENT_CREDITS")
        self.assertTrue(item.error)
        self.assertIsNotNone(item.dismissed_at)
        self.assertEqual(item.meta.get("dismissed_by"), str(self.teacher.id))
        body = response.json()["data"]
        self.assertEqual(body["item_id"], str(item.id))
        self.assertIs(body["dismissed"], True)
        self.assertIs(body["retryable"], False)

    def test_dismissed_is_not_retryable_anywhere(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS")
        keep(item)
        before = self.entry(self.results(), item)
        self.assertIs(before["retryable"], True)
        self.assertIs(before["dismissed"], False)
        self.assertIs(self.results()["resumable"], True)

        with self.captureOnCommitCallbacks(execute=True):
            self.dismiss(item)

        results = self.results()
        self.assertIs(self.entry(results, item)["retryable"], False)
        self.assertIs(self.entry(results, item)["dismissed"], True)
        self.assertIs(results["resumable"], False)
        self.refused(self.retry(item))
        body = self.body(self.retry_failed())
        self.assertEqual(body["retried"], [])
        self.assertEqual(
            body["skipped"],
            [{"item_id": str(item.id), "reason_code": "INSUFFICIENT_CREDITS"}],
        )
        self.assertEqual(self.launched, [])

    def test_a_dismissed_grading_item_is_not_retryable(self):
        item = self.item(task_type=GRADE, code="PROVIDER_FAILURE")
        self.assertIs(self.entry(self.results(), item)["retryable"], True)
        self.dismiss(item)
        self.assertIs(self.entry(self.results(), item)["retryable"], False)
        self.refused(self.retry(item))
        self.assertEqual(self.launched, [])

    def test_the_list_counts_it_dismissed_and_not_retryable(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS")
        keep(item)
        self.dismiss(item)
        rows = self.api.get(reverse("task-sessions")).json()["data"]["results"]
        row = next(r for r in rows if r["session_id"] == str(self.session.id))
        self.assertEqual(row["dismissed_count"], 1)
        self.assertEqual(row["retryable_count"], 0)
        self.assertIs(row["resumable"], False)

    def test_dismissing_twice_changes_nothing(self):
        item = self.item(code="PROVIDER_FAILURE")
        self.assertEqual(self.dismiss(item).status_code, 200)
        item.refresh_from_db()
        first = item.dismissed_at

        response = self.dismiss(item)

        self.assertEqual(response.status_code, 200, response.content[:400])
        item.refresh_from_db()
        self.assertEqual(item.dismissed_at, first)
        self.assertEqual(AuditEvent.objects.filter(action=DISMISSED).count(), 1)

    def test_an_item_that_did_not_fail_is_refused(self):
        for status in (
            BackgroundTaskStatus.SUCCESS,
            BackgroundTaskStatus.PENDING,
            BackgroundTaskStatus.CANCELLED,
        ):
            item = self.item(status=status)
            self.refused(self.dismiss(item))
            item.refresh_from_db()
            self.assertIsNone(item.dismissed_at)
        self.assertFalse(AuditEvent.objects.filter(action=DISMISSED).exists())

    def test_another_teacher_gets_404(self):
        item = self.item(code="PROVIDER_FAILURE")
        other = APIClient()
        other.force_authenticate(user=self.other)
        self.assertEqual(self.dismiss(item, api=other).status_code, 404)
        item.refresh_from_db()
        self.assertIsNone(item.dismissed_at)

    def test_an_unreachable_course_gets_404(self):
        item = self.item(code="PROVIDER_FAILURE")
        course = self.assignment.course
        course.session = Session.objects.create(
            name="Elsewhere",
            owner_type=SessionOwnerType.SCHOOL,
            school=School.objects.create(name="Elsewhere"),
        )
        course.save(update_fields=["session"])
        self.assertEqual(self.dismiss(item).status_code, 404)
        item.refresh_from_db()
        self.assertIsNone(item.dismissed_at)

    def test_no_credit_moves(self):
        wallet = fund(self.teacher, 5_000)
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS")
        keep(item)
        counts = (
            CreditLedger.objects.count(),
            CreditUsageLog.objects.count(),
            AIJob.objects.count(),
        )
        with self.captureOnCommitCallbacks(execute=True):
            self.dismiss(item)
        self.assertEqual(
            (
                CreditLedger.objects.count(),
                CreditUsageLog.objects.count(),
                AIJob.objects.count(),
            ),
            counts,
        )
        wallet.refresh_from_db()
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertEqual(wallet.total_remaining_credits(), 5_000)

    def test_an_audit_event_is_written(self):
        item = self.item(code="PROVIDER_FAILURE")
        self.dismiss(item)
        event = AuditEvent.objects.get(action=DISMISSED)
        self.assertEqual(event.actor_id, self.teacher.id)
        self.assertEqual(event.target_type, "BackgroundProcessingTask")
        self.assertEqual(event.target_id, item.id)
        self.assertEqual(event.reason_code, "PROVIDER_FAILURE")
        self.assertEqual(event.outcome, "SUCCESS")
        self.assertEqual(
            event.metadata,
            {
                "session_id": str(self.session.id),
                "item_id": str(item.id),
                "task_type": item.task_type,
            },
        )


class DismissFailed(DismissFixture, TestCase):
    def setUp(self):
        self.build()

    def test_reason_codes_dismiss_only_the_matching_items(self):
        provider = self.item(code="PROVIDER_FAILURE")
        unreadable = self.item(task_type=UPLOAD, code="FILE_UNREADABLE")
        done = self.item(status=BackgroundTaskStatus.SUCCESS)

        response = self.dismiss_failed({"reason_codes": ["PROVIDER_FAILURE"]})

        self.assertEqual(response.status_code, 200, response.content[:400])
        body = response.json()["data"]
        self.assertEqual(body["dismissed"], [str(provider.id)])
        self.assertEqual(
            body["skipped"],
            [{"item_id": str(unreadable.id), "reason_code": "FILE_UNREADABLE"}],
        )
        for item, dismissed in ((provider, True), (unreadable, False), (done, False)):
            item.refresh_from_db()
            self.assertEqual(item.dismissed_at is not None, dismissed)

    def test_without_codes_every_failed_item_is_dismissed(self):
        first = self.item(code="PROVIDER_FAILURE")
        second = self.item(task_type=UPLOAD, code="FILE_UNREADABLE")
        body = self.dismiss_failed().json()["data"]
        self.assertEqual(body["dismissed"], [str(first.id), str(second.id)])
        self.assertEqual(body["skipped"], [])
        self.assertEqual(AuditEvent.objects.filter(action=DISMISSED).count(), 2)

    def test_an_already_dismissed_item_is_not_dismissed_again(self):
        item = self.item(code="PROVIDER_FAILURE")
        self.dismiss(item)
        body = self.dismiss_failed().json()["data"]
        self.assertEqual(body["dismissed"], [])
        self.assertEqual(AuditEvent.objects.filter(action=DISMISSED).count(), 1)


class DismissedPaperIsStillGradedByGradeAll(TestCase):
    @patch("assignments.views.grade_engine_async")
    def test_grade_all_picks_up_the_dismissed_papers_submission(self, engine):
        engine.delay.return_value.id = "celery-id"
        teacher, student, course, assignment = make_classroom("dismiss-grade-all")
        submission = make_submission(assignment, student)
        session = BatchUploadSession.objects.create(
            teacher=teacher, course=course, task_type=BatchUploadType.GRADE
        )
        failed = BackgroundProcessingTask.objects.create(
            requested_by=teacher,
            task_type=BackgroundTaskType.BATCH_SUBMISSION_GRADING,
            batch_session=session,
            assignment=assignment,
            submission=submission,
            status=BackgroundTaskStatus.FAILURE,
            reason_code="PROVIDER_FAILURE",
            item_index=1,
        )
        api = APIClient()
        api.force_authenticate(user=teacher)
        with patch(
            "users.permissions.HasCreditBalance.has_permission", return_value=True
        ):
            dismissed = api.post(
                reverse(
                    "task-dismiss-item",
                    kwargs={"session_id": str(session.id), "item_id": str(failed.id)},
                )
            )
            self.assertEqual(dismissed.status_code, 200, dismissed.content[:400])
            submission.refresh_from_db()
            self.assertIsNone(submission.graded_at, "the submission stays ungraded")

            response = api.post(
                reverse("assignment-grade-all", kwargs={"pk": assignment.pk})
            )

        self.assertEqual(response.status_code, 202, response.content[:400])
        new_session = response.json()["data"]["session_id"]
        self.assertTrue(
            BackgroundProcessingTask.objects.filter(
                batch_session_id=new_session, submission=submission
            ).exists()
        )


@override_settings(BILLING_HOLDS_ENABLED=True)
class CancelSessionOnAQueuedJob(DismissFixture, TestCase):
    """Point 8, a check: the papers that ran held and returned credits; the
    job is QUEUED_PENDING_CREDITS with two papers waiting (no hold). Cancel
    the session: the waiting papers are cancelled, the job closes CANCELLED,
    reserved credits stay right and the invariant check finds nothing."""

    def setUp(self):
        self.build()

    def test_cancel_session_closes_the_queue_with_reserved_credits_right(self):
        wallet = fund(self.teacher, 10_000)
        job = make_job(
            wallet,
            "BATCH_GRADING",
            total_items=4,
            status=AIJobStatus.RESERVED,
            assignment=self.assignment,
        )
        ran = [
            make_item(
                job,
                task_type=GRADE,
                assignment=self.assignment,
                batch_session=self.session,
                status=BackgroundTaskStatus.STARTED,
            )
            for _ in range(2)
        ]
        for row in ran:
            self.assertTrue(holds().reserve(wallet, 1_000, job, item=row))
        self.assertEqual(CreditWallet.objects.get(pk=wallet.pk).reserved_credits, 2_000)
        for row in ran:
            holds().fail_item(job, item=row)
            BackgroundProcessingTask.objects.filter(pk=row.pk).update(
                status=BackgroundTaskStatus.FAILURE
            )
        AIJob.objects.filter(pk=job.pk).update(
            status=AIJobStatus.QUEUED_PENDING_CREDITS, queued_at=timezone.now()
        )
        waiting = [
            make_item(
                job,
                task_type=GRADE,
                assignment=self.assignment,
                batch_session=self.session,
                status=BackgroundTaskStatus.PENDING,
            )
            for _ in range(2)
        ]

        with self.captureOnCommitCallbacks(execute=True):
            response = self.api.post(
                reverse(
                    "task-cancel-session", kwargs={"session_id": str(self.session.id)}
                )
            )

        self.assertEqual(response.status_code, 200, response.content[:400])
        for row in waiting:
            row.refresh_from_db()
            self.assertEqual(row.status, BackgroundTaskStatus.CANCELLED)
        self.assertEqual(AIJob.objects.get(pk=job.pk).status, AIJobStatus.CANCELLED)
        self.assertEqual(CreditWallet.objects.get(pk=wallet.pk).reserved_credits, 0)
        self.assertEqual(holds().check_invariants(wallet), [])
        self.assertFalse(KeptUpload.objects.exists())
