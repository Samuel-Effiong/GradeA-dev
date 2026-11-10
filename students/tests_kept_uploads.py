"""Epic B1b-5 (08b §3, NOTES §5 and the SM's rulings §5.6): uploaded files
are kept, so a stopped upload item can be retried without a re-upload.

What is pinned here:
* QUEUE_LIFETIME_DAYS (08b §2.1, brought forward by Q1): 30 when unset,
  1-365, anything else stops start-up;
* the three upload routes keep the file at the door and send its id, and
  fall back to today's inline payload when storage is down (Q3 of 08b §9);
* the workers load the kept file and check its sha256; a missing or corrupt
  file is final (SM Q3), a storage outage retries;
* the file is deleted when its item succeeds or is cancelled (Q3b, Q5),
  by the hourly purge after `kept_until`, and after commit whenever its row
  is deleted (erasure included);
* the student data map entry (Q7a) and the export's field decision (Q7b),
  with the nested name held behind a False constant until the owner
  approves it.

New modules and the new model are looked up by name, so this file loads
before they exist. Every test uses the in-memory storage backend.

    python manage.py test students.tests_kept_uploads
"""

import hashlib
import importlib
import io
import json
import zipfile
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from celery.schedules import crontab
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db.models import Q
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APITestCase

from assignments.tasks import upload_answers_engine_async, upload_assignment_async
from AutoGrader.beat_locks import declared_lock
from classrooms.models import Topic
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)
from students.task_tracking import mark_processing_task_cancelled
from students.tests_upload_credit_door import PNG, RICH, _classroom, _file, _user
from users.models import UserTypes

#: The fallback a failed answer upload has always shown (assignments/tasks.py).
ANSWER_FALLBACK = (
    "We couldn't process this submission upload. Please check the file and "
    "try again, or contact support if this continues."
)

#: The two batch routes' prompt, pasted verbatim from students/views.py and
#: assignments/views.py at 583ceb1f8 (byte-identical in both; SM Q2).
BATCH_PROMPT_VERBATIM = (
    "\n"
    "            Analyze the image of an educational assignment and return a JSON\n"
    "\n"
    "            IMPORTANT: Return only valid JSON matching the required structure.\n"
    "            Do not include any explanatory text before or after the JSON\n"
    "            "
)

LIMIT_MESSAGE = "QUEUE_LIFETIME_DAYS must be a whole number of days from 1 to 365"


def kept_files():
    return importlib.import_module("students.kept_files")


def kept_uploads():
    return importlib.import_module("students.kept_uploads")


def KeptUpload():
    return apps.get_model("students", "KeptUpload")


def keep(item, content=PNG, *, relaunch=None, until=None, purged=False):
    """A KeptUpload row for `item`, its bytes in the memory store."""
    stored = kept_files().store(content)
    now = timezone.now()
    return KeptUpload().objects.create(
        processing_task=item,
        storage_key=stored.storage_key,
        original_name=item.file_name or "a.png",
        content_type="image/png",
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        relaunch=relaunch or {},
        kept_until=until or now + timedelta(days=30),
        purged_at=now if purged else None,
    )


def is_stored(storage_key):
    try:
        kept_files().load(storage_key)
    except kept_files().KeptFileMissing:
        return False
    return True


def metric_names(metric):
    return [call.args[0] for call in metric.call_args_list]


# --------------------------------------------------------------------------
# QUEUE_LIFETIME_DAYS (SM Q1; 08b §2.1)
# --------------------------------------------------------------------------


class QueueLifetimeSettingTests(SimpleTestCase):
    def parse(self, raw):
        return importlib.import_module(
            "AutoGrader.queue_lifetime"
        ).parse_queue_lifetime_days(raw)

    def test_unset_is_thirty(self):
        self.assertEqual(self.parse(None), 30)

    def test_the_setting_is_thirty_under_test(self):
        self.assertEqual(getattr(settings, "QUEUE_LIFETIME_DAYS", None), 30)

    def test_one_and_three_hundred_sixty_five_are_accepted(self):
        self.assertEqual(self.parse("1"), 1)
        self.assertEqual(self.parse("365"), 365)

    def test_anything_else_stops_start_up_with_a_clear_message(self):
        for raw in ("0", "366", "-1", "abc", "1.5", "", " "):
            with self.subTest(raw=raw):
                with self.assertRaisesMessage(ImproperlyConfigured, LIMIT_MESSAGE):
                    self.parse(raw)


# --------------------------------------------------------------------------
# The door: the three routes (08b §3.2 flow 1-2)
# --------------------------------------------------------------------------


class StudentRouteKeepsTheFileTests(APITestCase):
    def post(self, student, assignment):
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return self.client.post(
            url, {"answer": _file(name="answer.png")}, format="multipart"
        )

    @patch("students.views.launch_processing_task")
    def test_the_file_is_kept_and_the_task_gets_its_id(self, launch):
        launch.return_value.id = "t-1"
        _, student, _, assignment = _classroom("kept-student", RICH)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(student, assignment)

        self.assertEqual(response.status_code, 202, response.content[:400])
        item = BackgroundProcessingTask.objects.get(requested_by=student)
        row = KeptUpload().objects.get(processing_task=item)
        self.assertEqual(launch.call_args.args[3], {"kept_upload_id": str(row.id)})
        self.assertEqual(kept_files().load(row.storage_key), PNG)
        self.assertEqual(row.sha256, hashlib.sha256(PNG).hexdigest())
        self.assertEqual(row.size_bytes, len(PNG))
        self.assertEqual(row.original_name, "answer.png")
        self.assertEqual(row.content_type, "image/png")
        self.assertIsNone(row.purged_at)

    @patch("students.views.launch_processing_task")
    def test_kept_until_is_the_upload_time_plus_the_setting(self, launch):
        launch.return_value.id = "t-1"
        _, student, _, assignment = _classroom("kept-days", RICH)
        with override_settings(QUEUE_LIFETIME_DAYS=3):
            with self.captureOnCommitCallbacks(execute=True):
                self.post(student, assignment)
        row = KeptUpload().objects.get(processing_task__requested_by=student)
        self.assertEqual(row.kept_until - row.created_at, timedelta(days=3))

    @patch("audit.metrics.count")
    @patch("students.views.launch_processing_task")
    def test_storage_down_falls_back_to_the_inline_file(self, launch, metric):
        launch.return_value.id = "t-1"
        _, student, _, assignment = _classroom("kept-down", RICH)
        with patch(
            "students.kept_files.store",
            side_effect=kept_files().KeptFileUnavailable("down"),
        ):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.post(student, assignment)

        self.assertEqual(response.status_code, 202, response.content[:400])
        payload = launch.call_args.args[3]
        import base64

        self.assertEqual(base64.b64decode(payload["content_b64"]), PNG)
        self.assertNotIn("kept_upload_id", payload)
        self.assertFalse(KeptUpload().objects.exists())
        self.assertIn("kept_upload_fallback", metric_names(metric))

    @patch("students.views.launch_processing_task")
    def test_a_refused_upload_leaves_no_row_and_no_file(self, launch):
        _, student, _, assignment = _classroom("kept-refused", RICH)
        before = set(kept_files().MEMORY)
        with patch("students.views.DoorHold.hold", return_value=False), patch(
            "students.views.DoorHold.refusal_response",
            return_value=Response({"refused": True}, status=402),
        ):
            response = self.post(student, assignment)

        self.assertEqual(response.status_code, 402, response.content[:400])
        launch.assert_not_called()
        self.assertFalse(KeptUpload().objects.exists())
        self.assertEqual(set(kept_files().MEMORY), before)


class TeacherBatchRouteKeepsTheFilesTests(APITestCase):
    @patch("students.views.launch_processing_task")
    def test_every_file_is_kept_and_sent_by_id_with_the_shared_prompt(self, launch):
        launch.return_value.id = "t-1"
        teacher, _, _, assignment = _classroom("kept-batch", RICH)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [_file(name=f"s{n}.png") for n in range(2)]
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(url, {"answers": files}, format="multipart")

        self.assertEqual(response.status_code, 202, response.content[:400])
        rows = {
            str(row.id): row
            for row in KeptUpload().objects.filter(
                processing_task__batch_session__isnull=False
            )
        }
        self.assertEqual(len(rows), 2)
        sent = [call.args[3]["kept_upload_id"] for call in launch.call_args_list]
        self.assertEqual(sorted(sent), sorted(rows))
        for call in launch.call_args_list:
            self.assertEqual(call.args[4], BATCH_PROMPT_VERBATIM)
        for row in rows.values():
            self.assertEqual(kept_files().load(row.storage_key), PNG)


class AssignmentRouteKeepsTheFileTests(APITestCase):
    def post(self, teacher, course, topic=None):
        self.client.force_authenticate(user=teacher)
        data = {"course": str(course.id), "assignments": [_file(name="paper.png")]}
        if topic is not None:
            data["topic"] = str(topic.id)
        return self.client.post(
            reverse("assignment-upload-async"), data, format="multipart"
        )

    @patch("assignments.views.launch_processing_task")
    def test_the_file_is_kept_with_its_topic_for_a_retry(self, launch):
        launch.return_value.id = "t-1"
        teacher, _, course, _ = _classroom("kept-assign", RICH)
        topic = Topic.objects.create(name="Fractions", course=course)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.post(teacher, course, topic)

        self.assertEqual(response.status_code, 202, response.content[:400])
        row = KeptUpload().objects.get()
        kwargs = launch.call_args.kwargs
        self.assertEqual(kwargs["file_payload"], {"kept_upload_id": str(row.id)})
        self.assertEqual(kwargs["prompt_text"], BATCH_PROMPT_VERBATIM)
        self.assertEqual(row.relaunch, {"topic_id": str(topic.id)})
        self.assertEqual(kept_files().load(row.storage_key), PNG)

    @patch("assignments.views.launch_processing_task")
    def test_no_topic_is_kept_as_none(self, launch):
        launch.return_value.id = "t-1"
        teacher, _, course, _ = _classroom("kept-notopic", RICH)
        with self.captureOnCommitCallbacks(execute=True):
            self.post(teacher, course)
        self.assertEqual(KeptUpload().objects.get().relaunch, {"topic_id": None})


class SharedPromptTests(SimpleTestCase):
    def test_the_shared_prompt_is_the_old_route_string_byte_for_byte(self):
        prompts = importlib.import_module("assignments.upload_prompts")
        self.assertEqual(prompts.BATCH_UPLOAD_PROMPT, BATCH_PROMPT_VERBATIM)


# --------------------------------------------------------------------------
# The workers (08b §3.2 flow 3; SM Q3)
# --------------------------------------------------------------------------


class _WorkerCase(TestCase):
    def setUp(self):
        self.teacher, _, self.course, self.assignment = _classroom("kept-w", RICH)

    def answer_item(self):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
            assignment=self.assignment,
            file_name="a.png",
            celery_task_id=str(uuid4()),
        )

    def run_answers(self, item, payload):
        with patch(
            "assignments.tasks.upload_answers_engine",
            return_value=SimpleNamespace(id=uuid4()),
        ) as engine, self.captureOnCommitCallbacks(execute=True):
            outcome = upload_answers_engine_async.apply(
                args=(str(self.assignment.id), payload, "prompt", str(self.teacher.id)),
                kwargs={"processing_task_id": item.id},
            )
        item.refresh_from_db()
        return outcome, engine


class AnswerWorkerTests(_WorkerCase):
    def test_a_kept_file_is_loaded_and_deleted_when_the_item_succeeds(self):
        item = self.answer_item()
        row = keep(item)

        _, engine = self.run_answers(item, {"kept_upload_id": str(row.id)})

        self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        engine.assert_called_once()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertFalse(is_stored(row.storage_key))

    def test_the_inline_payload_still_works(self):
        import base64

        item = self.answer_item()
        payload = {
            "name": "a.png",
            "content_type": "image/png",
            "content_b64": base64.b64encode(PNG).decode("ascii"),
        }
        _, engine = self.run_answers(item, payload)
        self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        engine.assert_called_once()

    @patch("audit.metrics.count")
    def test_a_corrupt_file_fails_the_item_once_with_the_old_text(self, metric):
        item = self.answer_item()
        row = keep(item)
        kept_files().MEMORY[row.storage_key] = b"not the same bytes"

        with patch("students.kept_files.load", wraps=kept_files().load) as load:
            _, engine = self.run_answers(item, {"kept_upload_id": str(row.id)})

        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "")
        self.assertEqual(item.error, ANSWER_FALLBACK)
        engine.assert_not_called()
        self.assertEqual(load.call_count, 1)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertIn("kept_upload_integrity_failure", metric_names(metric))

    @patch("audit.metrics.count")
    def test_a_missing_file_fails_the_item_once_with_the_old_text(self, metric):
        item = self.answer_item()
        row = keep(item)
        del kept_files().MEMORY[row.storage_key]

        _, engine = self.run_answers(item, {"kept_upload_id": str(row.id)})

        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(item.reason_code, "")
        self.assertEqual(item.error, ANSWER_FALLBACK)
        engine.assert_not_called()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertIn("kept_upload_integrity_failure", metric_names(metric))

    @patch("audit.metrics.count")
    def test_storage_down_is_retried_and_the_file_stays_kept(self, metric):
        item = self.answer_item()
        row = keep(item)

        with patch(
            "students.kept_files.load",
            side_effect=kept_files().KeptFileUnavailable("down"),
        ) as load:
            outcome, engine = self.run_answers(item, {"kept_upload_id": str(row.id)})

        self.assertTrue(outcome.failed())
        self.assertEqual(
            load.call_count, 1 + (upload_answers_engine_async.max_retries or 0)
        )
        self.assertEqual(item.status, BackgroundTaskStatus.FAILURE)
        engine.assert_not_called()
        row.refresh_from_db()
        self.assertIsNone(row.purged_at)
        self.assertNotIn("kept_upload_integrity_failure", metric_names(metric))

    def test_a_failed_delete_on_success_leaves_the_row_for_the_purge(self):
        item = self.answer_item()
        row = keep(item)
        with patch(
            "students.kept_files.delete",
            side_effect=kept_files().KeptFileUnavailable("down"),
        ):
            self.run_answers(item, {"kept_upload_id": str(row.id)})

        self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        row.refresh_from_db()
        self.assertIsNone(row.purged_at)
        self.assertLessEqual(row.kept_until, timezone.now())
        self.assertTrue(is_stored(row.storage_key))


class AssignmentWorkerTests(_WorkerCase):
    def test_a_kept_file_is_loaded_and_its_digest_is_the_rows(self):
        session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.course,
            task_type=BatchUploadType.ASSIGNMENT,
            total_files=1,
        )
        item = BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ASSIGNMENT_UPLOAD,
            batch_session=session,
            file_name="paper.png",
            celery_task_id=str(uuid4()),
        )
        row = keep(item)
        outcome = SimpleNamespace(
            assignment=SimpleNamespace(id=self.assignment.id), already_uploaded=False
        )
        with patch(
            "assignments.tasks.upload_assignment_file", return_value=outcome
        ) as upload, self.captureOnCommitCallbacks(execute=True):
            upload_assignment_async.apply(
                kwargs={
                    "user_id": str(self.teacher.id),
                    "course_id": str(self.course.id),
                    "session_id": str(session.id),
                    "file_payload": {"kept_upload_id": str(row.id)},
                    "prompt_text": "prompt",
                    "file_name": "paper.png",
                    "processing_task_id": item.id,
                }
            )

        item.refresh_from_db()
        self.assertEqual(item.status, BackgroundTaskStatus.SUCCESS)
        self.assertEqual(upload.call_args.kwargs["sha256"], row.sha256)
        self.assertEqual(upload.call_args.args[1].read(), PNG)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)


class CancelDeletesTheFileTests(_WorkerCase):
    def test_a_cancelled_upload_item_loses_its_file(self):
        item = self.answer_item()
        row = keep(item)
        with self.captureOnCommitCallbacks(execute=True):
            mark_processing_task_cancelled(item.id)
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertFalse(is_stored(row.storage_key))


# --------------------------------------------------------------------------
# The purge (08b §3.2 retention and purge)
# --------------------------------------------------------------------------


class PurgeTests(_WorkerCase):
    def item_kept(self, until, **kwargs):
        return keep(self.answer_item(), until=until, **kwargs)

    def purge(self):
        with self.captureOnCommitCallbacks(execute=True):
            return kept_uploads().purge_due()

    def test_a_row_past_kept_until_is_deleted_and_marked(self):
        row = self.item_kept(timezone.now() - timedelta(minutes=1))
        self.purge()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertFalse(is_stored(row.storage_key))

    def test_a_row_not_yet_due_is_left(self):
        row = self.item_kept(timezone.now() + timedelta(minutes=1))
        self.purge()
        row.refresh_from_db()
        self.assertIsNone(row.purged_at)
        self.assertTrue(is_stored(row.storage_key))

    def test_a_row_already_purged_is_not_deleted_again(self):
        self.item_kept(timezone.now() - timedelta(days=1), purged=True)
        with patch("students.kept_files.delete") as delete:
            self.purge()
        delete.assert_not_called()

    def test_a_failed_delete_leaves_the_row_unpurged_for_the_next_run(self):
        row = self.item_kept(timezone.now() - timedelta(minutes=1))
        with patch(
            "students.kept_files.delete",
            side_effect=kept_files().KeptFileUnavailable("down"),
        ):
            self.purge()
        row.refresh_from_db()
        self.assertIsNone(row.purged_at)

    @patch("audit.metrics.count")
    def test_a_row_a_day_overdue_is_counted(self, metric):
        self.item_kept(timezone.now() - timedelta(hours=25))
        self.item_kept(timezone.now() - timedelta(hours=23))
        with patch(
            "students.kept_files.delete",
            side_effect=kept_files().KeptFileUnavailable("down"),
        ):
            self.purge()
        overdue = [
            call
            for call in metric.call_args_list
            if call.args[0] == "kept_upload_purge_overdue"
        ]
        self.assertEqual(len(overdue), 1)
        self.assertEqual(overdue[0].args[1], 1)

    def test_more_than_one_batch_is_purged_in_one_run(self):
        due = timezone.now() - timedelta(minutes=1)
        for _ in range(201):
            self.item_kept(due)
        self.purge()
        self.assertFalse(KeptUpload().objects.filter(purged_at__isnull=True).exists())

    def test_the_task_runs_one_at_a_time(self):
        task = importlib.import_module("students.tasks").purge_kept_uploads
        self.assertIsNotNone(declared_lock(task))

    def test_the_task_purges(self):
        row = self.item_kept(timezone.now() - timedelta(minutes=1))
        task = importlib.import_module("students.tasks").purge_kept_uploads
        with self.captureOnCommitCallbacks(execute=True):
            task.run()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)

    def test_the_task_is_scheduled_hourly(self):
        entry = settings.CELERY_BEAT_SCHEDULE["purge-kept-uploads-hourly"]
        self.assertEqual(entry["task"], "students.tasks.purge_kept_uploads")
        self.assertEqual(entry["schedule"], crontab(minute=15))


# --------------------------------------------------------------------------
# A deleted row takes its file with it, after commit (08b §3.3)
# --------------------------------------------------------------------------


class DeletedRowTests(_WorkerCase):
    def test_the_file_goes_only_after_the_delete_commits(self):
        item = self.answer_item()
        row = keep(item)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            item.delete()
        self.assertFalse(KeptUpload().objects.filter(pk=row.pk).exists())
        self.assertTrue(is_stored(row.storage_key))
        for callback in callbacks:
            callback()
        self.assertFalse(is_stored(row.storage_key))


class ErasureTests(TestCase):
    def setUp(self):
        self.teacher, self.student, self.course, self.assignment = _classroom(
            "kept-erase", RICH
        )
        item = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=self.assignment,
            file_name="mine.png",
        )
        self.row = keep(item)

    def erase(self, scope):
        data_erase = importlib.import_module("students.data_erase")
        admin = _user("kept-erase-admin", UserTypes.SUPER_ADMIN)
        with self.captureOnCommitCallbacks(execute=True):
            data_erase.erase_student(self.student.pk, scope, by=admin)

    def test_a_full_erasure_deletes_the_row_and_the_file(self):
        from students.data_erase import EraseScope

        self.erase(EraseScope("full"))
        self.assertFalse(KeptUpload().objects.filter(pk=self.row.pk).exists())
        self.assertFalse(is_stored(self.row.storage_key))

    def test_a_teachers_course_erasure_deletes_the_row_and_the_file(self):
        from students.data_erase import EraseScope

        self.erase(EraseScope("courses", frozenset({self.course.id})))
        self.assertFalse(KeptUpload().objects.filter(pk=self.row.pk).exists())
        self.assertFalse(is_stored(self.row.storage_key))


class StudentDataMapTests(SimpleTestCase):
    """Q7a: the owner approved KeptUpload as DELETE (2026-11-09)."""

    def test_a_kept_upload_is_deleted_with_the_student_through_its_task(self):
        from students.student_data_map import STUDENT_DATA_MAP, Role

        entry = STUDENT_DATA_MAP[("students.KeptUpload", "processing_task")]
        self.assertEqual(entry.role, Role.DELETE)
        self.assertEqual(entry.match, "PROCESSING_TASK")
        self.assertTrue(entry.export)
        self.assertTrue(entry.course_scoped)


# --------------------------------------------------------------------------
# The export (Q7b): the field decision now, the nested name held
# --------------------------------------------------------------------------


class ExportFieldDecisionTests(SimpleTestCase):
    def test_the_four_fields_are_exported_and_the_rest_left_out(self):
        from students import data_export

        self.assertEqual(
            data_export.EXPORT_FIELDS["students.KeptUpload"],
            ("original_name", "size_bytes", "kept_until", "purged_at"),
        )
        self.assertEqual(
            set(data_export.EXPORT_OMITTED["students.KeptUpload"]),
            {
                "id",
                "processing_task",
                "storage_key",
                "content_type",
                "sha256",
                "relaunch",
                "created_at",
            },
        )

    def test_the_nested_name_is_switched_on_at_b1b_8(self):
        from students import data_export

        self.assertIs(
            getattr(data_export, "KEPT_FILE_EXPORT_NAME_APPROVED", None), False
        )

    def test_the_nested_name_is_the_owner_approved_text_verbatim(self):
        # Approved text #18 (owner, 2026-11-10), team/approved_wording_b1b_
        # 2026-11-09.md at sha256 2f6e550e...ef6b67: `kept_file`.
        from students import data_export

        self.assertEqual(data_export.EXPORT_NAMES["kept_file"], "kept_file")


class ExportContentTests(TestCase):
    def setUp(self):
        _, self.student, _, assignment = _classroom("kept-export", RICH)
        self.kept_item = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
            file_name="kept.png",
        )
        self.row = keep(self.kept_item)
        self.plain_item = BackgroundProcessingTask.objects.create(
            requested_by=self.student,
            task_type=BackgroundTaskType.ANSWER_EXTRACTION,
            assignment=assignment,
            file_name="plain.png",
        )

    def uploads(self):
        from students.data_export import EXPORT_NAMES, Scope, build_export

        content, _ = build_export(self.student, Scope("all"))
        archive = zipfile.ZipFile(io.BytesIO(content))
        text = {name: archive.read(name).decode() for name in archive.namelist()}
        rows = json.loads(text[EXPORT_NAMES["students.BackgroundProcessingTask"]])
        return {row["file_name"]: row for row in rows}, "".join(text.values())

    def test_while_not_approved_no_row_has_the_nested_key(self):
        rows, _ = self.uploads()
        self.assertEqual(sorted(rows), ["kept.png", "plain.png"])
        for row in rows.values():
            self.assertNotIn("kept_file", row)

    def test_once_approved_a_kept_row_has_exactly_the_four_keys(self):
        with patch("students.data_export.KEPT_FILE_EXPORT_NAME_APPROVED", True):
            rows, everything = self.uploads()
        kept = rows["kept.png"]["kept_file"]
        self.assertEqual(
            sorted(kept), ["kept_until", "original_name", "purged_at", "size_bytes"]
        )
        self.assertEqual(kept["original_name"], "kept.png")
        self.assertEqual(kept["size_bytes"], len(PNG))
        self.assertIsNone(kept["purged_at"])
        self.assertIsNone(rows["plain.png"]["kept_file"])
        self.assertNotIn(self.row.storage_key, everything)
        self.assertNotIn(self.row.sha256, everything)


# --------------------------------------------------------------------------
# The table
# --------------------------------------------------------------------------


class TheTableTests(SimpleTestCase):
    def test_the_purge_index_covers_only_unpurged_rows(self):
        (index,) = KeptUpload()._meta.indexes
        self.assertEqual(index.name, "keptupload_due")
        self.assertEqual(index.fields, ["kept_until"])
        self.assertEqual(index.condition, Q(purged_at__isnull=True))

    def test_one_file_per_item(self):
        field = KeptUpload()._meta.get_field("processing_task")
        self.assertTrue(field.one_to_one)
        self.assertEqual(field.remote_field.on_delete.__name__, "CASCADE")
