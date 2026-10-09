"""
H-203: THE PRINCIPLE, and every mailbox-only road that must call it.

    An account with admin power that has never been verified can be entered
    only with its password; no road that proves only control of a mailbox (a
    reset code, a verification code, a Google identity) signs it in, verifies
    it or activates it.

"Admin power" is any one of user type SUPER_ADMIN, is_staff, is_superuser (the
three can differ: `create_superuser` leaves the type at TEACHER). H-164 closed
the reset road and the two verification roads. This row closes the three that
were left (found by Verifier 1 and by the road list):

  * Google sign-in on an existing, never-verified account: it stamped the
    email verified, activated it and signed it in.
  * POST /auth/register/school-admin (the invitation token): it activates a
    pending SCHOOL_ADMIN row and signs it in; a pending row that the Django
    admin gave is_staff or is_superuser passed.
  * enroll_student_by_email: it refuses any account whose TYPE is not STUDENT,
    but a STUDENT-typed row carrying is_staff passed.

Each road makes ONE call of `users.admin_power.holds_admin_power` and answers
as it answers a failed attempt on that road.

Run with:
    python manage.py test users.tests_admin_power_principle
"""

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import Course, School, Session
from classrooms.services import enroll_student_by_email
from classrooms.services.enrollment import NOT_A_STUDENT_MESSAGE, EnrollmentError
from users.models import UserTypes

User = get_user_model()

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
GOOGLE_FAILED = "Google sign-in failed. Please try again."
INVALID_INVITATION = "Invalid or expired activation token."
STRONG = "a-strong-passphrase-for-h203-only"  # pragma: allowlist secret


@override_settings(CACHES=LOCMEM)
class GoogleRoadTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.url = reverse("auth-google-auth")
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)

    def call(self, email):
        with patch("requests.post") as mocked_post, patch(
            "users.views.id_token.verify_oauth2_token"
        ) as mocked_verify:
            mocked_post.return_value.raise_for_status.return_value = None
            mocked_post.return_value.json.return_value = {
                "id_token": "fake-id-token",
                "access_token": "fake-access-token",
                "refresh_token": "fake-refresh-token",
                "expires_in": 3600,
            }
            mocked_verify.return_value = {
                "email": email,
                "email_verified": True,
                "given_name": "Google",
                "family_name": "Person",
            }
            return self.client.post(self.url, {"code": "oauth-code"}, format="json")

    def account(self, email, verified=False, active=True, **flags):
        flags.setdefault("user_type", UserTypes.TEACHER)
        return User.objects.create_user(
            email=email,
            password="Google-pass-h203",  # pragma: allowlist secret
            is_active=active,
            email_verified_at=timezone.now() if verified else None,
            **flags,
        )

    def assert_refused_and_untouched(self, account):
        before = account.password

        response = self.call(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        account.refresh_from_db()
        self.assertIsNone(account.email_verified_at)
        self.assertTrue(account.is_active)
        self.assertEqual(account.password, before)
        return response

    # -- the refusal, one account per marker ---------------------------------

    def test_google_refuses_a_never_verified_staff_account(self):
        self.assert_refused_and_untouched(
            self.account("gs.h203@x.example", is_staff=True)
        )

    def test_google_refuses_a_never_verified_superuser_flag(self):
        self.assert_refused_and_untouched(
            self.account("gf.h203@x.example", is_superuser=True)
        )

    def test_google_refuses_a_never_verified_super_admin_type(self):
        self.assert_refused_and_untouched(
            self.account("gt.h203@x.example", user_type=UserTypes.SUPER_ADMIN)
        )

    def test_google_refuses_a_command_line_superuser(self):
        account = User.objects.create_superuser(
            email="gc.h203@x.example",
            password="Google-pass-cmd",  # pragma: allowlist secret
        )
        self.assertEqual(account.user_type, UserTypes.TEACHER)

        self.assert_refused_and_untouched(account)

    def test_that_refusal_is_the_failed_google_sign_in_answer(self):
        account = self.account("gw.h203@x.example", is_superuser=True)

        response = self.call(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(GOOGLE_FAILED, str(response.data))

    def test_the_refusal_is_raised_before_any_write_to_the_account(self):
        """The Google arm runs inside a transaction that would undo an earlier
        write, so the end state alone cannot show the ORDER. This counts the
        statements the refused sign-in sent: a SELECT of the row (so the capture
        is not empty) and no UPDATE, INSERT or DELETE on the users table."""
        account = self.account("go.order.h203@x.example", is_superuser=True)
        table = connection.ops.quote_name(User._meta.db_table)

        with CaptureQueriesContext(connection) as captured:
            response = self.call(account.email)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        on_users = [q["sql"].lstrip().upper() for q in captured if table in q["sql"]]
        reads = [sql for sql in on_users if sql.startswith("SELECT")]
        writes = [
            sql for sql in on_users if sql.startswith(("UPDATE", "INSERT", "DELETE"))
        ]
        self.assertGreater(len(reads), 0)
        self.assertEqual(len(writes), 0)

    # -- controls: nothing else changes ----------------------------------------

    def test_a_verified_admin_still_signs_in_with_google(self):
        """Green on the old code too: only a NEVER-verified account is refused."""
        account = self.account(
            "gv.h203@x.example",
            verified=True,
            is_staff=True,
            is_superuser=True,
            user_type=UserTypes.SUPER_ADMIN,
        )

        response = self.call(account.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("access", response.data)

    def test_an_ordinary_never_verified_user_is_still_completed_by_google(self):
        """Green on the old code too: today an existing, never-verified,
        ordinary account is stamped verified, activated if inactive (with an
        unusable password) and signed in."""
        user = self.account("go.h203@x.example", active=False)

        response = self.call(user.email)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)
        self.assertFalse(user.has_usable_password())

    def test_a_verified_and_deactivated_account_is_still_refused_by_the_carve_out(self):
        """Green on the old code too: the existing carve-out is untouched."""
        user = self.account("gd.h203@x.example", verified=True, active=False)

        response = self.call(user.email)

        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        user.refresh_from_db()
        self.assertFalse(user.is_active)


@override_settings(CACHES=LOCMEM)
class SchoolAdminInvitationRoadTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        mailerlite = patch("users.views.sync_user_to_mailerlite")
        mailerlite.start()
        self.addCleanup(mailerlite.stop)
        self.school = School.objects.create(name="H203 Admin High")
        self.url = reverse("auth-register-school-admin")

    def pending(self, email, **flags):
        return User.objects.create_user(
            email=email,
            password="Pending-pass-h203",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=False,
            activation_token="invite-h203-token",
            activation_expires=timezone.now() + timedelta(days=1),
            **flags,
        )

    def redeem(self, user):
        return self.client.post(
            self.url,
            {"email": user.email, "token": "invite-h203-token", "password": STRONG},
            format="json",
        )

    def assert_refused_and_untouched(self, user):
        before = user.password

        response = self.redeem(user)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertNotIn("access", response.data)
        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNone(user.email_verified_at)
        self.assertEqual(user.activation_token, "invite-h203-token")
        self.assertEqual(user.password, before)

    def test_the_invitation_road_refuses_a_pending_row_carrying_is_staff(self):
        self.assert_refused_and_untouched(
            self.pending("is.h203@x.example", is_staff=True)
        )

    def test_the_invitation_road_refuses_a_pending_row_carrying_is_superuser(self):
        self.assert_refused_and_untouched(
            self.pending("isu.h203@x.example", is_superuser=True)
        )

    def test_that_refusal_is_the_invalid_invitation_answer(self):
        user = self.pending("iw.h203@x.example", is_staff=True)
        wrong = self.client.post(
            self.url,
            {"email": user.email, "token": "not-the-token", "password": STRONG},
            format="json",
        )

        refused = self.redeem(user)

        self.assertEqual(refused.status_code, wrong.status_code)
        self.assertEqual(refused.content, wrong.content)
        self.assertIn(INVALID_INVITATION, str(refused.data))

    def test_an_ordinary_pending_school_admin_still_registers(self):
        """Green on the old code too."""
        user = self.pending("ordinary.h203@x.example")

        response = self.redeem(user)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        user.refresh_from_db()
        self.assertTrue(user.is_active)
        self.assertIsNotNone(user.email_verified_at)


class AddStudentByEmailRoadTests(APITestCase):
    def setUp(self):
        for target in (
            "classrooms.services.notifications.safe_delay",
            "users.views.send_user_activation_email",
        ):
            patcher = patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        school = School.objects.create(name="H203 Add High")
        teacher = User.objects.create_user(
            email="h203.teacher@add-high.example",
            password="Teacher-pass-h203",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            school=school,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=teacher)
        self.course = Course.objects.create(
            name="Maths", teacher=teacher, session=session
        )

    def row(self, email, **flags):
        return User.objects.create_user(
            email=email,
            password="Student-pass-h203",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            is_active=True,
            **flags,
        )

    def test_a_student_typed_row_carrying_is_staff_is_refused(self):
        account = self.row("sstaff.h203@x.example", is_staff=True)
        before = account.password

        with self.assertRaises(EnrollmentError) as caught:
            enroll_student_by_email(course=self.course, email=account.email)

        self.assertEqual(str(caught.exception), NOT_A_STUDENT_MESSAGE)
        account.refresh_from_db()
        self.assertEqual(account.password, before)

    def test_a_student_typed_row_carrying_is_superuser_is_refused(self):
        account = self.row("sflag.h203@x.example", is_superuser=True)
        before = account.password

        with self.assertRaises(EnrollmentError) as caught:
            enroll_student_by_email(course=self.course, email=account.email)

        self.assertEqual(str(caught.exception), NOT_A_STUDENT_MESSAGE)
        account.refresh_from_db()
        self.assertEqual(account.password, before)

    def test_an_ordinary_student_is_still_enrolled(self):
        """Green on the old code too."""
        account = self.row("sordinary.h203@x.example")

        student, invited = enroll_student_by_email(
            course=self.course, email=account.email
        )

        self.assertEqual(student.pk, account.pk)
