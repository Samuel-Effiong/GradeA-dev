"""
Real GET /subscription/me payloads for every state, as the frontend
receives them (rendered JSON bytes, not response.data).

Each test asserts the state it builds. Set SUBSCRIPTION_ME_SAMPLES_DIR to
also write each payload to <dir>/<name>.json for the evidence folder.
"""

import json
import os
from datetime import timedelta
from pathlib import Path

from django.utils import timezone
from rest_framework import status

from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    SchoolCreditAllocation,
    StripeSubscriptionStatus,
)
from billing.tests.test_subscription_me_status import (
    PASSWORD,
    CustomUser,
    SubscriptionMeStatusTestBase,
    make_license_school,
    make_plan,
)
from users.models import UserTypes


class SubscriptionMePayloadSampleTests(SubscriptionMeStatusTestBase):
    def _capture(self, name, expected_http, expected_status=None):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, expected_http, response.content)
        envelope = json.loads(response.content)
        out_dir = os.environ.get("SUBSCRIPTION_ME_SAMPLES_DIR")
        if out_dir:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            sample = {"http_status": response.status_code, "body": envelope}
            (Path(out_dir) / f"{name}.json").write_text(
                json.dumps(sample, indent=2, sort_keys=True) + "\n"
            )
        if expected_status is None:
            self.assertFalse(envelope["success"])
            return envelope
        self.assertTrue(envelope["success"])
        self.assertEqual(envelope["data"]["status"], expected_status)
        return envelope["data"]

    def _license(self, admin, school, active=True, lapsed=False):
        plan = make_plan(
            f"LICENSE_{admin.pk.hex[:8]}",
            PlanTier.CUSTOM,
            40_000_000,
            category=PlanCategory.LICENSE,
        )
        if lapsed:
            start = timezone.now() - timedelta(days=60)
            end = timezone.now() - timedelta(days=30)
        else:
            start = timezone.now()
            end = timezone.now() + timedelta(days=30)
        return LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            billing_cycle_start=start,
            billing_cycle_end=end,
            is_active=active,
            auto_renew=active,
            stripe_status=(
                StripeSubscriptionStatus.ACTIVE
                if active
                else StripeSubscriptionStatus.CANCELED
            ),
            max_seats=5,
        )

    def _allocate(self, license_sub, user, active=True, admin=False):
        SchoolCreditAllocation.objects.create(
            license_subscription=license_sub,
            user=user,
            is_active=active,
            is_admin_allocation=admin,
            monthly_allocation=1000,
        )

    def test_individual_active(self):
        self.make_sub()
        payload = self._capture("individual_active", status.HTTP_200_OK, "ACTIVE")
        self.assertEqual(payload["subscription_type"], "INDIVIDUAL")

    def test_individual_expired(self):
        self.make_sub(
            is_active=False,
            auto_renew=False,
            billing_cycle_start=timezone.now() - timedelta(days=60),
            billing_cycle_end=timezone.now() - timedelta(days=30),
            stripe_status=StripeSubscriptionStatus.CANCELED,
        )
        payload = self._capture("individual_expired", status.HTTP_200_OK, "EXPIRED")
        self.assertEqual(payload["subscription_type"], "INDIVIDUAL")

    def test_individual_none(self):
        payload = self._capture("individual_none", status.HTTP_200_OK, "NONE")
        self.assertEqual(set(payload), {"status", "message"})

    def test_license_teacher_active(self):
        school, admin, teacher = make_license_school(
            "sample-admin-ta@example.com", "sample-teacher-ta@example.com"
        )
        self._allocate(self._license(admin, school), teacher)
        self.client.force_authenticate(user=teacher)
        payload = self._capture("license_teacher_active", status.HTTP_200_OK, "ACTIVE")
        self.assertEqual(payload["subscription_source"], "LICENSE_TEACHER")

    def test_license_teacher_expired_allocation_deactivated(self):
        school, admin, teacher = make_license_school(
            "sample-admin-td@example.com", "sample-teacher-td@example.com"
        )
        self._allocate(self._license(admin, school), teacher, active=False)
        self.client.force_authenticate(user=teacher)
        payload = self._capture(
            "license_teacher_expired_allocation_deactivated",
            status.HTTP_200_OK,
            "EXPIRED",
        )
        self.assertEqual(payload["subscription_source"], "LICENSE_TEACHER")
        self.assertTrue(payload["is_license_active"])

    def test_license_teacher_expired_parent_license_lapsed(self):
        school, admin, teacher = make_license_school(
            "sample-admin-tl@example.com", "sample-teacher-tl@example.com"
        )
        license_sub = self._license(admin, school, active=False, lapsed=True)
        self._allocate(license_sub, teacher)
        self.client.force_authenticate(user=teacher)
        payload = self._capture(
            "license_teacher_expired_parent_license_lapsed",
            status.HTTP_200_OK,
            "EXPIRED",
        )
        self.assertEqual(payload["subscription_source"], "LICENSE_TEACHER")
        self.assertFalse(payload["is_license_active"])

    def test_license_admin_active(self):
        school, admin, _ = make_license_school("sample-admin-aa@example.com")
        self._allocate(self._license(admin, school), admin, admin=True)
        self.client.force_authenticate(user=admin)
        payload = self._capture("license_admin_active", status.HTTP_200_OK, "ACTIVE")
        self.assertEqual(payload["subscription_source"], "LICENSE_ADMIN")

    def test_license_admin_expired(self):
        school, admin, _ = make_license_school("sample-admin-ae@example.com")
        self._license(admin, school, active=False, lapsed=True)
        self.client.force_authenticate(user=admin)
        payload = self._capture("license_admin_expired", status.HTTP_200_OK, "EXPIRED")
        self.assertEqual(payload["subscription_source"], "LICENSE_ADMIN")

    def test_student_caller_is_refused(self):
        student = CustomUser.objects.create_user(
            email="sample-student@example.com",
            password=PASSWORD,
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.client.force_authenticate(user=student)
        self._capture("student_forbidden", status.HTTP_403_FORBIDDEN)
