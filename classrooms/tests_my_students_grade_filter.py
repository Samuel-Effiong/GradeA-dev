"""`my-students` grade filter/sort: `min_grade`, `max_grade` and
`ordering=final_grade`/`-final_grade` against `grade` - a points-weighted
average (classrooms.final_grade.final_grade_from) across EVERY course this
teacher teaches the student, not one "relevant" course. `enrollments__course`
still picks which course `course_description`/`teacher` describe, but no
longer scopes the grade itself - a teacher's filter/sort is always "how is
this student doing with me overall," never just their first course.
"""

from decimal import Decimal

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

    def enrol_with_grade(self, student, course, grade):
        """Enrol `student` in `course` and, unless `grade` is None, back it
        with ONE 100-point assignment scored exactly `grade`% - so the
        student's REAL submissions (not a hand-set StudentCourse column)
        are what the production grade formula reads, same as any other
        student. `grade` is a plain number/None, not a Decimal, so callers
        read like the old API.
        """
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        if grade is None:
            return
        assignment = Assignment.objects.create(
            title=f"Assignment {self.uid()}",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            total_points=100,
        )
        StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={},
            score=Decimal(str(grade)),
            score_percentage=Decimal(str(grade)),
            submission_date=timezone.now(),
            graded_at=timezone.now(),
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

    def test_ordering_puts_ungraded_students_last_in_either_direction(self):
        graded = self.user(UserTypes.STUDENT, first="Graded")
        ungraded = self.user(UserTypes.STUDENT, first="Ungraded")
        self.enrol_with_grade(graded, self.course_a, 50)
        self.enrol_with_grade(ungraded, self.course_a, None)

        ascending = self._get(ordering="final_grade")
        descending = self._get(ordering="-final_grade")

        self.assertEqual(
            [r["id"] for r in ascending.data["results"]],
            [str(graded.id), str(ungraded.id)],
        )
        self.assertEqual(
            [r["id"] for r in descending.data["results"]],
            [str(graded.id), str(ungraded.id)],
        )

    def test_grade_is_a_cross_course_aggregate_not_scoped_by_enrollments_course(self):
        # Same student, two of the teacher's courses, equally-weighted
        # (100pt each) opposite scores. Combined, points-weighted: (90 +
        # 20) / 2 = 55 - the SAME figure regardless of which course
        # `enrollments__course` names, because grade is now a person-level
        # summary, not that one course's slice of it.
        course_b = self.course(self.teacher, name="Course B")
        student = self.user(UserTypes.STUDENT, first="TwoCourses")
        self.enrol_with_grade(student, self.course_a, 90)
        self.enrol_with_grade(student, course_b, 20)

        via_course_a = self._get(
            enrollments__course=str(self.course_a.id), min_grade=50
        )
        via_course_b = self._get(enrollments__course=str(course_b.id), min_grade=50)
        too_high = self._get(min_grade=60)

        self.assertEqual(
            {r["id"] for r in via_course_a.data["results"]}, {str(student.id)}
        )
        self.assertEqual(
            {r["id"] for r in via_course_b.data["results"]}, {str(student.id)}
        )
        self.assertEqual(too_high.data["results"], [])

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

    def test_another_teachers_courses_with_the_same_student_never_leak_into_the_grade(
        self,
    ):
        # A student shared with another teacher: that OTHER teacher's
        # course/submissions for this student must not be pulled into THIS
        # teacher's view of the student's grade - `_enrollments(obj)` is
        # already scoped to `teacher_course_access_q`, this just proves the
        # cross-course widening didn't quietly drop that scoping.
        other_teacher = self.user(UserTypes.TEACHER)
        other_course = self.course(other_teacher, name="Other Teacher's Course")
        student = self.user(UserTypes.STUDENT, first="Shared")
        self.enrol_with_grade(student, self.course_a, 90)
        self.enrol_with_grade(student, other_course, 10)

        response = self._get(min_grade=80)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(student.id)})
        self.assertEqual(
            self._row(response, student)["grade"]["percentage"], Decimal("90.00")
        )

    def test_grade_reflects_points_weighted_submissions_not_a_plain_mean(self):
        # A 5-point quiz at 100% and a 100-point exam at 60%: weighted,
        # the exam dominates - (5 + 60) / (5 + 100) * 100 ~= 61.90, not the
        # plain-mean 80%. Proves the row's filterable grade is
        # final_grade_from, not a separately computed average.
        student = self.user(UserTypes.STUDENT, first="Weighted")
        StudentCourse.objects.create(
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

        response = self._get(min_grade=61, max_grade=63)

        ids = {r["id"] for r in response.data["results"]}
        self.assertEqual(ids, {str(student.id)})
        percentage = self._row(response, student)["grade"]["percentage"]
        self.assertAlmostEqual(float(percentage), 61.90, places=1)
