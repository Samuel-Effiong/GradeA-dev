"""
H-85: a school admin who adds a teacher with an active individual
subscription is told only that the teacher can't be added yet.

THE DISCLOSURE
--------------
The refusal read "Teacher <address> has an active individual subscription.
Individual subscriptions cannot be converted to a license. Please cancel
the individual subscription first." Any school admin could type an address
and learn that its owner pays for an individual plan (found in the H-78
verification; the founder decided on a neutral message, 2026-10-02).

THE FIX
-------
Both refusals (_get_or_invite_teacher and _enroll_teacher_internal) carry
one fixed sentence, with no address and no mention of a subscription. The
exception class is unchanged (callers and the error mapping key on it), and
the ids-only log line still says why, for support.
"""

from datetime import timedelta
from unittest.mock import patch

from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from billing.license_service import (
    IndividualSubscriptionConflictError,
    LicenseSubscriptionService,
)
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

NEUTRAL = (
    "This teacher can't be added to your school yet. "
    "Please ask them to contact support."
)
TELLING_WORDS = ("subscription", "individual", "billing", "cancel", "plan", "paid")


class NeutralSubscriptionRefusalTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="H85 School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H85 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="H85 Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@h85school.edu",
            password="password123",  # pragma: allowlist secret
            first_name="School",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)
        self.client.force_authenticate(user=self.admin)
        self.licence = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=[],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

    def paying_teacher(self, email, school):
        teacher = CustomUser.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Some",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
        )
        UserSubscription.objects.filter(user=teacher).update(is_active=False)
        UserSubscription.objects.create(
            user=teacher,
            plan=self.individual_plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )
        return teacher

    def assertNeutral(self, text, teacher):
        self.assertEqual(text, NEUTRAL)
        self.assertNotIn(teacher.email, text)
        self.assertNotIn("@", text)
        for word in TELLING_WORDS:
            self.assertNotIn(word, text.lower())

    def assertNotEnrolled(self, teacher):
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(
                license_subscription=self.licence, user=teacher
            ).exists()
        )

    # --- Both refusals carry the neutral sentence ---------------------------

    def test_inviting_an_unattached_paying_teacher_is_refused_neutrally(self):
        """The H-85 case: no school, so any admin can try the address."""
        teacher = self.paying_teacher("paying@h85none.edu", None)

        with self.assertRaises(IndividualSubscriptionConflictError) as caught:
            LicenseSubscriptionService._get_or_invite_teacher(
                teacher.email, self.school, self.admin, raise_on_conflict=True
            )

        self.assertNeutral(str(caught.exception), teacher)

    def test_inviting_the_schools_own_paying_teacher_is_refused_neutrally(self):
        teacher = self.paying_teacher("paying@h85school.edu", self.school)

        with self.assertRaises(IndividualSubscriptionConflictError) as caught:
            LicenseSubscriptionService._get_or_invite_teacher(
                teacher.email, self.school, self.admin, raise_on_conflict=True
            )

        self.assertNeutral(str(caught.exception), teacher)

    def test_enrolling_a_paying_teacher_is_refused_neutrally(self):
        teacher = self.paying_teacher("paying@h85school.edu", self.school)

        with self.assertRaises(IndividualSubscriptionConflictError) as caught:
            LicenseSubscriptionService._enroll_teacher_internal(self.licence, teacher)

        self.assertNeutral(str(caught.exception), teacher)

    # --- What the admin sees ---------------------------------------------------

    def test_the_add_teachers_response_says_nothing_about_a_subscription(self):
        teacher = self.paying_teacher("paying@h85none.edu", None)

        response = self.client.post(
            reverse(
                "license-subscription-add-teachers", kwargs={"pk": self.licence.pk}
            ),
            {"teacher_emails": [teacher.email]},
            format="json",
        )

        body = response.content.decode()
        self.assertIn("This teacher can't be added to your school yet.", body)
        for word in ("subscription", "cancel", "individual"):
            self.assertNotIn(word, body.lower())
        self.assertNotEnrolled(teacher)

    def test_the_enrolment_result_carries_the_neutral_sentence(self):
        teacher = self.paying_teacher("paying@h85none.edu", None)

        result = LicenseSubscriptionService._invite_and_enroll_one_teacher(
            self.licence, self.school, self.admin, teacher.email
        )

        self.assertFalse(result["successful"])
        self.assertNeutral(result["error"], teacher)
        self.assertNotEnrolled(teacher)

    # --- Support can still see why ---------------------------------------------

    def test_the_log_still_gives_the_reason_by_id(self):
        teacher = self.paying_teacher("paying@h85none.edu", None)

        with self.assertLogs("billing.license_service", "WARNING") as logs:
            LicenseSubscriptionService._invite_and_enroll_one_teacher(
                self.licence, self.school, self.admin, teacher.email
            )

        reasons = [line for line in logs.output if "individual subscription" in line]
        self.assertEqual(len(reasons), 1, logs.output)
        self.assertIn(str(teacher.id), reasons[0])
        self.assertEqual([line for line in logs.output if "@" in line], [])
