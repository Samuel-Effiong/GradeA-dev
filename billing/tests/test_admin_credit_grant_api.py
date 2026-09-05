"""
billing/tests/test_admin_credit_grant_api.py
============================================
The superadmin credit-minting API (`AdminCreditManagementViewSet`).

WHY THIS FILE EXISTS
--------------------
`billing/views_admin_credits.py` sat at 26.9% coverage and NOTHING in the
suite exercised it. It is the endpoint that mints credits — the thing
customers pay money for — into an arbitrary user's wallet, plus the
grant-history endpoints that expose every grant in the system.

The uncovered lines were not incidental error branches; they were the
`grant` action's whole body and all four history actions. So none of this
had a test:

  * that a teacher, a school admin, or an anonymous caller cannot mint
    credits into anybody's wallet;
  * that a grant actually lands, in the right amount, priced by the target
    user's own plan block size;
  * that the immutable ledger records WHO authorised it — the only
    accountability trail for a money-equivalent action;
  * that the grant history endpoints do not leak across users.

These are behaviour tests, not coverage filler: each asserts a value or an
access decision that would change if the code regressed.
"""

import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from users.models import UserTypes

CustomUser = get_user_model()

BLOCK_SIZE = 500


def grant_url():
    return reverse("admin-credits-grant")


class _AdminCreditBase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Standard",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=10_000,
            overage_block_size=BLOCK_SIZE,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        self.superadmin = self._user(
            "grant.root@admincredit.test",
            UserTypes.SUPER_ADMIN,
            is_staff=True,
            is_superuser=True,
        )
        self.teacher = self._subscribed_teacher("grant.teacher@admincredit.test")

    def _user(self, email, user_type, **extra):
        return CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=user_type,
            is_active=True,
            **extra,
        )

    def _subscribed_teacher(self, email):
        user = self._user(email, UserTypes.TEACHER)
        CreditWallet.objects.get_or_create(user=user)
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        return user

    def payload(self, **overrides):
        body = {
            "user_id": str(self.teacher.id),
            "blocks": 2,
            "reason": "goodwill credit after an outage",
        }
        body.update(overrides)
        return body


class AdminCreditGrantAccessControlTests(_AdminCreditBase):
    """Who is allowed to mint credits. This is the security boundary."""

    def test_a_teacher_cannot_mint_credits(self):
        self.client.force_authenticate(user=self.teacher)
        before = CreditBucket.objects.count()

        response = self.client.post(grant_url(), self.payload(), format="json")

        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(
            CreditBucket.objects.count(),
            before,
            "a non-superadmin minted credits",
        )

    def test_a_school_admin_cannot_mint_credits(self):
        school_admin = self._user(
            "grant.schooladmin@admincredit.test", UserTypes.SCHOOL_ADMIN
        )
        self.client.force_authenticate(user=school_admin)
        before = CreditBucket.objects.count()

        response = self.client.post(grant_url(), self.payload(), format="json")

        self.assertIn(response.status_code, (403, 404))
        self.assertEqual(CreditBucket.objects.count(), before)

    def test_a_student_cannot_mint_credits(self):
        student = self._user("grant.student@admincredit.test", UserTypes.STUDENT)
        self.client.force_authenticate(user=student)

        response = self.client.post(grant_url(), self.payload(), format="json")

        self.assertIn(response.status_code, (403, 404))

    def test_an_anonymous_caller_cannot_mint_credits(self):
        before = CreditBucket.objects.count()

        response = self.client.post(grant_url(), self.payload(), format="json")

        self.assertIn(response.status_code, (401, 403, 404))
        self.assertEqual(CreditBucket.objects.count(), before)

    def test_both_superadmin_signals_are_required(self):
        """
        `IsSuperAdmin` requires user_type == SUPER_ADMIN AND is_superuser.
        Either alone must not unlock credit minting.
        """
        for email, user_type, is_superuser in (
            ("grant.flagonly@admincredit.test", UserTypes.TEACHER, True),
            ("grant.typeonly@admincredit.test", UserTypes.SUPER_ADMIN, False),
        ):
            with self.subTest(email=email):
                impostor = self._user(email, user_type, is_superuser=is_superuser)
                self.client.force_authenticate(user=impostor)
                before = CreditBucket.objects.count()

                response = self.client.post(grant_url(), self.payload(), format="json")

                self.assertIn(response.status_code, (403, 404))
                self.assertEqual(CreditBucket.objects.count(), before)


class AdminCreditGrantBehaviourTests(_AdminCreditBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.superadmin)

    def test_a_grant_lands_priced_by_the_targets_own_block_size(self):
        """
        The amount is not free-form: raw credits = blocks x the TARGET
        user's plan overage_block_size, so a block means the same thing
        here as in a paid overage purchase.
        """
        response = self.client.post(grant_url(), self.payload(blocks=3), format="json")

        self.assertEqual(response.status_code, 201, response.content)

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
        )
        self.assertEqual(bucket.total_credits, 3 * BLOCK_SIZE)
        self.assertEqual(bucket.used_credits, 0)

    def test_the_grant_is_recorded_in_the_immutable_ledger(self):
        self.client.post(grant_url(), self.payload(blocks=1), format="json")

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
        )
        rows = CreditLedger.objects.filter(bucket=bucket)
        self.assertEqual(rows.count(), 1, "the grant left no audit row")
        self.assertEqual(rows.first().amount, BLOCK_SIZE)

    def test_the_ledger_records_who_authorised_the_grant(self):
        """
        Accountability for a money-equivalent action. Without the granting
        admin's identity there is no way to answer "who gave this away?".
        """
        self.client.post(
            grant_url(), self.payload(reason="comped for a support case"), format="json"
        )

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
        )
        row = CreditLedger.objects.filter(bucket=bucket).first()
        blob = f"{row.reference} {row.metadata}"
        self.assertIn(
            "comped for a support case",
            blob,
            "the stated reason was not persisted to the audit trail",
        )
        self.assertIn(
            str(self.superadmin.id),
            blob,
            "the granting admin's identity is not in the audit trail",
        )

    def test_a_grant_with_no_expiry_never_expires(self):
        self.client.post(grant_url(), self.payload(), format="json")

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
        )
        self.assertIsNone(
            bucket.expires_at, "an unexpiring grant was given an expiry date"
        )

    def test_an_explicit_expiry_is_honoured(self):
        expiry = (timezone.now() + timedelta(days=10)).replace(microsecond=0)

        self.client.post(
            grant_url(), self.payload(expires_at=expiry.isoformat()), format="json"
        )

        bucket = CreditBucket.objects.get(
            wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
        )
        self.assertIsNotNone(bucket.expires_at)
        self.assertEqual(bucket.expires_at.replace(microsecond=0), expiry)

    def test_the_grant_is_a_manual_grant_not_purchased_overage(self):
        """
        Bucket type matters: MANUAL_GRANT keeps admin comps distinguishable
        from overage the customer actually paid for, in both the ledger and
        revenue analytics.
        """
        self.client.post(grant_url(), self.payload(), format="json")

        self.assertTrue(
            CreditBucket.objects.filter(
                wallet__user=self.teacher, bucket_type=CreditBucketType.MANUAL_GRANT
            ).exists()
        )
        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user=self.teacher, bucket_type=CreditBucketType.OVERAGE
            ).exists()
        )


class AdminCreditGrantValidationTests(_AdminCreditBase):
    def setUp(self):
        super().setUp()
        self.client.force_authenticate(user=self.superadmin)

    def _rejects(self, payload):
        before = CreditBucket.objects.count()
        response = self.client.post(grant_url(), payload, format="json")
        self.assertEqual(response.status_code, 400, response.content)
        self.assertEqual(
            CreditBucket.objects.count(),
            before,
            "a rejected grant still minted credits",
        )
        return response

    def test_zero_blocks_is_rejected(self):
        self._rejects(self.payload(blocks=0))

    def test_negative_blocks_is_rejected(self):
        """Otherwise this endpoint could DELETE credits from a wallet."""
        self._rejects(self.payload(blocks=-5))

    def test_an_absurdly_large_grant_is_rejected(self):
        """A capped maximum is what stops a fat-fingered zero from
        handing out a fortune."""
        self._rejects(self.payload(blocks=10_000))

    def test_a_non_numeric_block_count_is_rejected(self):
        self._rejects(self.payload(blocks="many"))

    def test_an_unknown_user_id_is_rejected(self):
        self._rejects(self.payload(user_id=str(uuid.uuid4())))

    def test_a_malformed_user_id_is_rejected(self):
        self._rejects(self.payload(user_id="not-a-uuid"))

    def test_a_user_with_no_plan_cannot_be_priced(self):
        """
        Blocks are priced against the TARGET's plan, so a user without one
        has no block size. That must be a clean 400, not a 500.
        """
        planless = self._user("grant.planless@admincredit.test", UserTypes.TEACHER)
        CreditWallet.objects.get_or_create(user=planless)

        self._rejects(self.payload(user_id=str(planless.id)))

    def test_an_oversized_reason_is_rejected(self):
        self._rejects(self.payload(reason="x" * 5_000))
