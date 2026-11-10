"""Epic B1b-5 (08b §3.2 flow 4, NOTES §5.6 Q2 and Q4): an upload item whose
file is kept is retried in place, like a grading item.

* A FAILED upload item is retried when its file is kept (a KeptUpload row,
  not purged) and its code is PROVIDER_FAILURE, INSUFFICIENT_CREDITS_MID_BATCH
  or, for upload items only, INSUFFICIENT_CREDITS (the late door). The same
  row: retry_count + 1, relaunched with the kept file's id, the shared prompt
  and the ids the route used.
* Everything else is refused exactly as before: NOT_RETRYABLE with
  resolution "replace_file" (purged, never kept, or a file refusal).
* A grading item with INSUFFICIENT_CREDITS is still refused (SM Q4).

    python manage.py test students.tests_kept_upload_retry
"""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from audit.enums import AuditAction
from audit.models import AuditEvent
from classrooms.models import Topic
from students.models import BackgroundTaskType
from students.tests_item_retry import GRADE, UPLOAD, RetryFixture
from students.tests_kept_uploads import BATCH_PROMPT_VERBATIM, keep

ASSIGNMENT_UPLOAD = BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD


class KeptUploadRetryTests(RetryFixture, TestCase):
    def setUp(self):
        self.build()

    def test_a_kept_answer_upload_stopped_for_credits_is_retried_in_place(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS_MID_BATCH")
        row = keep(item)

        response = self.retry(item)

        self.assertEqual(response.status_code, 202, response.content[:400])
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 1)
        self.assertEqual(
            self.launched,
            [
                (
                    "assignments.tasks.upload_answers_engine_async",
                    item.id,
                    (
                        str(self.assignment.id),
                        {"kept_upload_id": str(row.id)},
                        BATCH_PROMPT_VERBATIM,
                        str(self.teacher.id),
                    ),
                    {"session_id": str(self.session.id), "file_name": item.file_name},
                )
            ],
        )

    def test_a_kept_answer_upload_with_a_provider_failure_is_retried(self):
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        keep(item)
        self.assertEqual(self.retry(item).status_code, 202)

    def test_a_kept_late_door_upload_refused_for_credits_is_retried(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS")
        keep(item)
        self.assertEqual(self.retry(item).status_code, 202)

    def test_a_grading_item_refused_for_credits_is_still_refused(self):
        item = self.item(task_type=GRADE, code="INSUFFICIENT_CREDITS")
        envelope = self.refused(self.retry(item))
        self.assertNotIn("resolution", envelope.get("params") or {})
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 0)
        self.assertEqual(self.launched, [])

    def test_a_purged_file_is_refused_upload_it_again(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS_MID_BATCH")
        keep(item, purged=True)
        envelope = self.refused(self.retry(item))
        self.assertEqual(envelope["params"]["resolution"], "replace_file")
        item.refresh_from_db()
        self.assertEqual(item.retry_count, 0)
        self.assertEqual(self.launched, [])

    def test_a_file_past_kept_until_but_not_yet_purged_is_still_retried(self):
        item = self.item(task_type=UPLOAD, code="INSUFFICIENT_CREDITS_MID_BATCH")
        keep(item, until=timezone.now() - timedelta(minutes=5))
        self.assertEqual(self.retry(item).status_code, 202)

    def test_a_kept_file_refused_for_its_content_is_refused_upload_it_again(self):
        item = self.item(task_type=UPLOAD, code="FILE_UNREADABLE")
        keep(item)
        envelope = self.refused(self.retry(item))
        self.assertEqual(envelope["params"]["resolution"], "replace_file")
        self.assertEqual(self.launched, [])

    def test_a_kept_assignment_upload_is_relaunched_with_its_course_and_topic(self):
        topic = Topic.objects.create(name="Algebra", course=self.assignment.course)
        item = self.item(task_type=ASSIGNMENT_UPLOAD, code="PROVIDER_FAILURE")
        row = keep(item, relaunch={"topic_id": str(topic.id)})

        response = self.retry(item)

        self.assertEqual(response.status_code, 202, response.content[:400])
        self.assertEqual(
            self.launched,
            [
                (
                    "assignments.tasks.upload_assignment_async",
                    item.id,
                    (),
                    {
                        "user_id": str(self.teacher.id),
                        "course_id": str(self.assignment.course_id),
                        "topic_id": str(topic.id),
                        "session_id": str(self.session.id),
                        "file_payload": {"kept_upload_id": str(row.id)},
                        "prompt_text": BATCH_PROMPT_VERBATIM,
                        "file_name": item.file_name,
                    },
                )
            ],
        )

    def test_an_upload_retry_is_not_audited_as_grading(self):
        item = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        keep(item)
        self.retry(item)
        self.assertFalse(
            AuditEvent.objects.filter(action=AuditAction.GRADING_REQUESTED).exists()
        )

    def test_retry_failed_includes_a_kept_upload_item(self):
        kept = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")
        keep(kept)
        not_kept = self.item(task_type=UPLOAD, code="PROVIDER_FAILURE")

        response = self.retry_failed()

        self.assertEqual(response.status_code, 202, response.content[:400])
        data = self.body(response)
        self.assertEqual(data["retried"], [str(kept.id)])
        self.assertEqual(
            [entry["item_id"] for entry in data["skipped"]], [str(not_kept.id)]
        )
