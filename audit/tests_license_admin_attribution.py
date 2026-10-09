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

from audit.enums import AuditAction
from audit.models import AuditEvent
from audit.tests_state_change import LOCMEM_CACHE
from billing.models import (
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
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

    def test_add_teachers_names_the_admin_on_the_teachers_credit_grant(self):
        """Epic A S3 (SM pin): the credit grant now names the INITIATOR - the
        school admin - with the teacher as target, so it IS the admin's
        trace and the generic STATE_CHANGE is not written (S1's invariant:
        exactly one event naming the requester)."""
        response, events = self.add_teacher()

        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data["successful"], 1)
        # Epic A S4, updated on purpose: the admin's add also records the
        # seat taken (SUBSCRIPTION_CHANGE on the allocation, SM R4) and the
        # teacher's school set (PERMISSION_CHANGE) - all naming the admin.
        # Every event names the admin, so there is still no STATE_CHANGE.
        seat = SchoolCreditAllocation.objects.get(
            license_subscription=self.licence, user=self.teacher
        )
        self.assertEqual(
            set(events.values_list("actor_id", flat=True)), {self.admin.id}
        )
        self.assertEqual(
            sorted(
                events.values_list("action", "target_id", "metadata__ledger_type"),
                key=str,
            ),
            sorted(
                [
                    (AuditAction.CREDIT_TRANSACTION, self.teacher.id, "GRANT"),
                    (AuditAction.SUBSCRIPTION_CHANGE, seat.id, None),
                    (AuditAction.PERMISSION_CHANGE, self.teacher.id, None),
                ],
                key=str,
            ),
        )
        self.assertFalse(events.filter(action=AuditAction.STATE_CHANGE).exists())

    def test_remove_teachers_names_the_admin_on_the_clawback(self):
        """S3 (G7): the clawback goes through the ledger - an EXPIRE naming
        the admin, the teacher as target - and is the admin's one event."""
        self.add_teacher()

        response, events = self.post(
            "remove_teachers", {"teacher_ids": [str(self.teacher.id)]}
        )

        self.assertEqual(response.status_code, 200, response.data)
        # Epic A S4, updated on purpose: plus the seat released and the
        # teacher's school cleared, all naming the admin.
        seat = SchoolCreditAllocation.objects.get(
            license_subscription=self.licence, user=self.teacher
        )
        self.assertEqual(
            set(events.values_list("actor_id", flat=True)), {self.admin.id}
        )
        self.assertEqual(
            sorted(
                events.values_list("action", "target_id", "metadata__ledger_type"),
                key=str,
            ),
            sorted(
                [
                    (AuditAction.CREDIT_TRANSACTION, self.teacher.id, "EXPIRE"),
                    (AuditAction.SUBSCRIPTION_CHANGE, seat.id, None),
                    (AuditAction.PERMISSION_CHANGE, self.teacher.id, None),
                ],
                key=str,
            ),
        )
        self.assertFalse(events.filter(action=AuditAction.STATE_CHANGE).exists())
