"""H-291: an email address in an emailed link must survive the round trip.

The activation, school-admin invite and student invite links put the
address in the query string. Unencoded, a "+" in it (name+tag@gmail.com)
is read back as a space by every browser (URLSearchParams, like parse_qs
here), so the frontend rejects the address and the person cannot
activate. Each link is parsed back the way a browser does and the address
must come out exactly as it went in.
"""

from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from classrooms.models import School
from classrooms.serializers import _send_school_admin_invitation_email
from classrooms.services.notifications import student_registration_link
from users.models import UserTypes
from users.services import send_user_activation_email

User = get_user_model()

PLUS_EMAIL = "emanprojectz+test11@gmail.com"


def _query(url):
    return parse_qs(urlsplit(url).query)


@override_settings(
    FRONTEND_DOMAIN="teacher.example.test",
    STUDENT_FRONTEND_DOMAIN="student.example.test",
    SCHOOL_ADMIN_FRONTEND_DOMAIN="admin.example.test",
)
class EmailLinkEncodingTests(TestCase):
    def _user(self, email, user_type, **extra):
        return User.objects.create_user(
            email=email,
            password="password123",  # pragma: allowlist secret
            first_name="Eman",
            last_name="Test",
            user_type=user_type,
            **extra,
        )

    @patch("users.services.send_email_task.delay")
    def test_activation_link_keeps_a_plus_address(self, mock_send):
        user = self._user(PLUS_EMAIL, UserTypes.TEACHER)
        send_user_activation_email(user)
        url = mock_send.call_args.kwargs["merge_data"]["activation_url"]
        self.assertEqual(_query(url)["email"], [PLUS_EMAIL])
        self.assertIn("%2B", url)

    @patch("users.services.send_email_task.delay")
    def test_activation_link_for_a_student_keeps_a_plus_address(self, mock_send):
        user = self._user(PLUS_EMAIL, UserTypes.STUDENT)
        send_user_activation_email(user)
        url = mock_send.call_args.kwargs["merge_data"]["activation_url"]
        self.assertEqual(_query(url)["email"], [PLUS_EMAIL])

    @patch("users.services.send_email_task.delay")
    def test_activation_link_token_still_reads_back(self, mock_send):
        user = self._user(PLUS_EMAIL, UserTypes.TEACHER)
        send_user_activation_email(user)
        url = mock_send.call_args.kwargs["merge_data"]["activation_url"]
        user.refresh_from_db()
        self.assertEqual(_query(url)["token"], [user.activation_token])

    @patch("users.services.send_email_task.delay")
    def test_plain_address_link_is_unchanged(self, mock_send):
        user = self._user("plain.teacher@example.com", UserTypes.TEACHER)
        send_user_activation_email(user)
        url = mock_send.call_args.kwargs["merge_data"]["activation_url"]
        self.assertIn("?email=plain.teacher@example.com&token=", url)

    @patch("classrooms.serializers.send_email_task.delay")
    def test_school_admin_invite_keeps_a_plus_address(self, mock_send):
        school = School.objects.create(name="Riverside High")
        admin = self._user(
            PLUS_EMAIL,
            UserTypes.SCHOOL_ADMIN,
            school=school,
            is_active=False,
            activation_token="test-token-123",
        )
        with self.captureOnCommitCallbacks(execute=True):
            _send_school_admin_invitation_email(admin, school)
        url = mock_send.call_args.kwargs["merge_data"]["activation_url"]
        self.assertEqual(_query(url)["email"], [PLUS_EMAIL])
        self.assertEqual(_query(url)["token"], ["test-token-123"])

    def test_student_invite_keeps_a_plus_address(self):
        url = student_registration_link("tok123", PLUS_EMAIL)
        self.assertEqual(_query(url)["email"], [PLUS_EMAIL])
        self.assertTrue(urlsplit(url).path.endswith("/register/student/tok123"))

    def test_student_invite_keeps_an_address_with_an_ampersand(self):
        # "&" is legal in the local part and would otherwise end the param.
        email = "a&b+c@example.com"
        url = student_registration_link("tok123", email)
        self.assertEqual(_query(url)["email"], [email])
