"""Epic B1b step 1, FB-1 (08b §5.1; NOTES §1): deleting a task row returns
its credit hold and keeps the books true.

Before the fix, deleting a BackgroundProcessingTask while it was HELD left
its hold inside wallet.reserved_credits for good, and deleting any row of a
job that kept other rows made check_invariants report item_sum. The fix
(SM option A): a pre_delete receiver returns a HELD row's hold the way
fail_item does, adds the row's numbers to the job's deleted_items_* columns,
and finishes the job when no live row is left to run (SM ruling C4: a
SettlementRecord means SETTLED, none means FAILED, never QUEUED).

TransactionTestCase, flag on (as test_holds_wallet_deletion): every delete
really commits.
"""

from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.test import TransactionTestCase, override_settings

from assignments.models import Assignment, AssignmentStatus
from audit.models import AuditEvent
from billing.models import AIJob, AIJobStatus, HoldState, SettlementRecord
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import (
    BackgroundProcessingTask,
    BatchUploadSession,
    BatchUploadType,
    StudentSubmission,
)
from users.models import CustomUser, UserTypes

HELD = 300
ACTUAL = 120


def make_student(tag):
    return CustomUser.objects.create_user(
        email=f"fb1.student.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.STUDENT,
    )


def released_rows(job):
    return AuditEvent.objects.filter(action="CREDIT_HOLD_RELEASED", target_id=job.pk)


def release_rows_summary(job):
    """Every release row of `job` as (item_id or None, credits)."""
    return sorted(
        (row.metadata.get("item_id"), row.metadata["credits"])
        for row in released_rows(job)
    )


def deleted_items(job):
    row = fresh(job)
    return (
        row.deleted_items_held,
        row.deleted_items_drawn,
        row.deleted_items_released,
    )


@override_settings(BILLING_HOLDS_ENABLED=True)
class DeletedItemsBase(TransactionTestCase):
    def classroom(self, tag):
        self.wallet = funded_wallet(f"fb1.{tag}@example.com", 100_000)
        self.teacher = self.wallet.user
        session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[],
        )

    def enrolled_student(self, tag):
        student = make_student(tag)
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        submission = StudentSubmission.objects.create(
            assignment=self.assignment, student=student, answers={}
        )
        return student, submission

    def job(self, job_type="UPLOAD", total_items=3):
        # RESERVED, as open_door_job leaves a real door job (holds.py:1058).
        return make_job(
            self.wallet,
            job_type,
            total_items=total_items,
            status=AIJobStatus.RESERVED,
        )

    def held_item(self, job, **over):
        item = make_item(job, assignment=self.assignment, **over)
        self.assertTrue(holds().reserve(self.wallet, HELD, job, item=item))
        self.assertEqual(fresh(item).hold_state, HoldState.HELD)
        return item

    def settled_item(self, job, **over):
        item = self.held_item(job, **over)
        outcome = holds().settle(job, ACTUAL, item=item)
        self.assertTrue(outcome.settled)
        return item

    def reserved(self):
        return fresh(self.wallet).reserved_credits

    def assert_books_agree(self):
        self.assertEqual(holds().check_invariants(self.wallet), [])


class DeleteRoadTests(DeletedItemsBase):
    """08b §5.1: one test per delete road. The deleted row was HELD in an
    UPLOAD job that keeps a HELD sibling, so the job stays open."""

    def open_job_with_two_held(self, tag, first=None, second=None):
        self.classroom(tag)
        job = self.job()
        start = self.reserved()
        doomed = self.held_item(job, **(first or {}))
        kept = self.held_item(job, **(second or {}))
        return job, start, doomed, kept

    def assert_road(self, job, start, doomed_pk):
        self.assertFalse(BackgroundProcessingTask.objects.filter(pk=doomed_pk).exists())
        self.assertEqual(
            self.reserved(), start + HELD, "reserved: only the kept row's hold is left"
        )
        self.assert_books_agree()
        row = fresh(job)
        self.assertEqual(row.status, AIJobStatus.RESERVED)
        self.assertEqual(row.failed_items, 1)
        self.assertEqual(deleted_items(job), (HELD, 0, HELD))
        rows = list(released_rows(job))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].metadata["reason_label"], "failed")
        self.assertEqual(rows[0].metadata["item_id"], str(doomed_pk))
        self.assertEqual(rows[0].metadata["credits"], HELD)

    def test_t1_instance_delete(self):
        job, start, doomed, _ = self.open_job_with_two_held("t1")
        doomed_pk = doomed.pk  # Model.delete() sets the instance's pk to None
        doomed.delete()
        self.assert_road(job, start, doomed_pk)

    def test_t2_queryset_delete(self):
        job, start, doomed, _ = self.open_job_with_two_held("t2")
        BackgroundProcessingTask.objects.filter(pk=doomed.pk).delete()
        self.assert_road(job, start, doomed.pk)

    def erasure_setup(self, tag):
        self.classroom(tag)
        erased, erased_sub = self.enrolled_student(f"{tag}.erased")
        _, other_sub = self.enrolled_student(f"{tag}.other")
        job = self.job()
        start = self.reserved()
        doomed = self.held_item(job, submission=erased_sub)
        self.held_item(job, submission=other_sub)
        return erased, job, start, doomed

    def test_t3_full_erasure(self):
        from students.data_erase import EraseScope, erase_student

        erased, job, start, doomed = self.erasure_setup("t3")
        erase_student(erased.pk, EraseScope("full"), by=self.teacher)
        self.assertFalse(CustomUser.objects.filter(pk=erased.pk).exists())
        self.assert_road(job, start, doomed.pk)

    def test_t4_course_scoped_erasure(self):
        from students.data_erase import EraseScope, erase_student

        erased, job, start, doomed = self.erasure_setup("t4")
        erase_student(
            erased.pk,
            EraseScope("courses", frozenset({self.course.pk})),
            by=self.teacher,
        )
        self.assertTrue(CustomUser.objects.filter(pk=erased.pk).exists())
        self.assert_road(job, start, doomed.pk)

    def test_t5_assignment_cascade(self):
        """Every row of the job goes: the job finishes SETTLED (C4), because
        one row settled."""
        self.classroom("t5")
        job = self.job(total_items=2)
        start = self.reserved()
        settled = self.settled_item(job)
        doomed = self.held_item(job)
        self.assignment.delete()
        self.assertFalse(
            BackgroundProcessingTask.objects.filter(
                pk__in=[settled.pk, doomed.pk]
            ).exists()
        )
        self.assertEqual(self.reserved(), start, "reserved: back to the start")
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.SETTLED)
        self.assertEqual(deleted_items(job), (2 * HELD, ACTUAL, (HELD - ACTUAL) + HELD))
        # Exactly one release row, and it names the held row (SM, §1.10).
        self.assertEqual(release_rows_summary(job), [(str(doomed.pk), HELD)])

    def test_t6_batch_session_cascade(self):
        """Every row of the job goes and none settled: FAILED (C4)."""
        self.classroom("t6")
        batch = BatchUploadSession.objects.create(
            teacher=self.teacher,
            course=self.course,
            assignment=self.assignment,
            task_type=BatchUploadType.GRADE,
            total_files=2,
        )
        job = self.job(total_items=2)
        start = self.reserved()
        first = self.held_item(job, batch_session=batch)
        second = self.held_item(job, batch_session=batch)
        batch.delete()
        self.assertFalse(
            BackgroundProcessingTask.objects.filter(
                pk__in=[first.pk, second.pk]
            ).exists()
        )
        self.assertEqual(self.reserved(), start, "reserved: back to the start")
        self.assert_books_agree()
        row = fresh(job)
        self.assertEqual(row.status, AIJobStatus.FAILED)
        self.assertEqual(row.failed_items, 2)
        self.assertEqual(deleted_items(job), (2 * HELD, 0, 2 * HELD))


class TerminalRuleTests(DeletedItemsBase):
    """SM ruling C4: no live row left to run → SETTLED if the job has a
    SettlementRecord, FAILED if not; never QUEUED."""

    def test_t7_upload_with_a_settled_sibling_ends_settled(self):
        self.classroom("t7")
        job = self.job(total_items=2)
        start = self.reserved()
        self.settled_item(job)
        doomed = self.held_item(job)
        doomed.delete()
        self.assertEqual(self.reserved(), start)
        self.assert_books_agree()
        self.assertTrue(SettlementRecord.objects.filter(job=job).exists())
        self.assertEqual(fresh(job).status, AIJobStatus.SETTLED)

    def test_t8_upload_with_every_row_deleted_and_none_settled_ends_failed(self):
        self.classroom("t8")
        job = self.job(total_items=2)
        start = self.reserved()
        first = self.held_item(job)
        second = self.held_item(job)
        BackgroundProcessingTask.objects.filter(pk__in=[first.pk, second.pk]).delete()
        self.assertEqual(self.reserved(), start)
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.FAILED)

    def test_t8b_single_item_deleted_while_held_ends_failed(self):
        self.classroom("t8b")
        job = self.job("SINGLE_ITEM", total_items=1)
        start = self.reserved()
        item = self.held_item(job)
        item.delete()
        self.assertEqual(self.reserved(), start)
        self.assert_books_agree()
        row = fresh(job)
        self.assertEqual(row.status, AIJobStatus.FAILED)
        self.assertEqual(row.failed_items, 1)


class PreExistingFalseAlarmTests(DeletedItemsBase):
    def test_t9_a_settled_row_deleted_from_a_job_with_rows_left(self):
        """Before B1b this reported item_sum with the flag on, without any
        leak (NOTES §1.1). Nothing is returned; the numbers move to the
        job's deleted_items_* and the books agree."""
        self.classroom("t9")
        job = self.job(total_items=2)
        settled = self.settled_item(job)
        self.held_item(job)
        before = self.reserved()
        settled.delete()
        self.assertEqual(self.reserved(), before)
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.RESERVED)
        self.assertEqual(deleted_items(job), (HELD, ACTUAL, HELD - ACTUAL))
        self.assertEqual(released_rows(job).count(), 0)


class TwoReceiversTests(DeletedItemsBase):
    """SM ruling C3: on a teacher's deletion the wallet receiver (§21n) and
    this one both fire. Whichever runs second moves nothing."""

    def test_t10_wallet_receiver_first_then_the_row_delete(self):
        self.classroom("t10")
        job = self.job(total_items=1)
        start = self.reserved()
        item = self.held_item(job)
        holds().finish_job(job, AIJobStatus.CANCELLED)
        self.assertEqual(self.reserved(), start)
        item_pk = item.pk  # Model.delete() sets the instance's pk to None
        item.delete()
        self.assertEqual(self.reserved(), start)
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.CANCELLED)
        self.assertEqual(deleted_items(job), (HELD, 0, HELD))
        # finish_job's job-level row is the only return; nothing names the
        # item, so the receiver returned nothing again (C3; SM, §1.10).
        self.assertEqual(release_rows_summary(job), [(None, HELD)])
        self.assertNotIn(str(item_pk), [item for item, _ in release_rows_summary(job)])

    def test_t11_row_delete_first_then_the_wallet_receiver(self):
        self.classroom("t11")
        job = self.job(total_items=2)
        start = self.reserved()
        doomed = self.held_item(job)
        self.held_item(job)
        doomed_pk = doomed.pk  # Model.delete() sets the instance's pk to None
        doomed.delete()
        holds().finish_job(job, AIJobStatus.CANCELLED)
        self.assertEqual(self.reserved(), start)
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.CANCELLED)
        # The receiver's row names the item; finish_job's row is job-level
        # (no item_id) and returns the kept row's hold.
        item_rows = [
            row
            for row in released_rows(job)
            if row.metadata.get("item_id") == str(doomed_pk)
        ]
        self.assertEqual([row.metadata["credits"] for row in item_rows], [HELD])

    def test_t12_a_teacher_deletion_with_a_held_row_of_their_own(self):
        """End to end: the row goes through requested_by CASCADE, the wallet
        through the user. The job (requested by a student) survives with no
        wallet, its hold returned once and the row's numbers kept."""
        self.classroom("t12")
        student = make_student("t12")
        job = make_job(
            self.wallet,
            "UPLOAD",
            requested_by=student,
            total_items=1,
            status=AIJobStatus.RESERVED,  # as open_door_job leaves it
        )
        item = self.held_item(job, requested_by=self.teacher)
        self.teacher.delete()
        self.assertFalse(BackgroundProcessingTask.objects.filter(pk=item.pk).exists())
        row = fresh(job)
        self.assertIsNone(row.wallet_id)
        self.assertNotIn(row.status, holds().OPEN_STATUSES)
        self.assertEqual(deleted_items(job), (HELD, 0, HELD))
        # Once in total, whichever receiver the collector runs first.
        rows = release_rows_summary(job)
        self.assertEqual([credits for _, credits in rows], [HELD])


class FlagOffTests(DeletedItemsBase):
    def test_t13_a_row_held_while_the_flag_was_on_is_returned_with_it_off(self):
        self.classroom("t13")
        job = self.job(total_items=2)
        start = self.reserved()
        doomed = self.held_item(job)
        self.held_item(job)
        with override_settings(BILLING_HOLDS_ENABLED=False):
            doomed.delete()
        self.assertEqual(self.reserved(), start + HELD)
        self.assert_books_agree()


class MidRunDeletionTests(DeletedItemsBase):
    def test_t14_a_unit_whose_row_is_deleted_mid_run(self):
        """Characterization (SM, 2026-11-09): pinned as found, not designed.
        The receiver returns the hold; the unit's settle then looks the row
        up and finds it gone."""
        self.classroom("t14")
        job = self.job(total_items=2)
        start = self.reserved()
        doomed = self.held_item(job)
        self.held_item(job)
        with self.assertRaises(BackgroundProcessingTask.DoesNotExist):
            with holds().metered_unit(processing_task_id=doomed.pk):
                BackgroundProcessingTask.objects.filter(pk=doomed.pk).delete()
        self.assertEqual(self.reserved(), start + HELD)
        self.assertFalse(
            SettlementRecord.objects.filter(job=job, item_id=doomed.pk).exists()
        )
        self.assert_books_agree()


class SchemaTests(TransactionTestCase):
    def test_t15_the_three_columns_default_to_zero_and_refuse_negatives(self):
        wallet = funded_wallet("fb1.t15@example.com", 1000)
        job = make_job(wallet)
        self.assertEqual(deleted_items(job), (0, 0, 0))
        for column in (
            "deleted_items_held",
            "deleted_items_drawn",
            "deleted_items_released",
        ):
            with self.subTest(column=column):
                with self.assertRaises(IntegrityError):
                    with transaction.atomic():
                        AIJob.objects.filter(pk=job.pk).update(**{column: -1})


class StaleInstanceTests(DeletedItemsBase):
    def test_t17_a_stale_returned_instance_whose_row_was_held_again(self):
        """NOTES §1.15: the sweep returns a started row and its run's next
        call reserves on it again (holds.py:556-557 -> :242 -> :225). The
        collector's instance says RETURNED; the row is HELD."""
        self.classroom("t17")
        job = self.job()
        start = self.reserved()
        doomed = self.held_item(job)
        self.held_item(job)
        holds().fail_item(job, item=doomed)
        stale = fresh(doomed)
        self.assertEqual(stale.hold_state, HoldState.RETURNED)
        self.assertTrue(holds().reserve(self.wallet, HELD, job, item=doomed))
        self.assertEqual(fresh(doomed).hold_state, HoldState.HELD)
        stale.delete()
        self.assertEqual(
            self.reserved(), start + HELD, "reserved: only the kept row's hold is left"
        )
        self.assert_books_agree()
        self.assertEqual(deleted_items(job), (2 * HELD, 0, 2 * HELD))


class OwnTransactionTests(DeletedItemsBase):
    def test_t18_called_outside_a_transaction_it_never_half_applies(self):
        """SM condition (2): outside Collector.delete the receiver opens its
        own full atomic, so a failure after the wallet moved undoes it."""
        self.classroom("t18")
        job = self.job()
        start = self.reserved()
        doomed = self.held_item(job)
        self.held_item(job)
        module = holds()
        with patch.object(
            module, "_finish_if_nothing_left", side_effect=RuntimeError("boom")
        ):
            with self.assertRaises(RuntimeError):
                module.return_hold_on_item_delete(
                    sender=BackgroundProcessingTask, instance=fresh(doomed)
                )
        self.assertEqual(self.reserved(), start + 2 * HELD)
        self.assertEqual(fresh(doomed).hold_state, HoldState.HELD)
        self.assertEqual(deleted_items(job), (0, 0, 0))


class CascadeTerminalTests(DeletedItemsBase):
    """SM review of d81556a56: a cascade sends every pre_delete before any
    row is deleted, so the last row's siblings are still in the table."""

    def test_t19_an_assignment_cascade_with_a_held_and_a_returned_row(self):
        self.classroom("t19")
        job = self.job(total_items=2)
        start = self.reserved()
        self.held_item(job)
        returned = self.held_item(job)
        holds().fail_item(job, item=returned)
        self.assignment.delete()
        self.assertFalse(BackgroundProcessingTask.objects.filter(job=job).exists())
        self.assertEqual(self.reserved(), start, "reserved: back to the start")
        self.assert_books_agree()
        self.assertEqual(fresh(job).status, AIJobStatus.FAILED)
        self.assertEqual(deleted_items(job), (2 * HELD, 0, 2 * HELD))

    def test_t20_an_assignment_cascade_with_a_never_held_pending_row(self):
        """A row with a job but no hold that has not run (B1b-6's queued
        papers; today only a moment between attach and hold) is still in
        the table when the last pre_delete runs."""
        self.classroom("t20")
        job = self.job(total_items=2)
        start = self.reserved()
        self.held_item(job)
        pending = make_item(job, assignment=self.assignment)
        self.assertIsNone(fresh(pending).hold_state)
        self.assignment.delete()
        self.assertFalse(BackgroundProcessingTask.objects.filter(job=job).exists())
        self.assertEqual(self.reserved(), start, "reserved: back to the start")
        self.assert_books_agree()
        self.assertEqual(deleted_items(job), (HELD, 0, HELD))
        self.assertEqual(fresh(job).status, AIJobStatus.FAILED)
