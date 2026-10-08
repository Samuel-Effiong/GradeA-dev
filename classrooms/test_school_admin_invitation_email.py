"""_send_school_admin_invitation_email must build its activation_url from
SCHOOL_ADMIN_FRONTEND_DOMAIN, not FRONTEND_DOMAIN (the teacher app) - the
school-admin frontend is a genuinely separate app that refuses other roles,
so an invite built on the wrong domain sends the admin somewhere that
rejects them.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings

from classrooms.models import School
from classrooms.serializers import _send_school_admin_invitation_email
from users.models import CustomUser, UserTypes


class SchoolAdminInvitationEmailDomainTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Riverside High")
        self.admin = CustomUser.objects.create_user(
            email="admin@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Ada",
            last_name="Min",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=False,
            activation_token="test-token-123",
        )

    @override_settings(
        FRONTEND_DOMAIN="teacher.example.test",
        SCHOOL_ADMIN_FRONTEND_DOMAIN="admin.example.test",
    )
    @patch("classrooms.serializers.send_email_task.delay")
    def test_invite_uses_school_admin_frontend_domain(self, mock_send_email):
        with self.captureOnCommitCallbacks(execute=True):
            _send_school_admin_invitation_email(self.admin, self.school)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("admin.example.test", merge_data["activation_url"])
        self.assertNotIn("teacher.example.test", merge_data["activation_url"])
        self.assertIn(
            "/register/school-admin?email=admin@example.com&token=test-token-123",
            merge_data["activation_url"],
        )

    @override_settings(FRONTEND_DOMAIN="teacher.example.test")
    @patch("classrooms.serializers.send_email_task.delay")
    def test_falls_back_to_frontend_domain_when_school_admin_domain_unset(
        self, mock_send_email
    ):
        # settings.py's own default=FRONTEND_DOMAIN fallback is exercised at
        # process start (see AutoGrader/tests_school_admin_frontend_domain.py
        # for that). Here, SCHOOL_ADMIN_FRONTEND_DOMAIN is simply left equal
        # to FRONTEND_DOMAIN, as it would already be resolved to for any
        # deployment that never set the env var - confirming the invite
        # email still works in that (today's default) configuration.
        with override_settings(SCHOOL_ADMIN_FRONTEND_DOMAIN="teacher.example.test"):
            with self.captureOnCommitCallbacks(execute=True):
                _send_school_admin_invitation_email(self.admin, self.school)

        merge_data = mock_send_email.call_args.kwargs["merge_data"]
        self.assertIn("teacher.example.test", merge_data["activation_url"])
