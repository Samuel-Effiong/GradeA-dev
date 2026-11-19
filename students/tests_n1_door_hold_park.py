"""N1 (E2E rerun 2026-11-19, owner: fix on beta): a file whose door hold
loses the race for the free balance is parked, and only that file.

Three batches arrive at once against a tight balance (S11a). Each request's
door check sees enough, then the holds race. Before: the last file of the
third request lost its hold while its own earlier files had already started,
so batch_credit_refusal read it as a mid-batch stop. It stamped the session
`credits_exhausted_at`, and ensure_batch_has_credits then stopped the earlier
files although each held its own credits.

Now the losing file gets a parked file's outcome: INSUFFICIENT_CREDITS, kept,
retryable, and retried by hand after a top-up. The session is not marked, and
the siblings run. A real refusal in the worker (the gate, after the batch
ran part-way) still stops the rest (BE-B-06).

The race is made deterministic, with no AI and no sleeps. Batches A and B hold
first. Batch C's door check sees plenty (door_check_passes, the check/hold
gap), and before each of C's holds its already-held files are marked STARTED,
as the worker picks up a file the moment it is dispatched.
"""

from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APITestCase

from billing import holds as credit_holds
from billing.errors import InsufficientCreditsError
from billing.models import CreditWallet
from billing.testing.holds import fund, holds
from billing.tests.test_holds_door import (
    DOOR_ESTIMATE,
    door_check_passes,
    fixed_estimate,
    png_file,
)
from billing.tests.test_holds_entry import classroom
from students import task_tracking
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BatchUploadSession,
    KeptUpload,
)

MID = "INSUFFICIENT_CREDITS_MID_BATCH"
PARKED_CODE = "INSUFFICIENT_CREDITS"
#: Room for 8 of the 9 files (3 batches of 3 at DOOR_ESTIMATE each).
BALANCE = 8 * DOOR_ESTIMATE


def wallet_of(user):
    return CreditWallet.objects.get(user=user)


def racing_hold():
    """The real hold, but first the session's already-held files are
    STARTED: the worker took each one as soon as it was dispatched."""
    real = credit_holds.hold_door_item

    def hold(job, row, estimate):
        BackgroundProcessingTask.objects.filter(
            batch_session_id=row.batch_session_id, hold_state="HELD"
        ).exclude(pk=row.pk).update(status=BackgroundTaskStatus.STARTED)
        return real(job, row, estimate)

    return patch("assignments.upload_door.credit_holds.hold_door_item", hold)


@override_settings(BILLING_HOLDS_ENABLED=True)
class ThreeBatchesRaceForATightBalance(APITestCase):
    def setUp(self):
        self.teacher, _, self.course, self.assignment, _ = classroom("n1", BALANCE)
        self.client.force_authenticate(user=self.teacher)

    def answers(self, tag):
        url = reverse(
            "student-submission-batch-upload",
            kwargs={"assignment_id": self.assignment.pk},
        )
        files = [png_file(f"{tag}{i}.png") for i in range(3)]
        with patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(url, {"answers": files}, format="multipart")
        self.assertEqual(response.status_code, 202, response.content)
        return response.json()["data"]

    def assignments(self, tag):
        files = [png_file(f"{tag}{i}.png") for i in range(3)]
        with patch("assignments.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    reverse("assignment-upload-async"),
                    {"course": str(self.course.pk), "assignments": files},
                    format="multipart",
                )
        self.assertEqual(response.status_code, 202, response.content)
        return response.json()["data"]

    def race(self, third):
        """A and B hold six files; C's door check passes, then its holds race."""
        with fixed_estimate():
            self.answers("a")
            self.answers("b")
            with door_check_passes(), racing_hold():
                return third("c")

    def check_only_the_loser_is_parked(self, data):
        session = BatchUploadSession.objects.get(pk=data["session_id"])
        statuses = [(t["status"], t["reason_code"]) for t in data["tasks"]]
        self.assertEqual(
            statuses,
            [("ACCEPTED", None), ("ACCEPTED", None), ("PARKED", PARKED_CODE)],
        )
        rows = list(
            BackgroundProcessingTask.objects.filter(batch_session=session).order_by(
                "item_index"
            )
        )
        loser, siblings = rows[2], rows[:2]

        # The loser: a parked file's outcome, kept and retryable.
        self.assertEqual(loser.status, BackgroundTaskStatus.FAILURE)
        self.assertEqual(loser.reason_code, PARKED_CODE)
        self.assertNotEqual(loser.reason_code, MID)
        self.assertEqual(loser.hold_credits or 0, 0)
        kept = KeptUpload.objects.get(processing_task=loser)
        self.assertIsNone(kept.purged_at)

        # Nothing marked exhausted; the siblings holding credits run.
        session.refresh_from_db()
        self.assertIsNone(session.credits_exhausted_at)
        for sibling in siblings:
            self.assertEqual(sibling.hold_state, "HELD")
            self.assertEqual(sibling.hold_credits, DOOR_ESTIMATE)
            task_tracking.ensure_batch_has_credits(sibling.id)  # does not raise

        # The wallet is exact: 8 files held, nothing more.
        wallet = wallet_of(self.teacher)
        self.assertEqual(wallet.reserved_credits, BALANCE)
        self.assertEqual(holds().check_invariants(wallet), [])

        # Each held file settles for less than it held; the rest comes back.
        held = BackgroundProcessingTask.objects.filter(
            requested_by=self.teacher, hold_state="HELD"
        )
        self.assertEqual(held.count(), 8)
        for row in held:
            holds().settle(row.job, 3_000, item=row)
        wallet = wallet_of(self.teacher)
        self.assertEqual(wallet.reserved_credits, 0)
        self.assertEqual(wallet.total_remaining_credits(), BALANCE - 8 * 3_000)
        self.assertEqual(holds().check_invariants(wallet), [])
        self.assertEqual(holds().check_invariants(), [])
        return session, loser

    def test_the_answer_file_that_loses_the_race_is_parked(self):
        self.check_only_the_loser_is_parked(self.race(self.answers))

    def test_the_assignment_file_that_loses_the_race_is_parked(self):
        self.check_only_the_loser_is_parked(self.race(self.assignments))

    def test_the_parked_loser_is_retryable_and_retried_after_a_top_up(self):
        session, loser = self.check_only_the_loser_is_parked(self.race(self.answers))
        results = self.client.get(f"/api/v1/tasks/session-results/{session.pk}")
        entry = next(
            e
            for e in results.json()["data"]["failure_list"]
            if e["item_id"] == str(loser.pk)
        )
        self.assertEqual(entry["reason_code"], PARKED_CODE)
        self.assertIs(entry["retryable"], True)

        fund(self.teacher, 10 * DOOR_ESTIMATE)  # the top-up
        with fixed_estimate(), patch(
            "students.item_retry.launch_processing_task"
        ) as launch:
            with self.captureOnCommitCallbacks(execute=True):
                response = self.client.post(
                    f"/api/v1/tasks/session/{session.pk}/items/{loser.pk}/retry"
                )
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(launch.call_count, 1)
        loser.refresh_from_db()
        self.assertEqual(loser.retry_count, 1)
        self.assertEqual(KeptUpload.objects.filter(processing_task=loser).count(), 1)


@override_settings(BILLING_HOLDS_ENABLED=True)
class ARealMidBatchStopStillStopsTheRest(APITestCase):
    """BE-B-06 kept: a credit refusal in the worker after the batch ran
    part-way is the mid-batch stop. The session is marked and the items still
    to run stop before any provider call."""

    def test_a_worker_refusal_after_a_sibling_started_marks_and_stops(self):
        teacher, _, _, assignment, _ = classroom("n1mid", BALANCE)
        self.client.force_authenticate(user=teacher)
        url = reverse(
            "student-submission-batch-upload", kwargs={"assignment_id": assignment.pk}
        )
        files = [png_file(f"m{i}.png") for i in range(3)]
        with fixed_estimate(), patch("students.views.launch_processing_task"):
            with self.captureOnCommitCallbacks(execute=True):
                data = self.client.post(
                    url, {"answers": files}, format="multipart"
                ).json()["data"]
        session = BatchUploadSession.objects.get(pk=data["session_id"])
        first, second, third = BackgroundProcessingTask.objects.filter(
            batch_session=session
        ).order_by("item_index")
        BackgroundProcessingTask.objects.filter(pk=first.pk).update(
            status=BackgroundTaskStatus.STARTED
        )

        task_tracking.mark_processing_task_failure(
            second.id, InsufficientCreditsError("gate: the call does not fit")
        )

        second.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual(second.reason_code, MID)
        self.assertIsNotNone(session.credits_exhausted_at)
        with self.assertRaises(InsufficientCreditsError):
            task_tracking.ensure_batch_has_credits(third.id)
