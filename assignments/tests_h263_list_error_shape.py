"""H-263: a list input's validation errors keep their list shape.

djangorestframework 3.18.0 changed what a `many=True` serializer reports when
some of its items are invalid: a dict of the failing items' errors by index,
`{1: {...}}`, where it was a list with one entry per item, `{}` for each valid
one, `[{}, {...}]`. Our exception handler passes the errors through unchanged,
so the frontend would see the new shape on four inputs: a question's `rubric`,
`tasks`, `session_breakdown` and the bulk topic create.

The Senior Manager's ruling (2026-10-27): keep the list shape for now, with
`REST_FRAMEWORK["LIST_SERIALIZER_ERRORS_AS_DICT"] = False`. H-266 moves the
frontend to the dict shape before DRF 3.20 removes the switch. This test pins
the list shape on `rubric`.

Run with:
    python manage.py test assignments.tests_h263_list_error_shape
"""

from django.test import SimpleTestCase

from assignments.serializers import QuestionSerializer

NOT_A_NUMBER = ["A valid number is required."]


def question(rubric):
    return {
        "question_number": 1,
        "question_text": "Name the three states of matter.",
        "question_type": "SHORT-ANSWER",
        "points": 3,
        "options": [],
        "rubric": rubric,
    }


def rubric_item(points):
    return {"level": "Full", "points": points, "description": "All three named."}


class RubricErrorShapeTests(SimpleTestCase):
    def rubric_errors(self, rubric):
        serializer = QuestionSerializer(data=question(rubric))
        self.assertFalse(serializer.is_valid())
        self.assertEqual(list(serializer.errors), ["rubric"])
        return serializer.errors["rubric"]

    def test_a_valid_item_keeps_its_empty_place(self):
        self.assertEqual(
            self.rubric_errors([rubric_item(3), rubric_item("three")]),
            [{}, {"points": NOT_A_NUMBER}],
        )

    def test_the_failing_item_is_reported_at_its_own_position(self):
        self.assertEqual(
            self.rubric_errors([rubric_item("three"), rubric_item(3)]),
            [{"points": NOT_A_NUMBER}, {}],
        )
