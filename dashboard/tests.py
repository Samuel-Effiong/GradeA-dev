from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from dashboard.models import (
    SchoolAtRiskSnapshot,
    StudentRiskAlertState,
    TeacherInactivityAlertState,
)
from dashboard.risk import RiskInputs, StudentRiskEvaluator
from dashboard.services import (
    SchoolAdminWeeklySummaryService,
    WeeklyCourseSummaryService,
)
from dashboard.tasks import (
    send_at_risk_student_alerts,
    send_teacher_inactivity_alerts,
    send_weekly_course_summaries,
    send_weekly_school_admin_summaries,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserActivity, UserTypes


class StudentRiskEvaluatorTest(SimpleTestCase):
    def setUp(self):
        self.evaluator = StudentRiskEvaluator()

    def test_critical_grade_alone_triggers_at_risk(self):
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=3,
                submitted_count=3,
                graded_scores=[
                    (date(2026, 1, 1), 55.0),
                    (date(2026, 1, 8), 58.0),
                    (date(2026, 1, 15), 56.0),
                ],
            )
        )
        self.assertTrue(result.at_risk)
        self.assertAlmostEqual(result.average_grade, 56.33, places=1)

    def test_critical_missing_work_alone_triggers_at_risk_despite_healthy_grade(self):
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=3,
                submitted_count=1,
                graded_scores=[(date(2026, 1, 1), 82.0)],
            )
        )
        self.assertTrue(result.at_risk)
        self.assertEqual(result.average_grade, 82.0)

    def test_missing_work_below_expected_two_does_not_trigger_critical_bypass(self):
        # Only 1 assignment expected: the critical_missing_work bypass
        # requires expected_assignment_count >= 2.
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=1,
                submitted_count=0,
                graded_scores=[],
            )
        )
        self.assertFalse(result.at_risk)
        self.assertIsNone(result.average_grade)

    def test_single_moderate_flag_is_not_enough(self):
        # grade=65 (flag A only): below 70 but not below 60, full
        # submission, no trend data -> only 1 of 3 moderate flags.
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=1,
                submitted_count=1,
                graded_scores=[(date(2026, 1, 1), 65.0)],
            )
        )
        self.assertFalse(result.at_risk)

    def test_two_moderate_flags_trigger_at_risk(self):
        # grade=65 (flag A) + submission_rate=50% (flag B) = 2 of 3,
        # neither condition alone is critical.
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=4,
                submitted_count=2,
                graded_scores=[(date(2026, 1, 1), 65.0), (date(2026, 1, 8), 65.0)],
            )
        )
        self.assertTrue(result.at_risk)

    def test_zero_graded_submissions_is_not_auto_flagged(self):
        # Regression test for the `avg_grade_val or 0` bug: a student with
        # no graded work yet must not be treated as failing.
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=1,
                submitted_count=1,
                graded_scores=[],
            )
        )
        self.assertIsNone(result.average_grade)
        self.assertFalse(result.at_risk)

    def test_trend_improving_over_full_window(self):
        scores = [
            (date(2026, 1, 1), 50.0),
            (date(2026, 1, 8), 60.0),
            (date(2026, 1, 15), 70.0),
            (date(2026, 1, 22), 80.0),
        ]
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=4, submitted_count=4, graded_scores=scores
            )
        )
        self.assertEqual(result.grade_trend, "IMPROVING")

    def test_trend_declining_over_full_window(self):
        scores = [
            (date(2026, 1, 1), 90.0),
            (date(2026, 1, 8), 75.0),
            (date(2026, 1, 15), 60.0),
        ]
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=3, submitted_count=3, graded_scores=scores
            )
        )
        self.assertEqual(result.grade_trend, "DECLINING")

    def test_trend_ignores_single_outlier_that_endpoint_comparison_would_not(self):
        # [70, 95, 71]: raw first-vs-last comparison is ~flat/slightly up,
        # but a single midpoint spike shouldn't swing the regression fit
        # enough to call it a strong trend either way -> STABLE.
        scores = [
            (date(2026, 1, 1), 70.0),
            (date(2026, 1, 8), 95.0),
            (date(2026, 1, 15), 71.0),
        ]
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=3, submitted_count=3, graded_scores=scores
            )
        )
        self.assertEqual(result.grade_trend, "STABLE")

    def test_trend_same_day_scores_falls_back_to_first_vs_last(self):
        same_day = date(2026, 1, 1)
        scores = [(same_day, 90.0), (same_day, 60.0)]
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=2, submitted_count=2, graded_scores=scores
            )
        )
        self.assertEqual(result.grade_trend, "DECLINING")
        self.assertEqual(result.trend_delta, -30.0)

    def test_trend_insufficient_data_below_two_scores(self):
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=1,
                submitted_count=1,
                graded_scores=[(date(2026, 1, 1), 40.0)],
            )
        )
        self.assertEqual(result.grade_trend, "INSUFFICIENT DATA")

    def test_trend_only_uses_last_six_scores(self):
        # An old, sharply declining pair falls outside the 6-score window
        # and should not affect a otherwise-flat recent trend.
        old_decline = [
            (date(2026, 1, 1), 95.0),
            (date(2026, 1, 2), 40.0),
        ]
        recent_flat = [
            (date(2026, 2, 1), 70.0),
            (date(2026, 2, 8), 71.0),
            (date(2026, 2, 15), 69.0),
            (date(2026, 2, 22), 70.0),
            (date(2026, 3, 1), 70.0),
            (date(2026, 3, 8), 71.0),
        ]
        result = self.evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=8,
                submitted_count=8,
                graded_scores=old_decline + recent_flat,
            )
        )
        self.assertEqual(result.grade_trend, "STABLE")


class WeeklyCourseSummaryServiceTest(TestCase):
    def setUp(self):
        self.service = WeeklyCourseSummaryService()
        self.now = timezone.now()

        self.teacher = CustomUser.objects.create_user(
            email="weekly-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Weekly",
            last_name="Teacher",
        )
        self.session = Session.objects.create(name="2026 Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Biology",
            teacher=self.teacher,
            session=self.session,
        )

        self.student_low = CustomUser.objects.create_user(
            email="student-low@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Low",
            last_name="Performer",
        )
        self.student_missing = CustomUser.objects.create_user(
            email="student-missing@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Missing",
            last_name="Work",
        )
        self.student_up = CustomUser.objects.create_user(
            email="student-up@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Trending",
            last_name="Up",
        )

        for student in [self.student_low, self.student_missing, self.student_up]:
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        self.assignment_1 = Assignment.objects.create(
            title="Cells Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=20),
        )
        self.assignment_2 = Assignment.objects.create(
            title="Genetics Homework",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=10),
        )
        self.assignment_3 = Assignment.objects.create(
            title="Lab Reflection",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=3),
        )

        StudentSubmission.objects.create(
            assignment=self.assignment_1,
            student=self.student_low,
            answers={"q1": "a"},
            score=45,
            score_percentage=45,
            graded_at=self.now - timedelta(days=19),
        )
        StudentSubmission.objects.create(
            assignment=self.assignment_2,
            student=self.student_low,
            answers={"q1": "b"},
            score=40,
            score_percentage=40,
            graded_at=self.now - timedelta(days=9),
        )
        StudentSubmission.objects.create(
            assignment=self.assignment_3,
            student=self.student_low,
            answers={"q1": "c"},
            score=35,
            score_percentage=35,
            graded_at=self.now - timedelta(days=2),
        )

        StudentSubmission.objects.create(
            assignment=self.assignment_1,
            student=self.student_missing,
            answers={"q1": "a"},
            score=82,
            score_percentage=82,
            graded_at=self.now - timedelta(days=18),
        )

        StudentSubmission.objects.create(
            assignment=self.assignment_1,
            student=self.student_up,
            answers={"q1": "a"},
            score=60,
            score_percentage=60,
            graded_at=self.now - timedelta(days=19),
        )
        StudentSubmission.objects.create(
            assignment=self.assignment_2,
            student=self.student_up,
            answers={"q1": "b"},
            score=72,
            score_percentage=72,
            graded_at=self.now - timedelta(days=8),
        )
        StudentSubmission.objects.create(
            assignment=self.assignment_3,
            student=self.student_up,
            answers={"q1": "c"},
            score=88,
            score_percentage=88,
            graded_at=self.now - timedelta(days=1),
        )

    def test_build_course_summary_returns_at_risk_students_and_interventions(self):
        summary = self.service.build_course_summary(self.course, as_of=self.now)

        self.assertEqual(summary["course"]["name"], "Biology")
        self.assertEqual(summary["overall"]["student_count"], 3)
        self.assertEqual(summary["overall"]["published_assignment_count"], 3)
        self.assertEqual(summary["overall"]["relevant_assignment_count"], 3)

        at_risk_names = [
            student["student_name"] for student in summary["at_risk_students"]
        ]
        self.assertIn(self.student_low.get_full_name(), at_risk_names)
        self.assertIn(self.student_missing.get_full_name(), at_risk_names)
        self.assertNotIn(self.student_up.get_full_name(), at_risk_names)

        trending_up_names = [
            student["student_name"] for student in summary["trend_watch"]["trending_up"]
        ]
        self.assertIn(self.student_up.get_full_name(), trending_up_names)

        commonalities_text = " ".join(summary["commonalities"])
        self.assertIn("missing submissions", commonalities_text.lower())

        intervention_targets = [item["target"] for item in summary["interventions"]]
        self.assertIn(self.student_low.get_full_name(), intervention_targets)
        self.assertIn(self.student_missing.get_full_name(), intervention_targets)

    def test_build_course_summary_does_not_count_future_due_assignment_as_missing(self):
        future_assignment = Assignment.objects.create(
            title="Future Project",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now + timedelta(days=7),
        )

        summary = self.service.build_course_summary(self.course, as_of=self.now)

        self.assertEqual(summary["overall"]["published_assignment_count"], 4)
        self.assertEqual(summary["overall"]["relevant_assignment_count"], 3)

        student_up_summary = next(
            student
            for student in summary["trend_watch"]["trending_up"]
            if student["student_name"] == self.student_up.get_full_name()
        )
        self.assertEqual(student_up_summary["submission_rate"], 100.0)

        self.assertEqual(future_assignment.title, "Future Project")


class WeeklyCourseSummaryTaskTest(TestCase):
    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="summary-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Summary",
            last_name="Teacher",
        )
        self.teacher.settings.notify_weekly_summary = True
        self.teacher.settings.save(update_fields=["notify_weekly_summary"])

        self.session = Session.objects.create(name="Summary Term", teacher=self.teacher)
        self.course_one = Course.objects.create(
            name="Physics",
            teacher=self.teacher,
            session=self.session,
        )
        self.course_two = Course.objects.create(
            name="Chemistry",
            teacher=self.teacher,
            session=self.session,
        )

        self.student = CustomUser.objects.create_user(
            email="summary-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Summary",
            last_name="Student",
        )
        for course in [self.course_one, self.course_two]:
            StudentCourse.objects.create(
                student=self.student,
                course=course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        for course in [self.course_one, self.course_two]:
            assignment = Assignment.objects.create(
                title=f"{course.name} Assignment",
                course=course,
                status=AssignmentStatus.PUBLISHED,
                due_date=timezone.now() - timedelta(days=2),
            )
            StudentSubmission.objects.create(
                assignment=assignment,
                student=self.student,
                answers={"q1": "done"},
                score=82,
                score_percentage=82,
                graded_at=timezone.now() - timedelta(days=1),
            )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_queues_one_email_per_opted_in_course(self, mock_send_email):
        result = send_weekly_course_summaries()

        self.assertIn("Queued 2 weekly course summary email(s).", result)
        self.assertEqual(mock_send_email.call_count, 2)
        subjects = [call.kwargs["subject"] for call in mock_send_email.mock_calls]
        self.assertIn("Weekly course summary: Physics", subjects)
        self.assertIn("Weekly course summary: Chemistry", subjects)

    @patch("dashboard.tasks.ai_processor.generate_weekly_course_summary_narrative")
    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_uses_ai_narration_when_available(
        self, mock_send_email, mock_generate_narrative
    ):
        mock_generate_narrative.return_value = {
            "overall_narrative": "AI overall summary.",
            "at_risk_narrative": "AI at-risk narrative.",
            "commonality_narrative": "AI commonality narrative.",
            "intervention_narrative": "AI intervention narrative.",
        }

        result = send_weekly_course_summaries()

        self.assertIn("Queued 2 weekly course summary email(s).", result)
        self.assertEqual(mock_generate_narrative.call_count, 2)
        self.assertEqual(mock_send_email.call_count, 2)
        self.assertIn(
            "AI overall summary.", mock_send_email.mock_calls[0].kwargs["message"]
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_skips_teachers_who_are_not_opted_in(self, mock_send_email):
        self.teacher.settings.notify_weekly_summary = False
        self.teacher.settings.save(update_fields=["notify_weekly_summary"])

        result = send_weekly_course_summaries()

        self.assertIn("Queued 0 weekly course summary email(s).", result)
        mock_send_email.assert_not_called()

    def test_weekly_summary_schedule_is_registered(self):
        schedule = settings.CELERY_BEAT_SCHEDULE["send-weekly-course-summaries"]

        self.assertEqual(
            schedule["task"],
            "dashboard.tasks.send_weekly_course_summaries",
        )


class SchoolAdminWeeklySummaryServiceTest(TestCase):
    def setUp(self):
        self.service = SchoolAdminWeeklySummaryService()
        self.now = timezone.now()

        self.school = School.objects.create(name="Riverside High")

        self.admin = CustomUser.objects.create_user(
            email="schooladmin-summary@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="School",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )

        self.teacher = CustomUser.objects.create_user(
            email="school-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="School",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(name="School Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="World History",
            teacher=self.teacher,
            session=self.session,
        )

        self.student_at_risk = CustomUser.objects.create_user(
            email="at-risk-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="At",
            last_name="Risk",
            is_active=True,
        )
        self.student_healthy = CustomUser.objects.create_user(
            email="healthy-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Healthy",
            last_name="Student",
            is_active=True,
        )
        for student in [self.student_at_risk, self.student_healthy]:
            StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )

        assignment = Assignment.objects.create(
            title="Essay 1",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=3),
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=self.student_at_risk,
            answers={"q1": "a"},
            score=40,
            score_percentage=40,
            is_published=True,
            graded_at=self.now - timedelta(days=2),
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=self.student_healthy,
            answers={"q1": "a"},
            score=90,
            score_percentage=90,
            is_published=True,
            graded_at=self.now - timedelta(days=2),
        )

    def test_build_school_summary_counts_and_at_risk_students(self):
        summary = self.service.build_school_summary(self.school, as_of=self.now)

        self.assertEqual(summary["school"]["name"], "Riverside High")
        self.assertEqual(summary["overall"]["active_teacher_count"], 1)
        self.assertEqual(summary["overall"]["active_course_count"], 1)
        self.assertEqual(summary["overall"]["assignments_graded_this_week"], 1)

        at_risk_names = [
            student["student_name"] for student in summary["at_risk_students"]
        ]
        self.assertIn(self.student_at_risk.get_full_name(), at_risk_names)
        self.assertNotIn(self.student_healthy.get_full_name(), at_risk_names)
        self.assertEqual(summary["at_risk_student_count"], 1)

    def test_build_school_summary_teacher_activity_row(self):
        summary = self.service.build_school_summary(self.school, as_of=self.now)

        teacher_row = next(
            row for row in summary["teacher_activity"] if row["id"] == self.teacher.id
        )
        self.assertEqual(teacher_row["courses"], 1)
        self.assertEqual(teacher_row["students"], 2)
        self.assertTrue(teacher_row["status"])


class WeeklySchoolAdminSummaryTaskTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Lakeside Academy")

        self.admin = CustomUser.objects.create_user(
            email="lakeside-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Lakeside",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_weekly_summary = True
        self.admin.settings.save(update_fields=["notify_weekly_summary"])

        self.teacher = CustomUser.objects.create_user(
            email="lakeside-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Lakeside",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_queues_one_email_per_opted_in_admin(self, mock_send_email):
        result = send_weekly_school_admin_summaries()

        self.assertIn("Queued 1 weekly school admin summary email(s).", result)
        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["subject"],
            "Weekly school summary: Lakeside Academy",
        )
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"],
            [self.admin.email],
        )

    @patch(
        "dashboard.tasks.ai_processor.generate_weekly_school_admin_summary_narrative"
    )
    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_uses_ai_narration_when_available(
        self, mock_send_email, mock_generate_narrative
    ):
        mock_generate_narrative.return_value = {
            "overall_narrative": "AI school overall summary.",
            "at_risk_narrative": "AI at-risk narrative.",
            "teacher_activity_narrative": "AI teacher activity narrative.",
        }

        result = send_weekly_school_admin_summaries()

        self.assertIn("Queued 1 weekly school admin summary email(s).", result)
        mock_generate_narrative.assert_called_once()
        mock_send_email.assert_called_once()
        self.assertIn(
            "AI school overall summary.",
            mock_send_email.mock_calls[0].kwargs["message"],
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_falls_back_to_plaintext_when_ai_narration_fails(
        self, mock_send_email
    ):
        with patch(
            "dashboard.tasks.ai_processor.generate_weekly_school_admin_summary_narrative",
            side_effect=Exception("AI unavailable"),
        ):
            result = send_weekly_school_admin_summaries()

        self.assertIn("Queued 1 weekly school admin summary email(s).", result)
        mock_send_email.assert_called_once()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_task_skips_admins_who_are_not_opted_in(self, mock_send_email):
        self.admin.settings.notify_weekly_summary = False
        self.admin.settings.save(update_fields=["notify_weekly_summary"])

        result = send_weekly_school_admin_summaries()

        self.assertIn("Queued 0 weekly school admin summary email(s).", result)
        mock_send_email.assert_not_called()

    def test_weekly_school_admin_summary_schedule_is_registered(self):
        schedule = settings.CELERY_BEAT_SCHEDULE["send-weekly-school-admin-summaries"]

        self.assertEqual(
            schedule["task"],
            "dashboard.tasks.send_weekly_school_admin_summaries",
        )


class AtRiskStudentAlertTaskTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="At-Risk School")

        self.admin = CustomUser.objects.create_user(
            email="at-risk-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="At-Risk",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_at_risk_student_alerts = True
        self.admin.settings.save(update_fields=["notify_at_risk_student_alerts"])

        self.teacher = CustomUser.objects.create_user(
            email="at-risk-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="At-Risk",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(name="At-Risk Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="At-Risk Course", teacher=self.teacher, session=self.session
        )

    def _enroll_and_grade(self, student, score):
        StudentCourse.objects.get_or_create(
            student=student,
            course=self.course,
            defaults={"enrollment_status": EnrollmentStatusType.ENROLLED},
        )
        assignment = Assignment.objects.create(
            title=f"Assignment for {student.email}",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions={"q1": "q"},
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=score,
            score_percentage=score,
            is_published=True,
            graded_at=timezone.now(),
        )

    def _make_student(self, suffix):
        return CustomUser.objects.create_user(
            email=f"at-risk-student-{suffix}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name=suffix,
            is_active=True,
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_newly_at_risk_student_triggers_alert_and_persists_state(
        self, mock_send_email
    ):
        student = self._make_student("A")
        self._enroll_and_grade(student, 40)

        result = send_at_risk_student_alerts()

        self.assertIn("Queued 1 at-risk alert email(s).", result)
        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
        state = StudentRiskAlertState.objects.get(student=student, school=self.school)
        self.assertTrue(state.is_at_risk)
        self.assertIsNotNone(state.last_alerted_at)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_healthy_student_never_alerts(self, mock_send_email):
        student = self._make_student("B")
        self._enroll_and_grade(student, 90)

        result = send_at_risk_student_alerts()

        self.assertIn("Queued 0 at-risk alert email(s).", result)
        mock_send_email.assert_not_called()
        self.assertFalse(
            StudentRiskAlertState.objects.filter(
                student=student, school=self.school
            ).exists()
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_student_remaining_at_risk_is_not_realerted_on_second_run(
        self, mock_send_email
    ):
        student = self._make_student("C")
        self._enroll_and_grade(student, 30)

        send_at_risk_student_alerts()
        first_alerted_at = StudentRiskAlertState.objects.get(
            student=student, school=self.school
        ).last_alerted_at
        mock_send_email.reset_mock()

        result = send_at_risk_student_alerts()

        self.assertIn("Queued 0 at-risk alert email(s).", result)
        mock_send_email.assert_not_called()
        state = StudentRiskAlertState.objects.get(student=student, school=self.school)
        self.assertEqual(state.last_alerted_at, first_alerted_at)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_recovered_student_state_clears_and_relapse_realerts(self, mock_send_email):
        student = self._make_student("D")
        self._enroll_and_grade(student, 30)
        send_at_risk_student_alerts()
        self.assertTrue(
            StudentRiskAlertState.objects.get(
                student=student, school=self.school
            ).is_at_risk
        )

        # Recover: a new high-scoring submission pulls the average back up.
        self._enroll_and_grade(student, 100)
        self._enroll_and_grade(student, 100)
        self._enroll_and_grade(student, 100)
        mock_send_email.reset_mock()
        send_at_risk_student_alerts()

        state = StudentRiskAlertState.objects.get(student=student, school=self.school)
        self.assertFalse(state.is_at_risk)
        self.assertIsNone(state.average_score)
        mock_send_email.assert_not_called()

        # Relapse: drag the average back down below the threshold again.
        for _ in range(5):
            self._enroll_and_grade(student, 10)
        mock_send_email.reset_mock()
        result = send_at_risk_student_alerts()

        self.assertIn("Queued 1 at-risk alert email(s).", result)
        mock_send_email.assert_called_once()
        state.refresh_from_db()
        self.assertTrue(state.is_at_risk)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_disenrolled_student_treated_as_recovered(self, mock_send_email):
        student = self._make_student("E")
        self._enroll_and_grade(student, 20)
        send_at_risk_student_alerts()
        state = StudentRiskAlertState.objects.get(student=student, school=self.school)
        self.assertTrue(state.is_at_risk)

        enrollment = StudentCourse.objects.get(student=student, course=self.course)
        enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
        enrollment.save(update_fields=["enrollment_status"])

        mock_send_email.reset_mock()
        send_at_risk_student_alerts()

        state.refresh_from_db()
        self.assertFalse(state.is_at_risk)
        mock_send_email.assert_not_called()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_school_with_no_opted_in_admin_is_skipped(self, mock_send_email):
        self.admin.settings.notify_at_risk_student_alerts = False
        self.admin.settings.save(update_fields=["notify_at_risk_student_alerts"])
        student = self._make_student("F")
        self._enroll_and_grade(student, 20)

        result = send_at_risk_student_alerts()

        self.assertIn("Queued 0 at-risk alert email(s).", result)
        mock_send_email.assert_not_called()
        self.assertFalse(
            StudentRiskAlertState.objects.filter(
                student=student, school=self.school
            ).exists()
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_admin_in_different_school_not_notified(self, mock_send_email):
        other_school = School.objects.create(name="Other At-Risk School")
        other_admin = CustomUser.objects.create_user(
            email="other-at-risk-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Other",
            last_name="Admin",
            school=other_school,
            is_active=True,
        )
        other_admin.settings.notify_at_risk_student_alerts = True
        other_admin.settings.save(update_fields=["notify_at_risk_student_alerts"])

        student = self._make_student("G")
        self._enroll_and_grade(student, 20)
        send_at_risk_student_alerts()

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_snapshot_recorded_even_without_opted_in_admin(self, mock_send_email):
        # The trend chart must get daily data regardless of whether any
        # admin opted into the email alert.
        self.admin.settings.notify_at_risk_student_alerts = False
        self.admin.settings.save(update_fields=["notify_at_risk_student_alerts"])
        student = self._make_student("H")
        self._enroll_and_grade(student, 20)

        send_at_risk_student_alerts()

        mock_send_email.assert_not_called()
        snapshot = SchoolAtRiskSnapshot.objects.get(
            school=self.school, snapshot_date=timezone.now().date()
        )
        self.assertEqual(snapshot.at_risk_count, 1)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_snapshot_is_idempotent_when_run_twice_same_day(self, mock_send_email):
        student = self._make_student("I")
        self._enroll_and_grade(student, 20)

        send_at_risk_student_alerts()
        send_at_risk_student_alerts()

        self.assertEqual(
            SchoolAtRiskSnapshot.objects.filter(school=self.school).count(), 1
        )
        snapshot = SchoolAtRiskSnapshot.objects.get(school=self.school)
        self.assertEqual(snapshot.at_risk_count, 1)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_snapshot_count_reflects_recovered_students(self, mock_send_email):
        student = self._make_student("J")
        self._enroll_and_grade(student, 20)
        send_at_risk_student_alerts()
        self.assertEqual(
            SchoolAtRiskSnapshot.objects.get(school=self.school).at_risk_count, 1
        )

        self._enroll_and_grade(student, 100)
        self._enroll_and_grade(student, 100)
        self._enroll_and_grade(student, 100)
        send_at_risk_student_alerts()

        self.assertEqual(
            SchoolAtRiskSnapshot.objects.get(school=self.school).at_risk_count, 0
        )


class TeacherInactivityAlertTaskTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Inactivity School")

        self.admin = CustomUser.objects.create_user(
            email="inactivity-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Inactivity",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_teacher_activity_alerts = True
        self.admin.settings.save(update_fields=["notify_teacher_activity_alerts"])

        self.old_join_date = timezone.now() - timedelta(days=365)

    def _make_teacher(self, suffix, *, date_joined):
        return CustomUser.objects.create_user(
            email=f"inactivity-teacher-{suffix}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name=suffix,
            school=self.school,
            is_active=True,
            date_joined=date_joined,
        )

    def _set_last_activity(self, teacher, when):
        # Replace any existing rows so MAX(timestamp) reflects only `when`,
        # rather than being pinned to a previously-recorded newer timestamp.
        UserActivity.objects.filter(user=teacher).delete()
        activity = UserActivity.objects.create(user=teacher)
        UserActivity.objects.filter(pk=activity.pk).update(timestamp=when)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_inactive_teacher_triggers_alert_and_persists_state(self, mock_send_email):
        teacher = self._make_teacher("A", date_joined=self.old_join_date)
        self._set_last_activity(teacher, timezone.now() - timedelta(days=20))

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 1 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_called_once()
        state = TeacherInactivityAlertState.objects.get(teacher=teacher)
        self.assertTrue(state.is_flagged_inactive)
        self.assertIsNotNone(state.last_alerted_at)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_recently_active_teacher_never_alerts(self, mock_send_email):
        teacher = self._make_teacher("B", date_joined=self.old_join_date)
        self._set_last_activity(teacher, timezone.now() - timedelta(days=1))

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 0 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_not_called()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_staying_inactive_is_not_realerted(self, mock_send_email):
        teacher = self._make_teacher("C", date_joined=self.old_join_date)
        self._set_last_activity(teacher, timezone.now() - timedelta(days=20))

        send_teacher_inactivity_alerts()
        first_alerted_at = TeacherInactivityAlertState.objects.get(
            teacher=teacher
        ).last_alerted_at
        mock_send_email.reset_mock()

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 0 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_not_called()
        state = TeacherInactivityAlertState.objects.get(teacher=teacher)
        self.assertEqual(state.last_alerted_at, first_alerted_at)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_becoming_active_clears_flag_and_relapse_realerts(self, mock_send_email):
        teacher = self._make_teacher("D", date_joined=self.old_join_date)
        self._set_last_activity(teacher, timezone.now() - timedelta(days=20))
        send_teacher_inactivity_alerts()
        self.assertTrue(
            TeacherInactivityAlertState.objects.get(teacher=teacher).is_flagged_inactive
        )

        # Teacher logs back in.
        self._set_last_activity(teacher, timezone.now())
        mock_send_email.reset_mock()
        send_teacher_inactivity_alerts()

        state = TeacherInactivityAlertState.objects.get(teacher=teacher)
        self.assertFalse(state.is_flagged_inactive)
        mock_send_email.assert_not_called()

        # Goes inactive again -> should re-alert.
        self._set_last_activity(teacher, timezone.now() - timedelta(days=20))
        mock_send_email.reset_mock()
        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 1 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_called_once()
        state.refresh_from_db()
        self.assertTrue(state.is_flagged_inactive)

    @patch("dashboard.tasks.send_email_task.delay")
    def test_new_teacher_within_grace_period_not_flagged(self, mock_send_email):
        teacher = self._make_teacher(
            "E", date_joined=timezone.now() - timedelta(days=2)
        )
        # Never logged in at all.

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 0 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_not_called()
        self.assertFalse(
            TeacherInactivityAlertState.objects.filter(teacher=teacher).exists()
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_teacher_who_never_logged_in_but_joined_long_ago_is_flagged(
        self, mock_send_email
    ):
        teacher = self._make_teacher("F", date_joined=self.old_join_date)
        # No UserActivity rows at all.

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 1 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_called_once()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_school_with_no_opted_in_admin_is_skipped(self, mock_send_email):
        self.admin.settings.notify_teacher_activity_alerts = False
        self.admin.settings.save(update_fields=["notify_teacher_activity_alerts"])
        teacher = self._make_teacher("G", date_joined=self.old_join_date)
        self._set_last_activity(teacher, timezone.now() - timedelta(days=20))

        result = send_teacher_inactivity_alerts()

        self.assertIn("Queued 0 teacher-inactivity alert email(s).", result)
        mock_send_email.assert_not_called()
        self.assertFalse(
            TeacherInactivityAlertState.objects.filter(teacher=teacher).exists()
        )

    def test_teacher_inactivity_schedule_is_registered(self):
        schedule = settings.CELERY_BEAT_SCHEDULE["send-teacher-inactivity-alerts"]

        self.assertEqual(
            schedule["task"], "dashboard.tasks.send_teacher_inactivity_alerts"
        )


class TeacherFirstCourseMilestoneTest(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Milestone School")

        self.admin = CustomUser.objects.create_user(
            email="milestone-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Milestone",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_teacher_activity_alerts = True
        self.admin.settings.save(update_fields=["notify_teacher_activity_alerts"])

        self.teacher = CustomUser.objects.create_user(
            email="milestone-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Milestone",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(
            name="Milestone Term", teacher=self.teacher
        )

    @patch("dashboard.tasks.send_teacher_first_course_milestone_alert.delay")
    def test_first_course_dispatches_milestone_task(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            course = Course.objects.create(
                name="First Course", teacher=self.teacher, session=self.session
            )

        mock_delay.assert_called_once_with(str(course.id))

    @patch("dashboard.tasks.send_teacher_first_course_milestone_alert.delay")
    def test_second_course_does_not_dispatch_milestone_task(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            Course.objects.create(
                name="First Course", teacher=self.teacher, session=self.session
            )
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            Course.objects.create(
                name="Second Course", teacher=self.teacher, session=self.session
            )

        mock_delay.assert_not_called()

    @patch("dashboard.tasks.send_teacher_first_course_milestone_alert.delay")
    def test_course_by_teacher_without_school_does_not_dispatch(self, mock_delay):
        self.teacher.school = None
        self.teacher.save(update_fields=["school"])

        with self.captureOnCommitCallbacks(execute=True):
            Course.objects.create(
                name="Schoolless Course", teacher=self.teacher, session=self.session
            )

        mock_delay.assert_not_called()

    @patch("dashboard.tasks.send_teacher_first_course_milestone_alert.delay")
    def test_course_update_does_not_redispatch(self, mock_delay):
        with self.captureOnCommitCallbacks(execute=True):
            course = Course.objects.create(
                name="First Course", teacher=self.teacher, session=self.session
            )
        mock_delay.reset_mock()

        with self.captureOnCommitCallbacks(execute=True):
            course.name = "Renamed Course"
            course.save(update_fields=["name"])

        mock_delay.assert_not_called()


class TeacherFirstCourseMilestoneTaskTest(TestCase):
    """Direct tests of send_teacher_first_course_milestone_alert's email
    content/gating, invoked synchronously (bypassing .delay/Celery)."""

    def setUp(self):
        self.school = School.objects.create(name="Milestone Task School")

        self.admin = CustomUser.objects.create_user(
            email="milestone-task-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Milestone",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.admin.settings.notify_teacher_activity_alerts = True
        self.admin.settings.save(update_fields=["notify_teacher_activity_alerts"])

        self.teacher = CustomUser.objects.create_user(
            email="milestone-task-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Milestone",
            last_name="Teacher",
            school=self.school,
            is_active=True,
        )
        self.session = Session.objects.create(
            name="Milestone Task Term", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="First Course", teacher=self.teacher, session=self.session
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_sends_email_to_opted_in_admin(self, mock_send_email):
        from dashboard.tasks import send_teacher_first_course_milestone_alert

        result = send_teacher_first_course_milestone_alert(str(self.course.id))

        self.assertIn("Queued 1 teacher milestone alert email(s).", result)
        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )
        self.assertIn(
            "milestone", mock_send_email.mock_calls[0].kwargs["subject"].lower()
        )

    @patch("dashboard.tasks.send_email_task.delay")
    def test_no_email_when_admin_not_opted_in(self, mock_send_email):
        from dashboard.tasks import send_teacher_first_course_milestone_alert

        self.admin.settings.notify_teacher_activity_alerts = False
        self.admin.settings.save(update_fields=["notify_teacher_activity_alerts"])

        result = send_teacher_first_course_milestone_alert(str(self.course.id))

        self.assertIn("No opted-in admins", result)
        mock_send_email.assert_not_called()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_no_error_when_course_deleted_before_task_runs(self, mock_send_email):
        from dashboard.tasks import send_teacher_first_course_milestone_alert

        course_id = str(self.course.id)
        self.course.delete()

        result = send_teacher_first_course_milestone_alert(course_id)

        self.assertIn("no longer exists", result)
        mock_send_email.assert_not_called()

    @patch("dashboard.tasks.send_email_task.delay")
    def test_admin_in_different_school_not_notified(self, mock_send_email):
        from dashboard.tasks import send_teacher_first_course_milestone_alert

        other_school = School.objects.create(name="Other Milestone School")
        other_admin = CustomUser.objects.create_user(
            email="other-milestone-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Other",
            last_name="Admin",
            school=other_school,
            is_active=True,
        )
        other_admin.settings.notify_teacher_activity_alerts = True
        other_admin.settings.save(update_fields=["notify_teacher_activity_alerts"])

        send_teacher_first_course_milestone_alert(str(self.course.id))

        mock_send_email.assert_called_once()
        self.assertEqual(
            mock_send_email.mock_calls[0].kwargs["recipient_list"], [self.admin.email]
        )


class StudentDashboardOverviewAPITest(APITestCase):
    def setUp(self):
        self.now = timezone.now()

        # Create users
        self.teacher = CustomUser.objects.create_user(
            email="overview-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Overview",
            last_name="Teacher",
        )
        self.student = CustomUser.objects.create_user(
            email="overview-student@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Overview",
            last_name="Student",
        )

        self.session = Session.objects.create(
            name="Overview Term", teacher=self.teacher
        )

        # Course 1: Active
        self.course_active_1 = Course.objects.create(
            name="Active Course 1",
            teacher=self.teacher,
            session=self.session,
            is_active=True,
        )
        # Course 2: Active
        self.course_active_2 = Course.objects.create(
            name="Active Course 2",
            teacher=self.teacher,
            session=self.session,
            is_active=True,
        )
        # Course 3: Inactive
        self.course_inactive = Course.objects.create(
            name="Inactive Course",
            teacher=self.teacher,
            session=self.session,
            is_active=False,
        )
        # Course 4: Active (withdrawn student)
        self.course_withdrawn = Course.objects.create(
            name="Withdrawn Course",
            teacher=self.teacher,
            session=self.session,
            is_active=True,
        )

        # Enrollments
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_active_1,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_active_2,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_inactive,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_withdrawn,
            enrollment_status=EnrollmentStatusType.WITHDRAWN,
        )

        # Assignments for Active Course 1
        # 1. Published, submitted
        self.a1 = Assignment.objects.create(
            title="A1 Submitted",
            course=self.course_active_1,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=5),
        )
        StudentSubmission.objects.create(
            assignment=self.a1,
            student=self.student,
            answers={"q1": "a"},
            score=100,
            score_percentage=100,
            graded_at=self.now - timedelta(days=4),
        )

        # 2. Published, not yet due, pending submission
        self.a2 = Assignment.objects.create(
            title="A2 Future Pending",
            course=self.course_active_1,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now + timedelta(days=5),
        )

        # 3. Published, due, pending submission (overdue)
        self.a3 = Assignment.objects.create(
            title="A3 Due Pending",
            course=self.course_active_1,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now - timedelta(days=2),
        )

        # 4. Draft, pending (should be ignored)
        self.a4 = Assignment.objects.create(
            title="A4 Draft",
            course=self.course_active_1,
            status=AssignmentStatus.DRAFT,
            due_date=self.now + timedelta(days=5),
        )

        # Assignments for Active Course 2
        # 5. Published, no due date, pending submission
        self.a5 = Assignment.objects.create(
            title="A5 No Due Date Pending",
            course=self.course_active_2,
            status=AssignmentStatus.PUBLISHED,
            due_date=None,
        )

        # 6. Published, submitted
        self.a6 = Assignment.objects.create(
            title="A6 Submitted",
            course=self.course_active_2,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now + timedelta(days=2),
        )
        StudentSubmission.objects.create(
            assignment=self.a6,
            student=self.student,
            answers={"q1": "a"},
            score=90,
            score_percentage=90,
            graded_at=self.now + timedelta(days=1),
        )

        # Assignments for Inactive Course (should be ignored)
        self.a_inactive = Assignment.objects.create(
            title="A Inactive Course",
            course=self.course_inactive,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now + timedelta(days=2),
        )

        # Assignments for Withdrawn Course (should be ignored)
        self.a_withdrawn = Assignment.objects.create(
            title="A Withdrawn Course",
            course=self.course_withdrawn,
            status=AssignmentStatus.PUBLISHED,
            due_date=self.now + timedelta(days=2),
        )

        self.client.force_authenticate(user=self.student)

    def test_student_dashboard_overview_analytics(self):
        url = reverse("student-overview")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)

        # Expected:
        # 1. total_courses = 2 (Active Course 1, Active Course 2)
        # 2. assignments_submitted = 2 (a1, a6)
        # 3. assignments_pending_not_due = 2 (a2 [future], a5 [no due date])
        # 4. assignments_due_no_submission = 1 (a3 [passed due date])
        self.assertEqual(response.data["total_courses"], 2)
        self.assertEqual(response.data["assignments_submitted"], 2)
        self.assertEqual(response.data["assignments_pending_not_due"], 2)
        self.assertEqual(response.data["assignments_due_no_submission"], 1)


class SchoolAtRiskTrendAPITest(APITestCase):
    def setUp(self):
        self.school = School.objects.create(name="Trend School")
        self.admin = CustomUser.objects.create_user(
            email="trend-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Trend",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )
        self.teacher = CustomUser.objects.create_user(
            email="trend-teacher@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Trend",
            last_name="Teacher",
            is_active=True,
        )
        self.url = reverse("school-admin-at-risk-trend")

    def test_teacher_denied(self):
        self.client.force_authenticate(user=self.teacher)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_without_school_returns_400(self):
        admin_no_school = CustomUser.objects.create_user(
            email="noschool-admin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="No",
            last_name="School",
            is_active=True,
        )
        self.client.force_authenticate(user=admin_no_school)
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("dashboard.views.timezone.localdate")
    def test_weeks_with_no_snapshot_are_omitted(self, mock_localdate):
        today = date(2026, 8, 4)
        mock_localdate.return_value = today
        current_week_start = today - timedelta(days=today.weekday())

        # Only one snapshot exists at all, in the current week.
        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=today, at_risk_count=5
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["weeks"]), 1)
        self.assertEqual(response.data["weeks"][0]["at_risk_count"], 5)
        self.assertEqual(
            response.data["weeks"][0]["week_start"], current_week_start.isoformat()
        )

    @patch("dashboard.views.timezone.localdate")
    def test_week_reports_latest_snapshot(self, mock_localdate):
        today = date(2026, 8, 4)
        mock_localdate.return_value = today
        current_week_start = today - timedelta(days=today.weekday())

        SchoolAtRiskSnapshot.objects.create(
            school=self.school,
            snapshot_date=current_week_start,
            at_risk_count=3,
        )
        SchoolAtRiskSnapshot.objects.create(
            school=self.school,
            snapshot_date=current_week_start + timedelta(days=2),
            at_risk_count=7,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)

        self.assertEqual(len(response.data["weeks"]), 1)
        self.assertEqual(response.data["weeks"][0]["at_risk_count"], 7)

    @patch("dashboard.views.timezone.localdate")
    def test_snapshot_outside_window_is_excluded(self, mock_localdate):
        today = date(2026, 8, 4)
        mock_localdate.return_value = today
        current_week_start = today - timedelta(days=today.weekday())
        window_start = current_week_start - timedelta(weeks=7)

        # One day before the 8-week window starts.
        SchoolAtRiskSnapshot.objects.create(
            school=self.school,
            snapshot_date=window_start - timedelta(days=1),
            at_risk_count=99,
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url)

        self.assertEqual(response.data["weeks"], [])

    @patch("dashboard.views.timezone.localdate")
    def test_response_is_cached_per_admin(self, mock_localdate):
        today = date(2026, 8, 4)
        mock_localdate.return_value = today
        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=today, at_risk_count=2
        )

        self.client.force_authenticate(user=self.admin)
        first = self.client.get(self.url)
        self.assertEqual(first.data["weeks"][0]["at_risk_count"], 2)

        # A snapshot update after the first request shouldn't show up until
        # the per-admin cache entry expires.
        SchoolAtRiskSnapshot.objects.filter(
            school=self.school, snapshot_date=today
        ).update(at_risk_count=999)
        second = self.client.get(self.url)
        self.assertEqual(second.data["weeks"][0]["at_risk_count"], 2)

    def test_custom_start_and_end_date_window(self):
        # A snapshot far outside the default 8-week window is included
        # when start_date/end_date explicitly cover it.
        old_date = date(2026, 1, 5)  # Monday
        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=old_date, at_risk_count=4
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            self.url, {"start_date": "2026-01-01", "end_date": "2026-01-31"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["weeks"]), 1)
        self.assertEqual(response.data["weeks"][0]["at_risk_count"], 4)
        self.assertEqual(response.data["weeks"][0]["week_start"], "2026-01-05")

    def test_start_date_and_end_date_snap_to_calendar_week_boundaries(self):
        self.client.force_authenticate(user=self.admin)
        # Wednesday start, Wednesday end -> should snap out to the
        # enclosing Monday and Sunday.
        response = self.client.get(
            self.url, {"start_date": "2026-01-07", "end_date": "2026-01-14"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["window_start"], "2026-01-05")
        self.assertEqual(response.data["window_end"], "2026-01-18")

    def test_invalid_date_format_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {"start_date": "not-a-date"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_start_date_after_end_date_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            self.url, {"start_date": "2026-02-01", "end_date": "2026-01-01"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_window_exceeding_max_weeks_returns_400(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(
            self.url, {"start_date": "2020-01-01", "end_date": "2026-01-01"}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_only_end_date_provided_defaults_start_relative_to_it(self):
        old_date = date(2026, 1, 5)
        SchoolAtRiskSnapshot.objects.create(
            school=self.school, snapshot_date=old_date, at_risk_count=6
        )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get(self.url, {"end_date": "2026-01-18"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Default window is 8 weeks ending at end_date's week.
        self.assertEqual(response.data["window_end"], "2026-01-18")
        self.assertEqual(len(response.data["weeks"]), 1)
        self.assertEqual(response.data["weeks"][0]["at_risk_count"], 6)
