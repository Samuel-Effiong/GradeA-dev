"""A retry's "deleted after N days" message only for a file that really
expired (B1b, found in the hands-on test on staging).

`discard()` (cancel, refusal, success) moves kept_until to now and deletes
the file a moment later, so "purged_at at or after kept_until" was true for
every discard and the retry said the file was deleted after 30 days. The row
now records how it was purged (`purge_reason`): "lifetime" for the hourly
purge, "discarded" for discard(), blank for a file that was lost. Only
"lifetime" earns the days message. A discard falls through to the default
409; no new wording.

    python manage.py test students.tests_retry_purge_reason
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from assignments.tasks import upload_answers_engine_async
from students import kept_files, kept_uploads
from students.exceptions import StudentNameUnmatchedError
from students.item_retry import NOT_SAVED, deleted_after_days
from students.models import BackgroundTaskStatus
from students.task_tracking import (
    mark_processing_task_cancelled,
    mark_processing_task_success,
)
from students.tests_item_retry import UPLOAD, RetryFixture
from students.tests_kept_uploads import keep

DEFAULT_WHY = "This item can't be retried as it is."


class RetryAfterTheFileIsGoneTests(RetryFixture, TestCase):
    def setUp(self):
        self.build()

    def assertRefusedWith(self, item, why):
        response = self.retry(item)
        envelope = self.refused(response)
        self.assertEqual(envelope["params"]["resolution"], "replace_file")
        self.assertIn(why, response.content.decode())
        if why != deleted_after_days():
            self.assertNotIn("deleted after", response.content.decode())
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 0)
        self.assertEqual(self.launched, [])

    def purge(self):
        with self.captureOnCommitCallbacks(execute=True):
            kept_uploads.purge_due()

    def test_a_cancelled_item_gets_the_default_not_the_days_message(self):
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        row = keep(item)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(item.id)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at, "fixture: the cancel deleted the file")
        self.assertRefusedWith(item, DEFAULT_WHY)

    def test_a_refused_item_gets_the_default_not_the_days_message(self):
        item = self.item(task_type=UPLOAD, code="")
        item.status = BackgroundTaskStatus.STARTED
        item.save(update_fields=["status"])
        row = keep(item)
        refusal = StudentNameUnmatchedError(
            params={"file_name": "a.png", "name_state": "no name was found"}
        )
        with patch(
            "assignments.tasks.upload_answers_engine", side_effect=refusal
        ), self.captureOnCommitCallbacks(execute=True):
            upload_answers_engine_async.apply(
                args=(
                    str(self.assignment.id),
                    {"kept_upload_id": str(row.id)},
                    "prompt",
                    str(self.teacher.id),
                ),
                kwargs={"processing_task_id": item.id},
            )
        row.refresh_from_db()
        item.refresh_from_db()
        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertIsNotNone(row.purged_at, "fixture: the refusal deleted the file")
        self.assertRefusedWith(item, DEFAULT_WHY)

    def test_a_succeeded_item_gets_the_default_not_the_days_message(self):
        item = self.item(task_type=UPLOAD, code="")
        row = keep(item)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_success(item.id)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at, "fixture: success deleted the file")
        self.assertRefusedWith(item, DEFAULT_WHY)

    def test_a_file_the_lifetime_purge_deleted_keeps_the_days_message(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS_MID_BATCH")
        row = keep(item, until=timezone.now() - timedelta(minutes=5))
        self.purge()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at, "fixture: the purge deleted the file")
        self.assertRefusedWith(item, deleted_after_days())

    def test_a_discard_whose_delete_failed_stays_a_discard_when_the_purge_retries(
        self,
    ):
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        row = keep(item)
        with patch(
            "students.kept_files.delete",
            side_effect=kept_files.KeptFileUnavailable("down"),
        ), self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(item.id)
        row.refresh_from_db()
        self.assertIsNone(row.purged_at, "fixture: the first delete failed")

        self.purge()  # the hourly purge deletes it now

        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at, "fixture: the purge deleted it")
        self.assertEqual(row.purge_reason, "discarded")
        self.assertRefusedWith(item, DEFAULT_WHY)

    def test_a_lost_file_keeps_its_two_step_answer(self):
        # The worker found the file gone: the item fails with its own
        # message; the row is marked purged with no reason, so the retry
        # that follows says the default and offers replace_file.
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        row = keep(item)
        kept_files.delete(row.storage_key)
        with self.assertRaises(kept_uploads.KeptFileLost):
            kept_uploads.open_upload({"kept_upload_id": str(row.id)})
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertEqual(row.purge_reason, "")
        self.assertRefusedWith(item, DEFAULT_WHY)

    def test_no_kept_row_still_says_it_was_not_saved(self):
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        self.assertRefusedWith(item, NOT_SAVED)
