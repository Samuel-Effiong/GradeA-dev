"""H-280: GET /api/v1/schools/<id>/ answers 200 when a teacher or the
school admin has no name.

The view used to run its own response through
SchoolDetailSerializer(data=...).is_valid(raise_exception=True), so a
blank get_full_name() came back as 400 "Teacher name: This field may not
be blank." Now the response is serialized, not validated, and a nameless
teacher or admin shows their email as the name (the same
get_full_name() or email fallback as billing/stripe_service.py).
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, School, Session
from users.models import UserTypes

User = get_user_model()


def make_user(email, user_type, first="", last="", school=None):
    user = User.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        first_name=first,
        last_name=last,
    )
    user.user_type = user_type
    user.is_active = True
    user.school = school
    user.save()
    return user


class SchoolDetailWithBlankNames(APITestCase):
    def setUp(self):
        cache.clear()
        self.superadmin = User.objects.create_superuser(
            email="h280-super@x.test",
            password="password123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
        )
        self.superadmin.user_type = UserTypes.SUPER_ADMIN
        self.superadmin.save()
        self.school = School.objects.create(name="H280 High")
        self.client.force_authenticate(self.superadmin)

    def detail(self):
        return self.client.get(reverse("school-detail", kwargs={"pk": self.school.id}))

    def teachers_of(self, response, session):
        rows = [
            row
            for row in response.data["session_breakdown"]
            if row["session_id"] == str(session.id)
        ]
        self.assertEqual(len(rows), 1)
        return rows[0]["teachers"]

    def test_a_nameless_teacher_in_a_school_session_shows_their_email(self):
        nameless = make_user(
            "h280-nameless@x.test", UserTypes.TEACHER, school=self.school
        )
        session = Session.objects.create(
            name="2026/2027", owner_type="SCHOOL", school=self.school
        )
        Course.objects.create(name="Math", teacher=nameless, session=session)

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        teachers = self.teachers_of(response, session)
        self.assertEqual(
            [(t["teacher_id"], t["teacher_name"]) for t in teachers],
            [(str(nameless.id), "h280-nameless@x.test")],
        )

    def test_a_nameless_teacher_in_an_individual_session_shows_their_email(self):
        nameless = make_user("h280-solo@x.test", UserTypes.TEACHER, school=self.school)
        session = Session.objects.create(
            name="Fall 2026", owner_type="INDIVIDUAL", teacher=nameless
        )

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        teachers = self.teachers_of(response, session)
        self.assertEqual(
            [(t["teacher_id"], t["teacher_name"]) for t in teachers],
            [(str(nameless.id), "h280-solo@x.test")],
        )

    def test_a_whitespace_only_name_counts_as_no_name(self):
        blank = make_user(
            "h280-spaces@x.test", UserTypes.TEACHER, "   ", " ", school=self.school
        )
        session = Session.objects.create(
            name="Spring 2027", owner_type="INDIVIDUAL", teacher=blank
        )

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(
            self.teachers_of(response, session)[0]["teacher_name"], "h280-spaces@x.test"
        )

    def test_a_nameless_admin_shows_their_email(self):
        admin = make_user(
            "h280-admin@x.test", UserTypes.SCHOOL_ADMIN, school=self.school
        )

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["admin_id"], str(admin.id))
        self.assertEqual(response.data["admin_name"], "h280-admin@x.test")
        self.assertEqual(response.data["admin_email"], "h280-admin@x.test")

    def test_named_teachers_and_admin_still_show_their_names(self):
        make_user(
            "h280-named-admin@x.test",
            UserTypes.SCHOOL_ADMIN,
            "Ada",
            "Admin",
            school=self.school,
        )
        named = make_user(
            "h280-named@x.test",
            UserTypes.TEACHER,
            "Grace",
            "Hopper",
            school=self.school,
        )
        session = Session.objects.create(
            name="Winter 2027", owner_type="INDIVIDUAL", teacher=named
        )

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["admin_name"], "Ada Admin")
        self.assertEqual(
            self.teachers_of(response, session)[0]["teacher_name"], "Grace Hopper"
        )

    def test_the_view_does_not_validate_its_own_output(self):
        """A blank school name (allowed in the database, refused by
        SchoolDetailSerializer.school_name as input) still gives 200: the
        response is serialized, not run through is_valid."""
        School.objects.filter(pk=self.school.pk).update(name="")

        response = self.detail()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        self.assertEqual(response.data["school_name"], "")
        self.assertEqual(response.data["id"], str(self.school.id))
