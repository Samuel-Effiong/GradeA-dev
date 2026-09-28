"""
Roster import (bulk/CSV) rows that carry an email go through exactly the
single-add path (enrollment.enroll_student_by_email) - the founder's H-47
follow-through. A new student is active immediately with a generated
temporary password and gets the login-credentials email; nothing mints an
activation code, so the code-based student sign-up has nothing left to
complete. must_change_password is set but informational (the server doesn't
enforce it).
"""

from typing import Any
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from classrooms.services.enrollment import CROSS_SCHOOL_REJECTION_MESSAGE
from users.models import UserActivity, UserTypes

User = get_user_model()

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
NOTIFY = "classrooms.services.enrollment.notifications"
TEACHER_PASSWORD = "Teacher-Roster-Pw-31"  # pragma: allowlist secret
KIM_PASSWORD = "Kims-Own-Passw0rd!"  # pragma: allowlist secret


class SignInHelpers(APITestCase):
    """Shared helpers for the two test classes below; no tests of its own."""

    teacher: Any
    course: Course

    def other_course(self):
        return Course.objects.create(
            name="Bio 101", teacher=self.teacher, session=self.course.session
        )

    def sign_in(self, email, password):
        """A real /auth/login from a separate client, so the teacher's
        forced authentication on self.client is untouched."""
        response = self.client_class().post(
            reverse("login"), {"email": email, "password": password}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)


@override_settings(CACHES=LOCMEM_CACHE)
class RosterEmailRowsAreReadyToUseTests(SignInHelpers):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="roster.teacher@example.com",
            password=TEACHER_PASSWORD,
            first_name="Roster",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology", teacher=self.teacher, session=session
        )
        self.url = reverse("course-bulk-add-students", kwargs={"pk": self.course.pk})
        self.client.force_authenticate(user=self.teacher)
        patcher = patch(NOTIFY)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def _import(self, raw):
        response = self.client.post(self.url, {"raw_data": raw}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response

    def _enrollment(self, student):
        return StudentCourse.objects.get(student=student, course=self.course)

    def _emailed_password(self):
        call = self.notify.send_student_login_invitation_email.call_args
        return call.args[2]

    def test_a_new_student_is_active_with_a_temporary_password_and_no_code(self):
        response = self._import("Ada,Lovelace,Ada.Lovelace@Example.com")

        student = User.objects.get(email="ada.lovelace@example.com")
        self.assertTrue(student.is_active)
        self.assertTrue(student.must_change_password)
        self.assertIsNone(student.activation_token)
        self.assertIsNone(student.activation_expires)
        self.assertEqual((student.first_name, student.last_name), ("Ada", "Lovelace"))
        self.assertEqual(student.user_type, UserTypes.STUDENT)
        self.assertEqual(
            self._enrollment(student).enrollment_status, EnrollmentStatusType.PENDING
        )
        self.notify.send_student_login_invitation_email.assert_called_once()
        self.notify.send_bulk_enrollment_email.assert_not_called()
        # The roster response contract is unchanged.
        result = response.data["results"][0]
        self.assertEqual((result["status"], result["type"]), ("invited", "invitation"))

    def test_the_emailed_password_logs_in_and_activates_the_enrollment(self):
        self._import("Ada,Lovelace,ada@example.com")
        password = self._emailed_password()
        self.client.force_authenticate(user=None)

        login = self.client.post(
            reverse("login"),
            {"email": "ada@example.com", "password": password},
            format="json",
        )

        self.assertEqual(login.status_code, status.HTTP_200_OK, login.content)
        student = User.objects.get(email="ada@example.com")
        self.assertEqual(
            self._enrollment(student).enrollment_status, EnrollmentStatusType.ENROLLED
        )

    def test_a_student_who_has_signed_in_is_enrolled_and_keeps_their_password(self):
        student = User.objects.create_user(
            email="known@example.com",
            password="Known-Student-Pw-8",  # pragma: allowlist secret
            first_name="Known",
            last_name="Student",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        UserActivity.objects.create(user=student)  # the pre-last_login signal

        response = self._import("Other,Name,known@example.com")

        student.refresh_from_db()
        self.assertTrue(student.check_password("Known-Student-Pw-8"))
        self.assertEqual(student.first_name, "Known")
        self.assertEqual(
            self._enrollment(student).enrollment_status, EnrollmentStatusType.ENROLLED
        )
        self.notify.send_added_to_course_email.assert_called_once()
        self.notify.send_student_login_invitation_email.assert_not_called()
        result = response.data["results"][0]
        self.assertEqual(
            (result["status"], result["type"]), ("enrolled", "existing_student")
        )

    def test_reimporting_a_signed_in_student_with_the_flag_set_keeps_their_password(
        self,
    ):
        """The Verification Engineer's scenario: must_change_password is
        informational, so a student can sign in and use the app without ever
        clearing it. A re-import into a second course must not reset them."""
        kim = User.objects.create_user(
            email="kim@example.com",
            password=KIM_PASSWORD,
            first_name="Kim",
            last_name="Lee",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        User.objects.filter(pk=kim.pk).update(must_change_password=True)
        StudentCourse.objects.create(
            student=kim,
            course=self.other_course(),
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.sign_in("kim@example.com", KIM_PASSWORD)

        response = self._import("Kim,Lee,kim@example.com")

        kim.refresh_from_db()
        self.assertTrue(kim.check_password(KIM_PASSWORD))
        self.assertEqual(
            self._enrollment(kim).enrollment_status, EnrollmentStatusType.ENROLLED
        )
        self.notify.send_student_login_invitation_email.assert_not_called()
        self.notify.send_added_to_course_email.assert_called_once()
        self.assertEqual(response.data["results"][0]["status"], "enrolled")

    def test_a_legacy_inactive_row_with_a_code_is_promoted_and_its_code_cleared(self):
        legacy = User.objects.create_user(
            email="legacy@example.com",
            password=None,
            first_name="Legacy",
            last_name="Pending",
            user_type=UserTypes.STUDENT,
            is_active=False,
            activation_token="123456",
            activation_expires=timezone.now() + timezone.timedelta(hours=1),
        )

        self._import("Legacy,Pending,legacy@example.com")

        legacy.refresh_from_db()
        self.assertTrue(legacy.is_active)
        self.assertTrue(legacy.must_change_password)
        self.assertIsNone(legacy.activation_token)
        self.assertIsNone(legacy.activation_expires)
        self.assertTrue(legacy.check_password(self._emailed_password()))
        self.assertEqual(
            self._enrollment(legacy).enrollment_status, EnrollmentStatusType.PENDING
        )

    def test_a_student_who_never_signed_in_gets_a_fresh_password(self):
        """The reverse case: invited before, never signed in (no last_login,
        no UserActivity). Their first email's password was never used, so a
        re-add re-sends credentials."""
        never = User.objects.create_user(
            email="onboarding@example.com",
            password="Old-Temp-Pw-55",  # pragma: allowlist secret
            first_name="On",
            last_name="Boarding",
            user_type=UserTypes.STUDENT,
            is_active=True,
            must_change_password=True,
        )

        response = self._import("On,Boarding,onboarding@example.com")

        never.refresh_from_db()
        self.assertFalse(never.check_password("Old-Temp-Pw-55"))
        self.assertTrue(never.check_password(self._emailed_password()))
        self.assertEqual(
            self._enrollment(never).enrollment_status,
            EnrollmentStatusType.PENDING,
        )
        self.assertEqual(response.data["results"][0]["status"], "invited")

    def test_a_login_stamps_last_login(self):
        student = User.objects.create_user(
            email="stamp@example.com",
            password=KIM_PASSWORD,
            first_name="St",
            last_name="Amp",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        self.assertIsNone(student.last_login)

        self.sign_in("stamp@example.com", KIM_PASSWORD)

        student.refresh_from_db()
        self.assertIsNotNone(student.last_login)

    def test_an_already_enrolled_row_is_skipped_and_nothing_is_sent(self):
        student = User.objects.create_user(
            email="enrolled@example.com",
            password="Enrolled-Pw-77",  # pragma: allowlist secret
            first_name="Al",
            last_name="Ready",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        StudentCourse.objects.create(
            student=student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        response = self._import("Al,Ready,enrolled@example.com")

        result = response.data["results"][0]
        self.assertEqual(
            (result["status"], result["error"]), ("skipped", "Already enrolled")
        )
        self.notify.send_added_to_course_email.assert_not_called()
        self.notify.send_student_login_invitation_email.assert_not_called()

    def test_another_schools_student_is_refused_and_untouched(self):
        other_school = School.objects.create(name="Other School")
        foreign = User.objects.create_user(
            email="foreign@example.com",
            password=None,
            first_name="Far",
            last_name="Away",
            user_type=UserTypes.STUDENT,
            is_active=False,
            school=other_school,
            activation_token="654321",
            activation_expires=timezone.now() + timezone.timedelta(hours=1),
        )

        response = self._import("Far,Away,foreign@example.com")

        result = response.data["results"][0]
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["error"], CROSS_SCHOOL_REJECTION_MESSAGE)
        foreign.refresh_from_db()
        self.assertFalse(foreign.is_active)
        self.assertEqual(foreign.activation_token, "654321")
        self.assertFalse(StudentCourse.objects.filter(student=foreign).exists())
        self.notify.send_student_login_invitation_email.assert_not_called()

    def test_a_staff_email_is_refused(self):
        User.objects.create_user(
            email="colleague@example.com",
            password=TEACHER_PASSWORD,
            first_name="Col",
            last_name="League",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

        response = self._import("Col,League,colleague@example.com")

        self.assertEqual(response.data["results"][0]["status"], "failed")
        self.assertFalse(
            StudentCourse.objects.filter(
                student__email="colleague@example.com"
            ).exists()
        )

    def test_no_row_of_a_mixed_import_leaves_a_student_with_a_code(self):
        self._import(
            "Amy,One,a.one@example.com\n"
            "Bea,Two,b.two@example.com\n"
            "Cal,Three,\n"  # no email: the direct-add path, already ready to use
        )

        students = User.objects.filter(user_type=UserTypes.STUDENT)
        self.assertEqual(students.count(), 3)
        self.assertFalse(students.exclude(activation_token__isnull=True).exists())
        self.assertFalse(students.filter(is_active=False).exists())


@override_settings(CACHES=LOCMEM_CACHE)
class SingleAddExistingStudentTests(SignInHelpers):
    """POST /course/<id>/students (single add) shares enroll_student_by_email.
    Before this change it reset the password of any student whose
    must_change_password flag was still set - a pre-existing bug on beta and
    production, fixed by the same "has ever signed in" rule."""

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="single.teacher@example.com",
            password=TEACHER_PASSWORD,
            first_name="Single",
            last_name="Teacher",
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        session = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Chemistry", teacher=self.teacher, session=session
        )
        self.url = reverse("course-students", kwargs={"pk": self.course.pk})
        self.client.force_authenticate(user=self.teacher)
        patcher = patch(NOTIFY)
        self.notify = patcher.start()
        self.addCleanup(patcher.stop)

    def _student(self, email, password):
        student = User.objects.create_user(
            email=email,
            password=password,
            first_name="Ex",
            last_name="Isting",
            user_type=UserTypes.STUDENT,
            is_active=True,
        )
        User.objects.filter(pk=student.pk).update(must_change_password=True)
        return student

    def _add(self, email):
        response = self.client.post(self.url, {"email": email}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        return response

    def test_a_signed_in_student_is_enrolled_and_keeps_their_password(self):
        kim = self._student("kim@example.com", KIM_PASSWORD)
        self.sign_in("kim@example.com", KIM_PASSWORD)

        response = self._add("kim@example.com")

        self.assertIs(response.data["is_new_student"], False)
        kim.refresh_from_db()
        self.assertTrue(kim.check_password(KIM_PASSWORD))
        self.assertEqual(
            StudentCourse.objects.get(
                student=kim, course=self.course
            ).enrollment_status,
            EnrollmentStatusType.ENROLLED,
        )
        self.notify.send_student_login_invitation_email.assert_not_called()
        self.notify.send_added_to_course_email.assert_called_once()

    def test_a_student_who_never_signed_in_is_re_sent_credentials(self):
        never = self._student("never@example.com", "Unused-Temp-Pw-3")

        response = self._add("never@example.com")

        self.assertIs(response.data["is_new_student"], True)
        never.refresh_from_db()
        self.assertFalse(never.check_password("Unused-Temp-Pw-3"))
        self.notify.send_student_login_invitation_email.assert_called_once()
