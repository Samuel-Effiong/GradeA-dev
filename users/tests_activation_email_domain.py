"""send_user_activation_email() must route students to
STUDENT_FRONTEND_DOMAIN and everyone else (teachers, school admins, ...) to
FRONTEND_DOMAIN, since students and teachers use separate frontend apps."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from users.models import UserTypes
from users.services import send_user_activation_email

User = get_user_model()


@override_settings(
    FRONTEND_DOMAIN="teacher.example.test",
    STUDENT_FRONTEND_DOMAIN="student.example.test",
)
class SendUserActivationEmailDomainTests(TestCase):
    @patch("users.services.send_email_task.delay")
    def test_student_gets_student_frontend_domain(self, mock_send_email):
        student = User.objects.create_user(
            email="student.activation@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Stu",
            last_name="Dent",
            user_type=UserTypes.STUDENT,
        )

        send_user_activation_email(student)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("student.example.test", merge_data["activation_url"])
        self.assertNotIn("teacher.example.test", merge_data["activation_url"])

    @patch("users.services.send_email_task.delay")
    def test_teacher_gets_frontend_domain(self, mock_send_email):
        teacher = User.objects.create_user(
            email="teacher.activation@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Tea",
            last_name="Cher",
            user_type=UserTypes.TEACHER,
        )

        send_user_activation_email(teacher)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("teacher.example.test", merge_data["activation_url"])
        self.assertNotIn("student.example.test", merge_data["activation_url"])

    @patch("users.services.send_email_task.delay")
    def test_school_admin_gets_frontend_domain(self, mock_send_email):
        """Only students get the student app - every other role stays on
        the teacher/admin frontend, including school admins."""
        admin = User.objects.create_user(
            email="admin.activation@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Ad",
            last_name="Min",
            user_type=UserTypes.SCHOOL_ADMIN,
        )

        send_user_activation_email(admin)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("teacher.example.test", merge_data["activation_url"])
        self.assertNotIn("student.example.test", merge_data["activation_url"])
