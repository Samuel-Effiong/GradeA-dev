"""N4 (owner-approved wording 2026-11-19; SM ruling (a)): dismissing an item
that finished successfully is 409 NOT_RETRYABLE (status and code kept, no new
code) with the owner's message, "This item finished successfully, so there's
nothing to dismiss.", given through the code's {why}. It carries no
remediation (null): the message says it all, and no new text is added.
NOT_RETRYABLE's remediation is unchanged for every other refusal, the retry
of the same item included."""

import pickle

from rest_framework.test import APITestCase

from AutoGrader import reason_codes
from AutoGrader.reason_codes import coded_body
from billing.tests.test_holds_entry import classroom
from students.item_retry import ItemNotRetryable
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
    BatchUploadSession,
    BatchUploadType,
)

NOTHING_TO_DISMISS = "This item finished successfully, so there's nothing to dismiss."
GENERAL_WHY = "This item can't be retried as it is."
GENERAL_REMEDIATION = "Fix what its failure message describes first, then try again."


class DismissingASucceededItem(APITestCase):
    def setUp(self):
        self.teacher, _, _, self.assignment, _ = classroom("n4", 10_000)
        self.client.force_authenticate(user=self.teacher)
        self.session = BatchUploadSession.objects.create(
            teacher=self.teacher,
            assignment=self.assignment,
            task_type=BatchUploadType.SUBMISSION,
            total_files=2,
        )

    def item(self, status, index=1):
        return BackgroundProcessingTask.objects.create(
            requested_by=self.teacher,
            task_type=BackgroundTaskType.BATCH_ANSWER_UPLOAD,
            batch_session=self.session,
            assignment=self.assignment,
            file_name=f"f{index}.png",
            item_index=index,
            status=status,
        )

    def post(self, item, action):
        return self.client.post(
            f"/api/v1/tasks/session/{self.session.pk}/items/{item.pk}/{action}"
        )

    def body(self, response):
        return response.json()["error"]["field_errors"]

    def test_dismiss_success_says_nothing_to_dismiss_with_no_remediation(self):
        done = self.item(BackgroundTaskStatus.SUCCESS)
        response = self.post(done, "dismiss")
        self.assertEqual(response.status_code, 409)
        body = self.body(response)
        self.assertEqual(body["error"], NOTHING_TO_DISMISS)
        self.assertEqual(response.json()["message"], NOTHING_TO_DISMISS)
        self.assertEqual(body["reason_code"], "NOT_RETRYABLE")
        self.assertIsNone(body["remediation"])
        done.refresh_from_db()
        self.assertIsNone(done.dismissed_at)

    def test_dismissing_a_running_item_keeps_the_general_refusal(self):
        running = self.item(BackgroundTaskStatus.STARTED)
        body = self.body(self.post(running, "dismiss"))
        self.assertEqual(body["error"], GENERAL_WHY)
        self.assertEqual(body["remediation"], GENERAL_REMEDIATION)

    def test_retrying_the_succeeded_item_keeps_the_general_refusal(self):
        done = self.item(BackgroundTaskStatus.SUCCESS)
        response = self.post(done, "retry")
        self.assertEqual(response.status_code, 409)
        body = self.body(response)
        self.assertNotEqual(body["error"], NOTHING_TO_DISMISS)
        self.assertEqual(body["remediation"], GENERAL_REMEDIATION)


class NoRemediationIsNull(APITestCase):
    def test_the_marker_renders_null_and_survives_a_pickle(self):
        NO_REMEDIATION = vars(reason_codes).get("NO_REMEDIATION", "unset")
        error = ItemNotRetryable(
            display={"why": NOTHING_TO_DISMISS}, remediation=NO_REMEDIATION
        )
        self.assertIsNone(error.remediation)
        again = pickle.loads(pickle.dumps(error))
        self.assertIsNone(again.remediation)
        self.assertEqual(str(again), NOTHING_TO_DISMISS)
        body = coded_body("NOT_RETRYABLE", {}, str(error), NO_REMEDIATION)
        self.assertIsNone(body["remediation"])
        # Without the marker the catalogue's remediation still applies.
        self.assertEqual(
            coded_body("NOT_RETRYABLE", {}, GENERAL_WHY)["remediation"],
            GENERAL_REMEDIATION,
        )
