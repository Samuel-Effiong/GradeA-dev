"""send_user_activation_email() must route students to
STUDENT_FRONTEND_DOMAIN, teachers to FRONTEND_DOMAIN, and school admins to
their own invitation flow entirely (see H-42) - never the generic
password-less activation email, since a school admin account never has a
password until they complete /register/school-admin."""

from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from classrooms.models import School
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
    def test_school_admin_never_gets_the_generic_activation_email(
        self, mock_send_email
    ):
        """H-42: a school admin's account has no password until they
        complete the invite at /register/school-admin. The generic
        activation email has no password step and would overwrite their
        still-valid invite token - so send_user_activation_email must not
        send it for this user_type at all (see
        classrooms.test_school_admin_otp_deadend for the resend-instead
        behavior this delegates to)."""
        school = School.objects.create(name="Domain Test School")
        admin = User.objects.create_user(
            email="admin.activation@example.com",
            first_name="Ad",
            last_name="Min",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
            activation_token="pre-existing-invite-token",
        )

        send_user_activation_email(admin)

        mock_send_email.assert_not_called()
        admin.refresh_from_db()
        self.assertNotEqual(admin.activation_token, "pre-existing-invite-token")
