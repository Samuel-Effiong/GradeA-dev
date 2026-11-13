"""Epic B1a step 3c amendment: door launches go out on commit (plan 08 M5,
§3.7; SM rulings 2026-11-06, NOTES §15g).

Flag on, the door pre-assigns the Celery id on the row, returns it as the
response's task_id, and sends the message only in transaction.on_commit.
A broker failure on that dispatch returns the file's hold.
"""

from unittest.mock import MagicMock, patch

from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from AutoGrader.dispatch import ProcessingTemporarilyUnavailable
from billing.models import AIJob
from billing.testing.holds import estimate_for, fresh, holds, make_item, make_job
from billing.tests.test_holds_door import (
    DOOR_ESTIMATE,
    fixed_estimate,
    png_file,
    wallet_of,
)
from billing.tests.test_holds_entry import classroom
from students.models import BackgroundProcessingTask
from students.task_tracking import launch_processing_task

STUDENT_LAUNCH = "students.views.launch_processing_task"
ASSIGNMENT_LAUNCH = "assignments.views.launch_processing_task"


def preassigned(launch_mock, call=0):
    return launch_mock.call_args_list[call].kwargs["_celery_task_id"]


@override_settings(BILLING_HOLDS_ENABLED=True)
class DoorLaunchOnCommitTests(APITestCase):
    def student_upload(self, student, assignment):
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        return self.client.post(url, {"answer": png_file()}, format="multipart")

    def test_c1_no_message_before_commit_then_exactly_one(self):
        teacher, student, _, assignment, submission = classroom(
            "c1", 10 * estimate_for()
        )
        submission.delete()
        with fixed_estimate(), patch(STUDENT_LAUNCH) as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.student_upload(student, assignment)
            self.assertEqual(response.status_code, 200)
            launch.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
        launch.assert_called_once()

    def test_c2_the_response_task_id_is_the_preassigned_id_on_the_row(self):
        teacher, student, _, assignment, submission = classroom(
            "c2", 10 * estimate_for()
        )
        submission.delete()
        with fixed_estimate(), patch(STUDENT_LAUNCH) as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.student_upload(student, assignment)
        celery_id = preassigned(launch)
        self.assertEqual(response.data["task_id"], celery_id)
        row = BackgroundProcessingTask.objects.get()
        self.assertEqual(row.celery_task_id, celery_id)
        self.assertEqual(launch.call_args.args[1].pk, row.pk)

    def test_c3_each_batch_file_launches_on_commit_with_its_own_id(self):
        teacher, _, _, assignment, _ = classroom("c3", 10 * DOOR_ESTIMATE)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), patch(STUDENT_LAUNCH) as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(url, {"answers": files}, format="multipart")
            self.assertEqual(response.status_code, 202)
            launch.assert_not_called()
            self.assertEqual(len(callbacks), 2)
            for callback in callbacks:
                callback()
        self.assertEqual(launch.call_count, 2)
        by_name = {t["file_name"]: t["task_id"] for t in response.data["tasks"]}
        for call in launch.call_args_list:
            name = call.kwargs["file_name"]
            self.assertEqual(call.kwargs["_celery_task_id"], by_name[name])
            row = call.args[1]
            self.assertEqual(row.file_name, name)
            self.assertEqual(fresh(row).celery_task_id, by_name[name])
        self.assertNotEqual(*by_name.values())

    def test_c4_an_assignment_file_launches_on_commit_with_the_preassigned_id(self):
        teacher, _, course, _, _ = classroom("c4", 10 * estimate_for())
        self.client.force_authenticate(user=teacher)
        with fixed_estimate(), patch(ASSIGNMENT_LAUNCH) as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.client.post(
                    reverse("assignment-upload-async"),
                    {"course": str(course.id), "assignments": [png_file("paper.png")]},
                    format="multipart",
                )
            launch.assert_not_called()
            self.assertEqual(len(callbacks), 1)
            callbacks[0]()
        self.assertEqual(response.data["tasks"][0]["task_id"], preassigned(launch))

    def test_c5_a_refused_hold_queues_no_callback(self):
        teacher, student, _, assignment, submission = classroom("c5", 6_000)
        submission.delete()
        holds().reserve(wallet_of(teacher), 2_000, make_job(wallet_of(teacher)))
        with fixed_estimate(), patch(STUDENT_LAUNCH) as launch:
            with self.captureOnCommitCallbacks(execute=False) as callbacks:
                response = self.student_upload(student, assignment)
        self.assertEqual(response.status_code, 402)
        self.assertEqual(callbacks, [])
        launch.assert_not_called()


class LaunchProcessingTaskIdTests(TestCase):
    def row(self, tag):
        teacher, _, _, _, _ = classroom(tag, 10 * estimate_for())
        job = make_job(wallet_of(teacher), "UPLOAD", total_items=1)
        return teacher, job, make_item(job, file_name="f.png")

    def test_c6_a_preassigned_id_goes_to_apply_async_not_the_task_kwargs(self):
        _, _, row = self.row("c6")
        task = MagicMock()
        launch_processing_task(task, row, "a", x=1, _celery_task_id="fixed-id")
        task.delay.assert_not_called()
        task.apply_async.assert_called_once_with(
            args=("a",),
            kwargs={"x": 1, "processing_task_id": str(row.id)},
            task_id="fixed-id",
        )

    def test_c7_without_it_the_dispatch_is_todays_delay(self):
        _, _, row = self.row("c7")
        task = MagicMock()
        launch_processing_task(task, row, "a", x=1)
        task.apply_async.assert_not_called()
        task.delay.assert_called_once_with("a", x=1, processing_task_id=str(row.id))

    def test_c8_a_broker_failure_returns_the_files_hold(self):
        teacher, job, row = self.row("c8")
        holds().reserve(wallet_of(teacher), DOOR_ESTIMATE, job, item=row)
        task = MagicMock()
        task.delay.side_effect = ConnectionError("broker down")
        task.apply_async.side_effect = ConnectionError("broker down")
        with self.assertRaises(ProcessingTemporarilyUnavailable):
            launch_processing_task(task, row, _celery_task_id="fixed-id")
        row = fresh(row)
        self.assertEqual((row.status, row.hold_state), ("FAILURE", "RETURNED"))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
        self.assertEqual(fresh(job).released_credits, DOOR_ESTIMATE)
        self.assertTrue(AIJob.objects.filter(pk=job.pk).exists())


BROKER_TEXT = "redis://10.0.0.9:6379"


@override_settings(BILLING_HOLDS_ENABLED=True)
class FailingCallbackUnderAnOuterAtomicTests(APITestCase):
    """§15g addendum: under an outer atomic (TestCase is one), a raising door
    callback is accounted for and logged by its guard, and the later
    callbacks still run."""

    def test_c9_the_first_file_fails_and_the_second_still_launches(self):
        teacher, _, _, assignment, _ = classroom("c9", 10 * DOOR_ESTIMATE)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        task = MagicMock()
        task.apply_async.side_effect = [ConnectionError(BROKER_TEXT), MagicMock()]
        files = [png_file("s1.png"), png_file("s2.png")]
        with fixed_estimate(), patch(
            "students.views.upload_answers_engine_async", task
        ), self.assertLogs(level="WARNING") as logs:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(task.apply_async.call_count, 2)
        first = BackgroundProcessingTask.objects.get(file_name="s1.png")
        second = BackgroundProcessingTask.objects.get(file_name="s2.png")
        self.assertEqual((first.status, first.hold_state), ("FAILURE", "RETURNED"))
        self.assertEqual(second.hold_state, "HELD")
        self.assertEqual(wallet_of(teacher).reserved_credits, DOOR_ESTIMATE)
        text = "\n".join(logs.output)
        self.assertIn("failed on commit", text)
        self.assertNotIn(BROKER_TEXT, text)

    def test_c11_an_early_non_broker_error_still_returns_the_hold(self):
        teacher, student, _, assignment, submission = classroom(
            "c11", 10 * estimate_for()
        )
        submission.delete()
        self.client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        with fixed_estimate(), patch(
            STUDENT_LAUNCH, side_effect=RuntimeError("before any accounting")
        ), self.assertLogs("assignments.upload_door", level="ERROR"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    url, {"answer": png_file()}, format="multipart"
                )
        self.assertEqual(response.status_code, 200)
        row = BackgroundProcessingTask.objects.get()
        self.assertEqual((row.status, row.hold_state), ("FAILURE", "RETURNED"))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)


@override_settings(BILLING_HOLDS_ENABLED=True)
class FailingLaunchInAutocommitTests(TransactionTestCase):
    """§15g addendum C-10: in autocommit the door launch runs at once and a
    broker failure still reaches the view (503), with the hold returned."""

    def test_c10_a_broker_failure_still_answers_503(self):
        teacher, student, _, assignment, submission = classroom(
            "c10", 10 * estimate_for()
        )
        submission.delete()
        client = APIClient()
        client.force_authenticate(user=student)
        url = reverse(
            "student-submission-upload-async", kwargs={"assignment_id": assignment.pk}
        )
        task = MagicMock()
        task.apply_async.side_effect = ConnectionError(BROKER_TEXT)
        with fixed_estimate(), patch(
            "students.views.upload_answers_engine_async", task
        ):
            response = client.post(url, {"answer": png_file()}, format="multipart")
        self.assertEqual(response.status_code, 503)
        row = BackgroundProcessingTask.objects.get()
        self.assertEqual((row.status, row.hold_state), ("FAILURE", "RETURNED"))
        self.assertEqual(wallet_of(teacher).reserved_credits, 0)
