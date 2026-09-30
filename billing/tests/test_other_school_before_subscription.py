"""
H-78: a school admin adding a teacher who belongs to ANOTHER school must
learn only that, never the teacher's billing status; and neither the
not-business nor the individual-subscription refusal writes the teacher's
address to the log.

_get_or_invite_teacher checked "has an active individual subscription"
before "belongs to another school", so adding another tenant's teacher who
pays for their own plan answered "... has an active individual
subscription ... Please cancel the individual subscription first": another
school's teacher's billing status, disclosed across tenants. The school
check now comes first; the wording of every refusal is unchanged.

The individual-subscription refusal logged its message, address included,
and _invite_and_enroll_one_teacher logged every refusal's text, which
carries the address for the not-business and individual-subscription
refusals. Both now log ids and the exception's class only, like bundle 4's
other two refusals.
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

OTHER_SCHOOL = "This teacher already belongs to another school."
BILLING_WORDS = ("subscription", "billing", "cancel")


class OtherSchoolBeforeSubscriptionTest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="H78 Own School")
        self.other_school = School.objects.create(name="H78 Other School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H78 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.individual_plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="H78 Individual Plan",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@h78own.edu",
            password="password123",  # pragma: allowlist secret
            first_name="School",
            last_name="Admin",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        mail = patch("billing.license_service.send_email_task")
        self.mail = mail.start()
        self.addCleanup(mail.stop)
        self.client.force_authenticate(user=self.admin)
        self.licence = LicenseSubscriptionService.create_license_subscription(
            school=self.school,
            plan=self.plan,
            teacher_emails=[],
            max_seats=5,
            billing_method=LicenseBillingMethod.OFFLINE,
        )

    def teacher(self, email, school, paying):
        teacher = CustomUser.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Some",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
        )
        if paying:
            UserSubscription.objects.create(
                user=teacher,
                plan=self.individual_plan,
                is_active=True,
                billing_cycle_start=timezone.now(),
                billing_cycle_end=timezone.now() + timedelta(days=30),
            )
        return teacher

    def add(self, emails):
        return self.client.post(
            reverse(
                "license-subscription-add-teachers", kwargs={"pk": self.licence.pk}
            ),
            {"teacher_emails": emails},
            format="json",
        )

    def invite(self, email, raise_on_conflict):
        return LicenseSubscriptionService._get_or_invite_teacher(
            email, self.school, self.admin, raise_on_conflict=raise_on_conflict
        )

    def assertNoBillingWords(self, text):
        for word in BILLING_WORDS:
            self.assertNotIn(word, text.lower())

    def assertNotEnrolled(self, teacher):
        self.assertFalse(
            SchoolCreditAllocation.objects.filter(
                license_subscription=self.licence, user=teacher
            ).exists()
        )

    # --- The school check comes first --------------------------------------

    def test_another_schools_paying_teacher_gets_only_the_other_school_refusal(self):
        teacher = self.teacher("paying@h78other.edu", self.other_school, paying=True)

        response = self.add([teacher.email])

        body = response.content.decode()
        self.assertIn(OTHER_SCHOOL, body)
        self.assertNoBillingWords(body)
        self.assertNotEnrolled(teacher)
        self.mail.assert_not_called()

    def test_the_service_raises_the_school_refusal_first(self):
        teacher = self.teacher("paying2@h78other.edu", self.other_school, paying=True)

        with self.assertRaises(ValueError) as caught:
            self.invite(teacher.email, raise_on_conflict=True)

        self.assertNotIsInstance(caught.exception, IndividualSubscriptionConflictError)
        self.assertEqual(str(caught.exception), OTHER_SCHOOL)

    def test_the_non_raising_path_logs_the_school_refusal_first(self):
        teacher = self.teacher("paying3@h78other.edu", self.other_school, paying=True)

        with self.assertLogs("billing.license_service", "WARNING") as logs:
            result = self.invite(teacher.email, raise_on_conflict=False)

        self.assertIsNone(result)
        # The messages, not logs.output: that carries the logger's name,
        # billing.license_service.
        text = "\n".join(record.getMessage() for record in logs.records)
        self.assertIn("belongs to school", text)
        self.assertNoBillingWords(text)

    def test_control_own_schools_paying_teacher_still_gets_the_subscription_refusal(
        self,
    ):
        """Unchanged: the admin's own teacher's subscription is theirs to see."""
        teacher = self.teacher("paying@h78own.edu", self.school, paying=True)

        with self.assertRaises(IndividualSubscriptionConflictError) as caught:
            self.invite(teacher.email, raise_on_conflict=True)

        self.assertIn("active individual subscription", str(caught.exception))

    def test_control_unattached_paying_teacher_still_gets_the_subscription_refusal(
        self,
    ):
        """Unchanged: a teacher with no school isn't another tenant's."""
        teacher = self.teacher("paying@h78none.edu", None, paying=True)

        with self.assertRaises(IndividualSubscriptionConflictError):
            self.invite(teacher.email, raise_on_conflict=True)

    def test_control_another_schools_teacher_without_a_plan_gets_the_school_refusal(
        self,
    ):
        teacher = self.teacher("free@h78other.edu", self.other_school, paying=False)

        response = self.add([teacher.email])

        body = response.content.decode()
        self.assertIn(OTHER_SCHOOL, body)
        self.assertNoBillingWords(body)
        self.assertNotEnrolled(teacher)

    # --- No address in the log --------------------------------------------

    def assertLogsIdsNotAddress(self, output, email, *ids):
        text = "\n".join(output)
        self.assertNotIn(email, text)
        self.assertNotIn("@", text)
        for an_id in ids:
            self.assertIn(str(an_id), text)

    def test_the_subscription_refusal_logs_the_teacher_id_not_the_address(self):
        teacher = self.teacher("paying4@h78own.edu", self.school, paying=True)

        for raise_on_conflict in (False, True):
            with self.subTest(raise_on_conflict=raise_on_conflict):
                with self.assertLogs("billing.license_service", "WARNING") as logs:
                    try:
                        self.invite(teacher.email, raise_on_conflict)
                    except IndividualSubscriptionConflictError:
                        pass
                self.assertLogsIdsNotAddress(logs.output, teacher.email, teacher.id)

    def test_adding_teachers_logs_no_address_for_either_refusal(self):
        """Through the endpoint, every logger: the refusal's own line and
        _invite_and_enroll_one_teacher's "Skipped enrolling" line."""
        paying = self.teacher("paying5@h78own.edu", self.school, paying=True)
        not_business = "someone@gmail.com"

        for email, ids in (
            (paying.email, (paying.id, self.licence.id)),
            (not_business, (self.licence.id,)),
        ):
            with self.subTest(email=email):
                with self.assertLogs(level="DEBUG") as logs:
                    self.add([email])
                self.assertLogsIdsNotAddress(logs.output, email, *ids)
                self.assertIn("Skipped enrolling", "\n".join(logs.output))
