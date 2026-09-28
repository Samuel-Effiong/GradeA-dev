"""
`backfill_pending_student_invites` converts every leftover pre-cutover
pending student invite (is_active=False + activation_token, from the old
scheme enroll_student_by_email no longer creates - see
classrooms/services/enrollment.py) to the new active-immediately scheme.

Coverage: --dry-run touches nothing and sends no mail; a real run converts
correctly and sends the new login email; running it a second time is a
no-op (idempotent) because a converted row no longer matches the
selection query.
"""

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from rest_framework.test import APITestCase

from users.models import UserTypes

from .models import Course, EnrollmentStatusType, Session, StudentCourse
from .tests import User


class BackfillPendingStudentInvitesTest(APITestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="backfill-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Backfill",
            last_name="Teacher",
            user_type="TEACHER",
            is_active=True,
        )
        self.session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=self.session
        )
        self.student = User.objects.create(
            email="legacy-invite@example.com",
            first_name="Legacy",
            last_name="Invite",
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token="112233",
        )
        self.student.set_unusable_password()
        self.student.save()
        self.enrollment = StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_dry_run_touches_nothing_and_sends_no_mail(self, mock_email):
        out = StringIO()
        call_command("backfill_pending_student_invites", "--dry-run", stdout=out)

        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        self.assertFalse(self.student.has_usable_password())
        self.assertFalse(self.student.must_change_password)
        mock_email.assert_not_called()
        self.assertIn("legacy-invite@example.com", out.getvalue())
        self.assertIn("dry run", out.getvalue())

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_real_run_converts_and_sends_login_email(self, mock_email):
        out = StringIO()
        call_command("backfill_pending_student_invites", stdout=out)

        self.student.refresh_from_db()
        self.assertTrue(self.student.is_active)
        self.assertTrue(self.student.must_change_password)
        self.assertTrue(self.student.has_usable_password())

        mock_email.assert_called_once()
        called_student, called_course, called_password = mock_email.call_args[0]
        self.assertEqual(called_student, self.student)
        self.assertEqual(called_course, self.course)
        self.assertTrue(self.student.check_password(called_password))

        # Converting must not itself change the enrollment's status - that
        # is login's job (activate_pending_enrollments_on_login), not the
        # backfill's.
        self.enrollment.refresh_from_db()
        self.assertEqual(
            self.enrollment.enrollment_status, EnrollmentStatusType.PENDING
        )

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_idempotent_second_run_is_a_no_op(self, mock_email):
        call_command("backfill_pending_student_invites")
        self.student.refresh_from_db()
        first_password_hash = self.student.password

        mock_email.reset_mock()
        out = StringIO()
        call_command("backfill_pending_student_invites", stdout=out)

        self.student.refresh_from_db()
        self.assertEqual(self.student.password, first_password_hash)
        mock_email.assert_not_called()
        self.assertIn("0 student(s) converted", out.getvalue())

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_student_who_has_since_logged_in_is_never_revisited(self, mock_email):
        """
        A student converted by an earlier run (or manually) who has since
        logged in and chosen their own password - is_active=True,
        must_change_password=False - must not be reprocessed even though
        they still carry a stale activation_token from before the cutover.
        """
        self.student.is_active = True
        self.student.must_change_password = False
        self.student.set_password(
            "their-own-chosen-password"
        )  # pragma: allowlist secret
        self.student.save()

        call_command("backfill_pending_student_invites")

        self.student.refresh_from_db()
        self.assertTrue(self.student.check_password("their-own-chosen-password"))
        self.assertFalse(self.student.must_change_password)
        mock_email.assert_not_called()

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_skips_student_with_no_pending_enrollment(self, mock_email):
        self.enrollment.delete()

        out = StringIO()
        call_command("backfill_pending_student_invites", stdout=out)

        self.student.refresh_from_db()
        self.assertFalse(self.student.is_active)
        mock_email.assert_not_called()
        self.assertIn("1 skipped", out.getvalue())
