from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse

User = get_user_model()


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
