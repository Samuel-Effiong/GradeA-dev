from datetime import timedelta
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from dashboard.services import WeeklyCourseSummaryService
from dashboard.tasks import send_weekly_course_summaries
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


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
