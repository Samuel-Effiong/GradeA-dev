"""H-263 / H-266: a list input's validation errors use the dict-by-index shape.

djangorestframework 3.18.0 changed what a `many=True` serializer reports when
some of its items are invalid: a dict of the failing items' errors by index,
`{1: {...}}`, where it was a list with one entry per item, `{}` for each valid
one, `[{}, {...}]`. H-263 kept the old list shape with
`LIST_SERIALIZER_ERRORS_AS_DICT = False`. H-266 adopts the dict shape
(`LIST_SERIALIZER_ERRORS_AS_DICT = True`) so the frontend can target individual
items, and before DRF 3.20 removes the switch.

Run with:
    python manage.py test assignments.tests_h263_list_error_shape
"""

from collections.abc import Mapping

from django.test import SimpleTestCase

from assignments.serializers import QuestionSerializer
from users.renderers import flatten_errors

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

    def test_only_the_failing_item_appears_keyed_by_index(self):
        self.assertEqual(
            self.rubric_errors([rubric_item(3), rubric_item("three")]),
            {1: {"points": NOT_A_NUMBER}},
        )

    def test_the_failing_item_is_reported_at_its_own_index(self):
        self.assertEqual(
            self.rubric_errors([rubric_item("three"), rubric_item(3)]),
            {0: {"points": NOT_A_NUMBER}},
        )

    def test_the_dict_shape_is_a_mapping(self):
        errors = self.rubric_errors([rubric_item(3), rubric_item("three")])
        self.assertIsInstance(errors, Mapping)

    def test_the_error_message_is_byte_identical_to_the_list_shape(self):
        serializer = QuestionSerializer(
            data=question([rubric_item(3), rubric_item("three")])
        )
        serializer.is_valid()
        message = flatten_errors(serializer.errors)
        self.assertEqual(message, "Points: A valid number is required.")
