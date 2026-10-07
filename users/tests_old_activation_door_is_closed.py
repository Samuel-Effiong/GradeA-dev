"""
H-152: the old activation door is closed.

`POST /auth/register/student` let a student who held a 6-digit code set a
password AND type their own name. Since the temporary-password scheme
nothing mints such a code; what is left are invitations sent before it.
User's decision (2026-10-07): the door is closed outright. A student does
not name themselves, and the accounts that still hold a code are converted
by a one-off command (classrooms: backfill_pending_student_invites), or
healed when their teacher adds them again by email.

Both routes of the old scheme, the door and the renewal of a code
(`POST /course/student/renew-student-token`), now answer every request the
same way: 410, one fixed sentence. A valid code, an expired one, a wrong
one and none at all cannot be told apart, nothing is written, nothing is
mailed, and the budget for wrong codes is not spent, since nothing can be
guessed any more.

Run with:
    python manage.py test users.tests_old_activation_door_is_closed
"""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes
from users.throttling import register_student_failure_budget_spent

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
CALLER_PASSWORD = "Caller-Chosen-Pw-77"  # pragma: allowlist secret
CLOSED = (
    "Invitations of this kind are no longer used. "
    "Ask your teacher to add you to the class again."
)
CODE = "123456"


@override_settings(CACHES=LOCMEM)
class ClosedDoorBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        # The per-address rate limits are not under test.
        from classrooms.views import CourseViewSet
        from users.views import AuthViewSet

        for viewset in (AuthViewSet, CourseViewSet):
            patcher = patch.object(viewset, "get_throttles", return_value=[])
            patcher.start()
            self.addCleanup(patcher.stop)
        mail = patch("classrooms.services.notifications.safe_delay")
        self.mail = mail.start()
        self.addCleanup(mail.stop)

        self.teacher = User.objects.create_user(
            email="owner@h152.test",
            password=CALLER_PASSWORD,
            first_name="Grace",
            last_name="Hopper",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        session = Session.objects.create(name="H152", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H152 course", teacher=self.teacher, session=session
        )

    def leftover(self, email="left.over@h152.test", *, expired=False, **names):
        """An invitation of the old scheme: switched off, holding a code,
        with a pending place in the course. The old scheme stored no name;
        the student typed it at the door."""
        delta = timedelta(hours=-1) if expired else timedelta(hours=23)
        student = User.objects.create_user(
            email=email,
            password=None,
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token=CODE,
            activation_expires=timezone.now() + delta,
            **names,
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        return student

    def knock(self, **body):
        return self.client.post(reverse("auth-register-student"), body, format="json")

    def renew(self, **body):
        return self.client.post(
            reverse("course-renew-activation-token"), body, format="json"
        )

    def snapshot(self, student):
        row = User.objects.get(pk=student.pk)
        return (
            row.is_active,
            row.activation_token,
            row.activation_expires,
            row.first_name,
            row.middle_name,
            row.last_name,
            row.password,
            row.email_verified_at,
            bool(row.profile_image),
        )

    def assert_closed(self, response):
        self.assertEqual(response.status_code, status.HTTP_410_GONE, response.content)
        self.assertIn(CLOSED, response.content.decode())


class TheDoorTest(ClosedDoorBase):
    def test_a_valid_code_no_longer_opens_it(self):
        student = self.leftover()
        before = self.snapshot(student)

        response = self.knock(
            token=CODE,
            first_name="Caller",
            last_name="Chosen",
            password=CALLER_PASSWORD,
        )

        self.assert_closed(response)
        # Not switched on, not named, no password, the code not spent.
        self.assertEqual(self.snapshot(student), before)
        self.assertFalse(User.objects.get(pk=student.pk).is_active)

    def test_every_knock_gets_the_same_answer_byte_for_byte(self):
        self.leftover()
        self.leftover("ex.pired@h152.test", expired=True)
        valid = self.knock(
            token=CODE,
            first_name="Caller",
            last_name="Chosen",
            password=CALLER_PASSWORD,
        )
        self.assert_closed(valid)
        others = {
            "a wrong code": self.knock(
                token="999999",
                first_name="Caller",
                last_name="Chosen",
                password=CALLER_PASSWORD,
            ),
            "a code alone": self.knock(token=CODE),
            "nothing at all": self.knock(),
        }
        for label, response in others.items():
            with self.subTest(knock=label):
                self.assertEqual(response.status_code, valid.status_code)
                self.assertEqual(response.content, valid.content)

    def test_an_expired_code_is_not_offered_a_renewal(self):
        self.leftover(expired=True)
        response = self.knock(
            token=CODE,
            first_name="Caller",
            last_name="Chosen",
            password=CALLER_PASSWORD,
        )
        self.assert_closed(response)
        self.assertNotIn("renew", response.content.decode().lower())
        self.assertNotIn(CODE, response.content.decode())

    def test_wrong_codes_spend_no_budget_and_never_pause_the_door(self):
        for guess in range(40):
            self.assert_closed(self.knock(token=f"9{guess:05d}"))
        self.assertFalse(register_student_failure_budget_spent())
        self.assert_closed(self.knock(token=CODE))

    def test_the_answer_names_no_course_and_no_classmate(self):
        self.leftover()
        mate = User.objects.create_user(
            email="class.mate@h152.test",
            password=CALLER_PASSWORD,
            first_name="Ada",
            last_name="Lovelace",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        StudentCourse.objects.create(
            student=mate,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        response = self.knock(
            token=CODE, first_name="Ada", last_name="Lovelace", password=CALLER_PASSWORD
        )
        self.assert_closed(response)
        text = response.content.decode()
        self.assertNotIn("H152 course", text)
        self.assertNotIn("exact name", text)
        self.assertNotIn("Lovelace", text)


class TheRenewalTest(ClosedDoorBase):
    def test_an_expired_code_is_not_renewed_and_nobody_is_mailed(self):
        student = self.leftover(expired=True)
        before = self.snapshot(student)

        response = self.renew(token=CODE)

        self.assert_closed(response)
        self.assertEqual(self.snapshot(student), before)
        self.mail.assert_not_called()

    def test_every_request_gets_the_same_answer_byte_for_byte(self):
        self.leftover(expired=True)
        known = self.renew(token=CODE)
        self.assert_closed(known)
        for label, response in {
            "a wrong code": self.renew(token="999999"),
            "nothing at all": self.renew(),
        }.items():
            with self.subTest(request=label):
                self.assertEqual(response.status_code, known.status_code)
                self.assertEqual(response.content, known.content)
        self.assertFalse(register_student_failure_budget_spent())


class TheConversionCommandCountsTheNamelessTest(ClosedDoorBase):
    """The one-off command that converts what is left of the old scheme
    (run by the founder, never by the team, on live data). A converted
    account is switched on with whatever name it has, and the old scheme
    stored none: the dry run now says how many of those it would convert
    have no name, because each of them must then be named by a teacher."""

    def run_command(self, *args):
        out = StringIO()
        call_command("backfill_pending_student_invites", *args, stdout=out)
        return out.getvalue()

    def test_the_dry_run_counts_the_accounts_with_no_name(self):
        self.leftover("no.name.one@h152.test")
        self.leftover("no.name.two@h152.test")
        self.leftover("has.name@h152.test", first_name="Ada", last_name="Lovelace")

        text = self.run_command("--dry-run")

        self.assertIn("3 converted", text)
        self.assertIn("2 of the converted have no name", text)
        # A dry run changes nothing and mails nobody.
        self.assertEqual(User.objects.filter(is_active=False).count(), 3)
        self.mail.assert_not_called()

    def test_the_real_run_reports_the_same_count(self):
        self.leftover("no.name.one@h152.test")
        self.leftover("has.name@h152.test", first_name="Ada", last_name="Lovelace")

        text = self.run_command()

        self.assertIn("2 converted", text)
        self.assertIn("1 of the converted have no name", text)

    def test_the_output_still_carries_ids_only(self):
        self.leftover("no.name.one@h152.test")
        self.leftover("has.name@h152.test", first_name="Ada", last_name="Lovelace")
        for args in (["--dry-run"], []):
            text = self.run_command(*args)
            self.assertNotIn("@", text, args)
            self.assertNotIn("Lovelace", text, args)
