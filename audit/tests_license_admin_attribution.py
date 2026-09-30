"""S1 R2 (v2's V1): a school admin's licence change is traceable to the admin.

At f300c6b a school admin's successful add_teachers left one event: the
teacher's CREDIT_TRANSACTION, whose actor is the wallet owner (the teacher).
Any surviving event suppressed the generic one, so nothing named the admin
who acted - on the very route plan 08 G1 names. Now only a surviving event
that names the requester stands in for the generic STATE_CHANGE; events
naming others are side effects and stay.

Adapted from v2's probe (`tests_vf2_s1_attribution.py`): real JWTs, the real
middleware stack, and a plan whose seats carry a credit grant.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent
from audit.tests_state_change import LOCMEM_CACHE
from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from billing.tests.test_h38_part2_removed_teacher_routes import (
    API,
    jwt_client,
    make_user,
)
from classrooms.models import School
from users.models import UserTypes


@override_settings(CACHES=LOCMEM_CACHE)
class SchoolAdminLicenceChangesNameTheAdminTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="Attribution Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.school = School.objects.create(name="Attribution School")
        self.admin = make_user(
            "admin@attribution.test", UserTypes.SCHOOL_ADMIN, self.school
        )
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
        )
        self.teacher = make_user("teacher@attribution.test", UserTypes.TEACHER)
        self.client = jwt_client(self.admin.email)

    def post(self, action, body):
        before = set(AuditEvent.objects.values_list("pk", flat=True))
        response = self.client.post(
            f"{API}/license-subscriptions/{self.licence.id}/{action}",
            body,
            format="json",
        )
        return response, AuditEvent.objects.exclude(pk__in=before)

    def add_teacher(self):
        return self.post("add_teachers", {"teacher_emails": [self.teacher.email]})

    def test_add_teachers_names_the_admin_and_keeps_the_teachers_credit_grant(self):
        response, events = self.add_teacher()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["successful"], 1)
        self.assertTrue(
            events.filter(
                action=AuditAction.CREDIT_TRANSACTION, actor_id=self.teacher.id
            ).exists(),
            "the teacher's credit grant is a side effect and must stay",
        )
        by_admin = events.filter(actor_id=self.admin.id)
        self.assertEqual(
            list(by_admin.values_list("action", "outcome", "metadata__route")),
            [
                (
                    AuditAction.STATE_CHANGE,
                    AuditOutcome.SUCCESS,
                    "license-subscription-add-teachers",
                )
            ],
        )

    def test_remove_teachers_names_the_admin(self):
        """Control: no ledger row today, so the generic event always named
        the admin here; it must keep doing so once S3 adds one (G7)."""
        self.add_teacher()

        response, events = self.post(
            "remove_teachers", {"teacher_ids": [str(self.teacher.id)]}
        )

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(events.filter(actor_id=self.admin.id).count(), 1)
