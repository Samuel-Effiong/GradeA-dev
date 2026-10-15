"""
H-241: the full per-question breakdown, the adjustable hardest/easiest
limit (default 3), and which students struggled with each question, on
`teacher-admin/dashboard/assignments/<id>`.

Builds on H-240's helpers (dashboard/tests_h240_question_difficulty.py):
hand-built submissions for the rules, production-shaped grading feedback
for the endpoint.

Run with:
    python manage.py test dashboard.tests_h241_question_breakdown
"""

from django.core.cache import cache
from django.db import connection
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.extraction_schemas import BLANK, ILLEGIBLE, NOT_FOUND_IN_DOCUMENT
from dashboard.question_difficulty import (
    MINIMUM_STUDENTS,
    STATUS_NO_DIFFERENCE,
    STATUS_NOT_ENOUGH_DATA,
    question_difficulty,
)
from dashboard.tests_h240_question_difficulty import (
    LOCMEM,
    QUESTIONS,
    class_of,
    evaluation,
    graded_feedback,
    numbers,
    paper,
    submission,
)
from dashboard.tests_teacher_all_students import Builder
from students.models import StudentSubmission
from users.models import UserTypes


def by_number(entries):
    return {entry["question_number"]: entry for entry in entries}


class BreakdownRulesTest(SimpleTestCase):
    # -- the breakdown -----------------------------------------------------

    def test_every_question_is_listed_hardest_first(self):
        result = question_difficulty(
            paper(1, 2, 3, 4, 5, 6), class_of({1: 8, 2: 3, 3: 9, 4: 1, 5: 5, 6: 7})
        )
        self.assertEqual(numbers(result["breakdown"]), [4, 2, 5, 6, 1, 3])
        self.assertEqual(
            [q["average_percentage"] for q in result["breakdown"]],
            [10.0, 30.0, 50.0, 70.0, 80.0, 90.0],
        )
        self.assertTrue(all(q["enough_data"] for q in result["breakdown"]))

    def test_the_cards_are_the_ends_of_the_breakdown(self):
        result = question_difficulty(
            paper(1, 2, 3, 4, 5, 6, 7, 8),
            class_of({n: n for n in range(1, 9)}),
        )
        self.assertEqual(result["hardest"], result["breakdown"][:3])
        self.assertEqual(result["easiest"], list(reversed(result["breakdown"][-3:])))

    def test_a_question_short_of_the_minimum_is_shown_marked_after_trusted_ones(self):
        # Q2's answers were lost on two papers: 3 students, 0%. It would
        # top the list; it is shown, but flagged and after the trusted ones.
        subs = []
        for i in range(MINIMUM_STUDENTS):
            lost = i < 2
            subs.append(
                submission(
                    evaluation(1, 4),
                    evaluation(
                        2,
                        0,
                        answer_status=NOT_FOUND_IN_DOCUMENT if lost else "ANSWERED",
                    ),
                    evaluation(3, 9),
                )
            )
        result = question_difficulty(paper(1, 2, 3), subs)
        self.assertEqual(numbers(result["breakdown"]), [1, 3, 2])
        q2 = result["breakdown"][-1]
        self.assertFalse(q2["enough_data"])
        self.assertEqual(q2["students_counted"], 3)
        self.assertEqual(q2["average_percentage"], 0.0)
        self.assertNotIn(2, numbers(result["hardest"]) + numbers(result["easiest"]))

    def test_a_question_nobody_has_a_mark_on_comes_last_in_paper_order(self):
        result = question_difficulty(paper(1, 2, 3, 4), class_of({3: 2, 1: 8}))
        self.assertEqual(numbers(result["breakdown"]), [3, 1, 2, 4])
        for entry in result["breakdown"][2:]:
            self.assertEqual(entry["average_percentage"], 0.0)
            self.assertEqual(entry["students_counted"], 0)
            self.assertFalse(entry["enough_data"])

    def test_an_unmarked_question_comes_after_a_thinly_marked_one(self):
        # Q1 has 2 students at 60%; Q2 has no marks. Neither is trusted,
        # but "no marks" must not read as 0% and jump ahead of Q1.
        result = question_difficulty(paper(1, 2), class_of({1: 6}, students=2))
        self.assertEqual(numbers(result["breakdown"]), [1, 2])
        self.assertEqual(result["breakdown"][0]["average_percentage"], 60.0)
        self.assertEqual(result["breakdown"][1]["average_percentage"], 0.0)
        self.assertEqual(result["breakdown"][1]["students_counted"], 0)

    def test_the_breakdown_is_there_when_nothing_is_ranked(self):
        few = question_difficulty(paper(1, 2), class_of({1: 1, 2: 9}, students=2))
        self.assertEqual(few["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(numbers(few["breakdown"]), [1, 2])

        equal = question_difficulty(paper(1, 2), class_of({1: 5, 2: 5}))
        self.assertEqual(equal["status"], STATUS_NO_DIFFERENCE)
        self.assertEqual(numbers(equal["breakdown"]), [1, 2])

    def test_no_internal_field_leaks_into_an_entry(self):
        result = question_difficulty(paper(1, 2), class_of({1: 2, 2: 8}))
        for entry in result["breakdown"]:
            self.assertNotIn("position", entry)

    # -- who struggled -----------------------------------------------------

    def test_students_under_half_the_marks_are_named_lowest_first(self):
        subs = [
            submission(evaluation(1, 9), name="Ada"),
            submission(evaluation(1, 5), name="Ben"),  # exactly half: not struggling
            submission(evaluation(1, 4), name="Cy"),
            submission(evaluation(1, 1), name="Dee"),
            submission(evaluation(1, 4), name="Abe"),
        ]
        result = question_difficulty(paper(1), subs)
        struggling = result["breakdown"][0]["struggling_students"]
        self.assertEqual(
            [(s["full_name"], s["percentage"]) for s in struggling],
            [("Dee", 10.0), ("Abe", 40.0), ("Cy", 40.0)],
        )
        dee = struggling[0]
        self.assertEqual(dee["score_awarded"], 1.0)
        self.assertEqual(dee["max_points"], 10.0)
        self.assertFalse(dee["left_blank"])
        self.assertEqual(dee["student_id"], subs[3].student.id)

    def test_a_blank_answer_is_named_and_flagged(self):
        subs = [
            submission(
                evaluation(1, 0, answer_status=BLANK, level_achieved="not_attempted"),
                name="Eve",
            )
        ]
        entry = question_difficulty(paper(1), subs)["breakdown"][0]
        self.assertEqual(entry["struggling_students"][0]["full_name"], "Eve")
        self.assertTrue(entry["struggling_students"][0]["left_blank"])

    def test_an_unread_answer_names_the_student_for_checking_not_reteaching(self):
        subs = [
            submission(evaluation(1, 0, answer_status=ILLEGIBLE), name="Zed"),
            submission(
                evaluation(1, 0, answer_status=NOT_FOUND_IN_DOCUMENT), name="Al"
            ),
            submission(evaluation(1, 2), name="Bo"),
        ]
        entry = question_difficulty(paper(1), subs)["breakdown"][0]
        self.assertEqual(
            [s["full_name"] for s in entry["unread_students"]], ["Al", "Zed"]
        )
        self.assertEqual(entry["not_read"], 2)
        self.assertEqual([s["full_name"] for s in entry["struggling_students"]], ["Bo"])

    def test_a_hand_changed_paper_names_nobody(self):
        subs = [submission(evaluation(1, 0), score=10, name="Overridden")]
        entry = question_difficulty(paper(1), subs)["breakdown"][0]
        self.assertEqual(entry["struggling_students"], [])
        self.assertEqual(entry["unread_students"], [])

    def test_each_question_names_its_own_students(self):
        subs = [
            submission(evaluation(1, 1), evaluation(2, 9), name="OnlyQ1"),
            submission(evaluation(1, 9), evaluation(2, 1), name="OnlyQ2"),
        ]
        result = by_number(question_difficulty(paper(1, 2), subs)["breakdown"])
        self.assertEqual(
            [s["full_name"] for s in result[1]["struggling_students"]], ["OnlyQ1"]
        )
        self.assertEqual(
            [s["full_name"] for s in result[2]["struggling_students"]], ["OnlyQ2"]
        )


@override_settings(CACHES=LOCMEM)
class AssignmentAnalyticsBreakdownTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.term = self.session(self.teacher)
        self.course_obj = self.course(self.teacher, self.term)
        self.assignment_obj = self.assignment(self.course_obj)
        self.assignment_obj.questions = QUESTIONS
        self.assignment_obj.total_points = 40
        self.assignment_obj.save()
        self.client.force_authenticate(self.teacher)

    def _url(self, **params):
        url = reverse(
            "teacher-admin-assignments",
            kwargs={"assignment_id": self.assignment_obj.id},
        )
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def _get(self, clear=True, **params):
        if clear:
            cache.clear()
        response = self.client.get(self._url(**params))
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def _graded(self, marks, statuses=None, first="Test", last="Student"):
        student = self.user(UserTypes.STUDENT, first=first, last=last)
        self.enrol(student, self.course_obj)
        grading = graded_feedback(marks, statuses)
        summary = grading["grading_summary"]
        return StudentSubmission.objects.create(
            assignment=self.assignment_obj,
            student=student,
            answers=[{"question_number": n, "answer_html": "x"} for n in marks],
            score=round(float(summary["total_score"]), 2),
            ai_score=round(float(summary["total_score"]), 2),
            max_points=int(float(summary["max_total_points"])),
            score_percentage=round(float(summary["percentage"]), 2),
            feedback=grading,
            graded_at=timezone.now(),
            ai_graded_at=timezone.now(),
            is_published=True,
        )

    def _class(self, students=MINIMUM_STUDENTS, prefix="Pupil"):
        # Q1 90%, Q2 40%, Q3 60%, Q4 100%.
        return [
            self._graded({1: 9, 2: 8, 3: 3, 4: 5}, first=f"{prefix}{i}")
            for i in range(students)
        ]

    def test_the_breakdown_is_served_hardest_first(self):
        self._class()
        data = self._get()
        self.assertEqual(
            [
                (q["question_number"], q["average_percentage"])
                for q in data["question_breakdown"]
            ],
            [("2", 40.0), ("3", 60.0), ("1", 90.0), ("4", 100.0)],
        )
        self.assertTrue(all(q["enough_data"] for q in data["question_breakdown"]))

    def test_struggling_and_unread_students_are_named(self):
        self._class()
        self._graded(
            {1: 9, 2: 0, 3: 3, 4: 5}, statuses={2: BLANK}, first="Nia", last="Blank"
        )
        self._graded(
            {1: 9, 2: 8, 3: 3, 4: 0},
            statuses={4: NOT_FOUND_IN_DOCUMENT},
            first="Uri",
            last="Lost",
        )
        data = self._get()
        q2 = by_number(data["question_breakdown"])["2"]
        # Everyone scored 8/20 (40%) on Q2; Nia left it blank (0%).
        self.assertEqual(q2["struggling_students"][0]["full_name"], "Nia Blank")
        self.assertTrue(q2["struggling_students"][0]["left_blank"])
        self.assertEqual(len(q2["struggling_students"]), MINIMUM_STUDENTS + 2)
        q4 = by_number(data["question_breakdown"])["4"]
        self.assertEqual([s["full_name"] for s in q4["unread_students"]], ["Uri Lost"])
        self.assertEqual(q4["struggling_students"], [])
        student_id = q4["unread_students"][0]["student_id"]
        self.assertEqual(
            str(StudentSubmission.objects.get(student__first_name="Uri").student_id),
            str(student_id),
        )

    def test_the_default_card_limit_is_three(self):
        # Eight questions so half the paper (4) is not the binding cap.
        questions = [
            {"question_number": n, "question_text": f"Q{n}", "points": 10}
            for n in range(1, 9)
        ]
        self.assignment_obj.questions = questions
        self.assignment_obj.save()
        for i in range(MINIMUM_STUDENTS):
            self._graded_on(questions, {n: n for n in range(1, 9)}, f"P{i}")
        data = self._get()
        self.assertEqual(len(data["hardest_questions"]), 3)
        self.assertEqual(len(data["easiest_questions"]), 3)
        limited = self._get(question_limit=1)
        self.assertEqual(
            [q["question_number"] for q in limited["hardest_questions"]], ["1"]
        )
        self.assertEqual(
            [q["question_number"] for q in limited["easiest_questions"]], ["8"]
        )
        self.assertEqual(len(limited["question_breakdown"]), 8)

    def _graded_on(self, questions, marks, first):
        from ai_processor.services import ai_processor

        student = self.user(UserTypes.STUDENT, first=first)
        self.enrol(student, self.course_obj)
        finalized = ai_processor._finalize_grading_result(
            [{"question_number": n, "score_awarded": a} for n, a in marks.items()],
            questions,
        )
        return StudentSubmission.objects.create(
            assignment=self.assignment_obj,
            student=student,
            answers=[],
            score=finalized["total_score"],
            max_points=finalized["max_total_points"],
            score_percentage=finalized["percentage"],
            feedback={"question_evaluations": finalized["question_evaluations"]},
            graded_at=timezone.now(),
            is_published=True,
        )

    def test_each_limit_has_its_own_cached_answer(self):
        self._class()
        cache.clear()
        first = self._get(clear=False, question_limit=1)
        second = self._get(clear=False)
        self.assertEqual(len(first["hardest_questions"]), 1)
        self.assertEqual(len(second["hardest_questions"]), 2)

    def test_a_bad_limit_is_a_400_naming_it(self):
        for bad in ("abc", "0", "-2"):
            response = self.client.get(self._url(question_limit=bad))
            self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
            self.assertIn("question_limit", response.data["detail"])

    def test_naming_students_costs_no_query_per_student(self):
        def queries_for(students):
            StudentSubmission.objects.filter(assignment=self.assignment_obj).delete()
            self._class(students=students, prefix=f"Batch{students}x")
            cache.clear()
            count = []

            def counter(execute, sql, params, many, context):
                count.append(sql)
                return execute(sql, params, many, context)

            with connection.execute_wrapper(counter):
                self._get(clear=False)
            return len(count)

        self.assertEqual(
            queries_for(MINIMUM_STUDENTS), queries_for(3 * MINIMUM_STUDENTS)
        )
