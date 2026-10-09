"""
H-203, the licence invitation road.

THE PRINCIPLE (users/admin_power.py): an account with admin power that has
never been verified can be entered only with its password; no road that proves
only control of a mailbox signs it in, verifies it or activates it.

`LicenseSubscriptionService._get_or_invite_teacher` is such a road. For an
existing TEACHER-typed account that has never signed in
(`must_change_password` True) it set a fresh password and mailed it to the
address, whatever the account's marks. A command-line superuser is
TEACHER-typed, so a staff-ticked, never-verified row would be handed a
password by mail. It is now refused, with the answer for an email that cannot
be added, BEFORE the school and subscription checks (so it tells a school admin
nothing about the row) and before anything is written: no password, no mail,
no seat. A teacher that verified and was then switched off (H-202) is refused
the same way.

Run with:
    python manage.py test billing.tests.test_licence_invite_admin_power
"""

from unittest.mock import patch

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

CANNOT_BE_ADDED = "This email can't be added as a teacher."
OTHER_SCHOOL = "This teacher already belongs to another school."
LICENCE_PASSWORD = "Licence-pass-h203"  # pragma: allowlist secret


class LicenceInviteRoadTests(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="H203 Licence School")
        self.other_school = School.objects.create(name="H203 Other School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H203 Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@h203school.edu",
            password=LICENCE_PASSWORD,
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

    def teacher(self, email, verified=False, active=True, school=None, **flags):
        return CustomUser.objects.create_user(
            email=email,
            password=LICENCE_PASSWORD,
            first_name="Some",
            last_name="Teacher",
            user_type=flags.pop("user_type", UserTypes.TEACHER),
            school=school,
            is_active=active,
            must_change_password=flags.pop("must_change_password", True),
            email_verified_at=timezone.now() if verified else None,
            **flags,
        )

    def invite(self, email, raise_on_conflict=True):
        """Returns (result, mails queued). The invitation mail is queued in
        transaction.on_commit, so the callbacks are captured and run."""
        with self.captureOnCommitCallbacks(execute=True):
            result = LicenseSubscriptionService._get_or_invite_teacher(
                email, self.school, self.admin, raise_on_conflict=raise_on_conflict
            )
        return result, self.mail.delay.call_count

    def add(self, emails):
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(
                reverse(
                    "license-subscription-add-teachers", kwargs={"pk": self.licence.pk}
                ),
                {"teacher_emails": emails},
                format="json",
            )

    def seats(self, teacher):
        return SchoolCreditAllocation.objects.filter(
            license_subscription=self.licence, user=teacher
        ).count()

    def assert_refused_and_untouched(self, account, raise_on_conflict=True):
        before = account.password

        if raise_on_conflict:
            with self.assertRaises(ValueError) as caught:
                self.invite(account.email, raise_on_conflict=True)
            self.assertEqual(str(caught.exception), CANNOT_BE_ADDED)
        else:
            result, _ = self.invite(account.email, raise_on_conflict=False)
            self.assertIsNone(result)

        account.refresh_from_db()
        self.assertEqual(account.password, before)
        self.assertIsNone(account.school_id)
        self.assertEqual(self.mail.delay.call_count, 0)
        self.assertEqual(self.seats(account), 0)

    # -- the refusal -----------------------------------------------------------

    def test_a_never_verified_staff_teacher_is_refused_and_untouched(self):
        self.assert_refused_and_untouched(
            self.teacher("staff.h203@x.edu", is_staff=True)
        )

    def test_a_never_verified_superuser_flag_teacher_is_refused_and_untouched(self):
        self.assert_refused_and_untouched(
            self.teacher("flag.h203@x.edu", is_superuser=True)
        )

    def test_a_command_line_superuser_is_refused_and_untouched(self):
        account = CustomUser.objects.create_superuser(
            email="cmd.h203@x.edu",
            password=LICENCE_PASSWORD,
            must_change_password=True,
        )
        self.assertEqual(account.user_type, UserTypes.TEACHER)

        self.assert_refused_and_untouched(account)

    def test_the_non_raising_path_returns_none_and_writes_nothing(self):
        self.assert_refused_and_untouched(
            self.teacher("quiet.h203@x.edu", is_staff=True), raise_on_conflict=False
        )

    def test_a_switched_off_verified_teacher_is_refused_and_untouched(self):
        self.assert_refused_and_untouched(
            self.teacher("off.h203@x.edu", verified=True, active=False)
        )

    def test_the_refusal_does_not_say_which_school_the_row_belongs_to(self):
        account = self.teacher(
            "elsewhere.h203@x.edu", is_staff=True, school=self.other_school
        )

        with self.assertRaises(ValueError) as caught:
            self.invite(account.email)

        self.assertEqual(str(caught.exception), CANNOT_BE_ADDED)
        self.assertNotIn(OTHER_SCHOOL, str(caught.exception))

    def test_through_the_route_the_row_fails_and_no_seat_or_mail_is_used(self):
        account = self.teacher("route.h203@x.edu", is_staff=True)
        before = account.password

        response = self.add([account.email])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["failed"], 1)
        self.assertEqual(response.data["successful"], 0)
        self.assertEqual(response.data["errors"][0]["error"], CANNOT_BE_ADDED)
        account.refresh_from_db()
        self.assertEqual(account.password, before)
        self.assertEqual(self.seats(account), 0)
        self.assertEqual(self.mail.delay.call_count, 0)

    def test_the_refusal_is_raised_before_any_write_to_the_account(self):
        """_get_or_invite_teacher is atomic, so a write made before the refusal
        would be undone and the end state alone cannot show the ORDER. This
        counts the statements the refused call sent: a SELECT of the row (so the
        capture is not empty) and no UPDATE, INSERT or DELETE on the users table."""
        account = self.teacher("order.h203@x.edu", is_staff=True)
        table = connection.ops.quote_name(CustomUser._meta.db_table)

        with CaptureQueriesContext(connection) as captured:
            with self.assertRaises(ValueError):
                self.invite(account.email)

        on_users = [q["sql"].lstrip().upper() for q in captured if table in q["sql"]]
        reads = [sql for sql in on_users if sql.startswith("SELECT")]
        writes = [
            sql for sql in on_users if sql.startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertGreater(len(reads), 0)
        self.assertEqual(len(writes), 0)

    # -- controls: nothing else changes ---------------------------------------

    def test_an_ordinary_never_signed_in_teacher_still_gets_a_new_password_and_mail(
        self,
    ):
        """Green on the old code too."""
        account = self.teacher("plain.h203@x.edu")
        before = account.password

        result, mails = self.invite(account.email)

        self.assertEqual(result.pk, account.pk)
        account.refresh_from_db()
        self.assertNotEqual(account.password, before)
        self.assertEqual(mails, 1)

    def test_an_ordinary_never_verified_inactive_teacher_is_still_added(self):
        """Green on the old code too: only switched-off VERIFIED rows and
        never-verified admin-power rows are refused."""
        account = self.teacher("pending.h203@x.edu", active=False)

        result, _ = self.invite(account.email)

        self.assertEqual(result.pk, account.pk)

    def test_a_verified_staff_teacher_who_has_onboarded_is_still_added(self):
        """Green on the old code too: a verified, active admin-power account
        is not refused, and with must_change_password False gets no reset."""
        account = self.teacher(
            "onboarded.h203@x.edu",
            verified=True,
            is_staff=True,
            must_change_password=False,
        )
        before = account.password

        result, mails = self.invite(account.email)

        self.assertEqual(result.pk, account.pk)
        account.refresh_from_db()
        self.assertEqual(account.password, before)
        self.assertEqual(mails, 0)

    def test_an_account_that_is_not_a_teacher_gets_the_same_words(self):
        """Green on the old code too: the answer the new refusal borrows."""
        account = self.teacher(
            "student.h203@x.edu",
            user_type=UserTypes.STUDENT,
            must_change_password=False,
        )

        with self.assertRaises(ValueError) as caught:
            self.invite(account.email)

        self.assertEqual(str(caught.exception), CANNOT_BE_ADDED)
