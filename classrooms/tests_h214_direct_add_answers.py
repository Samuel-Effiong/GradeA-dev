"""
H-214: the direct add answers a refusal with 400, not 500.

`direct_add_student` calls `serializer.save()` inside a `try` that ends in a
blanket `except Exception` answering 500. The serializer's `create` refuses by
raising DRF's own `ValidationError` ("already enrolled", the school gate), so
those refusals came back as "Unable to add student to course" with status 500:
a teacher's mistake looked like a server fault, and the status told a caller
which refusal it was (H-71 removed the same tell for admin addresses).

DRF's own exceptions (`APIException` and its children) pass through the view's
`try`; any other exception still answers 500.

Run with:
    python manage.py test classrooms.tests_h214_direct_add_answers
"""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.exceptions import PermissionDenied
from rest_framework.test import APITestCase

from classrooms.models import Course, School, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
PASSWORD = "Pass-h214-direct"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM)
class DirectAddAnswersTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        for target in (
            "classrooms.services.notifications.safe_delay",
            "classrooms.services.notifications.send_student_login_invitation_email",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.school = School.objects.create(name="H214 High")
        self.other_school = School.objects.create(name="H214 Other High")
        self.teacher = User.objects.create_user(
            email="owner@h214.example",
            password=PASSWORD,
            user_type=UserTypes.TEACHER,
            school=self.school,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=self.teacher, session=session
        )
        self.client.force_authenticate(self.teacher)

    def student(self, email, school=None):
        return User.objects.create_user(
            email=email,
            password=PASSWORD,
            user_type=UserTypes.STUDENT,
            school=school,
            is_active=True,
        )

    def direct(self, email, first="Probe"):
        cache.clear()
        return self.client.post(
            reverse("course-direct-add-student", kwargs={"pk": self.course.id}),
            {"first_name": first, "last_name": "Pupil", "email": email},
        )

    def test_an_already_enrolled_student_is_a_400_with_the_reason(self):
        account = self.student("again.h214@x.example")
        StudentCourse.objects.create(student=account, course=self.course)

        response = self.direct(account.email, first="Other")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        # The serializer's create() raises ValidationError(<sentence>), which
        # DRF answers as a one-item list. The exact sentence, read from that
        # item: "already enrolled" alone also matches the duplicate-name
        # refusal in validate().
        self.assertEqual(len(response.data), 1)
        self.assertEqual(
            str(response.data[0]), "Student is already enrolled in this course."
        )
        self.assertEqual(
            StudentCourse.objects.filter(student=account, course=self.course).count(),
            1,
        )

    def test_a_student_of_another_school_is_a_400_and_stays_out(self):
        account = self.student("elsewhere.h214@x.example", school=self.other_school)

        response = self.direct(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            StudentCourse.objects.filter(student=account, course=self.course).exists()
        )

    # -- controls ---------------------------------------------------------------

    def test_an_ordinary_student_is_still_added(self):
        """Green on the old code too."""
        account = self.student("fine.h214@x.example")

        response = self.direct(account.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(student=account, course=self.course).exists()
        )

    def test_an_unexpected_failure_is_still_a_500_with_the_fixed_message(self):
        """Green on the old code too: only DRF's own exceptions pass through."""
        with patch(
            "classrooms.serializers.DirectAddStudentSerializer.save",
            side_effect=RuntimeError("database exploded"),
        ):
            response = self.direct("boom.h214@x.example")

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.data["message"], "Unable to add student to course")

    def test_a_drf_exception_that_is_not_a_validation_error_also_passes_through(self):
        """Not only ValidationError: any of DRF's own exceptions keeps its status."""
        with patch(
            "classrooms.serializers.DirectAddStudentSerializer.save",
            side_effect=PermissionDenied("not yours"),
        ):
            response = self.direct("denied.h214@x.example")

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
