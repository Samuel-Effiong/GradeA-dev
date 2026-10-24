"""
H-248: the hardest/easiest cards are the bottom and top 20% of the paper's
questions (the user's rule), on `teacher-admin/dashboard/assignments/<id>`.

  * Each list holds 20% of the questions on the paper, rounded up, at most
    LIST_LIMIT (5).
  * A paper of fewer than MINIMUM_QUESTIONS (3) questions names nothing.
  * A tie at the edge of a list is shown whole, even past 5.
  * The two lists never share a question.
  * `?question_limit=` can only shorten a list; ties still show whole.

Builds on H-240's helpers (dashboard/tests_h240_question_difficulty.py).

Run with:
    python manage.py test dashboard.tests_h248_twenty_percent_cards
"""

from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status

from dashboard.question_difficulty import (
    LIST_LIMIT,
    MINIMUM_QUESTIONS,
    MINIMUM_STUDENTS,
    STATUS_READY,
    STATUS_TOO_FEW_QUESTIONS,
    list_size_for_paper,
    question_difficulty,
)
from dashboard.tests_h240_question_difficulty import (
    LOCMEM,
    AnalyticsEndpointFixtures,
    class_of,
    numbers,
    paper,
)


def distinct(count):
    """A paper of `count` questions and a class scoring Q n at n%."""
    questions = range(1, count + 1)
    return paper(*questions), class_of({n: n for n in questions}, available=100)


class ListSizeTest(SimpleTestCase):
    def test_twenty_percent_of_the_paper_rounded_up_at_most_five(self):
        table = {
            3: 1,
            4: 1,
            5: 1,
            6: 2,
            10: 2,
            11: 3,
            15: 3,
            20: 4,
            21: 5,
            25: 5,
            26: 5,
            30: 5,
            50: 5,
        }
        for size, expected in table.items():
            with self.subTest(size=size):
                self.assertEqual(list_size_for_paper(size), expected)
                assignment, subs = distinct(size)
                result = question_difficulty(assignment, subs)
                self.assertEqual(result["status"], STATUS_READY)
                self.assertEqual(result["list_size"], expected)
                self.assertEqual(
                    numbers(result["hardest"]), list(range(1, expected + 1))
                )
                self.assertEqual(
                    numbers(result["easiest"]),
                    list(range(size, size - expected, -1)),
                )

    def test_under_three_questions_nothing_is_named(self):
        self.assertEqual(MINIMUM_QUESTIONS, 3)
        for size in (0, 1, 2):
            with self.subTest(size=size):
                assignment, subs = distinct(size)
                result = question_difficulty(assignment, subs)
                self.assertEqual(result["status"], STATUS_TOO_FEW_QUESTIONS)
                self.assertEqual(result["list_size"], 0)
                self.assertEqual(result["hardest"], [])
                self.assertEqual(result["easiest"], [])
                # The breakdown is still there for the teacher.
                self.assertEqual(len(result["breakdown"]), size)

    def test_the_twenty_percent_is_of_every_question_on_the_paper(self):
        # Ten questions, only five marked: 20% of ten is two per list.
        result = question_difficulty(
            paper(*range(1, 11)), class_of({1: 1, 2: 2, 3: 3, 4: 4, 5: 5})
        )
        self.assertEqual(result["list_size"], 2)
        self.assertEqual(numbers(result["hardest"]), [1, 2])
        self.assertEqual(numbers(result["easiest"]), [5, 4])

    def test_a_three_question_paper_with_two_marked_is_ranked(self):
        result = question_difficulty(paper(1, 2, 3), class_of({1: 2, 2: 8}))
        self.assertEqual(result["status"], STATUS_READY)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2])


class TiesTest(SimpleTestCase):
    def test_a_tie_for_hardest_is_shown_whole(self):
        # Five questions: one card each, but Q1 and Q2 tie for lowest.
        result = question_difficulty(
            paper(1, 2, 3, 4, 5), class_of({1: 2, 2: 2, 3: 5, 4: 6, 5: 9})
        )
        self.assertEqual(result["list_size"], 1)
        self.assertEqual(numbers(result["hardest"]), [1, 2])
        self.assertEqual(numbers(result["easiest"]), [5])

    def test_a_tie_for_easiest_is_shown_whole(self):
        result = question_difficulty(
            paper(1, 2, 3, 4, 5), class_of({1: 1, 2: 5, 3: 6, 4: 9, 5: 9})
        )
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [4, 5])

    def test_a_tie_at_the_edge_not_the_bottom_is_shown_whole(self):
        # Ten questions, two cards: Q2 is the second card and ties with Q3.
        marks = {n: 50 + n for n in range(1, 11)}
        marks.update({1: 10, 2: 20, 3: 20})
        result = question_difficulty(
            paper(*range(1, 11)), class_of(marks, available=100)
        )
        self.assertEqual(numbers(result["hardest"]), [1, 2, 3])

    def test_a_tie_goes_past_five(self):
        # Thirty questions; seven tie for lowest. All seven are hardest.
        marks = {n: 30 + n for n in range(1, 31)}
        marks.update({n: 5 for n in range(1, 8)})
        result = question_difficulty(
            paper(*range(1, 31)), class_of(marks, available=100)
        )
        self.assertEqual(result["list_size"], LIST_LIMIT)
        self.assertEqual(numbers(result["hardest"]), list(range(1, 8)))
        self.assertEqual(numbers(result["easiest"]), [30, 29, 28, 27, 26])

    def test_ties_within_a_list_follow_the_paper(self):
        result = question_difficulty(
            paper(2, 10, 11, 12, 13), class_of({2: 4, 10: 4, 11: 6, 12: 7, 13: 9})
        )
        self.assertEqual(numbers(result["hardest"]), [2, 10])


class NeverSharedTest(SimpleTestCase):
    def test_a_tie_for_hardest_does_not_reach_into_easiest(self):
        # 50, 50, 90: both 50s are hardest; only Q3 can be easy.
        result = question_difficulty(paper(1, 2, 3), class_of({1: 5, 2: 5, 3: 9}))
        self.assertEqual(numbers(result["hardest"]), [1, 2])
        self.assertEqual(numbers(result["easiest"]), [3])

    def test_a_tie_for_easiest_does_not_reach_into_hardest(self):
        result = question_difficulty(paper(1, 2, 3), class_of({1: 1, 2: 9, 3: 9}))
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2, 3])

    def test_a_wide_tie_for_hardest_leaves_the_rest_to_easiest(self):
        result = question_difficulty(
            paper(1, 2, 3, 4, 5), class_of({1: 5, 2: 5, 3: 5, 4: 5, 5: 9})
        )
        self.assertEqual(numbers(result["hardest"]), [1, 2, 3, 4])
        self.assertEqual(numbers(result["easiest"]), [5])

    def test_few_ranked_questions_on_a_long_paper_are_split_not_shared(self):
        # Ten questions, two cards each, but only two are marked: the
        # better one is not hardest, and neither is on both lists.
        result = question_difficulty(paper(*range(1, 11)), class_of({1: 2, 2: 8}))
        self.assertEqual(result["list_size"], 2)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2])

    def test_no_question_is_on_both_lists_at_any_size(self):
        for size in range(3, 31):
            with self.subTest(size=size):
                marks = {n: n // 3 for n in range(1, size + 1)}
                result = question_difficulty(
                    paper(*range(1, size + 1)), class_of(marks, available=100)
                )
                hard, easy = set(numbers(result["hardest"])), set(
                    numbers(result["easiest"])
                )
                self.assertFalse(hard & easy)


class QuestionLimitTest(SimpleTestCase):
    def test_the_limit_shortens_a_list(self):
        assignment, subs = distinct(20)
        result = question_difficulty(assignment, subs, limit=1)
        self.assertEqual(result["list_size"], 1)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [20])

    def test_the_limit_never_lengthens_a_list(self):
        assignment, subs = distinct(10)
        self.assertEqual(question_difficulty(assignment, subs, limit=4)["list_size"], 2)
        assignment, subs = distinct(50)
        self.assertEqual(
            question_difficulty(assignment, subs, limit=50)["list_size"], LIST_LIMIT
        )

    def test_a_tie_still_shows_whole_under_a_limit(self):
        assignment = paper(*range(1, 11))
        marks = {n: n for n in range(1, 11)}
        marks[2] = 1
        result = question_difficulty(assignment, class_of(marks), limit=1)
        self.assertEqual(numbers(result["hardest"]), [1, 2])


@override_settings(CACHES=LOCMEM)
class TwentyPercentAtTheEndpointTest(AnalyticsEndpointFixtures):
    def _paper_of(self, count):
        questions = [
            {"question_number": n, "question_text": f"Q{n}", "points": 10}
            for n in range(1, count + 1)
        ]
        self.assignment_obj.questions = questions
        self.assignment_obj.total_points = 10 * count
        self.assignment_obj.save()
        return questions

    def _url(self, **params):
        url = reverse(
            "teacher-admin-assignments",
            kwargs={"assignment_id": self.assignment_obj.id},
        )
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def _served(self, clear=True, **params):
        if clear:
            cache.clear()
        response = self.client.get(self._url(**params))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def test_the_four_question_paper_shows_one_of_each(self):
        self._class()  # Q1 90%, Q2 40%, Q3 60%, Q4 100%
        data = self._served()
        self.assertEqual(
            [q["question_number"] for q in data["hardest_questions"]], ["2"]
        )
        self.assertEqual(
            [q["question_number"] for q in data["easiest_questions"]], ["4"]
        )
        self.assertEqual(data["question_difficulty"]["list_size"], 1)

    def test_a_two_question_paper_says_why_nothing_is_named(self):
        self._paper_of(2)
        for _ in range(MINIMUM_STUDENTS):
            self._graded({1: 2, 2: 8})
        data = self._served()
        summary = data["question_difficulty"]
        self.assertEqual(summary["status"], STATUS_TOO_FEW_QUESTIONS)
        self.assertEqual(summary["list_size"], 0)
        self.assertEqual(data["hardest_questions"], [])
        self.assertEqual(len(data["question_breakdown"]), 2)

    def test_a_limit_past_five_is_the_same_answer_as_five(self):
        self._class()
        cache.clear()
        self._served(clear=False, question_limit=5)
        # 99 is served from five's cache entry: the key is the limit applied.
        with patch("dashboard.views.cache.set") as stored:
            self._served(clear=False, question_limit=99)
        stored.assert_not_called()
