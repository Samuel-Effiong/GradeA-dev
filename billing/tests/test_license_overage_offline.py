"""
Tests for the offline (off-app payment) license overage request flow:
LicenseSubscriptionService.request_overage_offline /
approve_overage_offline_request / reject_overage_offline_request.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingTransaction,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    LicenseBillingRecord,
    LicenseBillingRecordType,
    LicenseOverageOfflineRequest,
    LicenseOverageOfflineRequestStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes


class OfflineOverageRequestTestCase(TransactionTestCase):
    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="root@gradea.com",
            password="test123",  # pragma: allowlist secret
            first_name="Root",
            last_name="Admin",
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=299,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        self.allocation = LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )

    def _request(self, blocks=3):
        return LicenseSubscriptionService.request_overage_offline(
            self.license_sub,
            self.admin,
            total_blocks=blocks,
            allocations={str(self.teacher.id): blocks},
        )

    # -- request_overage_offline -----------------------------------------

    @patch("billing.license_service.send_email_task")
    def test_request_creates_pending_row_and_snapshots_pricing(self, mock_email):
        result = self._request(blocks=3)

        assert result["action"] == "offline_request_pending"
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])
        assert req.status == LicenseOverageOfflineRequestStatus.PENDING
        assert req.total_blocks == 3
        assert req.block_size_snapshot == 5000
        assert req.unit_price_cents_snapshot == 299
        assert req.amount_cents_quoted == 3 * 299
        assert req.allocations == {str(self.teacher.id): 3}

    @patch("billing.license_service.send_email_task")
    def test_request_queues_super_admin_email(self, mock_email):
        self._request(blocks=2)
        assert mock_email.delay.called
        recipients = [
            call.kwargs["recipient_list"][0] for call in mock_email.delay.call_args_list
        ]
        assert self.super_admin.email in recipients

    def test_request_rejects_inactive_license(self):
        self.license_sub.is_active = False
        self.license_sub.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValueError, "not active"):
            LicenseSubscriptionService.initiate_overage_purchase(
                self.license_sub,
                requesting_user=self.admin,
                total_blocks=1,
                allocations={str(self.teacher.id): 1},
                payment_method="offline_request",
            )

    # -- approve_overage_offline_request ----------------------------------

    @patch("billing.license_service.send_email_task")
    def test_approve_grants_credits_when_all_teachers_active(self, mock_email):
        result = self._request(blocks=3)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        approved = LicenseSubscriptionService.approve_overage_offline_request(
            req,
            performed_by=self.super_admin,
            amount_confirmed_cents=850,
            payment_reference="WIRE-1",
            payment_method_label="Wire transfer",
        )

        assert approved.status == LicenseOverageOfflineRequestStatus.APPROVED
        assert approved.amount_confirmed_cents == 850
        assert len(approved.fulfilled_allocations) == 1
        assert approved.skipped_allocations == []

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
        )
        assert bucket.total_credits == 3 * 5000

        ledger = CreditLedger.objects.get(user=self.teacher, bucket=bucket)
        assert ledger.metadata["request_id"] == str(req.id)
        assert ledger.metadata["approved_by"] == self.super_admin.email

        self.allocation.refresh_from_db()
        wallet = self.teacher.credit_wallet
        wallet.refresh_from_db()
        assert wallet.overage_blocks_used == 3

        billing_record = LicenseBillingRecord.objects.get(
            record_type=LicenseBillingRecordType.OFFLINE_OVERAGE_REQUEST_APPROVED
        )
        assert billing_record.amount_paid_cents == 850

        txn = BillingTransaction.objects.get(
            transaction_type=BillingTransactionType.LICENSE_OFFLINE_OVERAGE_PURCHASE
        )
        assert txn.amount_cents == 850

    @patch("billing.license_service.send_email_task")
    def test_approve_partial_fulfillment_when_teacher_removed(self, mock_email):
        result = self._request(blocks=3)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        LicenseSubscriptionService.remove_teacher_from_license(
            self.license_sub, self.teacher
        )

        approved = LicenseSubscriptionService.approve_overage_offline_request(
            req, performed_by=self.super_admin, amount_confirmed_cents=850
        )

        assert approved.status == LicenseOverageOfflineRequestStatus.APPROVED
        assert approved.fulfilled_allocations == []
        assert len(approved.skipped_allocations) == 1
        assert not CreditBucket.objects.filter(
            wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
        ).exists()

    @patch("billing.license_service.send_email_task")
    def test_approve_auto_rejects_whole_request_when_license_inactive(self, mock_email):
        result = self._request(blocks=3)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        self.license_sub.is_active = False
        self.license_sub.save(update_fields=["is_active"])

        approved = LicenseSubscriptionService.approve_overage_offline_request(
            req, performed_by=self.super_admin, amount_confirmed_cents=850
        )

        assert approved.status == LicenseOverageOfflineRequestStatus.REJECTED
        assert "no longer active" in approved.rejection_reason
        assert not CreditBucket.objects.filter(
            wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
        ).exists()

    @patch("billing.license_service.send_email_task")
    def test_approve_twice_raises(self, mock_email):
        result = self._request(blocks=1)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        LicenseSubscriptionService.approve_overage_offline_request(
            req, performed_by=self.super_admin, amount_confirmed_cents=299
        )
        req.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "already been reviewed"):
            LicenseSubscriptionService.approve_overage_offline_request(
                req, performed_by=self.super_admin, amount_confirmed_cents=299
            )

    # -- reject_overage_offline_request ------------------------------------

    @patch("billing.license_service.send_email_task")
    def test_reject_touches_no_credits(self, mock_email):
        result = self._request(blocks=2)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        rejected = LicenseSubscriptionService.reject_overage_offline_request(
            req, performed_by=self.super_admin, rejection_reason="No proof of payment."
        )

        assert rejected.status == LicenseOverageOfflineRequestStatus.REJECTED
        assert rejected.rejection_reason == "No proof of payment."
        assert not CreditBucket.objects.filter(
            wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
        ).exists()
        assert not LicenseBillingRecord.objects.filter(
            record_type=LicenseBillingRecordType.OFFLINE_OVERAGE_REQUEST_APPROVED
        ).exists()

    @patch("billing.license_service.send_email_task")
    def test_reject_then_approve_raises(self, mock_email):
        result = self._request(blocks=1)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        LicenseSubscriptionService.reject_overage_offline_request(
            req, performed_by=self.super_admin, rejection_reason="No."
        )
        req.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "already been reviewed"):
            LicenseSubscriptionService.approve_overage_offline_request(
                req, performed_by=self.super_admin, amount_confirmed_cents=299
            )

    @patch("billing.license_service.send_email_task")
    def test_approve_then_reject_raises(self, mock_email):
        result = self._request(blocks=1)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

        LicenseSubscriptionService.approve_overage_offline_request(
            req, performed_by=self.super_admin, amount_confirmed_cents=299
        )
        req.refresh_from_db()

        with self.assertRaisesMessage(ValueError, "already been reviewed"):
            LicenseSubscriptionService.reject_overage_offline_request(
                req, performed_by=self.super_admin, rejection_reason="Too late."
            )

    # -- shared _grant_overage_blocks helper (regression guard on refactor) -

    @patch("billing.license_service.send_email_task")
    def test_grant_overage_blocks_used_by_superadmin_and_offline_paths_differ_in_metadata(
        self, mock_email
    ):
        # Superadmin immediate-grant path
        LicenseSubscriptionService.initiate_overage_purchase(
            self.license_sub,
            requesting_user=self.super_admin,
            total_blocks=1,
            allocations={str(self.teacher.id): 1},
        )
        superadmin_ledger = CreditLedger.objects.get(
            user=self.teacher, metadata__purchase_channel="SUPERADMIN_OFFLINE"
        )
        assert superadmin_ledger.metadata["manual"] is True

        # Offline-request-approval path
        result = self._request(blocks=1)
        req = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])
        LicenseSubscriptionService.approve_overage_offline_request(
            req, performed_by=self.super_admin, amount_confirmed_cents=299
        )
        offline_ledger = CreditLedger.objects.get(
            user=self.teacher, metadata__request_id=str(req.id)
        )
        assert "purchase_channel" not in offline_ledger.metadata
        assert offline_ledger.reference != superadmin_ledger.reference


class OfflineOverageRequestPermissionTests(APITestCase):
    """
    LicenseOverageOfflineRequestViewSet (list/approve/reject) is
    superadmin-only — a school admin may CREATE a request (via the
    existing purchase_overage action, covered by
    IsSchoolAdminOrSuperAdmin elsewhere) but must never see the review
    queue or action other schools' requests.
    """

    def setUp(self):
        self.school = School.objects.create(name="Test School")
        self.admin = CustomUser.objects.create_user(
            email="admin@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Admin",
            last_name="User",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="root@gradea.com",
            password="test123",  # pragma: allowlist secret
            first_name="Root",
            last_name="Admin",
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Test License Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
            overage_block_price=299,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="teacher@school.edu",
            password="test123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="User",
            user_type=UserTypes.TEACHER,
            school=self.school,
        )
        LicenseSubscriptionService.add_teacher_to_license(
            self.license_sub, self.teacher.email
        )
        with patch("billing.license_service.send_email_task"):
            result = LicenseSubscriptionService.request_overage_offline(
                self.license_sub,
                self.admin,
                total_blocks=1,
                allocations={str(self.teacher.id): 1},
            )
        self.request_id = result["request_id"]
        self.list_url = reverse("license-overage-offline-request-list")
        self.approve_url = reverse(
            "license-overage-offline-request-approve", kwargs={"pk": self.request_id}
        )
        self.reject_url = reverse(
            "license-overage-offline-request-reject", kwargs={"pk": self.request_id}
        )

    def test_school_admin_cannot_list(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_admin_cannot_approve(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            self.approve_url, {"amount_confirmed_cents": 299}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_school_admin_cannot_reject(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            self.reject_url, {"rejection_reason": "No."}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_super_admin_can_list(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.get(self.list_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["results"]), 1)
        row = response.data["results"][0]
        self.assertEqual(row["teacher_breakdown"][0]["is_currently_active"], True)

    def test_super_admin_can_approve(self):
        self.client.force_authenticate(user=self.super_admin)
        response = self.client.post(
            self.approve_url, {"amount_confirmed_cents": 299}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "APPROVED")
