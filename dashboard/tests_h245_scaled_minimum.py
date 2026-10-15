"""
H-245: the ranking minimum on `teacher-admin/dashboard/assignments/<id>`
scales with the class (the user's decision): half the students enrolled,
rounded up, never fewer than 3 nor more than 10. A fixed five left a
class of four with empty hardest/easiest cards forever, and ranked a class
of forty on five papers.

Run with:
    python manage.py test dashboard.tests_h245_scaled_minimum
"""

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse

from classrooms.models import EnrollmentStatusType
from dashboard.question_difficulty import (
    MINIMUM_CAP,
    MINIMUM_FLOOR,
    STATUS_NOT_ENOUGH_DATA,
    STATUS_READY,
    minimum_for_class,
    question_difficulty,
)
from dashboard.tests_h240_question_difficulty import (
    LOCMEM,
    AnalyticsEndpointFixtures,
    class_of,
    paper,
)


class MinimumForClassTest(SimpleTestCase):
    def test_half_the_class_rounded_up_within_three_and_ten(self):
        self.assertEqual((MINIMUM_FLOOR, MINIMUM_CAP), (3, 10))
        table = {
            None: 3,
            0: 3,
            1: 3,
            4: 3,
            6: 3,
            7: 4,
            8: 4,
            9: 5,
            10: 5,
            11: 6,
            19: 10,
            20: 10,
            40: 10,
            500: 10,
        }
        for size, minimum in table.items():
            with self.subTest(size=size):
                self.assertEqual(minimum_for_class(size), minimum)

    def test_a_nonsense_size_is_the_floor(self):
        self.assertEqual(minimum_for_class(-4), 3)

    def test_the_minimum_given_is_the_one_applied_and_reported(self):
        three = question_difficulty(
            paper(1, 2), class_of({1: 2, 2: 8}, students=3), minimum=3
        )
        self.assertEqual(three["status"], STATUS_READY)
        self.assertEqual(three["minimum_students"], 3)
        self.assertTrue(all(q["enough_data"] for q in three["breakdown"]))

        four = question_difficulty(
            paper(1, 2), class_of({1: 2, 2: 8}, students=3), minimum=4
        )
        self.assertEqual(four["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(four["minimum_students"], 4)
        self.assertFalse(any(q["enough_data"] for q in four["breakdown"]))


@override_settings(CACHES=LOCMEM)
class ClassSizeAtTheEndpointTest(AnalyticsEndpointFixtures):
    """The endpoint sizes the class from the course's enrollment."""

    def _difficulty(self):
        return self._get()["question_difficulty"]

    def _cached_difficulty(self):
        """The endpoint as a teacher reads it, cache and all."""
        response = self.client.get(
            reverse(
                "teacher-admin-assignments",
                kwargs={"assignment_id": self.assignment_obj.id},
            )
        )
        return response.data["question_difficulty"]

    def test_a_class_of_four_all_graded_is_ranked(self):
        self._class(students=4)
        summary = self._difficulty()
        self.assertEqual(summary["minimum_students"], 3)
        self.assertEqual(summary["status"], STATUS_READY)

    def test_a_class_of_forty_waits_for_ten_papers(self):
        self._absent(35)
        self._class(students=5)
        summary = self._difficulty()
        self.assertEqual(summary["minimum_students"], 10)
        self.assertEqual(summary["status"], STATUS_NOT_ENOUGH_DATA)

    def test_a_withdrawn_student_does_not_size_the_class(self):
        for _ in range(6):
            self._student(EnrollmentStatusType.WITHDRAWN)
        self._class(students=4)
        self.assertEqual(self._difficulty()["minimum_students"], 3)

    def test_enrolling_a_student_changes_the_minimum_at_once(self):
        cache.clear()
        self._class(students=5)
        self.assertEqual(self._cached_difficulty()["minimum_students"], 3)
        self._absent(5)
        # No cache clear: the enrollment write must invalidate the answer.
        self.assertEqual(self._cached_difficulty()["minimum_students"], 5)
