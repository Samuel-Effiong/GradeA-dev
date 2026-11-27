"""
Student page crash (owner, 2026-11-27, option B): a released grade's
student page read a field the student projection (H-127) never sent and
crashed ("Cannot read properties of undefined (reading 'replace')").

The owner chose to send students three more fields of the saved grading
result, and nothing else:
  * calculation_notes, of score_calculation_verification;
  * evaluation_rationale, of each question evaluation;
  * grading_confidence, at the top level of the result (where the grader
    writes it: ai_processor/grading_schemas.py).

Each is copied only when present and plain (`_copy_plain`): a missing one
is left out (never null), a nested one is dropped. Everything else the
projection hid stays hidden.

    python manage.py test students.tests_student_feedback_three_fields
"""

import json

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework import status

from students import feedback_projection
from students.feedback_projection import student_safe_feedback
from students.tests_student_feedback_routes import (
    FULL_FEEDBACK,
    StudentFeedbackRoutesBase,
    as_plain,
)

NOTE = "score_calculation_verification"

#: Still hidden from a student, wherever they sit in FULL_FEEDBACK.
STILL_HIDDEN_MARKERS = (
    "second_opinion",
    "second-grader-model",
    "first-grader-model",
    "SECOND GRADER RATIONALE",
    "INTERNAL NOTE ON LEVEL (summary)",
    "evidence_quotes",
    "flag_for_review",
    "graded_by",
    "snapped_from",
    "for_teacher",
    "ADVICE TO THE TEACHER",
    "follow_up_actions",
    "RAW SECOND OPINION ERROR",
    "grading_model",
    "A KEY NOBODY HAS CLASSIFIED",
    "snapped_to_rubric_level_count",
    "model_answer",
    "confidence_note",
)


def without(name, value=FULL_FEEDBACK):
    """`value` with every key `name` removed, at any depth."""
    if isinstance(value, dict):
        return {k: without(name, v) for k, v in value.items() if k != name}
    if isinstance(value, list):
        return [without(name, item) for item in value]
    return value


class ProjectionTests(SimpleTestCase):
    def test_the_whitelists_name_the_three_fields(self):
        self.assertIn(
            "calculation_notes", feedback_projection.STUDENT_VERIFICATION_FIELDS
        )
        self.assertIn(
            "evaluation_rationale", feedback_projection.STUDENT_EVALUATION_FIELDS
        )
        self.assertEqual(
            getattr(feedback_projection, "STUDENT_TOP_LEVEL_FIELDS", None),
            ("grading_confidence",),
        )

    def test_each_is_shown_when_present(self):
        shown = student_safe_feedback(FULL_FEEDBACK)
        self.assertEqual(
            shown[NOTE]["calculation_notes"], "CALCULATION NOTE FOR THE TEACHER"
        )
        self.assertEqual(
            shown["question_evaluations"][0]["evaluation_rationale"],
            "INTERNAL NOTE ON LEVEL selection.",
        )
        self.assertEqual(shown["grading_confidence"], 92)

    def test_each_is_left_out_when_missing(self):
        for name in ("calculation_notes", "evaluation_rationale", "grading_confidence"):
            with self.subTest(name=name):
                shown = student_safe_feedback(without(name))
                self.assertNotIn(name, json.dumps(shown))

    def test_a_missing_confidence_is_absent_not_null(self):
        shown = student_safe_feedback(without("grading_confidence"))
        self.assertNotIn("grading_confidence", shown)

    def test_a_nested_value_is_dropped(self):
        nested = {"secret": "x"}
        feedback = {
            "grading_confidence": nested,
            "question_evaluations": [{"evaluation_rationale": [nested]}],
            NOTE: {"calculation_notes": nested},
        }
        shown = student_safe_feedback(feedback)
        self.assertNotIn("grading_confidence", shown)
        self.assertEqual(shown["question_evaluations"], [{}])
        self.assertEqual(shown[NOTE], {})

    def test_everything_else_stays_hidden(self):
        body = json.dumps(student_safe_feedback(FULL_FEEDBACK))
        for marker in STILL_HIDDEN_MARKERS:
            with self.subTest(marker=marker):
                self.assertNotIn(marker, body)

    def test_the_three_are_all_that_is_new_at_the_top_level(self):
        self.assertEqual(
            set(student_safe_feedback(FULL_FEEDBACK)),
            {
                "grading_summary",
                "question_evaluations",
                "overall_performance_analysis",
                NOTE,
                "recommendations",
                "grading_confidence",
            },
        )


class StudentRouteTests(StudentFeedbackRoutesBase):
    """The two routes a student's page reads a released grade from."""

    def detail_summary(self):
        response = self.client.get(
            reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response, response.data["performance_summary"]

    def own_submission(self):
        response = self.client.get(
            reverse("student-submission-detail", kwargs={"pk": self.submission.pk})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        return response, response.data["feedback"]

    def test_a_published_grade_carries_the_three(self):
        for read in (self.detail_summary, self.own_submission):
            with self.subTest(route=read.__name__):
                response, shown = read()
                shown = as_plain(shown)
                self.assertEqual(
                    shown[NOTE]["calculation_notes"],
                    "CALCULATION NOTE FOR THE TEACHER",
                )
                self.assertEqual(
                    shown["question_evaluations"][0]["evaluation_rationale"],
                    "INTERNAL NOTE ON LEVEL selection.",
                )
                self.assertEqual(shown["grading_confidence"], 92)
                body = json.dumps(as_plain(response.data))
                for marker in STILL_HIDDEN_MARKERS:
                    self.assertNotIn(marker, body)

    def test_a_published_grade_without_them_leaves_them_out(self):
        feedback = FULL_FEEDBACK
        for name in ("calculation_notes", "evaluation_rationale", "grading_confidence"):
            feedback = without(name, feedback)
        self.set_grade(is_published=True, feedback=feedback)
        for read in (self.detail_summary, self.own_submission):
            with self.subTest(route=read.__name__):
                _, shown = read()
                body = json.dumps(as_plain(shown))
                for name in (
                    "calculation_notes",
                    "evaluation_rationale",
                    "grading_confidence",
                ):
                    self.assertNotIn(name, body)
                self.assertEqual(as_plain(shown)["grading_summary"]["total_score"], 8)

    def test_an_unpublished_grade_still_shows_nothing(self):
        self.set_grade(is_published=False)
        _, shown = self.detail_summary()
        self.assertIsNone(shown)
