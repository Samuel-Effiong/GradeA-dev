"""
QA bug (owner chose option 3, 2026-11-25): the student's assignment page
read feedback.score_calculation_verification.individual_scores and crashed,
because the student projection (H-127) never copied that block.

The projection copies individual_scores, manual_sum, (owner,
2026-11-25, after the page crashed formatting it) verification_status and
(owner, 2026-11-27, option B, after the page crashed again)
calculation_notes. snapped_to_rubric_level_count and anything else in the
block are never shown, whether the note says PASS or not. A missing block, or one that is
not a dictionary, is left out.

    python manage.py test students.tests_student_score_verification
"""

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status

from students import feedback_projection
from students.feedback_projection import student_safe_feedback
from students.tests_student_feedback_routes import StudentFeedbackRoutesBase

NOTE = "score_calculation_verification"
HIDDEN = ("snapped_to_rubric_level_count",)


def note(status_text="PASS", **extra):
    block = {
        "individual_scores": [4, 3.5, 0],
        "manual_sum": 7.5,
        "verification_status": status_text,
        "calculation_notes": "Left out a repeated evaluation of question 2.",
        "snapped_to_rubric_level_count": 2,
    }
    block.update(extra)
    return block


class ProjectionTests(SimpleTestCase):
    def test_the_named_fields(self):
        self.assertEqual(
            getattr(feedback_projection, "STUDENT_VERIFICATION_FIELDS", None),
            (
                "individual_scores",
                "manual_sum",
                "verification_status",
                "calculation_notes",
            ),
        )

    def test_a_pass_note_shows_the_arithmetic_and_the_status(self):
        shown = student_safe_feedback({NOTE: note()})
        self.assertEqual(
            shown[NOTE],
            {
                "individual_scores": [4, 3.5, 0],
                "manual_sum": 7.5,
                "verification_status": "PASS",
                "calculation_notes": "Left out a repeated evaluation of question 2.",
            },
        )

    def test_a_corrected_note_shows_the_arithmetic_and_the_status(self):
        shown = student_safe_feedback({NOTE: note("CORRECTED", correction_note="x")})
        self.assertEqual(
            shown[NOTE],
            {
                "individual_scores": [4, 3.5, 0],
                "manual_sum": 7.5,
                "verification_status": "CORRECTED",
                "calculation_notes": "Left out a repeated evaluation of question 2.",
            },
        )

    def test_a_nested_status_is_dropped(self):
        shown = student_safe_feedback({NOTE: note({"secret": "x"})})
        self.assertNotIn("verification_status", shown[NOTE])

    def test_the_teachers_parts_of_the_note_never_appear(self):
        for text in ("PASS", "CORRECTED", "FAIL"):
            shown = student_safe_feedback({NOTE: note(text, a_new_key="later")})
            for name in HIDDEN + ("a_new_key",):
                self.assertNotIn(name, shown[NOTE])

    def test_a_missing_block_leaves_the_key_out(self):
        self.assertNotIn(NOTE, student_safe_feedback({"grading_summary": {}}))

    def test_a_block_that_is_not_a_dictionary_leaves_the_key_out(self):
        for value in (None, "PASS", [1, 2], 7):
            self.assertNotIn(NOTE, student_safe_feedback({NOTE: value}))

    def test_a_nested_value_under_individual_scores_is_dropped(self):
        shown = student_safe_feedback(
            {NOTE: note(individual_scores=[{"question": 1, "secret": "x"}])}
        )
        self.assertEqual(
            shown[NOTE],
            {
                "manual_sum": 7.5,
                "verification_status": "PASS",
                "calculation_notes": "Left out a repeated evaluation of question 2.",
            },
        )

    def test_a_note_with_no_shown_field_is_an_empty_block(self):
        """The block was there, so the key is there; nothing is invented
        inside it."""
        self.assertEqual(
            student_safe_feedback({NOTE: {"snapped_to_rubric_level_count": 2}})[NOTE],
            {},
        )


class AssignmentDetailRouteTests(StudentFeedbackRoutesBase):
    def summary(self):
        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response.data["performance_summary"]

    def test_a_published_grade_carries_individual_scores(self):
        self.set_grade(is_published=True, feedback={NOTE: note()})
        shown = self.summary()
        self.assertEqual(shown[NOTE]["individual_scores"], [4, 3.5, 0])
        self.assertEqual(shown[NOTE]["manual_sum"], 7.5)
        self.assertEqual(shown[NOTE]["verification_status"], "PASS")
        self.assertEqual(
            shown[NOTE]["calculation_notes"],
            "Left out a repeated evaluation of question 2.",
        )
        for name in HIDDEN:
            self.assertNotIn(name, shown[NOTE])

    def test_an_unpublished_grade_is_still_null(self):
        self.set_grade(is_published=False, feedback={NOTE: note()})
        self.assertIsNone(self.summary())
