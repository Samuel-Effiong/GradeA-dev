from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from users.models import UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class TeacherRegistrationEncodingTests(APITestCase):
    """
    `/auth/register` accepts JSON, form-encoded and multipart bodies - DRF
    enables all three parsers by default and settings.py does not narrow
    them - so it has to survive all three.

    It did not. The view opened with `request.data.pop("user_type", None)`,
    and for a non-JSON body `request.data` is a QueryDict, which Django
    locks so a request stays a faithful record of what arrived. QueryDict.pop
    checks that lock BEFORE looking at the key, so every form-encoded
    registration raised AttributeError and came back 500 - even one that
    never mentioned user_type. The strip was redundant anyway: user_type is
    one of CustomUserSerializer.PRIVILEGED_FIELDS, forced read-only unless
    the serializer carries a super admin in its context, and this call site
    passes no context at all.

    The cache is pinned to LocMem and cleared because the register throttle
    (10/hour, keyed per IP) counts in the default cache, which is otherwise
    Redis shared with every other suite.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)

        activation = patch("users.serializers.send_user_activation_email")
        activation.start()
        self.addCleanup(activation.stop)

    def test_form_encoded_registration_succeeds(self):
        response = self.client.post(
            reverse("auth-register"),
            {
                "email": "form.encoded@gmail.com",
                "password": "strongpass123",  # pragma: allowlist secret
                "first_name": "Form",
                "last_name": "Encoded",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.filter(email="form.encoded@gmail.com").exists())

    def test_multipart_registration_succeeds(self):
        response = self.client.post(
            reverse("auth-register"),
            {
                "email": "multipart.signup@gmail.com",
                "password": "strongpass123",  # pragma: allowlist secret
                "first_name": "Multi",
                "last_name": "Part",
            },
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            User.objects.filter(email="multipart.signup@gmail.com").exists()
        )

    def test_client_cannot_choose_its_own_user_type(self):
        """
        What the deleted strip was there to prevent. The serializer, not the
        view, is what stops it - so it holds for every body encoding, and
        the request succeeds as an ordinary teacher signup instead of 500ing.
        """
        for encoding in ("json", "multipart"):
            with self.subTest(encoding=encoding):
                email = f"promotion.attempt.{encoding}@gmail.com"
                response = self.client.post(
                    reverse("auth-register"),
                    {
                        "email": email,
                        "password": "strongpass123",  # pragma: allowlist secret
                        "first_name": "Promotion",
                        "last_name": "Attempt",
                        "user_type": UserTypes.SUPER_ADMIN,
                    },
                    format=encoding,
                )

                self.assertEqual(response.status_code, status.HTTP_200_OK)
                user = User.objects.get(email=email)
                self.assertEqual(user.user_type, UserTypes.TEACHER)
                self.assertFalse(user.is_superuser)


class StudentRegistrationTests(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="teacher.register@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Register",
            user_type="TEACHER",
            is_active=True,
        )
        self.session = Session.objects.create(name="Spring 2026", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Chemistry",
            teacher=self.teacher,
            session=self.session,
        )

    def test_register_student_rejects_duplicate_name_in_pending_course(self):
        enrolled_student = User.objects.create_user(
            email="existing.student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Jane",
            last_name="Doe",
            user_type="STUDENT",
            is_active=True,
        )
        pending_student = User.objects.create_user(
            email="pending.student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="",
            last_name="",
            user_type="STUDENT",
            is_active=False,
            activation_token="pending-token",
            activation_expires=timezone.now() + timezone.timedelta(days=1),
        )

        StudentCourse.objects.create(
            student=enrolled_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=pending_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        response = self.client.post(
            reverse("auth-register-student"),
            {
                "first_name": "Jane",
                "middle_name": "",
                "last_name": "Doe",
                "password": "strongpass123",  # pragma: allowlist secret
                "token": "pending-token",
            },
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("already enrolled", str(response.data))
