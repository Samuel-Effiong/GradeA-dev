"""Teacher-admin `all_students` endpoint: cross-course (or single-course)
student listing with an optional `at_risk` filter, backing an "All Students"
view that can show the full roster or just the at-risk subset."""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


class Builder:
    _n = 0

    @classmethod
    def uid(cls):
        cls._n += 1
        return cls._n

    def user(self, kind, *, school=None, first="Test", last=None, **extra):
        n = self.uid()
        return CustomUser.objects.create_user(
            email=f"all-students-{kind.lower()}-{n}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name=last or f"User{n}",
            school=school,
            is_active=True,
            **extra,
        )

    def session(self, teacher, name=None):
        return Session.objects.create(
            name=name or f"Term {self.uid()}", teacher=teacher
        )

    def course(self, teacher, session, name=None, **extra):
        n = self.uid()
        return Course.objects.create(
            name=name or f"Course {n}",
            teacher=teacher,
            session=session,
            is_active=extra.pop("is_active", True),
            **extra,
        )

    def enrol(self, student, course, status_=None):
        return StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=status_ or EnrollmentStatusType.ENROLLED,
        )

    def assignment(self, course, *, due=None):
        return Assignment.objects.create(
            title=f"Assignment {self.uid()}",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            due_date=due,
        )

    def submit(self, assignment, student, pct):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=pct,
            score_percentage=pct,
            graded_at=timezone.now(),
            is_published=True,
        )

    def at_risk_student(self, course, first="AtRisk"):
        """2 expected assignments, 0 submitted - a 0% submission rate with
        >=2 expected is critical_missing_work, which alone makes at_risk."""
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, course)
        for _ in range(2):
            self.assignment(course, due=timezone.now() - timedelta(days=1))
        return student

    def safe_student(self, course, first="Safe"):
        """1 expected assignment, submitted at 100% - nowhere near any
        critical or moderate threshold."""
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, course)
        assignment = self.assignment(course, due=timezone.now() - timedelta(days=1))
        self.submit(assignment, student, 100)
        return student


@override_settings(CACHES=LOCMEM)
class TeacherAllStudentsTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.session_obj = self.session(self.teacher)
        self.course_a = self.course(self.teacher, self.session_obj, name="Course A")
        self.course_b = self.course(self.teacher, self.session_obj, name="Course B")
        self.client.force_authenticate(self.teacher)
        self.url = reverse(
            "teacher-admin-all-students", kwargs={"session_id": self.session_obj.id}
        )

    def _get(self, **params):
        cache.clear()
        return self.client.get(self.url, params)

    def test_lists_students_across_every_course_in_the_session(self):
        a = self.safe_student(self.course_a, first="InA")
        b = self.safe_student(self.course_b, first="InB")

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(a.id), str(b.id)})

    def test_course_id_scopes_to_one_course(self):
        a = self.safe_student(self.course_a, first="InA")
        self.safe_student(self.course_b, first="InB")

        response = self._get(course_id=str(self.course_a.id))

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(a.id)})

    def test_at_risk_true_filters_to_only_at_risk_students(self):
        risky = self.at_risk_student(self.course_a, first="Risky")
        self.safe_student(self.course_b, first="Safe")

        response = self._get(at_risk="true")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(risky.id)})
        self.assertTrue(all(r["at_risk"] for r in response.data["results"]))

    def test_at_risk_false_filters_to_only_safe_students(self):
        self.at_risk_student(self.course_a, first="Risky")
        safe = self.safe_student(self.course_b, first="Safe")

        response = self._get(at_risk="false")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(safe.id)})

    def test_at_risk_filter_combines_with_course_id(self):
        risky_a = self.at_risk_student(self.course_a, first="RiskyA")
        self.at_risk_student(self.course_b, first="RiskyB")

        response = self._get(at_risk="true", course_id=str(self.course_a.id))

        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(risky_a.id)})

    def test_student_id_scopes_to_one_students_rows(self):
        wanted = self.safe_student(self.course_a, first="Wanted")
        self.safe_student(self.course_b, first="NotWanted")

        response = self._get(student_id=str(wanted.id))

        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(wanted.id)})

    def test_student_id_combines_with_course_id(self):
        # Same student in both courses - naming one course must return only
        # that course's row for them, not both.
        student = self.user(UserTypes.STUDENT, first="TwoCourses")
        self.enrol(student, self.course_a)
        self.enrol(student, self.course_b)

        response = self._get(
            student_id=str(student.id), course_id=str(self.course_a.id)
        )

        self.assertEqual(len(response.data["results"]), 1)
        self.assertEqual(
            response.data["results"][0]["course_id"], str(self.course_a.id)
        )

    def test_student_id_combines_with_at_risk(self):
        risky = self.at_risk_student(self.course_a, first="Risky")

        matches = self._get(student_id=str(risky.id), at_risk="true")
        no_match = self._get(student_id=str(risky.id), at_risk="false")

        self.assertEqual(
            {r["student_id"] for r in matches.data["results"]}, {str(risky.id)}
        )
        self.assertEqual(no_match.data["results"], [])

    def test_student_id_for_a_student_outside_this_session_is_empty(self):
        other_teacher = self.user(UserTypes.TEACHER)
        other_session = self.session(other_teacher)
        other_course = self.course(other_teacher, other_session)
        other_student = self.safe_student(other_course, first="NotMine")

        response = self._get(student_id=str(other_student.id))

        self.assertEqual(response.data["results"], [])

    def test_rows_report_course_and_submission_counts(self):
        student = self.safe_student(self.course_a, first="Solo")

        response = self._get()

        row = response.data["results"][0]
        self.assertEqual(row["student_id"], str(student.id))
        self.assertEqual(row["course_id"], str(self.course_a.id))
        self.assertEqual(row["course_name"], self.course_a.name)
        self.assertEqual(row["assignment_submitted"], 1)
        self.assertEqual(row["assignment_assigned"], 1)
        self.assertFalse(row["at_risk"])

    def test_withdrawn_enrollment_is_excluded(self):
        student = self.user(UserTypes.STUDENT, first="Withdrawn")
        self.enrol(student, self.course_a, status_=EnrollmentStatusType.WITHDRAWN)

        response = self._get()

        self.assertEqual(response.data["results"], [])

    def test_unreachable_course_id_is_404(self):
        other_teacher = self.user(UserTypes.TEACHER)
        other_session = self.session(other_teacher)
        other_course = self.course(other_teacher, other_session)

        response = self._get(course_id=str(other_course.id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_course_id_outside_the_session_is_404(self):
        other_session = self.session(self.teacher, name="Other term")
        other_course = self.course(self.teacher, other_session)

        response = self._get(course_id=str(other_course.id))

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_session_with_no_courses_is_an_empty_first_page(self):
        empty_session = self.session(self.teacher, name="Empty term")
        url = reverse(
            "teacher-admin-all-students", kwargs={"session_id": empty_session.id}
        )
        cache.clear()

        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 0)
        self.assertEqual(response.data["results"], [])

    def test_pagination_without_at_risk_filter_covers_every_student_once(self):
        for i in range(25):
            self.safe_student(self.course_a, first=f"S{i:03d}")

        first = {r["student_id"] for r in self._get(page=1).data["results"]}
        second = {r["student_id"] for r in self._get(page=2).data["results"]}

        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 5)
        self.assertFalse(first & second)

    def test_pagination_with_at_risk_filter_covers_every_match_once(self):
        for i in range(25):
            self.at_risk_student(self.course_a, first=f"R{i:03d}")
        # A different course, so this student's expected-assignment count
        # isn't inflated by course_a's 50 at-risk-fixture assignments.
        self.safe_student(self.course_b, first="NotCounted")

        first = {
            r["student_id"] for r in self._get(at_risk="true", page=1).data["results"]
        }
        second = {
            r["student_id"] for r in self._get(at_risk="true", page=2).data["results"]
        }

        self.assertEqual(len(first), 20)
        self.assertEqual(len(second), 5)
        self.assertFalse(first & second)

    def test_another_teachers_students_are_never_visible(self):
        other_teacher = self.user(UserTypes.TEACHER)
        other_session = self.session(other_teacher)
        other_course = self.course(other_teacher, other_session)
        self.safe_student(other_course, first="NotMine")
        mine = self.safe_student(self.course_a, first="Mine")

        response = self._get()

        ids = {r["student_id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(mine.id)})

    def test_non_teacher_is_forbidden(self):
        student = self.user(UserTypes.STUDENT)
        self.client.force_authenticate(student)

        response = self._get()

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
