"""
Epic A S7d: INSUFFICIENT_CREDITS_MID_BATCH's second approved wording (QA,
the founder, 2026-10-01). With nothing finished yet the item reads "Credits
ran out before any of the {total} items were finished.", not "after 0 of
N". students.task_tracking._mid_batch_error picks it: the one place both S7c
raise sites (the item that runs out, and the items stopped before any
provider call) build the error.
"""

from django.test import TestCase

from AutoGrader.error_messages import describe_background_task_error
from students import task_tracking
from students.models import BackgroundProcessingTask, BackgroundTaskStatus
from students.tests_credits_mid_batch import GradingBatchFixture

NONE_FINISHED = "Credits ran out before any of the 4 items were finished."
SOME_FINISHED = "Credits ran out after 1 of 4 items. The finished items are saved."


class MidBatchWordingTests(GradingBatchFixture, TestCase):
    # GradingBatchFixture is a mixin, not a TestCase: without TestCase these
    # tests were never collected (mutation batch 4 caught it, as V1/V2).
    def error(self):
        return task_tracking._mid_batch_error(self.session.id)

    def test_nothing_finished_reads_before_any(self):
        error = self.error()

        self.assertEqual(str(error), NONE_FINISHED)
        self.assertEqual(error.params, {"completed": 0, "total": 4})
        # The per-item text the session shows goes through the message layer.
        self.assertEqual(
            describe_background_task_error(error, fallback_message="x"),
            NONE_FINISHED,
        )

    def test_once_one_finished_it_reads_after_n_of_m(self):
        BackgroundProcessingTask.objects.filter(pk=self.items[0].pk).update(
            status=BackgroundTaskStatus.SUCCESS
        )

        error = self.error()

        self.assertEqual(str(error), SOME_FINISHED)
        self.assertEqual(error.params, {"completed": 1, "total": 4})
