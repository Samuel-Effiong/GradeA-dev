"""
billing/tests/test_append_only_audit_tables.py
==============================================
Locks the append-only guarantee on the two financial audit tables,
`CreditLedger` and `CreditUsageLog`.

WHAT WAS WRONG
--------------
Both models' docstrings called themselves an immutable audit trail.
Nothing enforced it:

  * `CreditLedger.user` was a `CASCADE` FK to `CustomUser`. Deleting one
    production teacher took 17,761 ledger rows with them - measured with
    Django's own deletion collector against production, read-only.
  * `CreditUsageLog` hung off `wallet` (CASCADE), so it died the same
    way, one level deeper.
  * `CreditLedger.bucket` was `SET_NULL`, which does not delete the row
    but does UPDATE it - a silent edit to an "immutable" record.
  * `CreditLedgerViewSet` was a full `ModelViewSet` exposing POST,
    PATCH and DELETE over HTTP to superadmins.
  * Nothing stopped `.update()` / `.delete()` from any code path.

THE LOAD-BEARING TEST
---------------------
`test_deleting_user_preserves_ledger_rows`. Everything else here would
still pass if the guards were implemented as a `delete()` override,
because Django's deletion collector NEVER calls `Model.delete()` or
`QuerySet.delete()` for cascaded rows - it issues bulk SQL directly. The
cascade is the case a naive implementation misses, and the case that was
actually destroying data.

WHY `is_refunded` IS EXEMPT
---------------------------
`SubscriptionService.refund_credits` settles a usage log by flipping
`is_refunded`, and reporting across dashboard/, classrooms/ and billing/
filters on it. It is deliberately mutable; the financial substance of
the row is not. `test_usage_log_is_refunded_remains_writable` and
`test_refund_flow_still_works_end_to_end` pin both halves of that.
"""

import uuid

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from billing.immutable import ImmutableRecordError, allow_unsafe_mutation
from billing.models import (
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditUsageLog,
    CreditWallet,
)
from users.models import UserTypes

CustomUser = get_user_model()


class AppendOnlyTestBase(TestCase):
    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="ledger-owner@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        self.bucket = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1_000,
            used_credits=0,
        )

    def make_ledger(self, **overrides):
        kwargs = {
            "user": self.user,
            "bucket": self.bucket,
            "ledger_type": CreditLedgerType.GRANT,
            "amount": 1_000,
            "reference": "test grant",
        }
        kwargs.update(overrides)
        return CreditLedger.record(**kwargs)

    def make_usage_log(self, **overrides):
        kwargs = {
            "wallet": self.wallet,
            "bucket": self.bucket,
            "amount": 50,
            "feature": "Grading Assignment",
        }
        kwargs.update(overrides)
        return CreditUsageLog.record(**kwargs)


class CreditLedgerAppendOnlyTests(AppendOnlyTestBase):
    def test_creation_still_works(self):
        row = self.make_ledger()
        self.assertIsNotNone(row.pk)
        self.assertEqual(CreditLedger.objects.count(), 1)

    def test_bulk_create_still_works(self):
        CreditLedger.objects.bulk_create(
            [
                CreditLedger.build(
                    user=self.user,
                    bucket=self.bucket,
                    ledger_type=CreditLedgerType.CONSUME,
                    amount=-10,
                )
                for _ in range(3)
            ]
        )
        self.assertEqual(CreditLedger.objects.count(), 3)

    def test_instance_delete_is_blocked(self):
        row = self.make_ledger()
        with self.assertRaises(ImmutableRecordError):
            row.delete()
        self.assertEqual(CreditLedger.objects.count(), 1)

    def test_queryset_delete_is_blocked(self):
        self.make_ledger()
        with self.assertRaises(ImmutableRecordError):
            CreditLedger.objects.all().delete()
        self.assertEqual(CreditLedger.objects.count(), 1)

    def test_queryset_update_is_blocked(self):
        self.make_ledger()
        with self.assertRaises(ImmutableRecordError):
            CreditLedger.objects.all().update(amount=999_999)
        self.assertEqual(CreditLedger.objects.first().amount, 1_000)

    def test_resaving_an_existing_row_is_blocked(self):
        row = self.make_ledger()
        row.amount = 999_999
        with self.assertRaises(ImmutableRecordError):
            row.save()
        row.refresh_from_db()
        self.assertEqual(row.amount, 1_000)

    def test_identity_is_captured_as_values(self):
        row = self.make_ledger()
        self.assertEqual(row.user_id, self.user.id)
        self.assertEqual(row.user_email, self.user.email)

    def test_deleting_user_preserves_ledger_rows(self):
        """
        THE regression test. A `delete()` override would not catch this:
        cascaded deletes bypass it entirely and go straight to bulk SQL.
        """
        self.make_ledger()
        user_id, user_email = self.user.id, self.user.email

        self.user.delete()

        self.assertEqual(CreditLedger.objects.count(), 1)
        row = CreditLedger.objects.first()
        self.assertEqual(row.user_id, user_id)
        self.assertEqual(row.user_email, user_email)

    def test_deleting_bucket_does_not_edit_or_remove_ledger_rows(self):
        """The old SET_NULL silently UPDATEd an 'immutable' row."""
        row = self.make_ledger()
        bucket_id = self.bucket.id

        self.bucket.delete()

        row.refresh_from_db()
        self.assertEqual(CreditLedger.objects.count(), 1)
        self.assertEqual(row.bucket_id, bucket_id)


class CreditUsageLogAppendOnlyTests(AppendOnlyTestBase):
    def test_creation_still_works(self):
        log = self.make_usage_log()
        self.assertIsNotNone(log.pk)

    def test_instance_delete_is_blocked(self):
        log = self.make_usage_log()
        with self.assertRaises(ImmutableRecordError):
            log.delete()
        self.assertEqual(CreditUsageLog.objects.count(), 1)

    def test_queryset_delete_is_blocked(self):
        self.make_usage_log()
        with self.assertRaises(ImmutableRecordError):
            CreditUsageLog.objects.all().delete()
        self.assertEqual(CreditUsageLog.objects.count(), 1)

    def test_financial_fields_are_frozen(self):
        self.make_usage_log()
        with self.assertRaises(ImmutableRecordError):
            CreditUsageLog.objects.all().update(amount=999_999)
        self.assertEqual(CreditUsageLog.objects.first().amount, 50)

    def test_usage_log_is_refunded_remains_writable(self):
        """
        Deliberate exemption: the refund flow settles a log by flipping
        this flag, and every reporting query filters on it.
        """
        log = self.make_usage_log()
        CreditUsageLog.objects.filter(pk=log.pk).update(is_refunded=True)
        log.refresh_from_db()
        self.assertTrue(log.is_refunded)

    def test_is_refunded_writable_via_save_with_update_fields(self):
        log = self.make_usage_log()
        log.is_refunded = True
        log.save(update_fields=["is_refunded"])
        log.refresh_from_db()
        self.assertTrue(log.is_refunded)

    def test_full_save_of_existing_row_is_still_blocked(self):
        """An unrestricted save() must not slip through the exemption."""
        log = self.make_usage_log()
        log.amount = 999_999
        with self.assertRaises(ImmutableRecordError):
            log.save()

    def test_identity_is_captured_as_values(self):
        log = self.make_usage_log()
        self.assertEqual(log.user_id, self.user.id)
        self.assertEqual(log.user_email, self.user.email)

    def test_deleting_user_preserves_usage_logs(self):
        self.make_usage_log()
        user_id = self.user.id

        self.user.delete()

        self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(CreditUsageLog.objects.first().user_id, user_id)

    def test_deleting_wallet_preserves_usage_logs(self):
        self.make_usage_log()
        wallet_id = self.wallet.id

        self.wallet.delete()

        self.assertEqual(CreditUsageLog.objects.count(), 1)
        self.assertEqual(CreditUsageLog.objects.first().wallet_id, wallet_id)


class EscapeHatchTests(AppendOnlyTestBase):
    def test_allow_unsafe_mutation_permits_and_then_restores(self):
        row = self.make_ledger()

        with allow_unsafe_mutation():
            CreditLedger.objects.filter(pk=row.pk).update(amount=7)

        row.refresh_from_db()
        self.assertEqual(row.amount, 7)

        # Guard is restored on exit.
        with self.assertRaises(ImmutableRecordError):
            CreditLedger.objects.filter(pk=row.pk).update(amount=8)

    def test_escape_hatch_restores_even_when_body_raises(self):
        with self.assertRaises(RuntimeError):
            with allow_unsafe_mutation():
                raise RuntimeError("boom")

        self.make_ledger()
        with self.assertRaises(ImmutableRecordError):
            CreditLedger.objects.all().delete()


class CreditLedgerEndpointIsReadOnlyTests(AppendOnlyTestBase):
    """
    The viewset was a ModelViewSet whose own docstring called the ledger
    immutable while exposing POST/PATCH/DELETE to superadmins.
    """

    def setUp(self):
        super().setUp()
        self.superadmin = CustomUser.objects.create_user(
            email="super@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.superadmin)
        self.row = self.make_ledger()

    def test_list_still_permitted(self):
        response = self.client.get(reverse("credit-ledger-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_create_is_rejected(self):
        response = self.client.post(
            reverse("credit-ledger-list"),
            {
                "user_id": str(uuid.uuid4()),
                "ledger_type": CreditLedgerType.GRANT,
                "amount": 5,
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_patch_is_rejected(self):
        response = self.client.patch(
            reverse("credit-ledger-detail", args=[self.row.pk]),
            {"amount": 5},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)

    def test_delete_is_rejected(self):
        response = self.client.delete(
            reverse("credit-ledger-detail", args=[self.row.pk])
        )
        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(CreditLedger.objects.count(), 1)
