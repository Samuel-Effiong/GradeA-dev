"""Epic B1b-7 (plan §3.10, §7 item 6; 08b §1.5; NOTES §7.2 D7-c, contract
§7.10): GET billing/jobs/<id>, the job's estimate, actual cost, counts and
state, for its owner, a school admin of the owner's license, and the super
admin. Anyone else gets 404. It returns codes and numbers only: the app
shows the approved texts (#15) from them.

    python manage.py test billing.tests.test_b1b7_job_api
"""

import uuid
from datetime import timedelta

from django.test import TestCase
from django.urls import NoReverseMatch, reverse
from django.utils import timezone
from rest_framework.test import APIClient

from billing.models import (
    AIJobStatus,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    SchoolCreditAllocation,
)
from billing.testing.holds import make_job
from billing.tests.test_beat_lock_catch_up import make_school_licence
from classrooms.models import School
from users.models import CustomUser, UserTypes

KEYS = {
    "id",
    "job_type",
    "state",
    "estimate",
    "verdict",
    "actual",
    "items",
    "stopped_at_item",
    "queued_at",
    "expires_at",
    "created_at",
}


def user(tag, user_type=UserTypes.TEACHER, **over):
    return CustomUser.objects.create_user(
        email=f"b1b7.job.{tag}@example.com",
        password="testpass123",  # pragma: allowlist secret  # nosec B106
        user_type=user_type,
        **over,
    )


def job_url(pk):
    try:
        return reverse("billing-job-detail", args=[pk])
    except NoReverseMatch:
        return f"/api/v1/billing/jobs/{pk}"


class JobApiTests(TestCase):
    def setUp(self):
        self.school, self.licence = make_school_licence(
            "b1b7job",
            billing_cycle_start=timezone.now() - timedelta(days=5),
            billing_cycle_end=timezone.now() + timedelta(days=25),
        )
        self.owner = user("owner", school=self.school)
        SchoolCreditAllocation.objects.create(
            license_subscription=self.licence,
            user=self.owner,
            monthly_allocation=100_000,
            is_active=True,
            next_credit_grant_at=timezone.now() + timedelta(days=25),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.owner)
        self.expires = timezone.now() + timedelta(days=30)
        self.job = make_job(
            wallet,
            job_type="BATCH_GRADING",
            total_items=5,
            completed_items=2,
            failed_items=1,
            status=AIJobStatus.QUEUED_PENDING_CREDITS,
            estimate_p50=300,
            estimate_p90=450,
            estimate_source="assignment",
            sufficiency_verdict="MAYBE",
            consumed_credits=640,
            unbilled_credits=12,
            stopped_at_item=4,
            queued_at=timezone.now(),
            expires_at=self.expires,
        )

    def get(self, who):
        client = APIClient(raise_request_exception=False)
        if who is not None:
            client.force_authenticate(user=who)
        return client.get(job_url(self.job.pk))

    def test_the_owner_gets_the_job(self):
        response = self.get(self.owner)
        self.assertEqual(response.status_code, 200, response.content[:300])
        data = response.json()["data"]
        self.assertEqual(set(data), KEYS)
        self.assertEqual(data["id"], str(self.job.pk))
        self.assertEqual(data["job_type"], "BATCH_GRADING")
        self.assertEqual(data["state"], AIJobStatus.QUEUED_PENDING_CREDITS)
        self.assertEqual(
            data["estimate"], {"p50": 300, "p90": 450, "source": "assignment"}
        )
        self.assertEqual(data["verdict"], "MAYBE")
        self.assertEqual(
            data["actual"], {"consumed_credits": 640, "unbilled_credits": 12}
        )
        self.assertEqual(
            data["items"], {"total": 5, "completed": 2, "failed": 1, "left": 2}
        )
        self.assertEqual(data["stopped_at_item"], 4)
        self.assertIsNotNone(data["expires_at"])
        self.assertIsNotNone(data["queued_at"])

    def test_another_teacher_a_student_and_an_unknown_id_get_404(self):
        for who in (user("other"), user("student", UserTypes.STUDENT)):
            with self.subTest(who=who.user_type):
                self.assertEqual(self.get(who).status_code, 404)
        client = APIClient(raise_request_exception=False)
        client.force_authenticate(user=self.owner)
        self.assertEqual(client.get(job_url(uuid.uuid4())).status_code, 404)

    def test_anonymous_gets_401(self):
        self.assertEqual(self.get(None).status_code, 401)

    def test_a_school_admin_of_the_owners_license_gets_it(self):
        response = self.get(self.licence.admin_user)
        self.assertEqual(response.status_code, 200, response.content[:300])
        self.assertEqual(set(response.json()["data"]), KEYS)

    def test_a_school_admin_of_another_license_gets_404(self):
        # A second license on the same plan (a second make_plan would break
        # the plan-name unique key; NOTES §7.12-§7.13).
        school = School.objects.create(name="B1b-7 other school")
        admin = user("otheradmin", UserTypes.SCHOOL_ADMIN, school=school)
        LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=self.licence.plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=timezone.now() - timedelta(days=5),
            billing_cycle_end=timezone.now() + timedelta(days=25),
            billing_method=LicenseBillingMethod.STRIPE,
        )
        self.assertEqual(self.get(admin).status_code, 404)

    def test_the_super_admin_gets_it(self):
        admin = user("super", UserTypes.SUPER_ADMIN, is_superuser=True)
        self.assertEqual(self.get(admin).status_code, 200)

    def test_a_removed_teacher_is_out_of_the_school_admins_scope(self):
        SchoolCreditAllocation.objects.filter(user=self.owner).update(is_active=False)
        self.assertEqual(self.get(self.licence.admin_user).status_code, 404)
        self.assertEqual(self.get(self.owner).status_code, 200)
