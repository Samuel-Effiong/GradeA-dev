"""
dashboard/tests_dashboard_audit_fixes.py
========================================
Regression tests for the defects found in the §8 (dashboard) production-
readiness review. Each was first reproduced against the unfixed code by a
probe that asserted the bug was present; these tests assert the opposite, so
reverting a fix fails them.

What each class pins, and the measured failure it prevents:

* StudentGradeVisibilityTest - grades the teacher had NOT released reached the
  student through the student dashboard (summary average and best/worst,
  overview percentage, assignment list score/feedback/"GRADED" status), even
  though students/serializers.py hides them everywhere else.
* StudentAssignmentListScopeTest - draft assignments and withdrawn courses'
  work were listed to students; `submission_date` came from whichever
  student's submission sorted first; each row cost extra queries.
* TeacherAtRiskExpectedWorkTest - drafts and not-yet-due work counted as
  "expected", flagging a 95% student at-risk.
* WeeklyDigestStudentCountTest - a student in two courses counted twice.
* CourseTrendLabelTest - "INSUFFICIENT DATA" vs "INSUFFICIENT_DATA".
* DashboardQueryParamValidationTest - malformed query parameters were 500s.
"""

from datetime import timedelta

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
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
from dashboard.risk import TREND_INSUFFICIENT_DATA
from dashboard.services import (
    SchoolAdminWeeklySummaryService,
    WeeklyCourseSummaryService,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def make_user(kind, email, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="audit-pass-123",  # pragma: allowlist secret
        user_type=kind,
        first_name="Audit",
        last_name=email.split("@")[0],
        is_active=True,
        **extra,
    )


def make_course(teacher, name, **extra):
    session = Session.objects.create(name=f"{name} term", teacher=teacher)
    return Course.objects.create(
        name=name, teacher=teacher, session=session, is_active=True, **extra
    )


def enrol(student, course, enrollment_status=EnrollmentStatusType.ENROLLED):
    return StudentCourse.objects.create(
        student=student, course=course, enrollment_status=enrollment_status
    )


def make_assignment(course, title, *, status=AssignmentStatus.PUBLISHED, due=None):
    return Assignment.objects.create(
        title=title,
        course=course,
        status=status,
        due_date=due if due is not None else timezone.now() - timedelta(days=2),
    )


def make_submission(assignment, student, pct, *, published, feedback=None):
    return StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers={"q1": "x"},
        score=pct,
        score_percentage=pct,
        graded_at=timezone.now() - timedelta(days=1),
        is_published=published,
        feedback=feedback or {},
    )


@override_settings(CACHES=LOCMEM)
class StudentGradeVisibilityTest(APITestCase):
    """A grade the teacher has not released must not reach the student."""

    def setUp(self):
        cache.clear()
        self.teacher = make_user(UserTypes.TEACHER, "vis-teacher@audit.test")
        self.student = make_user(UserTypes.STUDENT, "vis-student@audit.test")
        self.course = make_course(self.teacher, "Visibility course")
        enrol(self.student, self.course)

        self.released_work = make_assignment(self.course, "Released")
        make_submission(self.released_work, self.student, 90, published=True)

        self.held_work = make_assignment(self.course, "Held back")
        make_submission(
            self.held_work,
            self.student,
            10,
            published=False,
            feedback={"note": "UNRELEASED FEEDBACK"},
        )
        self.client.force_authenticate(self.student)

    def test_summary_average_uses_released_grades_only(self):
        response = self.client.get(
            reverse("student-summary", kwargs={"course_id": self.course.id})
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["average_grade"], 90.0)

    def test_summary_best_and_worst_never_include_an_unreleased_grade(self):
        response = self.client.get(
            reverse("student-summary", kwargs={"course_id": self.course.id})
        )

        shown = [
            float(row["score_percentage"])
            for row in response.data["best_assignments"]
            + response.data["worst_assignments"]
        ]
        self.assertNotIn(10.0, shown)
        self.assertIn(90.0, shown)

    def test_overview_percentage_uses_released_grades_only(self):
        response = self.client.get(reverse("student-overview"))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["overall_percentage"], 90.0)

    def test_assignment_list_hides_unreleased_score_and_feedback(self):
        response = self.client.get(reverse("student-assignments"))

        rows = {row["title"]: row for row in response.data["results"]}
        held = rows["Held back"]
        self.assertIsNone(held["score"])
        self.assertIsNone(held["score_percentage"])
        self.assertNotIn("UNRELEASED FEEDBACK", str(held["feedback"]))

    def test_assignment_list_does_not_reveal_that_grading_happened(self):
        response = self.client.get(reverse("student-assignments"))

        rows = {row["title"]: row for row in response.data["results"]}
        self.assertEqual(rows["Held back"]["submission_status"], "SUBMITTED")
        self.assertEqual(rows["Released"]["submission_status"], "GRADED")

    def test_released_grade_is_still_shown(self):
        response = self.client.get(reverse("student-assignments"))

        rows = {row["title"]: row for row in response.data["results"]}
        self.assertEqual(float(rows["Released"]["score_percentage"]), 90.0)


@override_settings(CACHES=LOCMEM)
class StudentAssignmentListScopeTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = make_user(UserTypes.TEACHER, "scope-teacher@audit.test")
        self.student = make_user(UserTypes.STUDENT, "scope-student@audit.test")
        self.classmate = make_user(UserTypes.STUDENT, "scope-classmate@audit.test")
        self.course = make_course(self.teacher, "Scope course")
        enrol(self.student, self.course)
        enrol(self.classmate, self.course)
        self.client.force_authenticate(self.student)

    def _titles(self):
        cache.clear()
        response = self.client.get(reverse("student-assignments"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [row["title"] for row in response.data["results"]]

    def test_draft_assignments_are_not_listed(self):
        make_assignment(self.course, "Visible")
        make_assignment(self.course, "SECRET DRAFT", status=AssignmentStatus.DRAFT)

        self.assertEqual(self._titles(), ["Visible"])

    def test_withdrawn_course_work_is_not_listed(self):
        other_course = make_course(self.teacher, "Left this course")
        enrol(self.student, other_course, EnrollmentStatusType.WITHDRAWN)
        make_assignment(other_course, "From a withdrawn course")
        make_assignment(self.course, "Current")

        self.assertEqual(self._titles(), ["Current"])

    def test_a_classmates_withdrawal_does_not_hide_the_course(self):
        """Guards the obvious-but-wrong fix: excluding on the enrollments join
        would drop the course if ANY enrolment in it was withdrawn."""
        leaver = make_user(UserTypes.STUDENT, "scope-leaver@audit.test")
        enrol(leaver, self.course, EnrollmentStatusType.WITHDRAWN)
        make_assignment(self.course, "Still mine")

        self.assertEqual(self._titles(), ["Still mine"])

    def test_submission_date_is_this_students_own(self):
        assignment = make_assignment(self.course, "Dated")
        mine = make_submission(assignment, self.student, 80, published=True)
        theirs = make_submission(assignment, self.classmate, 80, published=True)
        # submission_date is auto-stamped on create; set distinct dates.
        StudentSubmission.objects.filter(pk=mine.pk).update(
            submission_date=timezone.now() - timedelta(days=3)
        )
        StudentSubmission.objects.filter(pk=theirs.pk).update(
            submission_date=timezone.now() - timedelta(days=40)
        )
        mine.refresh_from_db()

        cache.clear()
        response = self.client.get(reverse("student-assignments"))
        reported = response.data["results"][0]["submission_date"]

        self.assertEqual(reported[:19], mine.submission_date.isoformat()[:19])

    def test_query_count_does_not_grow_with_rows(self):
        def count():
            cache.clear()
            with CaptureQueriesContext(connection) as ctx:
                self.client.get(reverse("student-assignments"))
            return len(ctx)

        for i in range(2):
            make_submission(
                make_assignment(self.course, f"base {i}"),
                self.student,
                70,
                published=True,
            )
        few = count()
        for i in range(10):
            make_submission(
                make_assignment(self.course, f"more {i}"),
                self.student,
                70,
                published=True,
            )
        many = count()

        self.assertEqual(many, few, f"{few} queries for 2 rows, {many} for 12")


@override_settings(CACHES=LOCMEM)
class TeacherAtRiskExpectedWorkTest(APITestCase):
    """Only work a student could have submitted counts as missing."""

    def setUp(self):
        cache.clear()
        self.teacher = make_user(UserTypes.TEACHER, "risk-teacher@audit.test")
        self.student = make_user(UserTypes.STUDENT, "risk-student@audit.test")
        self.course = make_course(self.teacher, "Risk course")
        enrol(self.student, self.course)
        make_submission(
            make_assignment(self.course, "Real work"),
            self.student,
            95,
            published=True,
        )
        self.client.force_authenticate(self.teacher)

    def _student_row(self):
        cache.clear()
        response = self.client.get(
            reverse("teacher-admin-students", kwargs={"course_id": self.course.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The endpoint is paginated (StandardPageNumberPagination envelope).
        return response.data["results"][0]

    def _overview_at_risk_ids(self):
        cache.clear()
        response = self.client.get(
            reverse(
                "teacher-admin-overview",
                kwargs={"session_id": self.course.session_id},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return [str(row["student_id"]) for row in response.data["at_risk_students"]]

    def test_drafts_do_not_flag_a_strong_student(self):
        make_assignment(self.course, "draft 1", status=AssignmentStatus.DRAFT)
        make_assignment(self.course, "draft 2", status=AssignmentStatus.DRAFT)

        self.assertFalse(self._student_row()["at_risk"])
        self.assertNotIn(str(self.student.id), self._overview_at_risk_ids())

    def test_not_yet_due_work_does_not_flag_a_strong_student(self):
        for i in range(2):
            make_assignment(
                self.course,
                f"due next week {i}",
                due=timezone.now() + timedelta(days=7),
            )

        self.assertFalse(self._student_row()["at_risk"])
        self.assertNotIn(str(self.student.id), self._overview_at_risk_ids())

    def test_genuinely_missing_due_work_still_flags(self):
        """The fix must not make the check toothless."""
        for i in range(3):
            make_assignment(self.course, f"overdue {i}")

        self.assertTrue(self._student_row()["at_risk"])
        self.assertIn(str(self.student.id), self._overview_at_risk_ids())

    def test_assignment_assigned_still_reports_every_assignment(self):
        make_assignment(self.course, "draft", status=AssignmentStatus.DRAFT)

        self.assertEqual(self._student_row()["assignment_assigned"], 2)


class WeeklyDigestStudentCountTest(TestCase):
    def test_a_student_in_two_courses_is_counted_once(self):
        school = School.objects.create(name="Digest audit school")
        teacher = make_user(UserTypes.TEACHER, "dg-teacher@audit.test", school=school)
        student = make_user(UserTypes.STUDENT, "dg-student@audit.test")
        for name in ("first", "second"):
            enrol(student, make_course(teacher, f"digest {name}"))

        now = timezone.now()
        overall = SchoolAdminWeeklySummaryService()._build_overall_metrics(
            school, now - timedelta(days=7), now
        )

        self.assertEqual(overall["active_student_count"], 1)


class CourseTrendLabelTest(TestCase):
    def test_both_no_data_branches_use_the_shared_label(self):
        service = WeeklyCourseSummaryService()

        self.assertEqual(
            service._trend_from_values(None, None, threshold=1),
            TREND_INSUFFICIENT_DATA,
        )
        self.assertEqual(
            service._trend_from_values(None, 0, threshold=1),
            TREND_INSUFFICIENT_DATA,
        )


@override_settings(CACHES=LOCMEM)
class DashboardQueryParamValidationTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Param audit school")
        self.admin = make_user(
            UserTypes.SCHOOL_ADMIN, "param-admin@audit.test", school=self.school
        )
        teacher = make_user(
            UserTypes.TEACHER, "param-teacher@audit.test", school=self.school
        )
        make_course(teacher, "Param course")
        self.client.force_authenticate(self.admin)

    def _unit(self, **params):
        return self.client.get(reverse("school-admin-unit-performance"), params)

    def test_non_numeric_limit_is_a_400_not_a_500(self):
        response = self._unit(hardest_limit="abc")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("hardest_limit", response.data["detail"])

    def test_negative_limit_is_a_400_not_a_500(self):
        self.assertEqual(
            self._unit(reteach_limit="-1").status_code, status.HTTP_400_BAD_REQUEST
        )

    def test_out_of_range_threshold_is_a_400(self):
        self.assertEqual(
            self._unit(mastery_threshold="150").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_nan_threshold_is_a_400(self):
        self.assertEqual(
            self._unit(reteach_threshold="nan").status_code,
            status.HTTP_400_BAD_REQUEST,
        )

    def test_valid_parameters_still_work(self):
        response = self._unit(hardest_limit="3", mastery_threshold="65.5")

        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_course_performance_bad_page_size_falls_back_instead_of_crashing(self):
        response = self.client.get(
            reverse("school-admin-course-peformance"), {"page_size": "abc"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)
