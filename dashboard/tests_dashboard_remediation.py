"""
dashboard/tests_dashboard_remediation.py
========================================
Regression tests for the §8 dashboard findings approved for remediation.

Each class pins one finding, and each is written to FAIL if its defect comes
back - verified by running the suite against the pre-remediation code (see
the audit record in docs/CODEBASE_AUDIT_SECTIONS.md).

  1  SuperAdminTeacherMetricsTest / SuperAdminSchoolMetricsTest
       Join fan-out inflated counts and skewed averages (1 course -> 18;
       a 50% average -> 83.33); completion rate multiplied totals across
       courses instead of summing per course.
  2  SchoolAdminAIContextTest
       The teachers section was always `{}` behind a swallowed exception.
  3  TeacherAIContextTest / AIChatTransactionTest
       Context cost grew per assignment (+37 queries / 10 assignments), the
       whole dashboard was pasted into the prompt, and a DB transaction was
       held across the provider call.
  4  AtRiskAlertDeliveryTest
       A queue outage on the day a student became at-risk lost the alert.
  5  TeacherPerformanceStatsServiceTest / ExpectedSubmissionQueryTest
       Per-teacher and per-course query growth; teacher stats duplicated in
       views.py and services.py.
  6  FlaggedForReviewThresholdTest
       A bare 70 where every other view uses AI_CONFIDENCE_THRESHOLD (80).
  7  TeacherStudentsPaginationTest
       `page`/`page_size` accepted but every row returned.
"""

import logging
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase, APITransactionTestCase

from ai_processor.models import ChatMessage
from ai_processor.services import AI_CONFIDENCE_THRESHOLD
from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from dashboard.models import StudentRiskAlertState
from dashboard.services import (
    SchoolAdminWeeklySummaryService,
    TeacherAIContextService,
    TeacherPerformanceStatsService,
)
from dashboard.tasks import send_at_risk_student_alerts
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


# ---------------------------------------------------------------- fixtures


class Builder:
    """Terse fixture helpers shared by every class below."""

    _n = 0

    @classmethod
    def uid(cls):
        cls._n += 1
        return cls._n

    def user(self, kind, *, school=None, first="Test", last=None, **extra):
        n = self.uid()
        return CustomUser.objects.create_user(
            email=f"remediation-{kind.lower()}-{n}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name=last or f"User{n}",
            school=school,
            is_active=True,
            **extra,
        )

    def course(self, teacher, name=None, **extra):
        n = self.uid()
        session = Session.objects.create(name=f"Term {n}", teacher=teacher)
        return Course.objects.create(
            name=name or f"Course {n}",
            teacher=teacher,
            session=session,
            is_active=extra.pop("is_active", True),
            **extra,
        )

    def enrol(self, student, course, final_grade=None, status_=None):
        return StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=status_ or EnrollmentStatusType.ENROLLED,
            final_grade=final_grade,
        )

    def assignment(self, course, *, title=None, status_=None, due=None, **extra):
        return Assignment.objects.create(
            title=title or f"Assignment {self.uid()}",
            course=course,
            status=status_ or AssignmentStatus.PUBLISHED,
            due_date=due,
            **extra,
        )

    def submit(self, assignment, student, pct=None, **extra):
        graded = pct is not None
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=pct,
            score_percentage=pct,
            graded_at=timezone.now() if graded else None,
            is_published=extra.pop("is_published", True),
            **extra,
        )


def count_queries(fn):
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return len(ctx)


# ============================================================== finding 1


@override_settings(CACHES=LOCMEM)
class SuperAdminTeacherMetricsTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = self.user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_authenticate(self.admin)

    def _row(self, teacher):
        cache.clear()
        response = self.client.get(reverse("dashboard-teachers"), {"page_size": 100})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return next(
            r for r in response.data["results"] if r["teacher_id"] == str(teacher.id)
        )

    def test_one_course_is_reported_as_one_course(self):
        """The measured regression: joins made one course report as 18."""
        teacher = self.user(UserTypes.TEACHER)
        course = self.course(teacher)
        students = [self.user(UserTypes.STUDENT) for _ in range(3)]
        for s in students:
            self.enrol(s, course, final_grade=80)
        for _ in range(2):
            a = self.assignment(course)
            for s in students:
                self.submit(a, s, 80)

        row = self._row(teacher)

        self.assertEqual(row["number_of_courses"], 1)
        self.assertEqual(row["number_of_students"], 3)

    def test_average_is_the_mean_of_enrolment_grades_not_weighted_by_submissions(
        self,
    ):
        teacher = self.user(UserTypes.TEACHER)
        busy = self.course(teacher)
        quiet = self.course(teacher)
        top = self.user(UserTypes.STUDENT)
        low = self.user(UserTypes.STUDENT)
        self.enrol(top, busy, final_grade=100)
        self.enrol(low, quiet, final_grade=0)
        for _ in range(5):  # submissions only on the 100% course
            self.submit(self.assignment(busy), top, 100)
        # Saving a submission recomputes final_grade (classrooms/signals.py),
        # so pin the grades after the submissions exist.
        StudentCourse.objects.filter(student=top).update(final_grade=100)
        StudentCourse.objects.filter(student=low).update(final_grade=0)

        self.assertEqual(self._row(teacher)["average_student_performance"], 50.0)

    def test_completion_rate_sums_expected_work_per_course(self):
        """10 assignments with no students plus 30 students with no
        assignments expects 0 submissions, not 10 x 30 = 300."""
        teacher = self.user(UserTypes.TEACHER)
        work_only = self.course(teacher)
        roster_only = self.course(teacher)
        for _ in range(10):
            self.assignment(work_only)
        for _ in range(30):
            self.enrol(self.user(UserTypes.STUDENT), roster_only)
        real = self.course(teacher)
        student = self.user(UserTypes.STUDENT)
        self.enrol(student, real)
        self.submit(self.assignment(real), student, 70)
        self.assignment(real)  # 1 of 2 expected submitted

        self.assertEqual(self._row(teacher)["assignment_completion_rate"], 50.0)

    def test_query_count_does_not_grow_with_teachers_or_their_data(self):
        def page():
            cache.clear()
            self.client.get(reverse("dashboard-teachers"), {"page_size": 100})

        def grow(teachers, courses_each, assignments_each):
            for _ in range(teachers):
                t = self.user(UserTypes.TEACHER)
                for _ in range(courses_each):
                    c = self.course(t)
                    s = self.user(UserTypes.STUDENT)
                    self.enrol(s, c, final_grade=70)
                    for _ in range(assignments_each):
                        self.submit(self.assignment(c), s, 70)

        grow(2, 1, 1)
        small = count_queries(page)
        grow(12, 3, 4)
        large = count_queries(page)

        self.assertEqual(large, small, f"{small} queries -> {large}")


@override_settings(CACHES=LOCMEM)
class SuperAdminSchoolMetricsTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.admin = self.user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_authenticate(self.admin)

    def _row(self, school):
        cache.clear()
        response = self.client.get(reverse("dashboard-schools"), {"page_size": 100})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return next(
            r for r in response.data["results"] if r["school_id"] == str(school.id)
        )

    def _fan_out_school(self, name):
        """Two enrolments averaging 50, on courses with 5 and 1 assignments."""
        school = School.objects.create(name=name)
        teacher = self.user(UserTypes.TEACHER, school=school)
        heavy = self.course(teacher)
        light = self.course(teacher)
        self.enrol(self.user(UserTypes.STUDENT, school=school), heavy, final_grade=100)
        self.enrol(self.user(UserTypes.STUDENT, school=school), light, final_grade=0)
        for _ in range(5):
            self.assignment(heavy)
        self.assignment(light)
        return school

    def test_average_is_not_weighted_by_assignment_count(self):
        """The measured regression: 50.0 was reported as 83.33."""
        school = self._fan_out_school("Fan-out school")

        self.assertEqual(self._row(school)["average_performance"], 50.0)

    def test_counts_are_exact(self):
        school = self._fan_out_school("Count school")

        row = self._row(school)
        self.assertEqual(row["teachers"], 1)
        self.assertEqual(row["students"], 2)
        self.assertEqual(row["courses"], 2)

    def test_one_schools_figures_never_include_anothers(self):
        a = self._fan_out_school("Alpha tenant")
        b = School.objects.create(name="Beta tenant")
        tb = self.user(UserTypes.TEACHER, school=b)
        self.enrol(
            self.user(UserTypes.STUDENT, school=b), self.course(tb), final_grade=90
        )

        self.assertEqual(self._row(a)["average_performance"], 50.0)
        row_b = self._row(b)
        self.assertEqual(row_b["average_performance"], 90.0)
        self.assertEqual(row_b["courses"], 1)

    def test_query_count_does_not_grow_with_schools_or_their_data(self):
        def page():
            cache.clear()
            self.client.get(reverse("dashboard-schools"), {"page_size": 100})

        for i in range(2):
            self._fan_out_school(f"small {i}")
        small = count_queries(page)
        for i in range(15):
            self._fan_out_school(f"large {i}")
        large = count_queries(page)

        self.assertEqual(large, small, f"{small} queries -> {large}")


# ============================================================== finding 5


def reference_teacher_stats(teacher, now):
    """The pre-remediation per-teacher computation, kept as the oracle the
    bulk service must agree with. Deliberately naive."""
    from django.db.models import Avg, DurationField, ExpressionWrapper, F, Sum

    cutoff = now - timedelta(days=180)
    course_ids = list(teacher.courses.values_list("id", flat=True))
    active = StudentCourse.objects.filter(course_id__in=course_ids).exclude(
        enrollment_status=EnrollmentStatusType.WITHDRAWN
    )
    students = active.values("student").distinct().count()
    current = (
        active.filter(course__created_at__gte=cutoff)
        .values("student")
        .distinct()
        .count()
    )
    past = (
        active.filter(course__created_at__lt=cutoff)
        .values("student")
        .distinct()
        .count()
    )
    growth = ((current - past) / past * 100) if past else (100.0 if current else None)
    assignments = Assignment.objects.filter(course_id__in=course_ids)
    first = assignments.order_by("created_at").first()
    count = assignments.count()
    if first and count:
        weeks = (now - first.created_at).days / 7
        per_week = count / weeks if weeks > 0 else 0
    else:
        per_week = None
    graded = StudentSubmission.objects.filter(
        assignment__course_id__in=course_ids, graded_at__isnull=False
    )
    n = graded.count()
    total = graded.aggregate(
        t=Sum(
            ExpressionWrapper(
                F("graded_at") - F("submission_date"), output_field=DurationField()
            )
        )
    )["t"]
    turnaround = total.total_seconds() / (n * 86400) if n and total else None
    confidence = graded.aggregate(c=Avg("grading_confidence"))["c"]
    return {
        "courses": len(course_ids),
        "students": students,
        "growth": round(growth, 1) if growth is not None else None,
        "assignments_per_week": round(per_week, 1) if per_week is not None else None,
        "turnaround": round(turnaround, 1) if turnaround is not None else None,
        "ai_confidence": round(confidence, 1) if confidence is not None else None,
    }


class TeacherPerformanceStatsServiceTest(Builder, TestCase):
    def _varied_teacher(self, school, weeks_old=10):
        teacher = self.user(UserTypes.TEACHER, school=school)
        old = self.course(teacher)
        Course.objects.filter(pk=old.pk).update(
            created_at=timezone.now() - timedelta(days=400)
        )
        new = self.course(teacher)
        for c, n in ((old, 3), (new, 5)):
            for _ in range(n):
                self.enrol(self.user(UserTypes.STUDENT), c)
        self.enrol(
            self.user(UserTypes.STUDENT), new, status_=EnrollmentStatusType.WITHDRAWN
        )
        a = self.assignment(new)
        Assignment.objects.filter(pk=a.pk).update(
            created_at=timezone.now() - timedelta(weeks=weeks_old)
        )
        student = StudentCourse.objects.filter(course=new).first().student
        sub = self.submit(a, student, 75, grading_confidence=64.5)
        StudentSubmission.objects.filter(pk=sub.pk).update(
            submission_date=timezone.now() - timedelta(days=3)
        )
        return teacher

    def test_agrees_with_the_reference_computation(self):
        school = School.objects.create(name="Parity school")
        teachers = [self._varied_teacher(school, weeks_old=w) for w in (2, 9, 30)]
        teachers.append(self.user(UserTypes.TEACHER, school=school))  # idle
        now = timezone.now()

        stats = TeacherPerformanceStatsService().build(teachers, now=now)

        for teacher in teachers:
            expected = reference_teacher_stats(teacher, now)
            actual = {k: stats[teacher.id][k] for k in expected}
            self.assertEqual(actual, expected, f"teacher {teacher.id}")

    def test_query_count_is_fixed_whatever_the_number_of_teachers(self):
        school = School.objects.create(name="Scale school")
        few = [self._varied_teacher(school) for _ in range(2)]
        many = few + [self._varied_teacher(school) for _ in range(20)]
        service = TeacherPerformanceStatsService()

        self.assertEqual(
            count_queries(lambda: service.build(many)),
            count_queries(lambda: service.build(few)),
        )

    def test_every_requested_teacher_is_present(self):
        school = School.objects.create(name="Presence school")
        idle = self.user(UserTypes.TEACHER, school=school)

        stats = TeacherPerformanceStatsService().build([idle])

        self.assertEqual(stats[idle.id]["courses"], 0)
        self.assertIsNone(stats[idle.id]["rigor"])

    def test_weekly_digest_and_dashboard_share_the_implementation(self):
        """There is one implementation: the old view helper is gone and the
        digest delegates to the service."""
        import dashboard.views as views

        self.assertFalse(hasattr(views, "compute_teacher_performance_stats"))
        school = School.objects.create(name="Shared school")
        teacher = self._varied_teacher(school)
        now = timezone.now()

        with patch(
            "dashboard.services.TeacherPerformanceStatsService.build",
            wraps=TeacherPerformanceStatsService().build,
        ) as build:
            rows = SchoolAdminWeeklySummaryService()._build_teacher_activity(school)

        build.assert_called_once()
        self.assertEqual(
            {k: rows[0][k] for k in ("courses", "students", "growth")},
            {
                k: reference_teacher_stats(teacher, now)[k]
                for k in ("courses", "students", "growth")
            },
        )


@override_settings(CACHES=LOCMEM)
class SchoolTeacherEndpointsQueryTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Endpoint school")
        self.admin = self.user(UserTypes.SCHOOL_ADMIN, school=self.school)
        self.client.force_authenticate(self.admin)

    def _teacher_with_data(self):
        teacher = self.user(UserTypes.TEACHER, school=self.school)
        course = self.course(teacher)
        student = self.user(UserTypes.STUDENT)
        self.enrol(student, course)
        self.submit(self.assignment(course), student, 80)
        return teacher

    def test_teacher_list_cost_is_independent_of_teacher_count(self):
        def page():
            cache.clear()
            self.client.get(
                reverse("school-admin-teacher-performance"), {"page_size": 100}
            )

        for _ in range(2):
            self._teacher_with_data()
        small = count_queries(page)
        for _ in range(15):
            self._teacher_with_data()
        large = count_queries(page)

        self.assertEqual(large, small, f"{small} queries -> {large}")

    def test_teacher_list_never_includes_another_schools_teacher(self):
        other = School.objects.create(name="Other endpoint school")
        outsider = self.user(UserTypes.TEACHER, school=other)
        mine = self._teacher_with_data()

        cache.clear()
        response = self.client.get(reverse("school-admin-teacher-performance"))
        ids = {row["id"] for row in response.data["results"]}

        self.assertIn(str(mine.id), ids)
        self.assertNotIn(str(outsider.id), ids)


class ExpectedSubmissionQueryTest(Builder, APITestCase):
    """superadmin + school-admin `students`: expected submissions used two
    queries per active course."""

    @override_settings(CACHES=LOCMEM)
    def test_superadmin_students_cost_and_value(self):
        admin = self.user(UserTypes.SUPER_ADMIN, is_superuser=True)
        self.client.force_authenticate(admin)
        teacher = self.user(UserTypes.TEACHER)

        def build_courses(n):
            for _ in range(n):
                c = self.course(teacher)
                for _ in range(2):
                    self.assignment(c)
                for _ in range(3):
                    self.enrol(self.user(UserTypes.STUDENT), c)

        def page():
            cache.clear()
            return self.client.get(reverse("dashboard-students"))

        build_courses(2)
        small = count_queries(page)
        build_courses(20)
        large = count_queries(page)
        self.assertEqual(large, small, f"{small} queries -> {large}")

        # 22 courses x (2 assignments x 3 enrolments) = 132 expected, 0 submitted.
        self.course(teacher, is_active=False)  # inactive: must not count
        self.assertEqual(page().data["global_assignment_completion_rate"], 0.0)

    @override_settings(CACHES=LOCMEM)
    def test_school_students_counts_only_this_schools_active_courses(self):
        school = School.objects.create(name="Completion school")
        admin = self.user(UserTypes.SCHOOL_ADMIN, school=school)
        self.client.force_authenticate(admin)
        teacher = self.user(UserTypes.TEACHER, school=school)
        course = self.course(teacher)
        students = [self.user(UserTypes.STUDENT) for _ in range(2)]
        for s in students:
            self.enrol(s, course)
        a1 = self.assignment(course)
        self.assignment(course)  # the second of the 4 expected
        self.submit(a1, students[0], 70)  # 1 of 4 expected

        # Noise that must not count: another school, and an inactive course.
        outsider = self.user(
            UserTypes.TEACHER, school=School.objects.create(name="Noise")
        )
        noise = self.course(outsider)
        self.assignment(noise)
        self.enrol(self.user(UserTypes.STUDENT), noise)
        dormant = self.course(teacher, is_active=False)
        self.assignment(dormant)
        self.enrol(self.user(UserTypes.STUDENT), dormant)

        cache.clear()
        response = self.client.get(reverse("school-admin-students"))

        self.assertEqual(response.data["assignment_completion_rate"], 25.0)

    @override_settings(CACHES=LOCMEM)
    def test_school_students_cost_is_independent_of_course_count(self):
        """Measured before the fix: 9 queries for 2 courses, 65 for 30."""
        school = School.objects.create(name="Cost school")
        admin = self.user(UserTypes.SCHOOL_ADMIN, school=school)
        self.client.force_authenticate(admin)
        teacher = self.user(UserTypes.TEACHER, school=school)

        def build_courses(n):
            for _ in range(n):
                c = self.course(teacher)
                self.assignment(c)
                self.enrol(self.user(UserTypes.STUDENT), c)

        def page():
            cache.clear()
            self.client.get(reverse("school-admin-students"))

        build_courses(2)
        small = count_queries(page)
        build_courses(20)
        large = count_queries(page)

        self.assertEqual(large, small, f"{small} queries -> {large}")


# ============================================================== finding 6


@override_settings(CACHES=LOCMEM)
class FlaggedForReviewThresholdTest(Builder, APITestCase):
    def test_flagging_uses_the_canonical_confidence_threshold(self):
        self.assertEqual(AI_CONFIDENCE_THRESHOLD, 80)
        school = School.objects.create(name="Threshold school")
        admin = self.user(UserTypes.SCHOOL_ADMIN, school=school)
        teacher = self.user(UserTypes.TEACHER, school=school)
        course = self.course(teacher)
        student = self.user(UserTypes.STUDENT)
        self.enrol(student, course)
        for confidence in (79.9, 75, 80, 95):
            self.submit(
                self.assignment(course), student, 70, grading_confidence=confidence
            )

        self.client.force_authenticate(admin)
        response = self.client.get(reverse("school-admin-summary"))

        # 79.9 and 75 are below 80; 80 itself is not. Under the old bare 70,
        # neither would have been flagged.
        self.assertEqual(response.data["flagged_for_review_count"], 2)
        self.assertEqual(response.data["flagged_for_review_percentage"], 50.0)


# ============================================================== finding 7


@override_settings(CACHES=LOCMEM)
class TeacherStudentsPaginationTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.course_obj = self.course(self.teacher)
        self.client.force_authenticate(self.teacher)
        self.url = reverse(
            "teacher-admin-students", kwargs={"course_id": self.course_obj.id}
        )

    def _students(self, n):
        for i in range(n):
            self.enrol(self.user(UserTypes.STUDENT, first=f"S{i:03d}"), self.course_obj)

    def _get(self, **params):
        cache.clear()
        return self.client.get(self.url, params)

    def test_returns_the_standard_envelope_with_default_page_size(self):
        self._students(25)

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 25)
        self.assertEqual(len(response.data["results"]), 20)
        self.assertIsNotNone(response.data["next"])

    def test_page_two_holds_the_remainder_with_no_overlap(self):
        self._students(25)

        first = {r["student_id"] for r in self._get(page=1).data["results"]}
        second = {r["student_id"] for r in self._get(page=2).data["results"]}

        self.assertEqual(len(second), 5)
        self.assertFalse(first & second)

    def test_page_size_is_honoured_and_capped(self):
        self._students(5)
        self.assertEqual(len(self._get(page_size=2).data["results"]), 2)

        self._students(120)
        self.assertEqual(len(self._get(page_size=1000).data["results"]), 100)

    def test_invalid_page_size_falls_back_to_the_default(self):
        self._students(25)

        self.assertEqual(len(self._get(page_size="abc").data["results"]), 20)

    def test_page_past_the_end_is_404(self):
        self._students(3)

        self.assertEqual(self._get(page=5).status_code, status.HTTP_404_NOT_FOUND)

    def test_non_numeric_page_is_404(self):
        self._students(3)

        self.assertEqual(self._get(page="abc").status_code, status.HTTP_404_NOT_FOUND)

    def test_empty_course_is_an_empty_first_page(self):
        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])


# ============================================================== finding 4


class AtRiskAlertDeliveryTest(Builder, TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Alert delivery school")
        self.admin = self.user(UserTypes.SCHOOL_ADMIN, school=self.school)
        self.admin.settings.notify_at_risk_student_alerts = True
        self.admin.settings.save(update_fields=["notify_at_risk_student_alerts"])
        self.teacher = self.user(UserTypes.TEACHER, school=self.school)
        self.course_obj = self.course(self.teacher)

    def _struggling_student(self, first="Struggling"):
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, self.course_obj)
        self.submit(
            self.assignment(self.course_obj, due=timezone.now() - timedelta(days=2)),
            student,
            15,
        )
        return student

    def _second_admin(self):
        admin = self.user(UserTypes.SCHOOL_ADMIN, school=self.school)
        admin.settings.notify_at_risk_student_alerts = True
        admin.settings.save(update_fields=["notify_at_risk_student_alerts"])
        return admin

    def test_alert_lost_to_an_outage_is_delivered_after_recovery(self):
        student = self._struggling_student()

        with patch(
            "dashboard.tasks.send_email_task.delay",
            side_effect=ConnectionError("broker down"),
        ):
            outage = send_at_risk_student_alerts()

        state = StudentRiskAlertState.objects.get(student=student)
        self.assertIn("Queued 0", outage)
        self.assertTrue(state.alert_pending)
        self.assertIsNone(state.last_alerted_at)

        with patch("dashboard.tasks.send_email_task.delay") as delay:
            recovered = send_at_risk_student_alerts()

        self.assertIn("Queued 1", recovered)
        delay.assert_called_once()
        self.assertIn(student.get_full_name(), delay.call_args.kwargs["message"])
        state.refresh_from_db()
        self.assertFalse(state.alert_pending)
        self.assertIsNotNone(state.last_alerted_at)

    def test_a_delivered_alert_is_not_repeated(self):
        self._struggling_student()

        with patch("dashboard.tasks.send_email_task.delay"):
            send_at_risk_student_alerts()
        with patch("dashboard.tasks.send_email_task.delay") as delay:
            again = send_at_risk_student_alerts()

        self.assertIn("Queued 0", again)
        delay.assert_not_called()

    def test_outage_across_several_runs_still_delivers_once_recovered(self):
        student = self._struggling_student()

        with patch(
            "dashboard.tasks.send_email_task.delay", side_effect=OSError("down")
        ):
            send_at_risk_student_alerts()
            send_at_risk_student_alerts()
        with patch("dashboard.tasks.send_email_task.delay") as delay:
            send_at_risk_student_alerts()

        delay.assert_called_once()
        self.assertFalse(
            StudentRiskAlertState.objects.get(student=student).alert_pending
        )

    def test_partial_failure_keeps_the_alert_pending_for_retry(self):
        second = self._second_admin()
        student = self._struggling_student()

        def fail_for_second(**kwargs):
            if kwargs["recipient_list"] == [second.email]:
                raise ConnectionError("queue flapped")

        with patch(
            "dashboard.tasks.send_email_task.delay", side_effect=fail_for_second
        ):
            send_at_risk_student_alerts()
        self.assertTrue(
            StudentRiskAlertState.objects.get(student=student).alert_pending
        )

        with patch("dashboard.tasks.send_email_task.delay") as delay:
            send_at_risk_student_alerts()

        recipients = {tuple(c.kwargs["recipient_list"]) for c in delay.call_args_list}
        self.assertIn((second.email,), recipients)
        self.assertFalse(
            StudentRiskAlertState.objects.get(student=student).alert_pending
        )

    def test_recovery_during_an_outage_discharges_the_alert(self):
        student = self._struggling_student()
        with patch(
            "dashboard.tasks.send_email_task.delay", side_effect=OSError("down")
        ):
            send_at_risk_student_alerts()

        for _ in range(4):
            self.submit(
                self.assignment(
                    self.course_obj, due=timezone.now() - timedelta(days=1)
                ),
                student,
                100,
            )
        with patch("dashboard.tasks.send_email_task.delay") as delay:
            send_at_risk_student_alerts()

        delay.assert_not_called()
        state = StudentRiskAlertState.objects.get(student=student)
        self.assertFalse(state.is_at_risk)
        self.assertFalse(state.alert_pending)


# ============================================================== finding 2


@override_settings(CACHES=LOCMEM)
class SchoolAdminAIContextTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="Context school")
        self.admin = self.user(UserTypes.SCHOOL_ADMIN, school=self.school)
        self.client.force_authenticate(self.admin)
        self.url = reverse("school-admin-custom-ai-prompt")

    def _context(self, mock_retry):
        mock_retry.return_value = "ok"
        response = self.client.post(self.url, {"prompt": "How are my teachers doing?"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return mock_retry.call_args[0][1]

    def _teachers_section(self, context):
        return context.split("### TEACHERS METRICS")[1].split("###")[0]

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_teachers_section_carries_real_teacher_data(self, mock_retry):
        teacher = self.user(
            UserTypes.TEACHER, school=self.school, first="Zanele", last="Okafor"
        )
        for _ in range(3):
            self.course(teacher)

        section = self._teachers_section(self._context(mock_retry))

        self.assertNotEqual(section.strip(), "{}")
        self.assertIn("Zanele Okafor", section)
        self.assertIn('"courses":3', section)
        self.assertIn('"teachers_total":1', section)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_teachers_section_lists_every_teacher_not_just_one_page(self, mock_retry):
        for i in range(25):  # more than one default page of 20
            self.user(UserTypes.TEACHER, school=self.school, first=f"T{i:02d}")

        section = self._teachers_section(self._context(mock_retry))

        self.assertIn('"teachers_total":25', section)
        self.assertIn("T24", section)
        self.assertNotIn("limit", section)

    @patch("dashboard.services.SchoolAdminAIContextService.MAX_TEACHERS", 3)
    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_a_capped_section_says_what_it_left_out(self, mock_retry):
        for _ in range(5):
            self.user(UserTypes.TEACHER, school=self.school)

        section = self._teachers_section(self._context(mock_retry))

        self.assertIn('"teachers_total":5', section)
        self.assertIn("Showing 3 of 5 teachers", section)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_teachers_section_never_includes_another_schools_teacher(self, mock_retry):
        other = School.objects.create(name="Other context school")
        self.user(UserTypes.TEACHER, school=other, first="Outsider", last="Teacher")
        self.user(
            UserTypes.TEACHER, school=self.school, first="Insider", last="Teacher"
        )

        context = self._context(mock_retry)

        self.assertIn("Insider Teacher", context)
        self.assertNotIn("Outsider Teacher", context)
        self.assertNotIn("Other context school", context)

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_request_cost_is_independent_of_teacher_count(self, mock_retry):
        """Measured before the fix: 32 queries for a small school, 88 for a
        large one."""

        def grow(n):
            for _ in range(n):
                teacher = self.user(UserTypes.TEACHER, school=self.school)
                course = self.course(teacher)
                student = self.user(UserTypes.STUDENT, school=self.school)
                self.enrol(student, course, final_grade=70)
                self.submit(self.assignment(course), student, 70)

        def ask():
            cache.clear()  # measure a cold build, not cached sections
            self._context(mock_retry)

        ask()  # warm-up: the first request also creates the chat session
        grow(2)
        small = count_queries(ask)
        grow(12)
        large = count_queries(ask)

        self.assertEqual(large, small, f"{small} queries -> {large}")

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry")
    def test_a_failing_section_is_logged_and_marked_unavailable(self, mock_retry):
        with patch(
            "dashboard.services.SchoolAdminAIContextService.teachers",
            side_effect=RuntimeError("teachers blew up"),
        ):
            with self.assertLogs("dashboard.views", level=logging.ERROR) as logs:
                context = self._context(mock_retry)

        self.assertIn("UNAVAILABLE", self._teachers_section(context))
        self.assertTrue(
            any("context section failed" in line for line in logs.output), logs.output
        )


# ============================================================== finding 3


class TeacherAIContextTest(Builder, TestCase):
    def _teacher_of_size(self, courses, assignments_each, students_each, *, at_risk=1):
        teacher = self.user(UserTypes.TEACHER)
        now = timezone.now()
        for c_i in range(courses):
            course = self.course(teacher, name=f"Course-{teacher.id}-{c_i}")
            students = [
                self.user(UserTypes.STUDENT, first=f"Pupil{c_i}x{s}")
                for s in range(students_each)
            ]
            for s in students:
                self.enrol(s, course)
            for a_i in range(assignments_each):
                a = self.assignment(
                    course, title=f"Work-{c_i}-{a_i}", due=now - timedelta(days=1)
                )
                for idx, s in enumerate(students):
                    self.submit(
                        a, s, 20 if idx < at_risk else 85, grading_confidence=90
                    )
        return teacher

    def test_query_count_is_fixed_across_realistic_sizes(self):
        """Measured before the fix: +37 queries per 10 assignments.

        The proof is exact equality (assertEqual on the set of counts), not
        a threshold - any per-item query the service issues shows up as a
        different count at a different size, whatever that size is. These
        sizes only need to differ meaningfully from each other, not resemble
        a real teacher's course load; shrunk from (3,10,15)/(6,25,30) to cut
        this test's fixture-creation cost without weakening what it catches.
        """
        service = TeacherAIContextService()
        small = self._teacher_of_size(1, 1, 2)
        medium = self._teacher_of_size(2, 4, 5)
        large = self._teacher_of_size(3, 8, 8)

        counts = [
            count_queries(lambda t=t: service.build(t)) for t in (small, medium, large)
        ]

        self.assertEqual(len(set(counts)), 1, counts)

    def test_required_context_is_present(self):
        teacher = self._teacher_of_size(3, 4, 5, at_risk=2)
        upcoming = self.assignment(
            Course.objects.filter(teacher=teacher).first(),
            title="Next week's essay",
            due=timezone.now() + timedelta(days=7),
        )

        data = TeacherAIContextService().build(teacher)

        self.assertEqual(data["courses_total"], 3)
        self.assertEqual(
            {c["course"] for c in data["courses"]},
            set(Course.objects.filter(teacher=teacher).values_list("name", flat=True)),
        )
        self.assertEqual(data["at_risk_students_total"], 6)  # 2 per course
        self.assertEqual(sum(1 for s in data["students"] if s["at_risk"]), 6)
        self.assertEqual(
            data["lowest_scoring_assignments"][0]["average_score"], 59.0
        )  # (2x20 + 3x85) / 5
        self.assertIn(
            upcoming.title, [a["title"] for a in data["upcoming_assignments"]]
        )
        self.assertEqual(
            data["limits"], ["Nothing was left out: every record is listed."]
        )

    def test_limits_are_stated_never_silent(self):
        teacher = self._teacher_of_size(2, 3, 4, at_risk=4)
        service = TeacherAIContextService()

        with patch.object(TeacherAIContextService, "MAX_STUDENT_ROWS", 3), patch.object(
            TeacherAIContextService, "MAX_RECENT_ASSIGNMENTS", 2
        ):
            data = service.build(teacher)

        self.assertEqual(len(data["students"]), 3)
        self.assertEqual(data["student_enrolments_total"], 8)
        self.assertEqual(data["at_risk_students_total"], 8)
        self.assertEqual(len(data["recent_assignments"]), 2)
        self.assertEqual(data["assignments_total"], 6)
        joined = " ".join(data["limits"])
        self.assertIn("students: showing 3 of 8", joined)
        self.assertIn("recent_assignments: showing 2 of 6", joined)

    def test_at_risk_students_are_the_last_to_be_cut(self):
        teacher = self._teacher_of_size(1, 2, 10, at_risk=3)

        with patch.object(TeacherAIContextService, "MAX_STUDENT_ROWS", 3):
            data = TeacherAIContextService().build(teacher)

        self.assertTrue(all(row["at_risk"] for row in data["students"]))

    def test_context_size_is_bounded_as_data_grows(self):
        """ "modest" and "big" both already exceed MAX_STUDENT_ROWS/
        MAX_RECENT_ASSIGNMENTS below, so the listed rows are clamped to the
        same cap in both cases regardless of how far past it "big" goes -
        the proof only needs "big" to stay meaningfully over the caps, not
        to be enormous. Shrunk from (2,30,40) to cut fixture-creation cost.
        """
        from dashboard.services import dashboard_context_json

        service = TeacherAIContextService()
        with patch.object(
            TeacherAIContextService, "MAX_STUDENT_ROWS", 20
        ), patch.object(TeacherAIContextService, "MAX_RECENT_ASSIGNMENTS", 10):
            modest = len(
                dashboard_context_json(service.build(self._teacher_of_size(2, 6, 12)))
            )
            big = len(
                dashboard_context_json(service.build(self._teacher_of_size(2, 10, 15)))
            )

        # Totals and aggregates grow a little; the listed rows do not.
        self.assertLess(big, modest * 1.5, (modest, big))

    def test_other_teachers_data_never_appears(self):
        mine = self._teacher_of_size(1, 1, 2)
        theirs = self._teacher_of_size(1, 1, 2)
        their_course = Course.objects.get(teacher=theirs).name

        context = str(TeacherAIContextService().build(mine))

        self.assertNotIn(their_course, context)


@override_settings(CACHES=LOCMEM)
class TeacherAIChatRequestCostTest(Builder, APITestCase):
    """The endpoint, not just the service: a teacher chat request must cost
    the same however much the teacher has taught. Measured before the fix:
    65 queries for a small teacher, 307 for a large one."""

    @patch("dashboard.views.ai_processor.custom_ai_prompt_retry", return_value="ok")
    def test_request_cost_is_independent_of_teacher_size(self, _retry):
        teacher = self.user(UserTypes.TEACHER)
        self.client.force_authenticate(teacher)
        url = reverse("teacher-admin-custom-ai-prompt")

        def grow(courses, assignments, students):
            for _ in range(courses):
                course = self.course(teacher)
                pupils = [self.user(UserTypes.STUDENT) for _ in range(students)]
                for pupil in pupils:
                    self.enrol(pupil, course)
                for _ in range(assignments):
                    a = self.assignment(course, due=timezone.now() - timedelta(days=1))
                    for pupil in pupils:
                        self.submit(a, pupil, 70)

        def ask():
            cache.clear()
            response = self.client.post(url, {"prompt": "q"})
            self.assertEqual(response.status_code, status.HTTP_200_OK)

        ask()  # warm-up: the first request also creates the chat session
        grow(1, 1, 2)
        small = count_queries(ask)
        grow(3, 6, 5)
        large = count_queries(ask)

        self.assertEqual(large, small, f"{small} queries -> {large}")


class AIChatTransactionTest(Builder, APITransactionTestCase):
    """No database transaction may be held open across the provider call."""

    def setUp(self):
        cache.clear()

    def _assert_no_transaction_during_call(self, user, url):
        seen = {}

        def provider(*args, **kwargs):
            seen["in_atomic_block"] = connection.in_atomic_block
            return "answer"

        self.client.force_authenticate(user)
        with override_settings(CACHES=LOCMEM), patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry", side_effect=provider
        ):
            response = self.client.post(url, {"prompt": "question"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(
            seen["in_atomic_block"], "transaction held across provider call"
        )
        self.assertEqual(
            list(
                ChatMessage.objects.values_list("role", "content").order_by("timestamp")
            ),
            [("user", "question"), ("assistant", "answer")],
        )

    def test_teacher_chat(self):
        teacher = self.user(UserTypes.TEACHER)
        self.course(teacher)
        self._assert_no_transaction_during_call(
            teacher, reverse("teacher-admin-custom-ai-prompt")
        )

    def test_school_admin_chat(self):
        school = School.objects.create(name="Txn school")
        self._assert_no_transaction_during_call(
            self.user(UserTypes.SCHOOL_ADMIN, school=school),
            reverse("school-admin-custom-ai-prompt"),
        )

    def test_superadmin_chat(self):
        self._assert_no_transaction_during_call(
            self.user(UserTypes.SUPER_ADMIN, is_superuser=True),
            reverse("dashboard-custom-ai-prompt"),
        )

    def test_failed_provider_call_persists_nothing(self):
        teacher = self.user(UserTypes.TEACHER)
        self.client.force_authenticate(teacher)
        with override_settings(CACHES=LOCMEM), patch(
            "dashboard.views.ai_processor.custom_ai_prompt_retry",
            side_effect=RuntimeError("provider down"),
        ):
            response = self.client.post(
                reverse("teacher-admin-custom-ai-prompt"), {"prompt": "q"}
            )

        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(ChatMessage.objects.count(), 0)
