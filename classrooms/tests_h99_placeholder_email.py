"""H-99: a student's placeholder address is a server-made key, not a way in.

A student added with no email gets an address in @student.local. It used to
be `first.last<0-9999>`, and the code then looked that address up: a new
student who drew a suffix already in use was given the EXISTING account. A
different school's account was refused; a same-school or no-school account
was attached, so two teachers silently shared one student record.

(a) A generated address never attaches: with no email the student is always
    new, under an address checked to be free, with the unique column as the
    last line if two requests pick the same token.
(b) A caller may not supply an address in the placeholder domain on any add
    route: it would be the same attach, on purpose. The answer is the
    neutral "This email can't be added as a student."
"""

import re
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, Session, StudentCourse
from classrooms.serializers import (
    AddStudentToCourseSerializer,
    DirectAddStudentSerializer,
)
from classrooms.services import (
    NOT_A_STUDENT_MESSAGE,
    PLACEHOLDER_EMAIL_ATTEMPTS,
    EnrollmentError,
    enroll_student_by_email,
    is_placeholder_email,
    new_placeholder_email,
)
from classrooms.tests_support_add_by_email import add_by_email
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PROBE_PASSWORD = "Str0ng-h99-pass!"  # pragma: allowlist secret
GENERATOR = "classrooms.services.new_placeholder_email"
LOOKUP = "classrooms.services.find_account_by_email"
FRESH = "ada.lovelace.0123456789abcdef@student.local"


def make_user(email, user_type):
    user = User.objects.create_user(email=email, password=PROBE_PASSWORD)
    user.user_type = user_type
    user.is_active = True
    user.save()
    return user


@override_settings(CACHES=LOCMEM)
class PlaceholderTestCase(APITestCase):
    """Two unrelated individual teachers, each with a course. Teacher one
    already has a roster-only student, Ada Lovelace, added with no email."""

    def setUp(self):
        cache.clear()
        self.owner = make_user("owner@h99.test", UserTypes.TEACHER)
        self.other = make_user("other@h99.test", UserTypes.TEACHER)
        self.owner_course = self.course_of(self.owner, "Owner course")
        self.other_course = self.course_of(self.other, "Other course")

        response = self.post(
            self.owner,
            "course-direct-add-student",
            self.owner_course,
            {"first_name": "Ada", "last_name": "Lovelace"},
        )
        assert response.status_code < 300, response.content
        self.ada = User.objects.get(first_name="Ada", last_name="Lovelace")
        self.taken = self.ada.email

    def course_of(self, teacher, name):
        session = Session.objects.create(name=f"{name} term", teacher=teacher)
        return Course.objects.create(name=name, teacher=teacher, session=session)

    def post(self, teacher, url_name, course, data):
        cache.clear()
        self.client.force_authenticate(teacher)
        return self.client.post(reverse(url_name, kwargs={"pk": course.id}), data)

    def adas(self):
        return User.objects.filter(first_name="Ada", last_name="Lovelace")

    def assertAdaIsUntouched(self):
        """The first Ada is still only in her own teacher's course."""
        self.assertEqual(
            list(
                StudentCourse.objects.filter(student=self.ada).values_list(
                    "course_id", flat=True
                )
            ),
            [self.owner_course.id],
        )
        self.ada.refresh_from_db()
        self.assertEqual(self.ada.email, self.taken)


class TheGeneratedAddressTests(PlaceholderTestCase):
    def test_it_is_in_the_placeholder_domain_with_a_long_random_token(self):
        self.assertRegex(self.taken, r"^ada\.lovelace\.[0-9a-f]{16}@student\.local$")
        self.assertTrue(is_placeholder_email(self.taken))
        self.assertFalse(self.ada.has_usable_password())

    def test_two_addresses_for_one_name_differ(self):
        made = {new_placeholder_email("Ada", "Lovelace") for _ in range(50)}
        self.assertEqual(len(made), 50)

    def test_the_name_parts_are_folded_and_kept_short(self):
        address = new_placeholder_email("Anne-Marie " * 5, "O'Neill")
        local = address.split("@")[0]
        self.assertRegex(local, r"^[a-z0-9]{1,20}\.oneill\.[0-9a-f]{16}$")

    def test_is_placeholder_email_ignores_case_and_surrounding_spaces(self):
        for address in (
            "x@student.local",
            "X@Student.LOCAL",
            "  x@student.local  ",
        ):
            with self.subTest(address=address):
                self.assertTrue(is_placeholder_email(address))
        for other in ("x@student.local.example.com", "x@example.com", "", None):
            with self.subTest(address=other):
                self.assertFalse(is_placeholder_email(other))


class AGeneratedAddressNeverAttachesTests(PlaceholderTestCase):
    """The collision itself, forced: the generator hands out the address
    the first Ada already has, then a fresh one."""

    def colliding_then_fresh(self):
        return patch(GENERATOR, side_effect=[self.taken, FRESH])

    def assertANewAdaIn(self, course):
        new = self.adas().exclude(pk=self.ada.pk).get()
        self.assertEqual(new.email, FRESH)
        self.assertTrue(
            StudentCourse.objects.filter(student=new, course=course).exists()
        )
        self.assertAdaIsUntouched()
        return new

    def test_direct_add_makes_a_new_student(self):
        with self.colliding_then_fresh():
            response = self.post(
                self.other,
                "course-direct-add-student",
                self.other_course,
                {"first_name": "Ada", "last_name": "Lovelace"},
            )
        self.assertLess(response.status_code, 300, response.content)
        self.assertANewAdaIn(self.other_course)

    def test_a_bulk_row_makes_a_new_student(self):
        with self.colliding_then_fresh():
            response = self.post(
                self.other,
                "course-bulk-add-students",
                self.other_course,
                {"raw_data": "Ada,Lovelace"},
            )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.data["success_count"], 1, response.content)
        self.assertANewAdaIn(self.other_course)

    def test_the_same_teachers_other_course_gets_a_new_student_by_direct_add(self):
        """Direct add never reuses an account, even the teacher's own."""
        second = self.course_of(self.owner, "Owner second course")
        with self.colliding_then_fresh():
            response = self.post(
                self.owner,
                "course-direct-add-student",
                second,
                {"first_name": "Ada", "last_name": "Lovelace"},
            )
        self.assertLess(response.status_code, 300, response.content)
        self.assertANewAdaIn(second)

    def test_two_requests_picking_one_token_end_as_two_students(self):
        """The concurrent double-add: the address looked free to this
        request (the lookup is made blind here), another request has it.
        The unique column refuses the insert and a new address is taken."""
        with self.colliding_then_fresh(), patch(LOOKUP, return_value=None):
            response = self.post(
                self.other,
                "course-direct-add-student",
                self.other_course,
                {"first_name": "Ada", "last_name": "Lovelace"},
            )
        self.assertLess(response.status_code, 300, response.content)
        self.assertANewAdaIn(self.other_course)

    def test_when_every_attempt_collides_nothing_is_attached(self):
        with patch(GENERATOR, return_value=self.taken) as generator:
            direct = self.post(
                self.other,
                "course-direct-add-student",
                self.other_course,
                {"first_name": "Ada", "last_name": "Lovelace"},
            )
            bulk = self.post(
                self.other,
                "course-bulk-add-students",
                self.other_course,
                {"raw_data": "Ada,Lovelace"},
            )
        self.assertEqual(generator.call_count, 2 * PLACEHOLDER_EMAIL_ATTEMPTS)
        # The direct-add view answers a refusal raised while saving with its
        # generic failure; what matters here is that nothing was attached.
        self.assertGreaterEqual(direct.status_code, 400, direct.content)
        self.assertEqual(bulk.data["success_count"], 0, bulk.content)
        self.assertEqual(bulk.data["failure_count"], 1, bulk.content)
        self.assertEqual(self.adas().count(), 1)
        self.assertFalse(
            StudentCourse.objects.filter(course=self.other_course).exists()
        )
        self.assertAdaIsUntouched()


class ACallerSuppliedPlaceholderAddressIsRefusedTests(PlaceholderTestCase):
    def variants(self):
        """The first Ada's own address as a caller might send it, and an
        address in the domain that belongs to nobody."""
        local, _ = self.taken.split("@")
        return (
            self.taken,
            f"{local.upper()}@Student.LOCAL",
            f"  {self.taken}  ",
            "nobody.atall.ffffffffffffffff@student.local",
        )

    def assertNothingWasAdded(self):
        self.assertFalse(
            StudentCourse.objects.filter(course=self.other_course).exists()
        )
        self.assertEqual(
            User.objects.filter(email__iendswith="@student.local").count(), 1
        )
        self.assertAdaIsUntouched()

    def test_single_add_refuses_it(self):
        for address in self.variants():
            with self.subTest(address=address):
                response = self.post(
                    self.other,
                    "course-students",
                    self.other_course,
                    add_by_email(address),
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(NOT_A_STUDENT_MESSAGE, response.content.decode())
                self.assertNothingWasAdded()

    def test_direct_add_refuses_it(self):
        for address in self.variants():
            with self.subTest(address=address):
                response = self.post(
                    self.other,
                    "course-direct-add-student",
                    self.other_course,
                    {"first_name": "Ada", "last_name": "Lovelace", "email": address},
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(NOT_A_STUDENT_MESSAGE, response.content.decode())
                self.assertNothingWasAdded()

    def test_a_bulk_row_refuses_it(self):
        for address in self.variants():
            with self.subTest(address=address):
                response = self.post(
                    self.other,
                    "course-bulk-add-students",
                    self.other_course,
                    {"raw_data": f"Ada,Lovelace,{address}"},
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["failure_count"], 1, response.content)
                self.assertEqual(
                    response.data["results"][0]["error"], NOT_A_STUDENT_MESSAGE
                )
                self.assertNothingWasAdded()

    def test_each_form_refuses_it_before_any_lookup_or_save(self):
        """Isolates the two serializers' own check from the shared service's
        (single add) and from the attach gate (direct add)."""
        for address in self.variants():
            with self.subTest(form="single add", address=address):
                form = AddStudentToCourseSerializer(data=add_by_email(address))
                self.assertFalse(form.is_valid())
                self.assertEqual(form.errors["email"], [NOT_A_STUDENT_MESSAGE])
            with self.subTest(form="direct add", address=address):
                direct = DirectAddStudentSerializer(
                    data={
                        "first_name": "Ada",
                        "last_name": "Lovelace",
                        "email": address,
                    },
                    context={"course": self.other_course},
                )
                self.assertFalse(direct.is_valid())
                self.assertEqual(direct.errors["email"], [NOT_A_STUDENT_MESSAGE])

    def test_the_owner_cannot_use_it_either(self):
        """The rule is about the domain, not about whose student it is: the
        name match is how a teacher re-adds their own roster-only student."""
        second = self.course_of(self.owner, "Owner second course")
        response = self.post(
            self.owner, "course-students", second, add_by_email(self.taken)
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(StudentCourse.objects.filter(course=second).exists())

    def test_the_shared_service_refuses_it_and_logs_ids_only(self):
        with self.assertLogs("classrooms.services.enrollment", "INFO") as logs:
            with self.assertRaises(EnrollmentError) as raised:
                enroll_student_by_email(course=self.other_course, email=self.taken)
        self.assertEqual(str(raised.exception), NOT_A_STUDENT_MESSAGE)
        line = "\n".join(logs.output)
        self.assertIn(str(self.other_course.pk), line)
        self.assertIsNone(re.search(r"\S+@\S+", line))
        self.assertNothingWasAdded()

    def test_a_real_students_address_is_still_added(self):
        """Control: the refusal is for the placeholder domain only."""
        pupil = make_user("pupil@h99.test", UserTypes.STUDENT)
        response = self.post(
            self.other, "course-students", self.other_course, add_by_email(pupil.email)
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertTrue(
            StudentCourse.objects.filter(
                student=pupil, course=self.other_course
            ).exists()
        )
