"""Regression: dashboard/teachers/<id> reports a school admin's teacher's
remaining credits split by source (monthly / carry_over / overage), per the
2026-09-22 review decision. MANUAL_GRANT folds into "overage" (both are
outside the fixed plan allocation); TRIAL is excluded entirely, not just
zeroed, since a school-license teacher never has one (see the
license-invitation guard in users/signals.py)."""

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone
from rest_framework import status as http_status
from rest_framework.test import APIClient

from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import School
from users.models import CustomUser, UserTypes

TEST_PASSWORD = "password123"  # pragma: allowlist secret

URL = "/api/v1/school-admin/dashboard/teachers/{id}"


def make_school_admin(email, school):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.SCHOOL_ADMIN
    user.is_active = True
    user.school = school
    user.save()
    return user


def make_teacher(email, school):
    user = CustomUser.objects.create_user(email=email, password=TEST_PASSWORD)
    user.user_type = UserTypes.TEACHER
    user.is_active = True
    user.school = school
    user.save()
    return user


def make_bucket(wallet, bucket_type, total, used, *, expires_at=None):
    return CreditBucket.objects.create(
        wallet=wallet,
        bucket_type=bucket_type,
        total_credits=total,
        used_credits=used,
        expires_at=expires_at,
    )


class TeacherCreditsRemainingTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Credits Remaining School")
        self.admin = make_school_admin(
            "credits-remaining-admin@example.com", self.school
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.admin)
        self.now = timezone.now()

    def get(self, teacher_id):
        response = self.client.get(URL.format(id=teacher_id))
        self.assertEqual(
            response.status_code, http_status.HTTP_200_OK, response.content
        )
        return response.data["credits_remaining"]

    def test_each_source_is_isolated_and_manual_grant_folds_into_overage(self):
        teacher = make_teacher("credits-remaining-t1@example.com", self.school)
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        make_bucket(wallet, CreditBucketType.MONTHLY, total=1000, used=200)
        make_bucket(wallet, CreditBucketType.CARRY_OVER, total=300, used=50)
        make_bucket(wallet, CreditBucketType.OVERAGE, total=500, used=100)
        make_bucket(wallet, CreditBucketType.MANUAL_GRANT, total=200, used=0)

        credits_remaining = self.get(teacher.id)

        self.assertEqual(credits_remaining["monthly"], 800)
        self.assertEqual(credits_remaining["carry_over"], 250)
        # OVERAGE (500-100=400) + MANUAL_GRANT (200-0=200) = 600
        self.assertEqual(credits_remaining["overage"], 600)
        self.assertEqual(credits_remaining["total"], 800 + 250 + 600)

    def test_expired_buckets_are_excluded(self):
        teacher = make_teacher("credits-remaining-t2@example.com", self.school)
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        expired = self.now - timedelta(days=1)
        make_bucket(
            wallet, CreditBucketType.MONTHLY, total=1000, used=0, expires_at=expired
        )
        make_bucket(
            wallet, CreditBucketType.CARRY_OVER, total=300, used=0, expires_at=expired
        )
        make_bucket(
            wallet, CreditBucketType.OVERAGE, total=500, used=0, expires_at=expired
        )

        credits_remaining = self.get(teacher.id)

        self.assertEqual(
            credits_remaining,
            {"monthly": 0, "carry_over": 0, "overage": 0, "total": 0},
        )

    def test_trial_bucket_never_leaks_into_any_category(self):
        teacher = make_teacher("credits-remaining-t3@example.com", self.school)
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        make_bucket(wallet, CreditBucketType.MONTHLY, total=1000, used=0)
        make_bucket(
            wallet,
            CreditBucketType.TRIAL,
            total=5000,
            used=0,
            expires_at=self.now + timedelta(days=14),
        )

        credits_remaining = self.get(teacher.id)

        self.assertEqual(credits_remaining["monthly"], 1000)
        self.assertEqual(credits_remaining["carry_over"], 0)
        self.assertEqual(credits_remaining["overage"], 0)
        self.assertEqual(credits_remaining["total"], 1000)

    def test_teacher_with_no_wallet_gets_all_zero(self):
        teacher = make_teacher("credits-remaining-t4@example.com", self.school)

        credits_remaining = self.get(teacher.id)

        self.assertEqual(
            credits_remaining,
            {"monthly": 0, "carry_over": 0, "overage": 0, "total": 0},
        )
