"""
H-148: an invitation does not promise what the server does not do.

Three invitations carry a temporary password: the student's, the school
admin's, and the one for a teacher added under a school licence. Each said
"You'll be asked to choose your own password the first time you log in."
The server asks nobody: the enforcement of a first-login password change
was removed by a product decision (users/authentication.py), for all three
roles.

User's decision, 2026-10-07: the sentence leaves the two staff invitations
as well. The student's is held in
classrooms/tests_teacher_names_student_on_add.py; these are the staff two.

Run with:
    python manage.py test classrooms.tests_staff_invitations_promise_no_password_change
"""

from unittest.mock import patch

from django.test import TestCase

from billing.license_service import LicenseSubscriptionService
from classrooms.models import School
from classrooms.serializers import SchoolWithAdminSerializer
from users.models import CustomUser, UserTypes

PROMISES = ("choose your own password", "asked to", "first time you log in")
TEMPORARY = "Tmp-Example-Pw-7"  # pragma: allowlist secret


class StaffInvitationsPromiseNoPasswordChangeTest(TestCase):
    def assert_no_promise(self, text):
        for words in PROMISES:
            with self.subTest(words=words):
                self.assertNotIn(words, text)
        # The way in is still there.
        self.assertIn("temporary password: ", text)

    def test_the_school_admins_invitation(self):
        serializer = SchoolWithAdminSerializer(
            data={
                "school_name": "Riverside High",
                "admin_email": "admin@riverside.edu",
                "admin_first_name": "Ada",
                "admin_last_name": "Min",
            }
        )
        self.assertTrue(serializer.is_valid(), serializer.errors)
        with patch("classrooms.serializers.send_email_task") as sent:
            with self.captureOnCommitCallbacks(execute=True):
                serializer.save()

        text = sent.delay.call_args.kwargs["merge_data"]["top_content"]
        self.assertIn("school administrator for Riverside High", text)
        self.assert_no_promise(text)

    def test_the_licensed_teachers_invitation(self):
        school = School.objects.create(name="Riverside High")
        admin = CustomUser.objects.create_user(
            email="admin@riverside.edu",
            password=TEMPORARY,
            first_name="Ada",
            last_name="Min",
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        teacher = CustomUser.objects.create_user(
            email="new.teacher@riverside.edu",
            password=TEMPORARY,
            first_name="Grace",
            last_name="Hopper",
            user_type=UserTypes.TEACHER,
            school=school,
        )
        with patch("billing.license_service.send_email_task") as sent:
            with self.captureOnCommitCallbacks(execute=True):
                LicenseSubscriptionService._send_teacher_invitation(
                    teacher, school, admin, TEMPORARY
                )

        text = sent.delay.call_args.kwargs["merge_data"]["top_content"]
        self.assertIn("Ada Min has invited you to teach at Riverside High", text)
        self.assertIn(f"temporary password: {TEMPORARY}", text)
        self.assert_no_promise(text)
