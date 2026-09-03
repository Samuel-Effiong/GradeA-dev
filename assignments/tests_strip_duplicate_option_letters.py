"""Tests for the one-time data-repair command that strips doubled letter
markers baked directly into Assignment.questions options/model_answer text.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_rigor import RigorFixtureMixin


def objective_question(*, options, model_answer="", number=1):
    return {
        "question_number": number,
        "question_text": f"Question {number}",
        "question_type": "OBJECTIVE",
        "points": 10,
        "options": options,
        "rubric": [],
        "model_answer": model_answer,
    }


def essay_question(*, number=1):
    return {
        "question_number": number,
        "question_text": f"Question {number}",
        "question_type": "ESSAY",
        "points": 10,
        "options": [],
        "rubric": [{"level": "excellent", "description": "d", "points": 10}],
        "model_answer": "",
    }


class StripDuplicateOptionLettersCommandTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        self.course = self.make_course()

    def _run(self, **options):
        out = StringIO()
        call_command(
            "strip_duplicate_option_letters", stdout=out, stderr=out, **options
        )
        return out.getvalue()

    def test_command_strips_doubled_markers_from_options_and_model_answer(self):
        assignment = Assignment.objects.create(
            title="MCQ",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                objective_question(
                    options=["A. A) one", "B) (B) two"],
                    model_answer="A. A) one",
                )
            ],
        )

        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions[0]["options"], ["one", "two"])
        self.assertEqual(assignment.questions[0]["model_answer"], "one")
        self.assertIn("repaired           : 1 assignment(s)", output)
        self.assertIn("questions cleaned  : 1", output)

    def test_command_leaves_clean_options_untouched(self):
        assignment = Assignment.objects.create(
            title="Clean MCQ",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question(options=["one", "two"])],
        )

        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions[0]["options"], ["one", "two"])
        self.assertIn("repaired           : 0 assignment(s)", output)

    def test_command_ignores_non_objective_questions(self):
        assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[essay_question()],
        )

        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions, [essay_question()])
        self.assertIn("repaired           : 0 assignment(s)", output)

    def test_dry_run_writes_nothing(self):
        assignment = Assignment.objects.create(
            title="MCQ",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question(options=["A. A) one"])],
        )

        output = self._run(dry_run=True)
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions[0]["options"], ["A. A) one"])
        self.assertIn("would repair", output)
        self.assertIn("dry run", output)

    def test_command_is_idempotent(self):
        assignment = Assignment.objects.create(
            title="MCQ",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[objective_question(options=["A. A) one"])],
        )

        self._run()
        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.questions[0]["options"], ["one"])
        self.assertIn("repaired           : 0 assignment(s)", output)
