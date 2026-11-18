"""Epic E2, D1 (owner Q1): the plain-mean "average" figures become the
points-weighted formula while GRADE_CATEGORIES_ENABLED is on, over the SAME
rows, and stay today's plain mean while it is off.

A 1/5 quiz (20%) and a 90/100 test (90%): plain mean 55, points-weighted
91/105 = 86.67.
"""

from decimal import Decimal

from django.test import SimpleTestCase, TestCase

from classrooms import grade_service
from students.models import StudentSubmission

from .tests_e2_fixtures import (
    categories_off,
    categories_on,
    enroll,
    grade,
    make_assignment,
    make_course,
    make_student,
    make_teacher,
)

PCTS = [20.0, 90.0]
SCORED = [(Decimal("1"), 5), (Decimal("90"), 100)]


class AverageGradeTest(SimpleTestCase):
    @categories_off
    def test_dark_is_the_plain_mean(self):
        self.assertEqual(grade_service.average_grade(PCTS, SCORED), 55.0)
        self.assertEqual(grade_service.average_grade(PCTS, SCORED, ndigits=None), 55.0)

    @categories_on
    def test_on_is_points_weighted(self):
        self.assertEqual(grade_service.average_grade(PCTS, SCORED), 86.67)
        self.assertAlmostEqual(
            grade_service.average_grade(PCTS, SCORED, ndigits=None), 86.6666666, 5
        )

    @categories_on
    def test_on_nothing_weightable_is_none(self):
        self.assertIsNone(grade_service.average_grade([50.0], [(Decimal("5"), 0)]))
        self.assertIsNone(grade_service.average_grade([], []))


class MeanPercentageTest(TestCase):
    def setUp(self):
        teacher = make_teacher()
        self.course = make_course(teacher)
        student = make_student()
        enroll(student, self.course)
        grade(student, make_assignment(self.course, "Quiz", total_points=5), 1)
        grade(student, make_assignment(self.course, "Test", total_points=100), 90)
        self.rows = StudentSubmission.objects.filter(assignment__course=self.course)

    def avg(self, **extra):
        return self.rows.aggregate(avg=grade_service.mean_percentage(**extra))["avg"]

    @categories_off
    def test_dark_is_avg_of_percentages(self):
        self.assertEqual(round(self.avg(), 2), Decimal("55.00"))

    @categories_on
    def test_on_is_points_weighted_sql(self):
        self.assertEqual(round(self.avg(), 2), Decimal("86.67"))

    @categories_on
    def test_on_no_rows_stays_null(self):
        self.assertIsNone(
            self.rows.none().aggregate(avg=grade_service.mean_percentage())["avg"]
        )
        self.assertIsNone(
            StudentSubmission.objects.filter(pk__in=[]).aggregate(
                avg=grade_service.mean_percentage()
            )["avg"]
        )

    @categories_on
    def test_on_through_a_relation_with_a_filter(self):
        from django.db.models import Q

        from classrooms.models import Course

        row = (
            Course.objects.filter(pk=self.course.pk)
            .annotate(
                avg=grade_service.mean_percentage(
                    "assignments__submissions__score_percentage",
                    filter=Q(assignments__title="Test"),
                )
            )
            .values_list("avg", flat=True)
            .get()
        )
        self.assertEqual(round(row, 2), Decimal("90.00"))
