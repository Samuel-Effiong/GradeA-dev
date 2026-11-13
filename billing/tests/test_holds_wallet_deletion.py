"""Epic B1a step 9, finding A (NOTES §21m, §21n and its addenda): with credit
holds on, deleting a teacher who has AI history must work (H-298).

AIJob.wallet was PROTECT, so every job, finished or not, stopped the wallet
(and with it the teacher) from being deleted. The fix: finished jobs survive
with wallet NULL; open holds are released first by a pre_delete receiver on
CreditWallet; work that later reaches a wallet-less job moves nothing, and is
refused with the existing late-message error before any provider call.

TransactionTestCase throughout (§21n addendum, C1): the delete really
commits, so a row written inside pre_delete that points at a deleted row
would fail here, not hide inside a test transaction.
"""

import importlib
from datetime import timedelta
from unittest.mock import patch

from django.apps import apps
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from ai_processor.services import AIProcessor
from audit.models import AuditEvent
from billing.errors import InsufficientCreditsError
from billing.models import AIJob, AIJobStatus, CreditWallet, HoldState
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    fresh,
    funded_wallet,
    holds,
    make_item,
    make_job,
)
from users.models import CustomUser, UserTypes

PROVIDER = "_AIProcessor__ai_model"
MIGRATION = "billing.migrations.0079_aijob_wallet_set_null"
REVERSE_GUARD = "refuse_reverse_with_walletless_jobs"
HELD = 300


def make_student(tag):
    return CustomUser.objects.create_user(
        email=f"student.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=UserTypes.STUDENT,
    )


def released_rows(job):
    return AuditEvent.objects.filter(action="CREDIT_HOLD_RELEASED", target_id=job.pk)


@override_settings(BILLING_HOLDS_ENABLED=True)
class DeletionBase(TransactionTestCase):
    def settled_teacher(self, tag):
        """A teacher with one real, settled SINGLE_CALL job."""
        teacher = ai_ready_teacher(f"d.{tag}@example.com", 3 * estimate_for())
        with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
            call_ai(teacher)
        self.assertTrue(
            AIJob.objects.filter(
                wallet__user=teacher, status=AIJobStatus.SETTLED
            ).exists()
        )
        return teacher

    def open_upload_hold(self, tag):
        """An open UPLOAD job billed to a teacher's wallet but requested by a
        student, with one item HELD; the job and item outlive the teacher."""
        wallet = funded_wallet(f"d.{tag}@example.com", 1000)
        job = make_job(wallet, "UPLOAD", requested_by=make_student(tag))
        item = make_item(job)
        self.assertTrue(holds().reserve(wallet, HELD, job, item=item))
        self.assertEqual(fresh(item).hold_state, HoldState.HELD)
        return wallet.user, job, item

    def assert_gone(self, teacher, wallet_pk):
        self.assertFalse(CustomUser.objects.filter(pk=teacher.pk).exists())
        self.assertFalse(CreditWallet.objects.filter(pk=wallet_pk).exists())


class FinishedJobsTests(DeletionBase):
    def test_d1a_a_teacher_with_finished_ai_jobs_is_deleted(self):
        teacher = self.settled_teacher("d1a")
        wallet_pk = teacher.credit_wallet.pk
        teacher.delete()
        self.assert_gone(teacher, wallet_pk)

    def test_d1b_a_queryset_delete_of_that_teacher_works_too(self):
        teacher = self.settled_teacher("d1b")
        wallet_pk = teacher.credit_wallet.pk
        CustomUser.objects.filter(pk=teacher.pk).delete()
        self.assert_gone(teacher, wallet_pk)

    def test_d2_a_job_another_user_requested_survives_with_no_wallet(self):
        wallet = funded_wallet("d.d2@example.com", 1000)
        job = make_job(
            wallet,
            requested_by=make_student("d2"),
            status=AIJobStatus.SETTLED,
            reserved_credits=500,
            hold_drawn=400,
            released_credits=100,
        )
        teacher = wallet.user
        teacher.delete()
        self.assert_gone(teacher, wallet.pk)
        row = fresh(job)
        self.assertIsNone(row.wallet_id)
        self.assertEqual(
            (row.status, row.reserved_credits, row.hold_drawn, row.released_credits),
            (AIJobStatus.SETTLED, 500, 400, 100),
        )


class OpenHoldTests(DeletionBase):
    def assert_released_first(self, job, item):
        # PD-1's kill: the literal first, so the fragment is "'CANCELLED' != ".
        self.assertEqual("CANCELLED", fresh(job).status)
        self.assertIsNone(fresh(job).wallet_id)
        row = fresh(item)
        self.assertEqual(
            (row.hold_state, row.released_credits), (HoldState.RETURNED, HELD)
        )
        # Read after the commit: emit swallows its own failures (H-299).
        self.assertEqual(released_rows(job).count(), 1)
        metadata = released_rows(job).get().metadata
        self.assertEqual(
            (metadata["credits"], metadata["reason_label"]), (HELD, "cancelled")
        )

    def test_d3a_an_open_hold_is_cancelled_and_returned_before_the_wallet_goes(self):
        teacher, job, item = self.open_upload_hold("d3a")
        wallet_pk = job.wallet_id
        teacher.delete()
        self.assert_gone(teacher, wallet_pk)
        self.assert_released_first(job, item)

    def test_d3b_the_same_through_a_queryset_delete(self):
        teacher, job, item = self.open_upload_hold("d3b")
        wallet_pk = job.wallet_id
        CustomUser.objects.filter(pk=teacher.pk).delete()
        self.assert_gone(teacher, wallet_pk)
        self.assert_released_first(job, item)


class WalletlessJobTests(DeletionBase):
    def test_d4a_work_on_a_job_whose_wallet_was_deleted_moves_nothing(self):
        teacher, job, item = self.open_upload_hold("d4a")
        stale = AIJob.objects.select_related("wallet").get(pk=job.pk)
        teacher.delete()
        job_now = fresh(job)
        module = holds()
        with self.assertLogs("billing.holds", "WARNING") as logs:
            # PD-2's kill: settle first, on the fresh row (wallet None).
            self.assertEqual(
                module.settle(job_now, 100, item=fresh(item)), module.NOT_SETTLED
            )
            # A worker that loaded the job before the delete.
            self.assertEqual(
                module.settle(stale, 100, item=fresh(item)), module.NOT_SETTLED
            )
            self.assertEqual(module.fail_item(job_now, item=fresh(item)), 0)
            self.assertIsNone(module.finish_job(job_now, AIJobStatus.FAILED))
            self.assertIsNone(
                module._sweep_item(item.pk, job.pk, timezone.now() + timedelta(days=1))
            )
        self.assertTrue(any(str(job.pk) in line for line in logs.output), logs.output)
        self.assertEqual(fresh(job).status, AIJobStatus.CANCELLED)
        row = fresh(item)
        self.assertEqual(
            (row.hold_state, row.released_credits), (HoldState.RETURNED, HELD)
        )

    def test_d4b_a_call_or_successor_on_it_is_refused_with_no_refill_text(self):
        teacher, job, item = self.open_upload_hold("d4b")
        teacher.delete()
        job_now = fresh(job)
        module = holds()
        jobs_before = AIJob.objects.count()
        paths = (
            (
                "ensure_call_fits",
                lambda: module.ensure_call_fits(job_now, 50, item=fresh(item)),
            ),
            ("late door", lambda: module.open_late_door_job(job_now, fresh(item), 0)),
            ("retry", lambda: module.open_retry_job(job_now, fresh(item), 0)),
        )
        for name, call in paths:
            with self.subTest(path=name):
                with self.assertRaises(module.CreditHoldReturnedError) as caught:
                    call()
                self.assertNotIsInstance(caught.exception, InsufficientCreditsError)
                self.assertNotIn("refill", str(caught.exception).lower())
        self.assertEqual(AIJob.objects.count(), jobs_before)


class ReverseGuardTests(DeletionBase):
    def guard(self):
        return getattr(importlib.import_module(MIGRATION), REVERSE_GUARD)

    def test_d5a_the_0079_reverse_is_refused_while_a_job_has_no_wallet(self):
        guard = self.guard()
        wallet = funded_wallet("d.d5a@example.com", 0)
        job = make_job(wallet, status=AIJobStatus.SETTLED)
        AIJob.objects.filter(pk=job.pk).update(wallet=None)
        with self.assertRaisesMessage(RuntimeError, "Refusing to reverse billing 0079"):
            guard(apps, None)

    def test_d5b_the_0079_reverse_is_allowed_when_every_job_has_a_wallet(self):
        guard = self.guard()
        make_job(funded_wallet("d.d5b@example.com", 0), status=AIJobStatus.SETTLED)
        self.assertEqual(AIJob.objects.filter(wallet__isnull=True).count(), 0)
        guard(apps, None)
