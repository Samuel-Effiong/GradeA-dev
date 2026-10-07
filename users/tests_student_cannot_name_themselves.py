"""
H-148: a student does not name themselves.

Founder's rule (2026-10-06): only the teacher names a student.

`PATCH /users/<own id>` has refused a student's change of first, middle or
last name since 2026-07-09 (CustomUserSerializer.validate), and nothing
tested it. These tests are that rule's first. They change no behaviour:
every one of them is expected to pass on the code as it was, so each is
shown able to fail by a mutant of this row, not by a first red run.

What the rule looks at is WHOSE account is edited, not who asks: a super
admin editing a student's name is refused too. So a teacher's naming of a
student cannot go through this serializer as it stands; it goes through
the course's add-by-email route (classrooms/tests_teacher_names_student_on_add.py).

Also pinned: signing in with Google never writes a name on an account that
already exists.

Real JWTs, real endpoints.

Run with:
    python manage.py test users.tests_student_cannot_name_themselves
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase
from rest_framework.throttling import SimpleRateThrottle

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import CustomUser, UserTypes
from users.tests_patch_password import LOCMEM, WIDE, make
from users.tokens import EpochRefreshToken

REFUSAL = "Students are not allowed to edit their name."
NAMES = ("first_name", "middle_name", "last_name")


@override_settings(CACHES=LOCMEM)
class NameBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        rates = patch.dict(SimpleRateThrottle.THROTTLE_RATES, WIDE)
        rates.start()
        self.addCleanup(rates.stop)
        self.student = make("stu.dent@gmail.com", UserTypes.STUDENT)
        CustomUser.objects.filter(pk=self.student.pk).update(
            first_name="Ada", middle_name="King", last_name="Lovelace"
        )
        self.teacher = make("teach.er@gmail.com")
        # The student is this teacher's: the teacher can READ the account,
        # so what refuses the teacher's edit below is the edit rule itself
        # and not the account being out of sight.
        session = Session.objects.create(name="S", teacher=self.teacher)
        course = Course.objects.create(name="C", teacher=self.teacher, session=session)
        StudentCourse.objects.create(
            student=self.student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.superadmin = make(
            "root@example.com",
            UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_staff=True,
        )

    def client_for(self, user):
        client = APIClient()
        token = EpochRefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    def patch_account(self, actor, target, **body):
        return self.client_for(actor).patch(
            reverse("user-detail", kwargs={"pk": target.pk}), body, format="json"
        )

    def stored(self, user):
        row = CustomUser.objects.get(pk=user.pk)
        return (row.first_name, row.middle_name, row.last_name)

    def assert_refused(self, response, field):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        body = response.json()
        self.assertIs(body["success"], False)
        self.assertEqual(body["error"]["field_errors"][field], [REFUSAL])


class AStudentCannotChangeTheirNameTest(NameBase):
    def test_each_of_the_three_names_is_refused_and_nothing_changes(self):
        for field in NAMES:
            with self.subTest(field=field):
                response = self.patch_account(
                    self.student, self.student, **{field: "Changed"}
                )
                self.assert_refused(response, field)
                self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))

    def test_a_name_cannot_be_emptied_either(self):
        response = self.patch_account(self.student, self.student, middle_name="")
        self.assert_refused(response, "middle_name")
        self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))

    def test_a_student_with_no_name_cannot_give_themselves_one(self):
        """The state of a student added by email only, before this row."""
        CustomUser.objects.filter(pk=self.student.pk).update(
            first_name="", middle_name="", last_name=""
        )
        response = self.patch_account(
            self.student, self.student, first_name="Ada", last_name="Lovelace"
        )
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(self.stored(self.student), ("", "", ""))

    def test_asking_to_become_a_teacher_in_the_same_request_does_not_help(self):
        """The rule reads the account's type; a student cannot send one."""
        response = self.patch_account(
            self.student, self.student, user_type="TEACHER", first_name="Changed"
        )
        self.assert_refused(response, "first_name")
        row = CustomUser.objects.get(pk=self.student.pk)
        self.assertEqual(row.user_type, UserTypes.STUDENT)
        self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))

    def test_the_whole_account_sent_back_unchanged_is_accepted(self):
        """Pages send the whole object; an unchanged name is no change."""
        response = self.patch_account(
            self.student,
            self.student,
            first_name="Ada",
            middle_name="King",
            last_name="Lovelace",
            bio="I like engines.",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        row = CustomUser.objects.get(pk=self.student.pk)
        self.assertEqual(row.bio, "I like engines.")
        self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))


class WhoseAccountNotWhoAsksTest(NameBase):
    def test_a_super_admin_is_refused_on_a_students_account_too(self):
        response = self.patch_account(
            self.superadmin, self.student, first_name="Changed"
        )
        self.assert_refused(response, "first_name")
        self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))

    def test_a_teacher_cannot_edit_their_students_account_at_all(self):
        response = self.patch_account(self.teacher, self.student, bio="Changed")
        self.assertEqual(
            response.status_code, status.HTTP_403_FORBIDDEN, response.content
        )
        self.assertNotEqual(CustomUser.objects.get(pk=self.student.pk).bio, "Changed")
        response = self.patch_account(self.teacher, self.student, first_name="Changed")
        self.assertEqual(
            response.status_code, status.HTTP_403_FORBIDDEN, response.content
        )
        self.assertEqual(self.stored(self.student), ("Ada", "King", "Lovelace"))

    def test_a_teacher_still_changes_their_own_name(self):
        response = self.patch_account(self.teacher, self.teacher, first_name="Changed")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.stored(self.teacher)[0], "Changed")

    def test_a_super_admin_still_changes_their_own_name(self):
        response = self.patch_account(
            self.superadmin, self.superadmin, last_name="Changed"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.stored(self.superadmin)[2], "Changed")


@override_settings(CACHES=LOCMEM)
class GoogleSignInWritesNoNameTest(APITestCase):
    """An unknown address becomes a teacher with Google's names; an account
    that exists keeps the names it has."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def sign_in(self, email):
        with (
            patch("requests.post") as posted,
            patch("users.views.id_token.verify_oauth2_token") as verified,
        ):
            posted.return_value.raise_for_status.return_value = None
            posted.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_in": 3600,
            }
            verified.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Google",
                "middle_name": "Given",
                "family_name": "Name",
            }
            return self.client.post(
                reverse("auth-google-auth"), {"code": "oauth-code"}, format="json"
            )

    def names(self, email):
        row = CustomUser.objects.get(email=email)
        return (row.first_name, row.middle_name, row.last_name)

    def test_a_student_with_a_name_keeps_it(self):
        student = make("named.student@gmail.com", UserTypes.STUDENT)
        CustomUser.objects.filter(pk=student.pk).update(
            first_name="Ada", middle_name="", last_name="Lovelace"
        )

        response = self.sign_in(student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.names(student.email), ("Ada", "", "Lovelace"))

    def test_a_student_with_no_name_is_not_named_by_google(self):
        student = make("nameless.student@gmail.com", UserTypes.STUDENT)
        CustomUser.objects.filter(pk=student.pk).update(
            first_name="", middle_name="", last_name=""
        )

        response = self.sign_in(student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.names(student.email), ("", "", ""))

    def test_a_student_who_never_activated_is_let_in_and_keeps_the_stored_name(self):
        student = make(
            "pending.student@gmail.com",
            UserTypes.STUDENT,
            is_active=False,
            email_verified_at=None,
        )
        CustomUser.objects.filter(pk=student.pk).update(
            first_name="Ada", middle_name="", last_name="Lovelace"
        )

        response = self.sign_in(student.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(CustomUser.objects.get(pk=student.pk).is_active)
        self.assertEqual(self.names(student.email), ("Ada", "", "Lovelace"))
