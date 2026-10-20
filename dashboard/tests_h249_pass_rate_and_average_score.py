"""
H-249: each question also says its class average in marks
(`average_score`, out of `points`) and how many students passed it
(`passed_count`, `pass_rate`): at least half its marks, the same line as
`struggling_students`, so the two always add up to `students_counted`.

Builds on H-240's helpers (dashboard/tests_h240_question_difficulty.py).

Run with:
    python manage.py test dashboard.tests_h249_pass_rate_and_average_score
"""

from django.test import SimpleTestCase, override_settings

from ai_processor.extraction_schemas import BLANK, ILLEGIBLE
from dashboard.question_difficulty import question_difficulty
from dashboard.tests_h240_question_difficulty import (
    LOCMEM,
    AnalyticsEndpointFixtures,
    evaluation,
    paper,
    submission,
)


def one_question(*marks, points=10, available=10, statuses=None):
    """Q1's entry, one submission per mark (out of `available`)."""
    statuses = statuses or {}
    subs = [
        submission(evaluation(1, mark, available, statuses.get(i, "ANSWERED")))
        for i, mark in enumerate(marks)
    ]
    return question_difficulty(paper(1, points=points), subs)["breakdown"][0]


class AverageScoreTest(SimpleTestCase):
    def test_the_average_in_marks_out_of_the_questions_points(self):
        entry = one_question(9, 6, 3, 6)
        self.assertEqual(entry["average_percentage"], 60.0)
        self.assertEqual(entry["average_score"], 6.0)

    def test_it_is_out_of_the_papers_points(self):
        entry = one_question(12, 14, points=20, available=20)
        self.assertEqual(entry["average_score"], 13.0)

    def test_it_is_rounded_to_two_places(self):
        entry = one_question(1, 1, 0)
        self.assertEqual(entry["average_score"], 0.67)

    def test_a_mark_over_the_question_counts_as_full_marks(self):
        self.assertEqual(one_question(15, 5)["average_score"], 7.5)

    def test_null_when_nobody_has_a_mark(self):
        entry = question_difficulty(paper(1), [])["breakdown"][0]
        self.assertIsNone(entry["average_score"])

    def test_null_when_the_question_has_no_usable_points(self):
        for points in (None, 0, "ten"):
            with self.subTest(points=points):
                self.assertIsNone(one_question(5, 5, points=points)["average_score"])

    def test_points_given_as_text_are_read(self):
        self.assertEqual(one_question(5, 5, points="10")["average_score"], 5.0)


class PassRateTest(SimpleTestCase):
    def test_half_the_marks_is_a_pass_just_under_is_not(self):
        entry = one_question(5, 4.99, 10, 0)
        self.assertEqual(entry["passed_count"], 2)
        self.assertEqual(entry["pass_rate"], 50.0)

    def test_the_pass_rate_is_of_the_students_counted(self):
        entry = one_question(10, 4, 4, 4, 4, 4, 4)
        self.assertEqual(entry["students_counted"], 7)
        self.assertEqual(entry["passed_count"], 1)
        self.assertEqual(entry["pass_rate"], 14.29)

    def test_a_blank_answer_is_not_a_pass(self):
        entry = one_question(10, 0, statuses={1: BLANK})
        self.assertEqual(entry["passed_count"], 1)
        self.assertEqual(entry["pass_rate"], 50.0)

    def test_an_unread_answer_is_left_out_of_both_counts(self):
        entry = one_question(10, 0, 0, statuses={1: ILLEGIBLE, 2: ILLEGIBLE})
        self.assertEqual(entry["students_counted"], 1)
        self.assertEqual(entry["passed_count"], 1)
        self.assertEqual(entry["pass_rate"], 100.0)

    def test_passed_and_struggling_add_up_to_the_students_counted(self):
        entry = one_question(10, 7, 5, 4.5, 2, 0)
        self.assertEqual(
            entry["passed_count"] + len(entry["struggling_students"]),
            entry["students_counted"],
        )

    def test_nobody_counted_is_zero_passed_and_no_rate(self):
        entry = question_difficulty(paper(1), [])["breakdown"][0]
        self.assertEqual(entry["passed_count"], 0)
        self.assertIsNone(entry["pass_rate"])

    def test_a_hand_changed_paper_is_left_out(self):
        changed = submission(evaluation(1, 0), score=10)
        result = question_difficulty(paper(1), [submission(evaluation(1, 10)), changed])
        entry = result["breakdown"][0]
        self.assertEqual(entry["passed_count"], 1)
        self.assertEqual(entry["pass_rate"], 100.0)


class OnTheCardsTest(SimpleTestCase):
    def test_the_cards_carry_both(self):
        marks = {1: 2, 2: 8, 3: 5}
        subs = [
            submission(*[evaluation(n, m) for n, m in marks.items()]) for _ in range(5)
        ]
        result = question_difficulty(paper(1, 2, 3), subs)
        hardest, easiest = result["hardest"][0], result["easiest"][0]
        self.assertEqual((hardest["average_score"], hardest["pass_rate"]), (2.0, 0.0))
        self.assertEqual((easiest["average_score"], easiest["pass_rate"]), (8.0, 100.0))


@override_settings(CACHES=LOCMEM)
class ServedTest(AnalyticsEndpointFixtures):
    def test_every_question_is_served_with_both(self):
        self._class()  # Q1 9/10, Q2 8/20, Q3 3/5, Q4 5/5
        self._graded({1: 9, 2: 0, 3: 3, 4: 5}, statuses={2: BLANK})
        by_number = {q["question_number"]: q for q in self._get()["question_breakdown"]}
        q2 = by_number["2"]
        # Five at 8/20 (40%) and one blank: nobody reached half.
        self.assertEqual(q2["average_score"], 6.67)
        self.assertEqual(q2["passed_count"], 0)
        self.assertEqual(q2["pass_rate"], 0.0)
        q4 = by_number["4"]
        self.assertEqual(q4["average_score"], 5.0)
        self.assertEqual(q4["passed_count"], 6)
        self.assertEqual(q4["pass_rate"], 100.0)
        for entry in by_number.values():
            self.assertIn("average_score", entry)
            self.assertIn("passed_count", entry)
            self.assertIn("pass_rate", entry)
