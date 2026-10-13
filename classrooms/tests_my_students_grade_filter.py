"""`my-students` grade filter/sort: `min_grade`, `max_grade` and
`ordering=final_grade`/`-final_grade` against the `final_grade` annotation
StudentCourseViewSet.get_queryset adds for this action - the same
points-weighted figure (classrooms.final_grade.final_grade_from) the row's
"grade" field already shows, scoped to `enrollments__course` when given,
else the student's first course with this teacher (the same "relevant
course" StudentListSerializer resolves)."""

from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
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
            email=f"grade-filter-{kind.lower()}-{n}@audit.test",
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

    def enrol_with_grade(self, student, course, final_grade):
        return StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
            final_grade=Decimal(str(final_grade)) if final_grade is not None else None,
        )


@override_settings(CACHES=LOCMEM)
class MyStudentsGradeFilterTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.course_a = self.course(self.teacher, name="Course A")
        self.client.force_authenticate(self.teacher)

    def _get(self, **params):
        cache.clear()
        return self.client.get(URL, params)

    def _row(self, response, student):
        return next(r for r in response.data["results"] if r["id"] == str(student.id))

    def test_min_grade_excludes_students_below_the_threshold(self):
        low = self.user(UserTypes.STUDENT, first="Low")
        high = self.user(UserTypes.STUDENT, first="High")
        self.enrol_with_grade(low, self.course_a, 40)
        self.enrol_with_grade(high, self.course_a, 90)

        response = self._get(min_grade=60)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(high.id)})

    def test_max_grade_excludes_students_above_the_threshold(self):
        low = self.user(UserTypes.STUDENT, first="Low")
        high = self.user(UserTypes.STUDENT, first="High")
        self.enrol_with_grade(low, self.course_a, 40)
        self.enrol_with_grade(high, self.course_a, 90)

        response = self._get(max_grade=60)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(low.id)})

    def test_min_and_max_grade_combine_to_a_range(self):
        low = self.user(UserTypes.STUDENT, first="Low")
        mid = self.user(UserTypes.STUDENT, first="Mid")
        high = self.user(UserTypes.STUDENT, first="High")
        self.enrol_with_grade(low, self.course_a, 30)
        self.enrol_with_grade(mid, self.course_a, 65)
        self.enrol_with_grade(high, self.course_a, 95)

        response = self._get(min_grade=50, max_grade=80)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(mid.id)})

    def test_student_with_no_graded_work_is_excluded_from_a_grade_range(self):
        ungraded = self.user(UserTypes.STUDENT, first="Ungraded")
        self.enrol_with_grade(ungraded, self.course_a, None)

        response = self._get(min_grade=0)

        self.assertEqual(response.data["results"], [])

    def test_ordering_ascending_by_grade(self):
        low = self.user(UserTypes.STUDENT, first="Low")
        mid = self.user(UserTypes.STUDENT, first="Mid")
        high = self.user(UserTypes.STUDENT, first="High")
        self.enrol_with_grade(mid, self.course_a, 55)
        self.enrol_with_grade(low, self.course_a, 20)
        self.enrol_with_grade(high, self.course_a, 90)

        response = self._get(ordering="final_grade")

        self.assertEqual(
            [r["id"] for r in response.data["results"]],
            [str(low.id), str(mid.id), str(high.id)],
        )

    def test_ordering_descending_by_grade(self):
        low = self.user(UserTypes.STUDENT, first="Low")
        mid = self.user(UserTypes.STUDENT, first="Mid")
        high = self.user(UserTypes.STUDENT, first="High")
        self.enrol_with_grade(mid, self.course_a, 55)
        self.enrol_with_grade(low, self.course_a, 20)
        self.enrol_with_grade(high, self.course_a, 90)

        response = self._get(ordering="-final_grade")

        self.assertEqual(
            [r["id"] for r in response.data["results"]],
            [str(high.id), str(mid.id), str(low.id)],
        )

    def test_grade_filter_respects_the_named_course_not_a_different_one(self):
        # Same student, two of the teacher's courses, different grades.
        # Naming course_a's grade must not pick up course_b's.
        course_b = self.course(self.teacher, name="Course B")
        student = self.user(UserTypes.STUDENT, first="TwoCourses")
        self.enrol_with_grade(student, self.course_a, 90)
        self.enrol_with_grade(student, course_b, 20)

        high_scoped = self._get(enrollments__course=str(self.course_a.id), min_grade=80)
        low_scoped = self._get(enrollments__course=str(course_b.id), min_grade=80)

        self.assertEqual(
            {r["id"] for r in high_scoped.data["results"]}, {str(student.id)}
        )
        self.assertEqual(low_scoped.data["results"], [])

    def test_another_teachers_grades_are_never_visible(self):
        other_teacher = self.user(UserTypes.TEACHER)
        other_course = self.course(other_teacher, name="Other Course")
        other_student = self.user(UserTypes.STUDENT, first="NotMine")
        self.enrol_with_grade(other_student, other_course, 10)
        mine = self.user(UserTypes.STUDENT, first="Mine")
        self.enrol_with_grade(mine, self.course_a, 10)

        response = self._get(max_grade=50)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(mine.id)})

    def test_grade_reflects_points_weighted_submissions_not_a_plain_mean(self):
        # A 5-point quiz at 100% and a 100-point exam at 60%: weighted,
        # the exam dominates - (5 + 60) / (5 + 100) * 100 ~= 61.90, not the
        # plain-mean 80%. Proves the row's filterable grade is final_grade
        # (auto-recomputed by classrooms.signals), not a separately
        # computed average.
        from students.models import StudentSubmission

        student = self.user(UserTypes.STUDENT, first="Weighted")
        enrollment = StudentCourse.objects.create(
            student=student,
            course=self.course_a,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        quiz = Assignment.objects.create(
            title="Quiz",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            total_points=5,
        )
        exam = Assignment.objects.create(
            title="Exam",
            course=self.course_a,
            status=AssignmentStatus.PUBLISHED,
            total_points=100,
        )
        StudentSubmission.objects.create(
            assignment=quiz,
            student=student,
            answers={},
            score=5,
            score_percentage=100,
            graded_at=timezone.now(),
        )
        StudentSubmission.objects.create(
            assignment=exam,
            student=student,
            answers={},
            score=60,
            score_percentage=60,
            graded_at=timezone.now(),
        )
        enrollment.refresh_from_db()

        response = self._get(min_grade=61, max_grade=63)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(student.id)})
        self.assertIsNotNone(enrollment.final_grade)
        assert enrollment.final_grade is not None
        self.assertAlmostEqual(float(enrollment.final_grade), 61.90, places=1)
