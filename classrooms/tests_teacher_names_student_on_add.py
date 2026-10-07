"""
H-148: the teacher names a student when adding them by email.

Founder's rule (2026-10-06): a student does not name themselves; only the
teacher names a student. Decision of 2026-10-07 (option 1): the add-by-email
form takes first name and last name, required, typed by the teacher; middle
name optional.

Before this row the form took an email only and a new account was created
with an EMPTY name. A student cannot change their own name (the account
edit has refused that since July), and no teacher route set one: a student
added by email had no name and no way to one. A teacher-uploaded paper is
matched to a student by name, so such a student's paper matched nobody.

Rules held here (Senior Manager, 2026-10-07):
  * first and last name are required, at least two letters each;
  * a NEW account carries the typed name, and its invitation greets by it;
  * an address that already has a student account: the typed name FILLS an
    empty stored name and never replaces a non-empty one; the answer tells
    the teacher which name stands;
  * the one-exact-name-per-course rule applies to the typed or filled
    name, and its refusal goes to the teacher only;
  * a refusal for a staff, switched-off or already-enrolled address does
    not depend on the name typed and shows no name;
  * the invitation no longer says the student will be asked to choose a
    password (the server never enforced it).

Run with:
    python manage.py test classrooms.tests_teacher_names_student_on_add
"""

import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from classrooms.services import NOT_A_STUDENT_MESSAGE
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PROBE_PASSWORD = "Str0ng-h148-pass!"  # pragma: allowlist secret
INVITATION_SUBJECT = "Your account is ready. Log in and join your class"
NEW = "new.student@h148.test"
ADA = {"first_name": "Ada", "last_name": "Lovelace"}


def make_user(email, user_type, **fields):
    user = User.objects.create_user(email=email, password=PROBE_PASSWORD)
    user.user_type = user_type
    user.is_active = True
    for name, value in fields.items():
        setattr(user, name, value)
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class AddByEmailBase(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = make_user(
            "owner@h148.test", UserTypes.TEACHER, first_name="Grace", last_name="Hopper"
        )
        session = Session.objects.create(name="H148", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H148 course", teacher=self.teacher, session=session
        )
        mail = patch("classrooms.services.notifications.safe_delay")
        self.mail = mail.start()
        self.addCleanup(mail.stop)

    def add(self, email, **names):
        cache.clear()
        self.client.force_authenticate(self.teacher)
        return self.client.post(
            reverse("course-students", kwargs={"pk": self.course.id}),
            {"email": email, **names},
            format="json",
        )

    def account(self, email):
        return User.objects.filter(email=email).first()

    def name_of(self, email):
        user = self.account(email)
        return (user.first_name, user.middle_name, user.last_name)

    def enrolled(self, email, status_=None):
        rows = StudentCourse.objects.filter(student__email=email, course=self.course)
        if status_ is not None:
            rows = rows.filter(enrollment_status=status_)
        return rows.exists()

    def invitations(self):
        return [
            call.kwargs
            for call in self.mail.call_args_list
            if call.kwargs.get("subject") == INVITATION_SUBJECT
        ]

    def classmate(self, **names):
        """A student already in the course, with a name."""
        student = make_user("class.mate@h148.test", UserTypes.STUDENT, **names)
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return student

    def existing(self, email="has.account@h148.test", *, signed_in=True, **names):
        """A student account that exists and is in no course of this
        teacher."""
        fields = dict(names)
        if signed_in:
            fields["last_login"] = timezone.now()
        return make_user(email, UserTypes.STUDENT, **fields)


class TheNameIsRequiredTest(AddByEmailBase):
    def assert_refused_for(self, response, field):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn(field, json.dumps(response.json()))
        # Nothing was made.
        self.assertIsNone(self.account(NEW))
        self.assertEqual(self.invitations(), [])

    def test_an_email_alone_is_refused(self):
        response = self.add(NEW)
        self.assert_refused_for(response, "first_name")
        self.assertIn("last_name", json.dumps(response.json()))

    def test_each_of_the_two_names_is_required(self):
        self.assert_refused_for(self.add(NEW, last_name="Lovelace"), "first_name")
        self.assert_refused_for(self.add(NEW, first_name="Ada"), "last_name")

    def test_a_blank_name_is_refused(self):
        self.assert_refused_for(
            self.add(NEW, first_name="   ", last_name="Lovelace"), "first_name"
        )

    def test_a_name_of_one_letter_is_refused(self):
        """The direct add's rule: at least two letters each."""
        self.assert_refused_for(
            self.add(NEW, first_name="A", last_name="Lovelace"), "first_name"
        )
        self.assert_refused_for(
            self.add(NEW, first_name="Ada", last_name="L"), "last_name"
        )

    def test_the_middle_name_is_optional(self):
        response = self.add(NEW, **ADA)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.name_of(NEW), ("Ada", "", "Lovelace"))


class ANewAddressTest(AddByEmailBase):
    def test_the_account_carries_the_typed_name(self):
        response = self.add(
            NEW, first_name="Ada", middle_name="King", last_name="Lovelace"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertIs(response.data["is_new_student"], True)
        self.assertEqual(self.name_of(NEW), ("Ada", "King", "Lovelace"))
        self.assertTrue(self.enrolled(NEW, EnrollmentStatusType.PENDING))

    def test_the_typed_name_is_trimmed(self):
        self.add(NEW, first_name="  Ada ", last_name=" Lovelace  ")
        self.assertEqual(self.name_of(NEW), ("Ada", "", "Lovelace"))

    def test_the_answer_says_which_name_stands(self):
        response = self.add(NEW, **ADA)
        self.assertEqual(
            response.data["student_name"],
            {"first_name": "Ada", "middle_name": "", "last_name": "Lovelace"},
        )
        self.assertIs(response.data["typed_name_used"], True)

    def test_the_invitation_greets_by_the_typed_name(self):
        self.add(NEW, **ADA)
        (invitation,) = self.invitations()
        self.assertEqual(invitation["recipient_list"], [NEW])
        self.assertEqual(invitation["merge_data"]["name"], "Ada Lovelace")

    def test_the_invitation_does_not_promise_a_password_change(self):
        self.add(NEW, **ADA)
        (invitation,) = self.invitations()
        text = invitation["merge_data"]["top_content"]
        self.assertNotIn("choose your own password", text)
        self.assertNotIn("asked to", text)
        # What it still says: who invited, to what, and the way in.
        self.assertIn("Grace Hopper has invited you to join H148 course", text)
        self.assertIn("temporary password: ", text)


class TheNameClashRuleTest(AddByEmailBase):
    """One exact name per course, as on the direct add."""

    def assert_clash(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertIn("already enrolled in this course", json.dumps(response.json()))
        self.assertEqual(self.invitations(), [])

    def test_a_new_address_with_a_classmates_exact_name_is_refused(self):
        self.classmate(**ADA)

        response = self.add(NEW, first_name="ada", last_name="LOVELACE")

        self.assert_clash(response)
        # No account is left behind for the address, and no enrolment.
        self.assertIsNone(self.account(NEW))
        self.assertFalse(self.enrolled(NEW))

    def test_a_different_middle_name_is_a_different_name(self):
        self.classmate(**ADA)
        response = self.add(
            NEW, first_name="Ada", middle_name="King", last_name="Lovelace"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)

    def test_a_filled_name_that_clashes_is_refused_and_nothing_is_filled(self):
        self.classmate(**ADA)
        nameless = self.existing()

        response = self.add(nameless.email, **ADA)

        self.assert_clash(response)
        self.assertEqual(self.name_of(nameless.email), ("", "", ""))
        self.assertFalse(self.enrolled(nameless.email))


class AnAddressThatAlreadyHasAnAccountTest(AddByEmailBase):
    def test_a_stored_name_stands_and_the_teacher_is_shown_it(self):
        known = self.existing(
            first_name="Augusta", middle_name="Ada", last_name="Byron"
        )

        response = self.add(known.email, **ADA)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertIs(response.data["is_new_student"], False)
        self.assertEqual(self.name_of(known.email), ("Augusta", "Ada", "Byron"))
        self.assertEqual(
            response.data["student_name"],
            {"first_name": "Augusta", "middle_name": "Ada", "last_name": "Byron"},
        )
        self.assertIs(response.data["typed_name_used"], False)
        self.assertTrue(self.enrolled(known.email, EnrollmentStatusType.ENROLLED))

    def test_an_empty_name_is_filled_with_the_typed_one(self):
        nameless = self.existing()
        self.assertEqual(self.name_of(nameless.email), ("", "", ""))

        response = self.add(
            nameless.email, first_name="Ada", middle_name="King", last_name="Lovelace"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.name_of(nameless.email), ("Ada", "King", "Lovelace"))
        self.assertEqual(
            response.data["student_name"],
            {"first_name": "Ada", "middle_name": "King", "last_name": "Lovelace"},
        )
        self.assertIs(response.data["typed_name_used"], True)
        self.assertTrue(self.enrolled(nameless.email, EnrollmentStatusType.ENROLLED))

    def test_half_a_name_is_a_name_and_stands(self):
        """Only an account with NO name at all is filled."""
        half = self.existing(first_name="Augusta")

        response = self.add(half.email, **ADA)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.name_of(half.email), ("Augusta", "", ""))
        self.assertIs(response.data["typed_name_used"], False)

    def test_a_nameless_student_who_never_signed_in_is_filled_and_invited_by_name(self):
        never = self.existing(signed_in=False)

        response = self.add(never.email, **ADA)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertIs(response.data["is_new_student"], True)
        self.assertEqual(self.name_of(never.email), ("Ada", "", "Lovelace"))
        (invitation,) = self.invitations()
        self.assertEqual(invitation["merge_data"]["name"], "Ada Lovelace")
        self.assertTrue(self.enrolled(never.email, EnrollmentStatusType.PENDING))

    def test_a_named_student_who_never_signed_in_keeps_the_stored_name(self):
        never = self.existing(signed_in=False, first_name="Augusta", last_name="Byron")

        response = self.add(never.email, **ADA)

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(self.name_of(never.email), ("Augusta", "", "Byron"))
        self.assertIs(response.data["typed_name_used"], False)
        (invitation,) = self.invitations()
        self.assertEqual(invitation["merge_data"]["name"], "Augusta Byron")


class ARefusalDoesNotDependOnTheNameTest(AddByEmailBase):
    """Typing names against an address must not tell anything about it."""

    def both(self, email, **exact_name):
        """Once with the account's own exact name (or Ada's), once with
        another: the two answers must be the same, byte for byte."""
        first = self.add(email, **(exact_name or ADA))
        second = self.add(email, first_name="Somebody", last_name="Else")
        self.assertEqual(first.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(second.status_code, first.status_code)
        self.assertEqual(second.content, first.content)
        self.assertNotIn("student_name", json.dumps(first.json()))
        return first

    def test_a_staff_address(self):
        staff = make_user(
            "other.teacher@h148.test",
            UserTypes.TEACHER,
            first_name="Ada",
            last_name="Lovelace",
        )
        response = self.both(staff.email)
        self.assertIn(NOT_A_STUDENT_MESSAGE, json.dumps(response.json()))
        self.assertNotIn("Lovelace", json.dumps(response.json()))

    def test_a_switched_off_account(self):
        off = self.existing(first_name="Ada", last_name="Lovelace")
        User.objects.filter(pk=off.pk).update(is_active=False)
        response = self.both(off.email)
        self.assertNotIn("Lovelace", json.dumps(response.json()))
        self.assertEqual(self.name_of(off.email), ("Ada", "", "Lovelace"))

    def test_a_student_already_in_the_course(self):
        mate = self.classmate(first_name="Augusta", last_name="Byron")
        # Typed with the student's own exact name: the answer is still
        # "already enrolled", not the name-clash refusal.
        response = self.both(mate.email, first_name="Augusta", last_name="Byron")
        self.assertNotIn("Byron", json.dumps(response.json()))
        self.assertNotIn("exact name", json.dumps(response.json()))
        self.assertEqual(self.name_of(mate.email), ("Augusta", "", "Byron"))
