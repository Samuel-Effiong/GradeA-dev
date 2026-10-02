"""
Epic A S7d, QA catalogue section E: licence teacher management.

E1: a request refused as a whole is coded - TEACHER_LIST_EMPTY on both
routes (a string in place of a list included), LICENCE_INACTIVE,
LICENCE_SEATS_EXCEEDED (params as ints; teachers already on the licence
don't count against the seats).

E2: add_teachers lists every teacher: `added` (the successes, which used to
be only a count), `skipped` (TEACHER_ALREADY_ON_LICENCE, which used to be
silent) and `errors`, each coded. Another school's teacher is
TEACHER_IN_OTHER_SCHOOL even with a subscription of their own (H-78, SM
ruling Q1). One savepoint per teacher (Q2): a teacher whose unit fails -
by a real database error, or by an error after the account exists - leaves
no account, no seat and no invitation, and the others are added. Removal
answers every bad id alike (TEACHER_NOT_ON_LICENCE).

Rule 14: no MagicMock. Mail goes to `Sent`, a recording stand-in; the one
patched step is a real function that runs a real failing query or raises.
"""

import logging
import uuid
from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from django.db import connection
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
    UserSubscription,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

PASSWORD = "Str0ng-s7d-licence!"  # pragma: allowlist secret
SENTINEL = "SENTINEL_s7d_licence"


class Sent:
    """Records every send_email_task.delay() (a stand-in, not a MagicMock)."""

    def __init__(self):
        self.calls = []

    def delay(self, *args, **kwargs):
        self.calls.append(kwargs)

    def recipients(self):
        return [addr for call in self.calls for addr in call.get("recipient_list", [])]


@contextmanager
def every_log_line():
    lines = []

    class Keep(logging.Handler):
        def emit(self, record):
            lines.append(f"{record.name} {record.getMessage()} {record.__dict__}")

    handler = Keep(level=logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield lines
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


class LicenceFixture(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="S7d Licence School")
        self.other_school = School.objects.create(name="S7d SECRET Other School")
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="S7d Licence Plan",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
        )
        self.admin = CustomUser.objects.create_user(
            email="admin@s7d-lic.school.edu",
            password=PASSWORD,
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.sent = Sent()
        patcher = patch("billing.license_service.send_email_task", new=self.sent)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.licence = self.make_licence([], max_seats=10)
        self.client.force_authenticate(self.admin)

    def make_licence(self, teachers, max_seats):
        with self.captureOnCommitCallbacks(execute=True):
            licence = LicenseSubscriptionService.create_license_subscription(
                school=self.school,
                plan=self.plan,
                teacher_emails=teachers,
                max_seats=max_seats,
                billing_method=LicenseBillingMethod.OFFLINE,
            )
        self.sent.calls.clear()
        return licence

    def add(self, emails, licence=None):
        url = reverse(
            "license-subscription-add-teachers",
            kwargs={"pk": (licence or self.licence).pk},
        )
        with self.captureOnCommitCallbacks(execute=True):
            return self.client.post(url, {"teacher_emails": emails}, format="json")

    def remove(self, ids):
        url = reverse(
            "license-subscription-remove-teachers", kwargs={"pk": self.licence.pk}
        )
        return self.client.post(url, {"teacher_ids": ids}, format="json")

    def on_licence(self, licence=None):
        return set(
            SchoolCreditAllocation.objects.filter(
                license_subscription=licence or self.licence,
                is_active=True,
                is_admin_allocation=False,
            ).values_list("user__email", flat=True)
        )

    def teacher(self, email, school=None, **fields):
        return CustomUser.objects.create_user(
            email=email,
            password=PASSWORD,
            user_type=fields.pop("user_type", UserTypes.TEACHER),
            school=school,
            **fields,
        )

    def subscribe(self, user):
        individual = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="S7d Individual",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            monthly_credits=5000,
        )
        UserSubscription.objects.create(
            user=user,
            plan=individual,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
        )


class WholeRequestTests(LicenceFixture):
    def envelope(self, response):
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        return response.json()["error"]["field_errors"]

    def test_no_teachers_on_either_route(self):
        for bad in ([], "t@s7d-lic.school.edu"):
            with self.subTest(route="add", given=bad):
                envelope = self.envelope(self.add(bad))
                self.assertEqual(envelope["reason_code"], "TEACHER_LIST_EMPTY")
                self.assertEqual(envelope["error"], "Add at least one teacher.")
                self.assertEqual(
                    envelope["remediation"], "Enter the teachers' email addresses."
                )
            with self.subTest(route="remove", given=bad):
                envelope = self.envelope(self.remove(bad))
                self.assertEqual(envelope["reason_code"], "TEACHER_LIST_EMPTY")
                self.assertEqual(
                    envelope["remediation"], "Choose the teachers to remove."
                )
        self.assertFalse(CustomUser.objects.filter(email__startswith="t@").exists())

    def test_an_inactive_licence(self):
        self.licence.is_active = False
        self.licence.save(update_fields=["is_active"])

        envelope = self.envelope(self.add(["new@s7d-lic.school.edu"]))

        self.assertEqual(envelope["reason_code"], "LICENCE_INACTIVE")
        self.assertEqual(envelope["remediation"], "Renew the licence, or contact us.")

    def test_teachers_already_on_the_licence_need_no_seat(self):
        licence = self.make_licence(["t1@s7d-lic.school.edu"], max_seats=2)

        response = self.add(
            ["t1@s7d-lic.school.edu", "t2@s7d-lic.school.edu"], licence=licence
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()["data"]
        self.assertEqual(
            [a["teacher_email"] for a in data["added"]], ["t2@s7d-lic.school.edu"]
        )
        [skipped] = data["skipped"]
        self.assertEqual(skipped["reason_code"], "TEACHER_ALREADY_ON_LICENCE")
        self.assertEqual(skipped["status"], "skipped")
        self.assertEqual(skipped["params"], {"email": "t1@s7d-lic.school.edu"})
        self.assertEqual(
            skipped["message"], "t1@s7d-lic.school.edu is already on this licence."
        )
        self.assertIsNone(skipped["remediation"])

    def test_the_seat_refusal_writes_and_sends_nothing(self):
        licence = self.make_licence(["t1@s7d-lic.school.edu"], max_seats=1)

        response = self.add(["new@s7d-lic.school.edu"], licence=licence)

        envelope = self.envelope(response)
        self.assertEqual(envelope["reason_code"], "LICENCE_SEATS_EXCEEDED")
        self.assertEqual(
            envelope["params"],
            {"remaining": 0, "adding": 1, "in_use": 1, "max_seats": 1},
        )
        self.assertEqual(
            response.json()["message"],
            "Your licence has no seats left (1 of 1 in use).",
        )
        self.assertFalse(
            CustomUser.objects.filter(email="new@s7d-lic.school.edu").exists()
        )
        self.assertEqual(self.sent.calls, [])


class PerTeacherCodeTests(LicenceFixture):
    def errors_by_email(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return {e["teacher_email"]: e for e in response.json()["data"]["errors"]}

    def assert_error(self, entry, code, message, params):
        self.assertEqual(entry["reason_code"], code)
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["message"], message)
        self.assertEqual(entry["error"], message)
        self.assertEqual(entry["params"], params)
        self.assertEqual(entry["error_class"], "USER")

    def test_the_successes_are_listed(self):
        response = self.add(["a@s7d-lic.school.edu", "b@s7d-lic.school.edu"])

        data = response.json()["data"]
        self.assertEqual((data["successful"], data["failed"]), (2, 0))
        self.assertEqual(
            [(a["teacher_email"], a["status"]) for a in data["added"]],
            [("a@s7d-lic.school.edu", "added"), ("b@s7d-lic.school.edu", "added")],
        )
        ids = {
            str(u.id)
            for u in CustomUser.objects.filter(
                email__in=["a@s7d-lic.school.edu", "b@s7d-lic.school.edu"]
            )
        }
        self.assertEqual({a["teacher_id"] for a in data["added"]}, ids)

    def test_each_refusal_has_its_code(self):
        self.teacher("pupil@s7d-lic.school.edu", user_type=UserTypes.STUDENT)
        self.teacher("far@s7d-lic.school.edu", school=self.other_school)
        payer = self.teacher("payer@s7d-lic.school.edu")
        self.subscribe(payer)

        errors = self.errors_by_email(
            self.add(
                [
                    "someone@gmail.com",
                    "pupil@s7d-lic.school.edu",
                    "far@s7d-lic.school.edu",
                    "payer@s7d-lic.school.edu",
                ]
            )
        )

        self.assert_error(
            errors["someone@gmail.com"],
            "TEACHER_EMAIL_NOT_BUSINESS",
            "someone@gmail.com isn't a school or work email address.",
            {"email": "someone@gmail.com"},
        )
        self.assert_error(
            errors["pupil@s7d-lic.school.edu"],
            "TEACHER_EMAIL_OTHER_ROLE",
            "This email can't be added as a teacher.",
            {},
        )
        self.assert_error(
            errors["far@s7d-lic.school.edu"],
            "TEACHER_IN_OTHER_SCHOOL",
            "This teacher already belongs to another school.",
            {},
        )
        self.assert_error(
            errors["payer@s7d-lic.school.edu"],
            "TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION",
            "payer@s7d-lic.school.edu has their own subscription, which must be "
            "cancelled before they can join the licence.",
            {"email": "payer@s7d-lic.school.edu"},
        )
        self.assertNotIn("SECRET", str(errors))
        self.assertNotIn("student", str(errors["pupil@s7d-lic.school.edu"]).lower())
        self.assertEqual(self.on_licence(), set())

    def test_another_schools_paying_teacher_shows_no_billing_hint(self):
        """SM ruling Q1 / H-78: the school check comes first."""
        far = self.teacher("far-payer@s7d-lic.school.edu", school=self.other_school)
        self.subscribe(far)

        entry = self.errors_by_email(self.add([far.email]))[far.email]

        self.assertEqual(entry["reason_code"], "TEACHER_IN_OTHER_SCHOOL")
        body = str(entry).replace(far.email, "")
        for word in ("subscription", "billing", "cancel", "SECRET"):
            self.assertNotIn(word, body)

    def test_no_refusal_logs_the_address(self):
        """SM condition (b): the sync-only email params reach the requester
        only - never a log line."""
        payer = self.teacher("payer-log@s7d-lic.school.edu")
        self.subscribe(payer)
        licence = self.make_licence(["on@s7d-lic.school.edu"], max_seats=10)

        with every_log_line() as lines:
            self.add(
                ["nolog@gmail.com", payer.email, "on@s7d-lic.school.edu"],
                licence=licence,
            )

        text = "\n".join(lines)
        for address in ("nolog@gmail.com", payer.email, "on@s7d-lic.school.edu"):
            self.assertNotIn(address, text)


class OneSavepointPerTeacherTests(LicenceFixture):
    """SM ruling Q2, with both kinds of failure v2 asked for."""

    EMAILS = ["t1@s7d-lic.school.edu", "t2@s7d-lic.school.edu", "t3@s7d-lic.school.edu"]

    def assert_only_the_middle_teacher_failed(self, response):
        t1, t2, t3 = self.EMAILS
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()["data"]
        self.assertEqual(self.on_licence(), {t1, t3})
        self.assertFalse(CustomUser.objects.filter(email=t2).exists())
        self.assertEqual(sorted(self.sent.recipients()), [t1, t3])
        [error] = data["errors"]
        self.assertEqual(error["teacher_email"], t2)
        self.assertEqual(error["reason_code"], "TEACHER_ADD_FAILED")
        self.assertEqual(error["message"], "We couldn't add this teacher.")
        self.assertIs(error["retryable"], True)
        for leak in (SENTINEL, "division", "DataError", "RuntimeError", "Traceback"):
            self.assertNotIn(leak, response.content.decode())

    def failing_for(self, target, fail):
        real = LicenseSubscriptionService._enroll_teacher_internal

        def enroll(license_sub, teacher, *args, **kwargs):
            if teacher.email == target:
                fail()
            return real(license_sub, teacher, *args, **kwargs)

        return patch.object(
            LicenseSubscriptionService,
            "_enroll_teacher_internal",
            new=staticmethod(enroll),
        )

    def test_a_database_error_in_one_teachers_seat(self):
        def division_by_zero():
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT 1/0 AS {SENTINEL}")

        with self.failing_for(self.EMAILS[1], division_by_zero):
            response = self.add(self.EMAILS)

        self.assert_only_the_middle_teacher_failed(response)

    def test_an_error_after_the_account_exists(self):
        def boom():
            raise RuntimeError(SENTINEL)

        with self.failing_for(self.EMAILS[1], boom):
            response = self.add(self.EMAILS)

        self.assert_only_the_middle_teacher_failed(response)


class RemovalTests(LicenceFixture):
    def test_every_bad_id_answers_alike_and_the_good_one_is_removed(self):
        self.add(["good@s7d-lic.school.edu"])
        good = CustomUser.objects.get(email="good@s7d-lic.school.edu")
        stranger = self.teacher("x@s7d-lic.school.edu", school=self.other_school)
        student = self.teacher("s@s7d-lic.school.edu", user_type=UserTypes.STUDENT)
        idle = self.teacher("idle@s7d-lic.school.edu", school=self.school)
        bad = [
            str(uuid.uuid4()),
            str(stranger.id),
            str(student.id),
            str(idle.id),
            "not-a-uuid",
        ]

        response = self.remove(bad + [str(good.id)])

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        data = response.json()["data"]
        self.assertEqual((data["successful"], data["failed"]), (1, len(bad)))
        self.assertEqual(
            data["removed"], [{"teacher_id": str(good.id), "status": "removed"}]
        )
        self.assertNotIn("good@s7d-lic.school.edu", self.on_licence())
        items = [
            {k: v for k, v in entry.items() if k not in ("teacher_id", "reference")}
            for entry in data["errors"]
        ]
        self.assertEqual([e["teacher_id"] for e in data["errors"]], bad)
        for item in items[1:]:
            self.assertEqual(item, items[0])
        self.assertEqual(items[0]["reason_code"], "TEACHER_NOT_ON_LICENCE")
        self.assertEqual(
            items[0]["message"], "This teacher isn't an active teacher on this licence."
        )
