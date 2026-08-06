from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import Course, School, Session
from students.models import StudentSubmission
from students.services import (
    _maybe_notify_admins_grading_complete,
    upload_answers_engine,
)
from users.models import CustomUser, UserTypes


class StudentSubmissionGradeUpdateTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="One",
        )
        self.student = CustomUser.objects.create_user(
            email="student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="One",
        )
        self.session = Session.objects.create(name="Test Session", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Test Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Test Assignment",
            course=self.course,
            questions={"q1": "What is 1+1?"},
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )

        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers={"q1": "2"},
            score=80.00,
            score_percentage=80.00,
            max_points=100,
            feedback={
                "grading_summary": {
                    "total_score": 80.00,
                    "max_total_points": 100,
                    "percentage": 80.00,
                }
            },
        )
        self.url = reverse(
            "student-submission-update-grade", kwargs={"pk": self.submission.pk}
        )

    def test_teacher_can_update_grade(self):
        self.client.force_authenticate(user=self.teacher)
        # StudentSubmissionGradeUpdateSerializer only accepts `score` -
        # `feedback` isn't a writable field here, so it's ignored on
        # input. Only the grading_summary numbers inside the existing
        # feedback dict get recomputed from the new score.
        data = {"score": 95.00, "feedback": {"overall": "Great job!"}}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.submission.refresh_from_db()
        self.assertEqual(float(self.submission.score), 95.00)
        self.assertEqual(float(self.submission.score_percentage), 95.00)
        self.assertEqual(
            self.submission.feedback["grading_summary"],
            {"total_score": 95.00, "max_total_points": 100, "percentage": 95.00},
        )
        self.assertTrue(self.submission.was_regraded)
        self.assertIsNotNone(self.submission.regraded_at)

    def test_student_cannot_update_grade(self):
        self.client.force_authenticate(user=self.student)
        data = {"score": 100.00}
        response = self.client.patch(self.url, data, format="json")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)


class StudentSubmissionCrossTenantAccessTest(APITestCase):
    """
    Regression coverage for an IDOR: `grade` and `teacher_feedback` used to
    fetch the submission with StudentSubmission.objects.get(pk=pk) directly,
    bypassing get_queryset()'s `assignment__course__teacher=user` scoping
    that every other teacher-only action on this viewset relies on. Any
    authenticated teacher could grade or read any submission in the system
    given only its UUID. Fixed by routing both actions through
    self.get_object(), which applies get_queryset() + object permissions.
    """

    def setUp(self):
        self.owning_teacher = CustomUser.objects.create_user(
            email="owner@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Owning",
            last_name="Teacher",
        )
        self.other_teacher = CustomUser.objects.create_user(
            email="other@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Other",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="student2@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Some",
            last_name="Student",
        )
        session = Session.objects.create(
            name="Test Session", teacher=self.owning_teacher
        )
        self.course = Course.objects.create(
            name="Test Course", teacher=self.owning_teacher, session=session
        )
        self.assignment = Assignment.objects.create(
            title="Test Assignment",
            course=self.course,
            questions={"q1": "What is 1+1?"},
        )
        self.submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=self.student,
            answers={"q1": "2"},
        )

        # HasCreditBalance runs before the object lookup — an attacker with
        # an empty wallet gets a 403 either way, which would mask whether
        # the IDOR fix is actually in place. Give the attacking teacher
        # credits so a pass here can only mean the queryset scoping worked.
        for teacher in (self.owning_teacher, self.other_teacher):
            wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
            CreditBucket.objects.create(
                wallet=wallet,
                bucket_type=CreditBucketType.MONTHLY,
                total_credits=100_000,
                used_credits=0,
                expires_at=timezone.now() + timedelta(days=30),
            )

        self.grade_url = reverse(
            "student-submission-grade", kwargs={"pk": self.submission.pk}
        )
        self.feedback_url = reverse(
            "student-submission-teacher-feedback", kwargs={"pk": self.submission.pk}
        )

    @patch("students.views.grade_engine")
    def test_other_teacher_cannot_grade_submission(self, mock_grade_engine):
        self.client.force_authenticate(user=self.other_teacher)
        response = self.client.post(self.grade_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        # The load-bearing assertion: a denied request must not reach the
        # AI pipeline and spend the attacker's (or anyone's) credits.
        mock_grade_engine.assert_not_called()

    @patch("students.views.grade_engine")
    def test_owning_teacher_can_grade_submission(self, mock_grade_engine):
        mock_grade_engine.return_value = self.submission
        self.client.force_authenticate(user=self.owning_teacher)
        response = self.client.post(self.grade_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_grade_engine.assert_called_once()

    def test_other_teacher_cannot_read_teacher_feedback(self):
        self.submission.feedback = {
            "grading_summary": {
                "total_score": 8,
                "max_total_points": 10,
                "percentage": 80,
            }
        }
        self.submission.formatted_grade = "Great work!"
        self.submission.save(update_fields=["feedback", "formatted_grade"])

        self.client.force_authenticate(user=self.other_teacher)
        response = self.client.get(self.feedback_url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_owning_teacher_can_read_teacher_feedback(self):
        self.submission.feedback = {
            "grading_summary": {
                "total_score": 8,
                "max_total_points": 10,
                "percentage": 80,
            }
        }
        self.submission.formatted_grade = "Great work!"
        self.submission.save(update_fields=["feedback", "formatted_grade"])

        self.client.force_authenticate(user=self.owning_teacher)
        response = self.client.get(self.feedback_url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class StudentSubmissionNotificationTest(APITestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="teacher-notify@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name="Notify",
        )
        self.student = CustomUser.objects.create_user(
            email="student-notify@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="Notify",
        )
        self.session = Session.objects.create(
            name="Notification Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Notification Course", teacher=self.teacher, session=self.session
        )
        self.assignment = Assignment.objects.create(
            title="Notification Assignment",
            course=self.course,
            questions={"q1": "What is 2+2?"},
        )
        self.content = [{"type": "text", "text": "mock submission"}]
        self.mock_submission_payload = {
            "answers": [{"question_number": 1, "answer_html": "<p>4</p>"}]
        }

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_sent_when_teacher_enables_student_submission_notifications(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_called_once()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_not_sent_when_teacher_disables_student_submission_notifications(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = False
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_not_called()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_email_not_sent_again_when_existing_submission_is_updated(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload

        upload_answers_engine(self.assignment, self.content, self.student)
        upload_answers_engine(self.assignment, self.content, self.student)

        mock_send_email.assert_called_once()
        self.assertEqual(StudentSubmission.objects.count(), 1)

    @patch("students.services.send_email_task.delay")
    @patch("students.services.ai_processor.extract_answer_with_retry")
    def test_notification_failure_does_not_abort_submission_creation(
        self, mock_extract_answer_with_retry, mock_send_email
    ):
        self.teacher.settings.notify_student_submission = True
        self.teacher.settings.save(update_fields=["notify_student_submission"])
        mock_extract_answer_with_retry.return_value = self.mock_submission_payload
        mock_send_email.side_effect = RuntimeError("queue failed")

        upload_answers_engine(self.assignment, self.content, self.student)

        self.assertEqual(StudentSubmission.objects.count(), 1)
        mock_send_email.assert_called_once()


class SchoolAdminGradingCompleteNotificationTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Grading Complete School")

        self.admin = CustomUser.objects.create_user(
            email="grading-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Grading",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_grading_complete = True
        self.admin.settings.save(update_fields=["notify_grading_complete"])

        self.teacher = CustomUser.objects.create_user(
            email="grading-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Grading",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(name="Grading Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Grading Course",
            teacher=self.teacher,
            session=self.session,
        )
        self.assignment = Assignment.objects.create(
            title="Grading Assignment",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions={"q1": "What is 1+1?"},
        )

        self.student_one = CustomUser.objects.create_user(
            email="grading-student-one@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="One",
        )
        self.student_two = CustomUser.objects.create_user(
            email="grading-student-two@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name="Two",
        )

    def _make_submission(self, student, *, graded=True):
        return StudentSubmission.objects.create(
            assignment=self.assignment,
            student=student,
            answers={"q1": "2"},
            score=100,
            score_percentage=100,
            graded_at=timezone.now() if graded else None,
        )

    @patch("students.services.send_email_task.delay")
    def test_notifies_once_when_all_submissions_graded(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        self._make_submission(self.student_two, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
        self.assertIn(
            "Grading complete", mock_send_email.mock_calls[0].kwargs["subject"]
        )
        self.assignment.refresh_from_db()
        self.assertIsNotNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_when_some_submissions_ungraded(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        self._make_submission(self.student_two, graded=False)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_when_no_submissions_exist(self, mock_send_email):
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_notify_for_unpublished_assignment(self, mock_send_email):
        self.assignment.status = AssignmentStatus.DRAFT
        self.assignment.save(update_fields=["status"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()
        self.assignment.refresh_from_db()
        self.assertIsNone(self.assignment.admin_grading_notified_at)

    @patch("students.services.send_email_task.delay")
    def test_does_not_renotify_on_regrade(self, mock_send_email):
        self._make_submission(self.student_one, graded=True)
        _maybe_notify_admins_grading_complete(self.assignment)
        mock_send_email.assert_called_once()

        mock_send_email.reset_mock()
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    def test_concurrent_completion_only_claims_once(self):
        """Simulates two grading workers racing to observe "all graded"
        simultaneously: only one atomic UPDATE should succeed."""
        self._make_submission(self.student_one, graded=True)

        first_claim = Assignment.objects.filter(
            pk=self.assignment.pk, admin_grading_notified_at__isnull=True
        ).update(admin_grading_notified_at=timezone.now())
        second_claim = Assignment.objects.filter(
            pk=self.assignment.pk, admin_grading_notified_at__isnull=True
        ).update(admin_grading_notified_at=timezone.now())

        self.assertEqual(first_claim, 1)
        self.assertEqual(second_claim, 0)

    @patch("students.services.send_email_task.delay")
    def test_no_notification_when_teacher_has_no_school(self, mock_send_email):
        self.teacher.school = None
        self.teacher.save(update_fields=["school"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_no_notification_when_admin_not_opted_in(self, mock_send_email):
        self.admin.settings.notify_grading_complete = False
        self.admin.settings.save(update_fields=["notify_grading_complete"])
        self._make_submission(self.student_one, graded=True)

        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_not_called()

    @patch("students.services.send_email_task.delay")
    def test_admin_in_different_school_not_notified(self, mock_send_email):
        other_school = School.objects.create(name="Other School")
        other_admin = CustomUser.objects.create_user(
            email="other-grading-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Other",
            last_name="Admin",
            school=other_school,
            is_active=True,
        )
        other_admin.settings.notify_grading_complete = True
        other_admin.settings.save(update_fields=["notify_grading_complete"])

        self._make_submission(self.student_one, graded=True)
        _maybe_notify_admins_grading_complete(self.assignment)

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
