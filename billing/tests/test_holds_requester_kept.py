"""Epic B1a, NOTES §21v R2: an AIJob is a credit record (owner D1/D4), so it
outlives the user who requested it.

AIJob.requested_by was CASCADE: erasing a student deleted every job the
student had started, including jobs billed to a teacher's wallet, and their
settlement records. Billing 0080 makes it SET_NULL; the student data map
lists it as KEEP_RECORD, like BillingTransaction.user.

TransactionTestCase, flag on, as in test_holds_wallet_deletion (§21n C1): the
erasure and the deletes really commit.
"""

import importlib

from django.apps import apps
from django.test import TransactionTestCase, override_settings

from billing.models import AIJob, AIJobStatus, SettlementRecord
from billing.testing.holds import fresh, funded_wallet, holds, make_item, make_job
from students.data_erase import EraseScope, erase_student
from students.models import BackgroundProcessingTask
from students.student_data_map import STUDENT_DATA_MAP, Role
from users.models import CustomUser, UserTypes

MIGRATION = "billing.migrations.0080_aijob_requested_by_set_null"
REVERSE_GUARD = "refuse_reverse_with_requesterless_jobs"
HELD = 300
CHARGED = 100


def make_user(tag, user_type):
    return CustomUser.objects.create_user(
        email=f"rk.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=user_type,
    )


@override_settings(BILLING_HOLDS_ENABLED=True)
class RequesterKeptTests(TransactionTestCase):
    def student_upload_settled(self, tag):
        """A student's upload, billed to a teacher's wallet, with its one
        item settled (one SettlementRecord)."""
        wallet = funded_wallet(f"rk.{tag}.teacher@example.com", 1000)
        student = make_user(f"{tag}.student", UserTypes.STUDENT)
        job = make_job(wallet, "UPLOAD", requested_by=student)
        item = make_item(job)
        module = holds()
        self.assertTrue(module.reserve(wallet, HELD, job, item=item))
        module.settle(job, CHARGED, item=fresh(item))
        self.assertEqual(SettlementRecord.objects.filter(job=job).count(), 1)
        return student, wallet, job, item

    def test_r1_a_students_full_erasure_keeps_the_job_and_its_settlements(self):
        student, wallet, job, item = self.student_upload_settled("r1")
        admin = make_user("r1.admin", UserTypes.SUPER_ADMIN)
        erase_student(student.pk, EraseScope("full"), by=admin)
        self.assertFalse(CustomUser.objects.filter(pk=student.pk).exists())
        # The student's own processing row is erased (DELETE role).
        self.assertFalse(BackgroundProcessingTask.objects.filter(pk=item.pk).exists())
        # PR-1's kill: the job read first, so the fragment is the DoesNotExist.
        row = AIJob.objects.get(pk=job.pk)
        self.assertIsNone(row.requested_by_id)
        self.assertEqual(row.wallet_id, wallet.pk)
        records = SettlementRecord.objects.filter(job=job)
        self.assertEqual(
            list(records.values_list("item_id", "credits_charged")),
            [(item.pk, CHARGED)],
        )

    def test_r2_a_deleted_teachers_own_job_survives_with_no_requester(self):
        # §21n said a teacher's own jobs CASCADE with the teacher; R2 changes it.
        wallet = funded_wallet("rk.r2@example.com", 0)
        job = make_job(wallet, status=AIJobStatus.SETTLED, reserved_credits=50)
        wallet.user.delete()
        row = AIJob.objects.get(pk=job.pk)
        self.assertEqual(
            (row.requested_by_id, row.wallet_id, row.status, row.reserved_credits),
            (None, None, AIJobStatus.SETTLED, 50),
        )

    def test_r3_the_map_keeps_the_requester_column(self):
        entry = STUDENT_DATA_MAP[("billing.AIJob", "requested_by")]
        self.assertEqual(entry.role, Role.KEEP_RECORD)
        self.assertFalse(entry.export)
        self.assertFalse(entry.course_scoped)


class ReverseGuardTests(TransactionTestCase):
    def guard(self):
        return getattr(importlib.import_module(MIGRATION), REVERSE_GUARD)

    def test_r4a_the_0080_reverse_is_refused_while_a_job_has_no_requester(self):
        guard = self.guard()
        job = make_job(funded_wallet("rk.r4a@example.com", 0), status="SETTLED")
        AIJob.objects.filter(pk=job.pk).update(requested_by=None)
        with self.assertRaisesMessage(RuntimeError, "Refusing to reverse billing 0080"):
            guard(apps, None)

    def test_r4b_the_0080_reverse_is_allowed_when_every_job_has_a_requester(self):
        guard = self.guard()
        make_job(funded_wallet("rk.r4b@example.com", 0), status="SETTLED")
        self.assertEqual(AIJob.objects.filter(requested_by__isnull=True).count(), 0)
        guard(apps, None)
