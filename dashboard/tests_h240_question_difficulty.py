"""
H-240: hardest and easiest questions on the teacher's assignment analytics
(`teacher-admin/dashboard/assignments/<id>`), dashboard/question_difficulty.py.

Two layers. QuestionDifficultyRulesTest pins each rule on hand-built
submissions. AssignmentAnalyticsQuestionDifficultyTest goes through the
endpoint with feedback produced the way production produces it - the
grader's own _finalize_grading_result and _stamp_answer_provenance, and a
teacher's override through the real update-grade route - so the rules are
proven against the shapes they will actually meet.

Run with:
    python manage.py test dashboard.tests_h240_question_difficulty
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from ai_processor.extraction_schemas import (
    ANSWERED,
    BLANK,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)
from ai_processor.services import ai_processor
from billing.models import CreditBucket, CreditBucketType, CreditWallet
from classrooms.models import EnrollmentStatusType
from dashboard.question_difficulty import (
    MINIMUM_STUDENTS,
    STATUS_NO_DIFFERENCE,
    STATUS_NOT_ENOUGH_DATA,
    STATUS_READY,
    question_difficulty,
)
from dashboard.tests_teacher_all_students import Builder
from students.models import StudentSubmission
from users.models import UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def paper(*numbers, points=10, texts=None):
    texts = texts or {}
    return SimpleNamespace(
        questions=[
            {
                "question_number": n,
                "question_text": texts.get(n, f"Question {n}"),
                "question_type": "SHORT-ANSWER",
                "points": points,
            }
            for n in numbers
        ]
    )


def evaluation(number, awarded, available=10, answer_status=ANSWERED, **extra):
    entry = {
        "question_number": number,
        "score_awarded": awarded,
        "max_points": available,
        "answer_status": answer_status,
    }
    entry.update(extra)
    return entry


def submission(*evaluations, score=None):
    """A graded submission whose score is its marks' sum unless given."""
    if score is None:
        score = sum(e["score_awarded"] for e in evaluations)
    return SimpleNamespace(
        score=Decimal(str(score)),
        feedback={"question_evaluations": list(evaluations)},
    )


def class_of(marks_by_question, students=MINIMUM_STUDENTS, available=10):
    """`students` identical submissions, one mark per question."""
    return [
        submission(
            *[
                evaluation(n, awarded, available)
                for n, awarded in marks_by_question.items()
            ]
        )
        for _ in range(students)
    ]


def numbers(entries):
    return [entry["question_number"] for entry in entries]


class QuestionDifficultyRulesTest(SimpleTestCase):
    # -- the measure -------------------------------------------------------

    def test_difficulty_is_the_share_of_each_questions_own_marks(self):
        # Q1: 12 of 20 (60%). Q2: 2 of 2 (100%). Raw marks would call Q1
        # the easier one; as a share of its own marks it is the harder.
        subs = [
            submission(evaluation(1, 12, 20), evaluation(2, 2, 2))
            for _ in range(MINIMUM_STUDENTS)
        ]
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(result["hardest"][0]["average_percentage"], 60.0)
        self.assertEqual(numbers(result["easiest"]), [2])
        self.assertEqual(result["easiest"][0]["average_percentage"], 100.0)

    def test_the_average_is_over_students(self):
        subs = class_of({1: 10, 2: 10}, students=3) + class_of(
            {1: 0, 2: 10}, students=2
        )
        result = question_difficulty(paper(1, 2), subs)
        # Q1: three 100% and two 0% -> 60%.
        self.assertEqual(result["hardest"][0]["average_percentage"], 60.0)
        self.assertEqual(result["hardest"][0]["students_counted"], 5)
        self.assertEqual(result["students_analysed"], 5)

    def test_marks_are_clamped_to_the_question(self):
        subs = class_of({1: 15, 2: -5})
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["easiest"][0]["average_percentage"], 100.0)
        self.assertEqual(result["hardest"][0]["average_percentage"], 0.0)

    # -- unreadable answers ------------------------------------------------

    def test_an_answer_the_ai_could_not_find_is_not_counted_as_hard(self):
        # Every student actually did well on Q2, but for three of them the
        # scan lost the answer and it was scored 0. Counted, Q2 would be
        # the hardest question; it is a reading failure, not difficulty.
        subs = []
        for i in range(MINIMUM_STUDENTS + 3):
            lost = i < 3
            subs.append(
                submission(
                    evaluation(1, 5),
                    evaluation(
                        2,
                        0 if lost else 9,
                        answer_status=NOT_FOUND_IN_DOCUMENT if lost else ANSWERED,
                    ),
                    evaluation(3, 7),
                )
            )
        result = question_difficulty(paper(1, 2, 3), subs)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2])
        q2 = result["easiest"][0]
        self.assertEqual(q2["average_percentage"], 90.0)
        self.assertEqual(q2["students_counted"], MINIMUM_STUDENTS)
        self.assertEqual(q2["not_read"], 3)

    def test_an_illegible_answer_is_not_counted_either(self):
        subs = class_of({1: 9, 2: 3})
        subs.append(
            submission(
                evaluation(1, 0, answer_status=ILLEGIBLE),
                evaluation(2, 3),
            )
        )
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["easiest"][0]["average_percentage"], 90.0)
        self.assertEqual(result["easiest"][0]["not_read"], 1)

    # -- blank answers -----------------------------------------------------

    def test_a_blank_answer_counts_and_is_reported(self):
        subs = class_of({1: 10, 2: 10}, students=3)
        subs += [
            submission(
                evaluation(1, 10),
                evaluation(2, 0, answer_status=BLANK, level_achieved="not_attempted"),
            )
            for _ in range(2)
        ]
        result = question_difficulty(paper(1, 2), subs)
        q2 = result["hardest"][0]
        self.assertEqual(q2["question_number"], 2)
        self.assertEqual(q2["average_percentage"], 60.0)
        self.assertEqual(q2["not_attempted"], 2)
        self.assertEqual(q2["not_read"], 0)

    def test_a_result_from_before_answer_status_uses_the_level(self):
        legacy = {"question_number": 2, "score_awarded": 0, "max_points": 10}
        legacy_blank = dict(legacy, level_achieved="not_attempted")
        legacy_wrong = dict(legacy, level_achieved="incorrect")
        subs = [submission(evaluation(1, 10), legacy_blank) for _ in range(2)]
        subs += [submission(evaluation(1, 10), legacy_wrong) for _ in range(3)]
        result = question_difficulty(paper(1, 2), subs)
        q2 = result["hardest"][0]
        self.assertEqual(q2["students_counted"], 5)
        self.assertEqual(q2["not_attempted"], 2)

    # -- grades changed by hand --------------------------------------------

    def test_a_submission_whose_marks_no_longer_match_its_score_is_left_out(self):
        # The AI gave Q1 0 on these two; the teacher raised the total to 10
        # through update-grade, which rewrites only the total. Their Q1 mark
        # is no longer the grade the student holds.
        subs = class_of({1: 8, 2: 2})
        subs += [
            submission(evaluation(1, 0), evaluation(2, 2), score=10) for _ in range(2)
        ]
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["excluded_manual_grade_change"], 2)
        self.assertEqual(result["students_analysed"], MINIMUM_STUDENTS)
        self.assertEqual(result["easiest"][0]["average_percentage"], 80.0)

    def test_a_rounding_difference_is_not_a_grade_change(self):
        subs = [
            submission(evaluation(1, 3.333), evaluation(2, 6.667), score=10.0)
            for _ in range(MINIMUM_STUDENTS)
        ]
        subs.append(submission(evaluation(1, 3.334), evaluation(2, 6.67), score=10.0))
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["excluded_manual_grade_change"], 0)
        self.assertEqual(result["students_analysed"], MINIMUM_STUDENTS + 1)

    # -- matching questions ------------------------------------------------

    def test_a_question_not_on_the_paper_is_ignored(self):
        subs = [
            submission(evaluation(1, 2), evaluation(2, 8), evaluation(99, 0))
            for _ in range(MINIMUM_STUDENTS)
        ]
        result = question_difficulty(paper(1, 2), subs)
        self.assertNotIn(99, numbers(result["hardest"]) + numbers(result["easiest"]))
        self.assertEqual(numbers(result["hardest"]), [1])

    def test_string_and_integer_question_numbers_are_one_question(self):
        subs = class_of({1: 2, 2: 8}, students=3)
        subs += [submission(evaluation("1", 2), evaluation(" 2 ", 8)) for _ in range(2)]
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["hardest"][0]["students_counted"], 5)
        self.assertEqual(result["easiest"][0]["students_counted"], 5)

    def test_a_question_repeated_in_one_submission_counts_once(self):
        subs = [
            submission(evaluation(1, 0), evaluation(1, 10), evaluation(2, 5), score=15)
            for _ in range(MINIMUM_STUDENTS)
        ]
        result = question_difficulty(paper(1, 2), subs)
        q1 = result["hardest"][0]
        self.assertEqual(q1["question_number"], 1)
        self.assertEqual(q1["students_counted"], MINIMUM_STUDENTS)
        self.assertEqual(q1["average_percentage"], 0.0)

    def test_a_zero_mark_question_is_skipped(self):
        subs = [
            submission(
                evaluation(1, 0, available=0), evaluation(2, 2), evaluation(3, 9)
            )
            for _ in range(MINIMUM_STUDENTS)
        ]
        result = question_difficulty(paper(1, 2, 3), subs)
        self.assertEqual(numbers(result["hardest"]), [2])
        self.assertEqual(numbers(result["easiest"]), [3])

    def test_submissions_without_grading_feedback_are_skipped(self):
        subs = class_of({1: 2, 2: 8})
        subs += [
            SimpleNamespace(score=Decimal("0"), feedback=None),
            SimpleNamespace(score=Decimal("0"), feedback={}),
            SimpleNamespace(score=Decimal("0"), feedback={"question_evaluations": "x"}),
        ]
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["students_analysed"], MINIMUM_STUDENTS)
        self.assertEqual(result["excluded_manual_grade_change"], 0)

    # -- when nothing is named ---------------------------------------------

    def test_below_the_minimum_nothing_is_ranked(self):
        subs = class_of({1: 1, 2: 9}, students=MINIMUM_STUDENTS - 1)
        result = question_difficulty(paper(1, 2), subs)
        self.assertEqual(result["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(result["hardest"], [])
        self.assertEqual(result["easiest"], [])
        self.assertEqual(result["students_analysed"], MINIMUM_STUDENTS - 1)

    def test_at_the_minimum_questions_are_ranked(self):
        result = question_difficulty(paper(1, 2), class_of({1: 1, 2: 9}))
        self.assertEqual(result["status"], STATUS_READY)

    def test_a_question_short_of_the_minimum_is_not_ranked(self):
        # Q3's answers were lost on two papers, leaving it 3 students.
        subs = []
        for i in range(MINIMUM_STUDENTS):
            lost = i < 2
            subs.append(
                submission(
                    evaluation(1, 5),
                    evaluation(2, 9),
                    evaluation(
                        3,
                        0 if lost else 1,
                        answer_status=NOT_FOUND_IN_DOCUMENT if lost else ANSWERED,
                    ),
                )
            )
        result = question_difficulty(paper(1, 2, 3), subs)
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2])

    def test_a_single_question_is_neither_hardest_nor_easiest(self):
        result = question_difficulty(paper(1), class_of({1: 5}))
        self.assertEqual(result["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(result["hardest"], [])

    def test_all_questions_equal_names_none(self):
        result = question_difficulty(paper(1, 2, 3), class_of({1: 10, 2: 10, 3: 10}))
        self.assertEqual(result["status"], STATUS_NO_DIFFERENCE)
        self.assertEqual(result["hardest"], [])
        self.assertEqual(result["easiest"], [])

    def test_no_submissions(self):
        result = question_difficulty(paper(1, 2), [])
        self.assertEqual(result["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(result["students_analysed"], 0)

    def test_an_assignment_without_questions(self):
        result = question_difficulty(SimpleNamespace(questions=None), class_of({1: 1}))
        self.assertEqual(result["status"], STATUS_NOT_ENOUGH_DATA)

    # -- the two lists -----------------------------------------------------

    def test_at_most_two_of_each(self):
        marks = {n: n for n in range(1, 9)}
        result = question_difficulty(paper(*range(1, 9)), class_of(marks))
        self.assertEqual(numbers(result["hardest"]), [1, 2])
        self.assertEqual(numbers(result["easiest"]), [8, 7])

    def test_two_questions_give_one_each(self):
        result = question_difficulty(paper(1, 2), class_of({1: 3, 2: 7}))
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2])

    def test_three_questions_leave_the_middle_one_out(self):
        result = question_difficulty(paper(1, 2, 3), class_of({1: 3, 2: 5, 3: 7}))
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [3])

    def test_a_question_tied_with_a_hardest_one_is_not_easiest(self):
        # 10%, 50%, 50%, 90%: Q2 is named hardest; Q3 has the very same
        # average and must not be called easy.
        result = question_difficulty(
            paper(1, 2, 3, 4), class_of({1: 1, 2: 5, 3: 5, 4: 9})
        )
        self.assertEqual(numbers(result["hardest"]), [1, 2])
        self.assertEqual(numbers(result["easiest"]), [4])

    def test_a_question_tied_with_the_best_is_not_hardest(self):
        # 50%, 100%, 100%, 100%: only Q1 did worse than anything.
        result = question_difficulty(
            paper(1, 2, 3, 4), class_of({1: 5, 2: 10, 3: 10, 4: 10})
        )
        self.assertEqual(numbers(result["hardest"]), [1])
        self.assertEqual(numbers(result["easiest"]), [2, 3])

    def test_ties_follow_the_paper_not_the_number_as_text(self):
        # As text "10" sorts before "2"; on the paper Q2 comes first.
        result = question_difficulty(
            paper(2, 10, 11, 12), class_of({2: 4, 10: 4, 11: 9, 12: 9})
        )
        self.assertEqual(numbers(result["hardest"]), [2, 10])
        self.assertEqual(numbers(result["easiest"]), [11, 12])

    # -- what each entry says ----------------------------------------------

    def test_an_entry_carries_the_papers_own_question_as_plain_text(self):
        texts = {
            1: "<p>Define&nbsp;<b>photosynthesis</b>\n  &amp; give\tan example.</p>"
        }
        result = question_difficulty(paper(1, 2, texts=texts), class_of({1: 2, 2: 8}))
        entry = result["hardest"][0]
        self.assertEqual(
            entry["question_text"], "Define photosynthesis & give an example."
        )
        self.assertEqual(entry["question_type"], "SHORT-ANSWER")
        self.assertEqual(entry["points"], 10)
        self.assertEqual(
            set(entry),
            {
                "question_number",
                "question_text",
                "question_type",
                "points",
                "average_percentage",
                "students_counted",
                "not_attempted",
                "not_read",
            },
        )


# ── Through the endpoint, with production-shaped feedback ───────────────

QUESTIONS = [
    {
        "question_number": 1,
        "question_text": "<p>Name the capital of <b>France</b>.</p>",
        "question_type": "SHORT-ANSWER",
        "points": 10,
    },
    {
        "question_number": 2,
        "question_text": "<p>Explain the causes of the First World War.</p>",
        "question_type": "ESSAY",
        "points": 20,
    },
    {
        "question_number": 3,
        "question_text": "<p>Date the Treaty of Versailles.</p>",
        "question_type": "SHORT-ANSWER",
        "points": 5,
    },
    {
        "question_number": 4,
        "question_text": "<p>Who was Otto von Bismarck?</p>",
        "question_type": "SHORT-ANSWER",
        "points": 5,
    },
]


def graded_feedback(marks, statuses=None):
    """The grading result exactly as the pipeline saves it as `feedback`:
    corrected by _finalize_grading_result, then stamped by
    _stamp_answer_provenance."""
    statuses = statuses or {}
    evaluations = [
        {
            "question_number": number,
            "score_awarded": awarded,
            "level_achieved": (
                "not_attempted"
                if statuses.get(number) in (BLANK, NOT_FOUND_IN_DOCUMENT)
                else "fair"
            ),
            "level_decision": "clear",
        }
        for number, awarded in marks.items()
    ]
    finalized = ai_processor._finalize_grading_result(evaluations, QUESTIONS)
    grading = {
        "question_evaluations": finalized["question_evaluations"],
        "grading_summary": {
            "total_score": finalized["total_score"],
            "max_total_points": finalized["max_total_points"],
            "percentage": finalized["percentage"],
        },
        "grading_confidence": 90,
    }
    answers = [
        {
            "question_number": number,
            "answer_html": (
                ""
                if statuses.get(number, ANSWERED) in (BLANK, NOT_FOUND_IN_DOCUMENT)
                else "<p>answer</p>"
            ),
            "answer_status": statuses.get(number, ANSWERED),
        }
        for number in marks
    ]
    return ai_processor._stamp_answer_provenance(grading, QUESTIONS, answers)


@override_settings(CACHES=LOCMEM)
class AssignmentAnalyticsQuestionDifficultyTest(Builder, APITestCase):
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

    def _get(self):
        cache.clear()
        self.client.force_authenticate(self.teacher)
        response = self.client.get(
            reverse(
                "teacher-admin-assignments",
                kwargs={"assignment_id": self.assignment_obj.id},
            )
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data

    def _student(self, enrollment_status=None):
        student = self.user(UserTypes.STUDENT)
        self.enrol(student, self.course_obj, status_=enrollment_status)
        return student

    def _graded(self, marks, statuses=None, enrollment_status=None):
        """Saved with the fields the grading pipeline sets
        (students/services.py, the persist step)."""
        grading = graded_feedback(marks, statuses)
        summary = grading["grading_summary"]
        return StudentSubmission.objects.create(
            assignment=self.assignment_obj,
            student=self._student(enrollment_status),
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

    def _class(self, students=MINIMUM_STUDENTS):
        # Q1 90%, Q2 40%, Q3 60%, Q4 100% for every student.
        return [self._graded({1: 9, 2: 8, 3: 3, 4: 5}) for _ in range(students)]

    def test_the_hardest_and_easiest_questions_are_served(self):
        self._class()
        data = self._get()

        self.assertEqual(data["question_difficulty"]["status"], STATUS_READY)
        self.assertEqual(data["question_difficulty"]["students_analysed"], 5)
        self.assertEqual(
            [
                (q["question_number"], q["average_percentage"])
                for q in data["hardest_questions"]
            ],
            [("2", 40.0), ("3", 60.0)],
        )
        self.assertEqual(
            [
                (q["question_number"], q["average_percentage"])
                for q in data["easiest_questions"]
            ],
            [("4", 100.0), ("1", 90.0)],
        )
        hardest = data["hardest_questions"][0]
        self.assertEqual(
            hardest["question_text"], "Explain the causes of the First World War."
        )
        self.assertEqual(hardest["question_type"], "ESSAY")
        self.assertEqual(hardest["points"], 20.0)
        self.assertEqual(hardest["students_counted"], 5)

    def test_a_withdrawn_students_marks_do_not_count(self):
        self._class()
        # Enough zeros on Q4 to make it the hardest, were they counted.
        for _ in range(5):
            self._graded(
                {1: 9, 2: 8, 3: 3, 4: 0},
                enrollment_status=EnrollmentStatusType.WITHDRAWN,
            )
        data = self._get()
        self.assertEqual(data["question_difficulty"]["students_analysed"], 5)
        self.assertEqual(data["easiest_questions"][0]["question_number"], "4")
        self.assertEqual(data["easiest_questions"][0]["average_percentage"], 100.0)

    def test_an_ungraded_submission_does_not_count(self):
        self._class(students=MINIMUM_STUDENTS - 1)
        StudentSubmission.objects.create(
            assignment=self.assignment_obj,
            student=self._student(),
            answers=[],
        )
        data = self._get()
        self.assertEqual(data["question_difficulty"]["status"], STATUS_NOT_ENOUGH_DATA)
        self.assertEqual(data["question_difficulty"]["students_analysed"], 4)
        self.assertEqual(data["hardest_questions"], [])
        self.assertEqual(data["easiest_questions"], [])

    def test_a_lost_answer_is_reported_not_counted(self):
        self._class()
        # The scan lost Q4 on these papers; the grader scored it 0.
        for _ in range(3):
            self._graded({1: 9, 2: 8, 3: 3, 4: 0}, statuses={4: NOT_FOUND_IN_DOCUMENT})
        data = self._get()
        q4 = data["easiest_questions"][0]
        self.assertEqual(q4["question_number"], "4")
        self.assertEqual(q4["average_percentage"], 100.0)
        self.assertEqual(q4["students_counted"], 5)
        self.assertEqual(q4["not_read"], 3)

    def test_a_blank_answer_is_counted_and_reported(self):
        self._class(students=3)
        for _ in range(2):
            self._graded({1: 9, 2: 8, 3: 3, 4: 0}, statuses={4: BLANK})
        data = self._get()
        # Q4: three 100% and two blanks -> 60%, tied with Q3.
        by_number = {
            q["question_number"]: q
            for q in data["hardest_questions"] + data["easiest_questions"]
        }
        self.assertEqual(by_number["3"]["average_percentage"], 60.0)
        self.assertNotIn("4", [q["question_number"] for q in data["easiest_questions"]])

    @patch("students.views.formatted_grade_async")
    def test_a_teachers_override_takes_that_paper_out(self, _formatted):
        # The override re-words the feedback, a billed AI call.
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100_000,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        papers = self._class()
        override = papers[0]
        response = self.client.patch(
            reverse("student-submission-update-grade", kwargs={"pk": override.pk}),
            {"score": 40},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)

        data = self._get()
        summary = data["question_difficulty"]
        self.assertEqual(summary["excluded_manual_grade_change"], 1)
        self.assertEqual(summary["students_analysed"], MINIMUM_STUDENTS - 1)
        self.assertEqual(summary["status"], STATUS_NOT_ENOUGH_DATA)

    def test_the_response_always_carries_the_fields(self):
        data = self._get()
        self.assertEqual(data["hardest_questions"], [])
        self.assertEqual(data["easiest_questions"], [])
        self.assertEqual(
            data["question_difficulty"],
            {
                "status": STATUS_NOT_ENOUGH_DATA,
                "students_analysed": 0,
                "excluded_manual_grade_change": 0,
                "minimum_students": MINIMUM_STUDENTS,
            },
        )
