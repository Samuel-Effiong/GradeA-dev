"""
H-251: `my-students` filters by grade trend, and each row says its trend.

`?grade_trend=` takes ONE of improving, declining, stable or
insufficient_data (any letter case). Each row carries `grade_trend` and
`trend_delta` from the same StudentRiskEvaluator verdict as its `at_risk`,
so the filter can never disagree with the rows it returns. Anything else,
including a comma-separated list, is a 400 naming the parameter.

Run with:
    python manage.py test classrooms.tests_h251_grade_trend_filter
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.tests_my_students_at_risk_filter import LOCMEM, URL, Builder
from students.models import StudentSubmission
from users.models import UserTypes


@override_settings(CACHES=LOCMEM)
class GradeTrendFilterTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.course_a = self.course(self.teacher, name="Course A")
        # Shared assignments: every student hands in the same two, so no
        # student's expected count depends on another's.
        self.early = self.published_assignment(self.course_a)
        self.late = self.published_assignment(self.course_a)
        self.client.force_authenticate(self.teacher)

    def _student(self, first, *scores):
        """`scores` graded ten days, then one day, ago."""
        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, self.course_a)
        for assignment, score, days_ago in zip(
            (self.early, self.late), scores, (10, 1), strict=False
        ):
            when = timezone.now() - timedelta(days=days_ago)
            StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers={},
                score=score,
                score_percentage=score,
                submission_date=when,
                graded_at=when,
            )
        return student

    def _get(self, **params):
        cache.clear()
        return self.client.get(URL, params)

    def _names(self, response):
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return sorted(r["full_name"].split()[0] for r in response.data["results"])

    def _class(self):
        self._student("Up", 50, 80)
        self._student("Down", 70, 40)  # average 55: at risk
        self._student("Flat", 80, 81)
        self._student("New", 75)

    # -- the row -----------------------------------------------------------

    def test_each_row_says_its_trend_and_delta(self):
        up = self._student("Up", 50, 80)
        row = self._get().data["results"][0]
        self.assertEqual(row["id"], str(up.id))
        self.assertEqual(row["grade_trend"], "IMPROVING")
        self.assertEqual(row["trend_delta"], 30.0)

    def test_a_student_with_one_grade_has_insufficient_data(self):
        self._student("New", 75)
        row = self._get().data["results"][0]
        self.assertEqual(row["grade_trend"], "INSUFFICIENT DATA")
        self.assertEqual(row["trend_delta"], 0.0)

    # -- the filter --------------------------------------------------------

    def test_each_trend_filters_to_its_students(self):
        self._class()
        expected = {
            "improving": ["Up"],
            "declining": ["Down"],
            "stable": ["Flat"],
            "insufficient_data": ["New"],
        }
        for value, names in expected.items():
            with self.subTest(grade_trend=value):
                self.assertEqual(self._names(self._get(grade_trend=value)), names)

    def test_the_rows_returned_carry_the_trend_asked_for(self):
        self._class()
        self._student("Down2", 80, 50)
        response = self._get(grade_trend="declining")
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(
            {r["grade_trend"] for r in response.data["results"]}, {"DECLINING"}
        )

    def test_letter_case_does_not_matter(self):
        self._class()
        for value in ("Declining", "DECLINING", "Insufficient_Data"):
            with self.subTest(grade_trend=value):
                self.assertEqual(self._get(grade_trend=value).status_code, 200)
        self.assertEqual(self._names(self._get(grade_trend="DECLINING")), ["Down"])

    def test_empty_is_no_filter(self):
        self._class()
        self.assertEqual(len(self._names(self._get(grade_trend=""))), 4)

    def test_it_combines_with_the_other_filters(self):
        self._class()
        self._student("DownSafe", 100, 95)  # declining, but not at risk
        self.assertEqual(
            self._names(self._get(grade_trend="declining")), ["Down", "DownSafe"]
        )
        self.assertEqual(
            self._names(self._get(grade_trend="declining", at_risk="false")),
            ["DownSafe"],
        )
        self.assertEqual(
            self._names(self._get(grade_trend="declining", search="DownSafe")),
            ["DownSafe"],
        )

    def test_it_combines_with_the_session_filter(self):
        self._class()
        other = self.course(self.teacher, name="Course B")
        elsewhere = self.user(UserTypes.STUDENT, first="Elsewhere")
        self.enrol(elsewhere, other)
        self.assertEqual(
            self._names(
                self._get(
                    grade_trend="improving",
                    enrollments__course__session=str(self.course_a.session_id),
                )
            ),
            ["Up"],
        )

    def test_the_page_count_is_of_the_filtered_students(self):
        self._class()
        response = self._get(grade_trend="stable", page=1, page_size=10)
        self.assertEqual(response.data["count"], 1)

    # -- refused -----------------------------------------------------------

    def test_an_unknown_trend_is_a_400_naming_it(self):
        for bad in ("up", "improved", "declining,stable", "  "):
            with self.subTest(grade_trend=bad):
                response = self._get(grade_trend=bad)
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("grade_trend", response.data)
