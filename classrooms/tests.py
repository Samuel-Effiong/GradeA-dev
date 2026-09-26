from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from users.models import ACTIVATION_TOKEN_VALIDITY

from .models import Course, EnrollmentStatusType, School, Session, StudentCourse

User = get_user_model()


class SchoolModelTest(TestCase):
    """
    Tests for the School model.
    """

    def setUp(self):
        self.school_data = {
            "name": "Greenwood High",
            "address": "123 Education Lane",
            "phone": "+1234567890",
            "website": "https://greenwood.edu",
        }
        self.school = School.objects.create(**self.school_data)

    def test_school_creation(self):
        """Test that a School instance is created correctly."""
        self.assertEqual(self.school.name, self.school_data["name"])
        self.assertEqual(self.school.address, self.school_data["address"])
        self.assertEqual(self.school.phone, self.school_data["phone"])
        self.assertEqual(self.school.website, self.school_data["website"])
        self.assertIsNotNone(self.school.id)

    def test_school_str_representation(self):
        """Test the __str__ method of the School model."""
        self.assertEqual(str(self.school), self.school.name)

    def test_school_name_uniqueness(self):
        """Test that school names must be unique."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                School.objects.create(name="Greenwood High")

    def test_school_optional_fields(self):
        """Test that optional fields can be blank/null."""
        school = School.objects.create(name="Minimal School")
        self.assertIsNone(school.address)
        self.assertIsNone(school.phone)
        self.assertIsNone(school.website)


class SessionModelTest(TestCase):
    """
    Tests for the Session model.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Test",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Fall 2024", teacher=self.teacher)

    def test_session_creation(self):
        """Test that a Session instance is created correctly."""
        self.assertEqual(self.session.name, "Fall 2024")
        self.assertEqual(self.session.teacher, self.teacher)
        self.assertIsNotNone(self.session.id)

    def test_session_name_uniqueness_per_teacher(self):
        """Test the unique constraint: name per teacher."""
        # Same teacher, same name -> should fail. Session.save() calls
        # full_clean(), so validate_unique() catches this and raises
        # ValidationError before the DB constraint is ever reached.
        with self.assertRaises(ValidationError):
            Session.objects.create(name="Fall 2024", teacher=self.teacher)

        # Different teacher, same name -> should pass
        teacher2 = User.objects.create_user(
            email="teacher2@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Test2",
            last_name="Teacher2",
        )
        Session.objects.create(name="Fall 2024", teacher=teacher2)

    def test_session_ordering(self):
        """Test that sessions are ordered by created_at descending."""
        Session.objects.all().delete()
        today = date.today()
        yesterday = today - timedelta(days=1)

        s1 = Session.objects.create(name="Old Session", teacher=self.teacher)
        # Manually updating created_at because auto_now_add makes it uneditable during creation
        Session.objects.filter(id=s1.id).update(created_at=yesterday)

        s2 = Session.objects.create(name="New Session", teacher=self.teacher)
        Session.objects.filter(id=s2.id).update(created_at=today)

        sessions = Session.objects.all()
        self.assertEqual(sessions[0].name, "New Session")
        self.assertEqual(sessions[1].name, "Old Session")


class CourseModelTest(TestCase):
    """
    Tests for the Course model.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="course_teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Course",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Spring 2025", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Mathematics 101",
            teacher=self.teacher,
            session=self.session,
            description="Basic Math",
        )

    def test_course_creation(self):
        """Test that a Course instance is created correctly."""
        self.assertEqual(self.course.name, "Mathematics 101")
        self.assertEqual(self.course.teacher, self.teacher)
        self.assertEqual(self.course.session, self.session)
        self.assertTrue(self.course.is_active)

    def test_course_str_representation(self):
        """Test the __str__ method of the Course model."""
        expected_str = f"{self.session.name} - {self.course.name}"
        self.assertEqual(str(self.course), expected_str)

    def test_course_uniqueness_per_session(self):
        """Test unique constraint: name, teacher, session."""
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Course.objects.create(
                    name="Mathematics 101", teacher=self.teacher, session=self.session
                )

    def test_course_null_teacher_or_session(self):
        """Test that teacher and session can be null as per model definition."""
        course = Course.objects.create(name="Generic Course")
        self.assertIsNone(course.teacher)
        self.assertIsNone(course.session)

    def test_course_ordering(self):
        """Test that courses are ordered by name."""
        Course.objects.all().delete()
        Course.objects.create(name="B Course")
        Course.objects.create(name="A Course")
        courses = Course.objects.all()
        self.assertEqual(courses[0].name, "A Course")
        self.assertEqual(courses[1].name, "B Course")


class StudentCourseModelTest(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            email="teacher.studentcourse@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Course",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="Summer 2025", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology",
            teacher=self.teacher,
            session=self.session,
        )

    def test_blank_pending_student_names_do_not_conflict(self):
        first_pending_student = User.objects.create_user(
            email="pending1@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="",
            last_name="",
            user_type="STUDENT",
            is_active=False,
        )
        second_pending_student = User.objects.create_user(
            email="pending2@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="",
            last_name="",
            user_type="STUDENT",
            is_active=False,
        )

        StudentCourse.objects.create(
            student=first_pending_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        StudentCourse.objects.create(
            student=second_pending_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        self.assertEqual(StudentCourse.objects.filter(course=self.course).count(), 2)

    def test_exact_student_names_still_must_be_unique_per_course(self):
        first_student = User.objects.create_user(
            email="john.one@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="John",
            last_name="Doe",
            middle_name="",
            user_type="STUDENT",
            is_active=True,
        )
        second_student = User.objects.create_user(
            email="john.two@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="John",
            last_name="Doe",
            middle_name="",
            user_type="STUDENT",
            is_active=True,
        )

        StudentCourse.objects.create(
            student=first_student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        with self.assertRaises(ValidationError):
            StudentCourse.objects.create(
                student=second_student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )


class CourseStudentsActionAuthorizationTest(APITestCase):
    """
    Regression coverage for CourseViewSet.students(): it used to look the
    course up with a bare Course.objects.select_for_update().get(pk=...),
    bypassing get_queryset()'s teacher-ownership filter entirely. Any
    authenticated teacher could enroll a student into any other teacher's
    course just by knowing/guessing the course id.
    """

    def setUp(self):
        self.teacher_a = User.objects.create_user(
            email="course-idor-teacher-a@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="A",
            user_type="TEACHER",
            is_active=True,
        )
        self.teacher_b = User.objects.create_user(
            email="course-idor-teacher-b@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="B",
            user_type="TEACHER",
            is_active=True,
        )
        session_b = Session.objects.create(name="B's Session", teacher=self.teacher_b)
        self.teacher_b_course = Course.objects.create(
            name="B's Course", teacher=self.teacher_b, session=session_b
        )
        self.url = reverse("course-students", kwargs={"pk": self.teacher_b_course.pk})

    def test_teacher_cannot_enroll_a_student_into_another_teachers_course(self):
        self.client.force_authenticate(user=self.teacher_a)

        response = self.client.post(
            self.url, {"email": "some-student@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(
            StudentCourse.objects.filter(course=self.teacher_b_course).exists()
        )

    def test_owning_teacher_can_still_enroll_a_student(self):
        # Regression guard for the happy path: the fix must not turn a
        # legitimate enrollment into a 404 too.
        student = User.objects.create_user(
            email="course-idor-student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Some",
            last_name="Student",
            user_type="STUDENT",
            is_active=True,
        )
        self.client.force_authenticate(user=self.teacher_b)

        response = self.client.post(self.url, {"email": student.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(
            StudentCourse.objects.filter(
                course=self.teacher_b_course, student=student
            ).exists()
        )


class ActivationTokenValidityWindowTest(APITestCase):
    """
    Locks the shortened activation-token expiry in place. Previously 7
    days with no throttle on the verify endpoint, the combination made a
    6-digit numeric activation_token brute-forceable for a known email
    well within its validity window. The token stays 6 digits (by design -
    memorable, not clicked from a link), so the mitigation is a shorter
    window (see users.models.ACTIVATION_TOKEN_VALIDITY) plus a dedicated
    throttle on verify (see users.tests_throttling).

    The single-add invite (`course/<pk>/students`) no longer creates an
    activation_token at all - new students there are active immediately
    with a temporary password (see classrooms/services/enrollment.py). The
    bulk/CSV roster import's with-email branch is untouched and is now the
    only surviving path that creates this state, so the regression is
    exercised through it instead.
    """

    def test_bulk_imported_student_gets_the_shortened_window(self):
        teacher = User.objects.create_user(
            email="activation-window-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Teacher",
            last_name="Window",
            user_type="TEACHER",
            is_active=True,
        )
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        url = reverse("course-bulk-add-students", kwargs={"pk": course.pk})
        self.client.force_authenticate(user=teacher)

        before = timezone.now()
        response = self.client.post(
            url,
            {"raw_data": "New,Student,new-student@example.com"},
            format="json",
        )
        after = timezone.now()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student = User.objects.get(email="new-student@example.com")

        self.assertIsNotNone(student.activation_expires)
        self.assertGreaterEqual(
            student.activation_expires, before + ACTIVATION_TOKEN_VALIDITY
        )
        self.assertLessEqual(
            student.activation_expires, after + ACTIVATION_TOKEN_VALIDITY
        )
        # And explicitly not the old 7-day window.
        self.assertLess(student.activation_expires, before + timedelta(days=2))


class RenewActivationTokenTest(APITestCase):
    """
    Covers handle_expired_token (POST /course/renew-student-token). It used
    to filter `StudentCourse.objects.filter(student=user, is_active=False)`
    - but StudentCourse has no `is_active` field (that's PENDING vs
    ENROLLED/WITHDRAWN/COMPLETED via `enrollment_status`), so the "happy
    path" always raised FieldError and the caller only ever saw a 500.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="renew-token-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Renew",
            last_name="Teacher",
            user_type="TEACHER",
            is_active=True,
        )
        self.session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=self.session
        )
        self.student = User.objects.create_user(
            email="renew-token-student@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Renew",
            last_name="Student",
            user_type="STUDENT",
            is_active=False,
            activation_token="123456",
            activation_expires=timezone.now() - timedelta(hours=1),
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        self.url = reverse("course-renew-activation-token")

    def test_renews_token_for_pending_enrollment(self):
        old_token = self.student.activation_token

        response = self.client.post(self.url, {"token": old_token}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.student.refresh_from_db()
        self.assertNotEqual(self.student.activation_token, old_token)
        self.assertGreater(self.student.activation_expires, timezone.now())

    def test_unknown_token_returns_400_not_500(self):
        """An unknown token is an expected, client-caused case (ParseError)
        - it must surface as a 400 with the real message, not fall through
        the blanket `except Exception` into a generic 500."""
        response = self.client.post(self.url, {"token": "000000"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid token", str(response.data.get("detail", "")))

    def test_token_with_no_pending_enrollment_returns_400_not_500(self):
        """Same ParseError-swallowing bug, the other raise site in this
        view: a real, inactive user whose only enrollment isn't PENDING."""
        self.student.enrollments.update(enrollment_status=EnrollmentStatusType.ENROLLED)

        response = self.client.post(
            self.url, {"token": self.student.activation_token}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("No pending enrollment", str(response.data.get("detail", "")))


class EnrollStudentByEmailActiveImmediatelyTest(APITestCase):
    """
    Covers classrooms/services/enrollment.py::enroll_student_by_email after
    the student-invite-active change: a newly (or freshly re-) invited
    student is active immediately with a system-generated temporary
    password, mirroring the license-teacher invite
    (billing/license_service.py), instead of the old
    is_active=False + activation_token scheme.
    """

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="invite-active-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Invite",
            last_name="Teacher",
            user_type="TEACHER",
            is_active=True,
        )
        self.session = Session.objects.create(name="S", teacher=self.teacher)
        self.course = Course.objects.create(
            name="C", teacher=self.teacher, session=self.session
        )
        self.url = reverse("course-students", kwargs={"pk": self.course.pk})
        self.client.force_authenticate(user=self.teacher)

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_new_student_is_created_active_with_no_activation_token(self, mock_email):
        response = self.client.post(
            self.url, {"email": "brand-new-student@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student = User.objects.get(email="brand-new-student@example.com")

        self.assertTrue(student.is_active)
        self.assertTrue(student.must_change_password)
        self.assertIsNone(student.activation_token)
        self.assertIsNone(student.activation_expires)

        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.PENDING)

        mock_email.assert_called_once()
        called_student, called_course, called_password = mock_email.call_args[0]
        self.assertEqual(called_student, student)
        self.assertEqual(called_course, self.course)
        self.assertTrue(student.check_password(called_password))

    @patch("classrooms.services.notifications.send_added_to_course_email")
    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_already_onboarded_student_is_enrolled_immediately_no_password_reset(
        self, mock_login_email, mock_added_email
    ):
        student = User.objects.create_user(
            email="already-onboarded@example.com",
            password="whatever-they-chose",  # pragma: allowlist secret
            first_name="Already",
            last_name="Onboarded",
            user_type="STUDENT",
            is_active=True,
            must_change_password=False,
        )
        old_password_hash = student.password

        response = self.client.post(self.url, {"email": student.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student.refresh_from_db()

        self.assertEqual(student.password, old_password_hash)
        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.ENROLLED)

        mock_added_email.assert_called_once_with(student, self.course)
        mock_login_email.assert_not_called()

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_mid_onboarding_student_invited_to_second_course_gets_fresh_password(
        self, mock_email
    ):
        """
        SM-flagged scenario: a student invited to course A who never logs
        in, then invited to course B, must get a genuinely new, working
        password for course B - not a silent no-op.
        """
        student = User.objects.create_user(
            email="two-course-student@example.com",
            password="original-temp-password",  # pragma: allowlist secret
            first_name="Two",
            last_name="Course",
            user_type="STUDENT",
            is_active=True,
            must_change_password=True,
        )
        other_session = Session.objects.create(name="S2", teacher=self.teacher)
        course_a = Course.objects.create(
            name="Course A", teacher=self.teacher, session=other_session
        )
        StudentCourse.objects.create(
            student=student,
            course=course_a,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        response = self.client.post(self.url, {"email": student.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student.refresh_from_db()

        self.assertTrue(student.is_active)
        self.assertTrue(student.must_change_password)

        enrollment_b = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(enrollment_b.enrollment_status, EnrollmentStatusType.PENDING)
        # Course A's enrollment is untouched by inviting to course B.
        enrollment_a = StudentCourse.objects.get(student=student, course=course_a)
        self.assertEqual(enrollment_a.enrollment_status, EnrollmentStatusType.PENDING)

        mock_email.assert_called_once()
        called_student, called_course, called_password = mock_email.call_args[0]
        self.assertEqual(called_course, self.course)
        self.assertTrue(student.check_password(called_password))
        self.assertFalse(student.check_password("original-temp-password"))

    @patch("classrooms.services.notifications.send_student_login_invitation_email")
    def test_legacy_pending_row_self_heals_instead_of_fast_pathing(self, mock_email):
        """
        A pre-migration legacy row: is_active=False with an activation_token
        from the old scheme, and must_change_password left at its model
        default of False (the old scheme never set that field). The gate
        is `is_active and not must_change_password`, not
        `not must_change_password` alone, specifically so a row like this -
        which cannot actually log in, since is_active is False - is not
        mistaken for "already onboarded" and fast-pathed into ENROLLED with
        the "you're already active, sign in" email. Instead it must be
        healed: given a real password, activated, and sent the login email.
        """
        student = User.objects.create(
            email="legacy-pending@example.com",
            first_name="Legacy",
            last_name="Pending",
            user_type="STUDENT",
            is_active=False,
            must_change_password=False,
            activation_token="654321",
            activation_expires=timezone.now() + ACTIVATION_TOKEN_VALIDITY,
        )
        student.set_unusable_password()
        student.save()

        response = self.client.post(self.url, {"email": student.email}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        student.refresh_from_db()

        self.assertTrue(student.is_active)
        self.assertTrue(student.must_change_password)
        self.assertTrue(student.has_usable_password())

        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.PENDING)

        mock_email.assert_called_once()
        called_student, called_course, called_password = mock_email.call_args[0]
        self.assertEqual(called_course, self.course)
        self.assertTrue(student.check_password(called_password))


class ActivatePendingEnrollmentsOnLoginTest(APITestCase):
    """
    Covers users/serializers.py's CustomTokenObtainPairSerializer.validate
    hook into classrooms.services.enrollment.activate_pending_enrollments_on_login:
    a successful student login flips every PENDING enrollment for that
    student to ENROLLED, so the "invited but never clicked anything" state
    resolves itself the moment the student actually signs in.
    """

    PASSWORD = "correct-horse-battery-staple-1"  # pragma: allowlist secret

    def setUp(self):
        self.teacher = User.objects.create_user(
            email="login-activate-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            first_name="Login",
            last_name="Teacher",
            user_type="TEACHER",
            is_active=True,
        )
        self.session = Session.objects.create(name="S", teacher=self.teacher)
        self.login_url = reverse("login")

    def _tightened(self):
        from users.tests_throttling import tightened_rate

        return tightened_rate("login", "1000/min")

    def test_two_pending_enrollments_both_flip_on_one_login(self):
        student = User.objects.create_user(
            email="two-pending@example.com",
            password=self.PASSWORD,
            first_name="Two",
            last_name="Pending",
            user_type="STUDENT",
            is_active=True,
            must_change_password=False,
        )
        course_1 = Course.objects.create(
            name="Course 1", teacher=self.teacher, session=self.session
        )
        course_2 = Course.objects.create(
            name="Course 2", teacher=self.teacher, session=self.session
        )
        enrollment_1 = StudentCourse.objects.create(
            student=student,
            course=course_1,
            enrollment_status=EnrollmentStatusType.PENDING,
        )
        enrollment_2 = StudentCourse.objects.create(
            student=student,
            course=course_2,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        with self._tightened():
            response = self.client.post(
                self.login_url,
                {"email": student.email, "password": self.PASSWORD},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        enrollment_1.refresh_from_db()
        enrollment_2.refresh_from_db()
        self.assertEqual(enrollment_1.enrollment_status, EnrollmentStatusType.ENROLLED)
        self.assertEqual(enrollment_2.enrollment_status, EnrollmentStatusType.ENROLLED)

    def test_no_pending_enrollments_is_a_no_op(self):
        student = User.objects.create_user(
            email="no-pending@example.com",
            password=self.PASSWORD,
            first_name="No",
            last_name="Pending",
            user_type="STUDENT",
            is_active=True,
            must_change_password=False,
        )
        course = Course.objects.create(
            name="Course", teacher=self.teacher, session=self.session
        )
        enrollment = StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

        with self._tightened():
            response = self.client.post(
                self.login_url,
                {"email": student.email, "password": self.PASSWORD},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.ENROLLED)

    def test_non_student_login_never_touches_student_course(self):
        course = Course.objects.create(
            name="Course", teacher=self.teacher, session=self.session
        )
        student = User.objects.create_user(
            email="bystander-student@example.com",
            password=self.PASSWORD,
            first_name="Bystander",
            last_name="Student",
            user_type="STUDENT",
            is_active=True,
            must_change_password=False,
        )
        enrollment = StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.PENDING,
        )

        with self._tightened():
            response = self.client.post(
                self.login_url,
                {
                    "email": self.teacher.email,
                    "password": "password123",  # pragma: allowlist secret
                },
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.enrollment_status, EnrollmentStatusType.PENDING)
