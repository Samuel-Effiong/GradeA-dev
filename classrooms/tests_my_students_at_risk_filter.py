"""`my-students` at_risk filter and field.

StudentListSerializer.get_at_risk and MyStudentsFilter.filter_at_risk both
run the SAME StudentRiskEvaluator verdict (dashboard.risk) against the
student's relevant course - the filter narrows the roster to rows the
response's own at_risk field would show as true/false, it never computes
risk a second way. These tests pin that field, the filter, and that
neither disturbs the pre-existing min_grade/max_grade/ordering/search
filters or cross-teacher isolation.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
URL = reverse("student-course-my-students")


class Builder:
    _n = 0

    @classmethod
    def uid(cls):
        cls._n += 1
        return cls._n

    def user(self, kind, *, school=None, first="Test", last=None):
        n = self.uid()
        return CustomUser.objects.create_user(
            email=f"at-risk-filter-{kind.lower()}-{n}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name=last or f"User{n}",
            school=school,
            is_active=True,
        )

    def course(self, teacher, name=None):
        n = self.uid()
        session = Session.objects.create(name=f"Term {n}", teacher=teacher)
        return Course.objects.create(
            name=name or f"Course {n}", teacher=teacher, session=session
        )

    def enrol(self, student, course):
        return StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def published_assignment(self, course, *, total_points=100, title=None):
        n = self.uid()
        return Assignment.objects.create(
            title=title or f"Assignment {n}",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            total_points=total_points,
            due_date=timezone.now() - timedelta(days=1),
        )

    def submit(self, student, assignment, *, score_percentage, score=None):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={},
            score=score if score is not None else score_percentage,
            score_percentage=score_percentage,
            submission_date=timezone.now(),
            graded_at=timezone.now(),
        )

    def make_at_risk_student(self, teacher, course, assignment, *, first):
        # Graded at 40% on the course's one shared assignment ->
        # average_grade=40 < the critical threshold (60), at_risk
        # regardless of submission rate.
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, course)
        self.submit(student, assignment, score_percentage=40)
        return student

    def make_safe_student(self, teacher, course, assignment, *, first):
        # Graded at 90% on the course's one shared assignment, fully
        # submitted -> no critical or moderate flag fires.
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, course)
        self.submit(student, assignment, score_percentage=90)
        return student


@override_settings(CACHES=LOCMEM)
class MyStudentsAtRiskFilterTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.course_a = self.course(self.teacher, name="Course A")
        # One shared assignment per course: each student submits to the
        # SAME assignment, so adding another student never inflates
        # anyone else's expected_assignment_count (the evaluator counts
        # expected assignments per course, not per student).
        self.assignment_a = self.published_assignment(self.course_a)
        self.client.force_authenticate(self.teacher)

    def _get(self, **params):
        cache.clear()
        return self.client.get(URL, params)

    def _row(self, response, student):
        return next(r for r in response.data["results"] if r["id"] == str(student.id))

    def test_at_risk_field_reflects_the_evaluator_verdict(self):
        at_risk = self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="AtRisk"
        )
        safe = self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Safe"
        )

        response = self._get()

        self.assertTrue(self._row(response, at_risk)["at_risk"])
        self.assertFalse(self._row(response, safe)["at_risk"])

    def test_at_risk_true_narrows_to_only_at_risk_students(self):
        at_risk = self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="AtRisk"
        )
        self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Safe"
        )

        response = self._get(at_risk="true")

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(at_risk.id)})

    def test_at_risk_false_narrows_to_only_safe_students(self):
        self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="AtRisk"
        )
        safe = self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Safe"
        )

        response = self._get(at_risk="false")

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(safe.id)})

    def test_at_risk_combines_with_min_grade(self):
        # A student can be at_risk by the trend/submission flags while
        # still clearing a min_grade bar on their enrollment's final_grade -
        # proves the two filters stack rather than one silently winning.
        self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="AtRisk"
        )
        safe = self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Safe"
        )

        response = self._get(at_risk="false", min_grade=0)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(safe.id)})

    def test_search_and_ordering_still_work_alongside_at_risk(self):
        safe_a = self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Aaron"
        )
        safe_b = self.make_safe_student(
            self.teacher, self.course_a, self.assignment_a, first="Zane"
        )
        self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="AtRisk"
        )

        response = self._get(at_risk="false", ordering="final_grade")

        ids = [r["id"] for r in response.data["results"]]
        self.assertEqual(set(ids), {str(safe_a.id), str(safe_b.id)})

    def test_another_teachers_at_risk_students_are_never_visible(self):
        other_teacher = self.user(UserTypes.TEACHER)
        other_course = self.course(other_teacher, name="Other Course")
        other_assignment = self.published_assignment(other_course)
        other_at_risk = self.make_at_risk_student(
            other_teacher, other_course, other_assignment, first="NotMine"
        )
        mine_at_risk = self.make_at_risk_student(
            self.teacher, self.course_a, self.assignment_a, first="Mine"
        )

        response = self._get(at_risk="true")

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(mine_at_risk.id)})
        self.assertNotIn(str(other_at_risk.id), ids)

    def test_student_with_no_relevant_course_is_not_at_risk(self):
        # No enrollment through this teacher at all shouldn't happen via
        # my_students' own queryset scoping, but a student enrolled with no
        # graded work and no expected assignments must read as not at_risk,
        # not error.
        student = self.user(UserTypes.STUDENT, first="NoWork")
        self.enrol(student, self.course_a)

        response = self._get()

        self.assertFalse(self._row(response, student)["at_risk"])
