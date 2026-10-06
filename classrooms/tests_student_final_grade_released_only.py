"""H-130: the course final grade a STUDENT sees counts released work only.

The founder's rule (2026-10-06): a student should not know a grade exists
before the teacher releases it. `StudentCourse.final_grade` is recalculated
on every submission save from every GRADED submission, released or not, and
`/student-course` returned that stored number to the student: grading a
piece of work moved a number the student could read, and with one
unreleased item its exact score could be worked out from the rest.

The stored value stays what it is, the staff figure. For a student reader
the number and its letter come from released submissions only, through the
same formula. So the question every test here asks is the founder's: does
the student's figure differ between "submitted" and "graded but not
released"? It must not.

Scores are written the way production writes them (the AI grading persist
step and the publish routes); `final_grade` is never set by hand.
"""

from decimal import Decimal

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status

from classrooms.final_grade import final_grade_from
from classrooms.signals import compute_final_grade
from classrooms.tests_final_grade_zero_score import FinalGradeZeroScoreBase
from students.models import StudentSubmission


class StudentFinalGradeBase(FinalGradeZeroScoreBase):
    def setUp(self):
        super().setUp()
        self.first, self.second, self.third = (
            self.submission,
            self.ungraded[0],
            self.ungraded[1],
        )

    def release(self, submission):
        """The teacher's single-submission release route."""
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            reverse("student-submission-publish-grade", kwargs={"pk": submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def release_all(self, assignment):
        """The teacher's release-everything route for one assignment."""
        self.client.force_authenticate(self.teacher)
        response = self.client.post(
            reverse("assignment-publish-all-grades", kwargs={"pk": assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

    def detail(self, user, *, fresh=True):
        if fresh:
            cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(
            reverse("student-course-detail", kwargs={"pk": self.enrollment().pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data

    def listed(self, user, *, fresh=True):
        if fresh:
            cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(reverse("student-course-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        rows = [
            row
            for row in response.data["results"]
            if str(row["id"]) == str(self.enrollment().pk)
        ]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def assert_grade(self, data, expected, letter):
        self.assertIsNotNone(data["final_grade"])
        self.assertEqual(Decimal(data["final_grade"]), Decimal(expected))
        self.assertIsNotNone(data["grade_letter"])
        self.assertEqual(data["grade_letter"]["letter_grade"], letter)

    def assert_no_grade(self, data):
        self.assertIsNone(data["final_grade"])
        self.assertIsNone(data["grade_letter"])


class TheStudentsNumberDoesNotMoveBeforeRelease(StudentFinalGradeBase):
    def test_graded_but_unreleased_work_reads_like_submitted_work_on_the_detail(self):
        """The founder's rule itself: the same payload before and after
        grading, while nothing is released."""
        submitted = self.detail(self.student)

        self.grade_by_ai(self.first, 7)

        graded = self.detail(self.student)
        self.assertEqual(graded["final_grade"], submitted["final_grade"])
        self.assertEqual(graded["grade_letter"], submitted["grade_letter"])
        self.assert_no_grade(graded)

    def test_graded_but_unreleased_work_reads_like_submitted_work_on_the_list(self):
        submitted = self.listed(self.student)

        self.grade_by_ai(self.first, 7)

        graded = self.listed(self.student)
        self.assertEqual(graded["final_grade"], submitted["final_grade"])
        self.assertEqual(graded["grade_letter"], submitted["grade_letter"])
        self.assert_no_grade(graded)

    def test_an_unreleased_grade_does_not_move_a_number_already_shown(self):
        """10/10 released, then a 0/10 graded and held back. Counting it
        would halve the number and name the held-back score exactly."""
        self.grade_by_ai(self.first, 10)
        self.release(self.first)
        self.assert_grade(self.detail(self.student), "100.00", "A+")

        self.grade_by_ai(self.second, 0)

        self.assert_grade(self.detail(self.student), "100.00", "A+")
        self.assert_grade(self.listed(self.student), "100.00", "A+")

    def test_a_number_cached_before_grading_is_the_number_after_it(self):
        self.grade_by_ai(self.first, 10)
        self.release(self.first)
        before = self.detail(self.student)

        self.grade_by_ai(self.second, 0)

        after = self.detail(self.student, fresh=False)
        self.assertEqual(after["final_grade"], before["final_grade"])
        self.assertEqual(after["grade_letter"], before["grade_letter"])


class ReleaseIsWhatMovesTheStudentsNumber(StudentFinalGradeBase):
    def test_releasing_one_submission_adds_it(self):
        self.grade_by_ai(self.first, 10)
        self.release(self.first)
        self.grade_by_ai(self.second, 0)
        self.assert_grade(self.detail(self.student), "100.00", "A+")

        self.release(self.second)

        # no cache.clear(): the release must not leave the old number served
        self.assert_grade(self.detail(self.student, fresh=False), "50.00", "F")
        self.assert_grade(self.listed(self.student, fresh=False), "50.00", "F")

    def test_releasing_a_whole_assignment_adds_it(self):
        self.grade_by_ai(self.first, 10)
        self.release(self.first)
        self.grade_by_ai(self.second, 0)
        self.assert_grade(self.detail(self.student), "100.00", "A+")

        self.release_all(self.second.assignment)

        self.assert_grade(self.detail(self.student, fresh=False), "50.00", "F")

    def test_a_released_zero_is_a_grade(self):
        self.grade_by_ai(self.first, 0)
        self.release(self.first)

        self.assert_grade(self.detail(self.student), "0.00", "F")

    def test_released_work_is_weighted_by_its_points(self):
        """5/5 and 0/100: a plain mean of percentages would say 50."""
        self.grade_by_ai(self.first, 5, max_points=5)
        self.grade_by_ai(self.second, 0, max_points=100)
        self.release(self.first)
        self.release(self.second)

        self.assert_grade(self.detail(self.student), "4.76", "F")

    def test_released_work_with_no_stored_maximum_uses_the_assignments_points(self):
        """H-33's rows: graded before `max_points` was stored. 4 of the
        assignment's 10 points."""
        StudentSubmission.objects.filter(pk=self.first.pk).update(
            score=4, max_points=None, graded_at=timezone.now(), is_published=True
        )

        self.assert_grade(self.detail(self.student), "40.00", "F")


class StaffStillSeeEveryGradedItem(StudentFinalGradeBase):
    def setUp(self):
        super().setUp()
        self.grade_by_ai(self.first, 10)
        self.release(self.first)
        self.grade_by_ai(self.second, 0)

    def test_the_teacher_reads_the_stored_number(self):
        self.assert_grade(self.detail(self.teacher), "50.00", "F")
        self.assert_grade(self.listed(self.teacher), "50.00", "F")

    def test_the_stored_number_still_counts_unreleased_work(self):
        self.assert_final_grade("50.00")

    def test_the_teacher_and_the_student_differ_only_while_something_is_held_back(
        self,
    ):
        self.assert_grade(self.detail(self.student), "100.00", "A+")
        self.assert_grade(self.detail(self.teacher), "50.00", "F")

        self.release(self.second)

        student, teacher = self.detail(self.student), self.detail(self.teacher)
        self.assertEqual(student["final_grade"], teacher["final_grade"])
        self.assertEqual(student["grade_letter"], teacher["grade_letter"])


class OneFormula(StudentFinalGradeBase):
    """The student's figure and the stored one are the same arithmetic."""

    def test_with_everything_released_the_two_figures_are_equal(self):
        self.grade_by_ai(self.first, 7, max_points=10)
        self.grade_by_ai(self.second, 33, max_points=40)
        StudentSubmission.objects.filter(pk=self.third.pk).update(
            score=4, max_points=None, graded_at=timezone.now()
        )
        StudentSubmission.objects.filter(student=self.student).update(is_published=True)
        stored = compute_final_grade(self.student.pk, self.course.pk)

        self.assertEqual(stored, Decimal("73.33"))
        self.assertEqual(Decimal(self.detail(self.student)["final_grade"]), stored)

    def test_the_arithmetic(self):
        cases = {
            "nothing": ([], None),
            "a zero": ([(Decimal("0"), 10)], Decimal("0.00")),
            "weighted": ([(Decimal("5"), 5), (Decimal("0"), 100)], Decimal("4.76")),
            "no points anywhere": ([(Decimal("4"), None)], None),
            "zero points are left out": (
                [(Decimal("4"), 0), (Decimal("5"), 10)],
                Decimal("50.00"),
            ),
            "over the top is clamped": ([(Decimal("12"), 10)], Decimal("100.00")),
            "under zero is clamped": ([(Decimal("-2"), 10)], Decimal("0.00")),
            "a half rounds up": ([(Decimal("1"), 800)], Decimal("0.13")),
            "rounds to two places": ([(Decimal("2"), 3)], Decimal("66.67")),
        }
        for name, (scored, expected) in cases.items():
            with self.subTest(case=name):
                self.assertEqual(final_grade_from(scored), expected)
