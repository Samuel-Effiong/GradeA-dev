"""Teachers read "points", not "marks" (beta wording fix, owner 2026-11-12).

The assignment as formatted for teachers and students: the question heading
says "(N points)" and "(1 point)" for exactly one, the rubric is titled
"Grading Guide / Rubric:" with a "Points" column, and the PDF header says
"{total} points total" ("1 point total"). The singular is decided from the
value that is printed: 1, 1.0, Decimal("1.00") and "1" all count as one;
0, 0.5 and anything else say points.

    python manage.py test assignments.tests_points_not_marks
"""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from django.utils import timezone

from assignments import pdf_document
from assignments.services import AssignmentProcessingService as APS

from .tests_question_type_label import assignment, question


def render(data, **kwargs):
    return APS.format_assignment_standard_html(data, **kwargs)


class QuestionHeadingTests(SimpleTestCase):
    def heading(self, points, **kwargs):
        data = assignment(question(points=1))
        data["questions"][0]["points"] = points
        return render(data, **kwargs)

    def test_plural(self):
        self.assertIn("<strong>Question 1 (8 points)", self.heading(8))
        self.assertNotIn("marks)", self.heading(8))

    def test_exactly_one_is_singular(self):
        for value in (1, 1.0, Decimal("1.00"), "1"):
            with self.subTest(value=repr(value)):
                html = self.heading(value)
                self.assertIn("Question 1 (1 point)", html)
                self.assertNotIn("(1 points)", html)

    def test_zero_and_a_half_say_points(self):
        for value in (0, 0.5, Decimal("0.50"), 2, 10):
            with self.subTest(value=repr(value)):
                html = self.heading(value)
                self.assertIn(f"({value} points)", html)

    def test_the_type_label_still_follows(self):
        html = render(assignment(question(qtype="ESSAY", points=8)))
        self.assertIn("Question 1 (8 points) &mdash; Essay", html)
        html = render(assignment(question(qtype="ESSAY", points=1)))
        self.assertIn("Question 1 (1 point) &mdash; Essay", html)


class RubricTests(SimpleTestCase):
    def test_title_and_column(self):
        html = render(assignment(question(qtype="ESSAY")), include_rubric=True)
        self.assertIn("Grading Guide / Rubric:", html)
        self.assertIn('<th align="center">Points</th>', html)
        self.assertNotIn("Marking Guide", html)
        self.assertNotIn(">Marks<", html)


class PdfTotalTests(SimpleTestCase):
    def header(self, total):
        paper = SimpleNamespace(
            course=None,
            title="Physics",
            instructions="",
            due_date=timezone.now() + timedelta(days=1),
            total_points=total,
            questions=[],
        )
        with (
            patch("assignments.pdf_document.ensure_capacity"),
            patch("assignments.pdf_document.render_html_to_pdf") as renderer,
        ):
            renderer.return_value = b"%PDF"
            pdf_document.render_assignment_pdf(paper, include_rubric=False)
        return renderer.call_args.args[0]

    def test_plural(self):
        self.assertIn("20 points total</span>", self.header(20))

    def test_exactly_one_is_singular(self):
        for value in (1, 1.0, Decimal("1.00")):
            with self.subTest(value=repr(value)):
                html = self.header(value)
                self.assertIn("point total</span>", html)
                self.assertNotIn("points total", html)

    def test_zero_and_missing_keep_their_fallback(self):
        self.assertIn("N/A points total</span>", self.header(0))
        self.assertIn("N/A points total</span>", self.header(None))
        self.assertNotIn("marks total", self.header(None))
