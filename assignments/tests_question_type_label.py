"""
The question-type label in the teacher document heading.

WHY IT IS THERE

`question_type` lives in the database but, until this label, appeared
nowhere in the rendered document - so the AI re-extraction that every
assignment edit goes through (the frontend editor is free-form and
resends the whole document) had to re-derive the type from the question's
wording every single time. The extraction benchmark caught it drifting:
SHORT-ANSWER questions came back as ESSAY on a plain save.

The drift is not cosmetic. The grader marks an ESSAY as one overall
judgement of sustained quality and a SHORT-ANSWER against specific
required content, and AssignmentSerializer.validate() rejects a
non-HYBRID assignment whose questions disagree about their type - so a
drift can fail a teacher's edit outright.

WHAT THESE TESTS PIN

  * the label appears for teachers, in the heading, for every real type;
  * it does NOT appear on the student's paper (it is marking metadata,
    and the student view is unchanged from before this feature);
  * malformed or unknown types degrade to the old heading rather than
    printing something misleading;
  * it survives the HTML -> ProseMirror conversion, which is the only
    reason it helps at all - a label the converter drops would be a label
    the re-extraction never sees.

Run with:
    python manage.py test assignments.tests_question_type_label
"""

import json

from django.test import SimpleTestCase

from assignments.services import QUESTION_TYPE_LABELS, AssignmentProcessingService

APS = AssignmentProcessingService


def question(number=1, qtype="SHORT-ANSWER", points=8, **extra):
    data = {
        "question_number": number,
        "question_text": "<p>Explain why a body in circular motion accelerates.</p>",
        "question_type": qtype,
        "question_image": "",
        "points": points,
        "blooms_level": "Understand",
        "options": [],
        "rubric": [
            {"level": "excellent", "points": points, "description": "<p>Full.</p>"},
            {"level": "poor", "points": 0, "description": "<p>None.</p>"},
        ],
        "model_answer": "<p>Velocity is a vector.</p>",
    }
    data.update(extra)
    return data


def assignment(*questions):
    return {
        "title": "Physics",
        "instructions": "<p>Answer all.</p>",
        "total_points": sum(q["points"] for q in questions),
        "questions": list(questions),
    }


def render(data, **kwargs):
    return APS.format_assignment_standard_html(data, **kwargs)


class TeacherViewTest(SimpleTestCase):
    def test_short_answer_is_labelled(self):
        html = render(assignment(question(qtype="SHORT-ANSWER")))
        self.assertIn("Short Answer", html)

    def test_essay_is_labelled(self):
        html = render(assignment(question(qtype="ESSAY")))
        self.assertIn("Essay", html)

    def test_objective_is_labelled(self):
        html = render(
            assignment(
                question(qtype="OBJECTIVE", options=["Newton", "Joule"], rubric=[])
            )
        )
        self.assertIn("Multiple Choice", html)

    def test_the_label_sits_inside_the_question_heading(self):
        # It has to be IN the heading, not floating nearby: the heading is
        # the run the extractor associates with the question.
        html = render(assignment(question(qtype="ESSAY", points=8)))
        self.assertIn("Question 1 (8 marks) &mdash; Essay", html)

    def test_marks_and_number_are_unchanged(self):
        html = render(assignment(question(number=3, points=12)))
        self.assertIn("Question 3 (12 marks)", html)

    def test_each_question_gets_its_own_label(self):
        html = render(
            assignment(
                question(1, "OBJECTIVE", 2, options=["a", "b"], rubric=[]),
                question(2, "SHORT-ANSWER", 8),
                question(3, "ESSAY", 10),
            )
        )
        self.assertIn("Question 1 (2 marks) &mdash; Multiple Choice", html)
        self.assertIn("Question 2 (8 marks) &mdash; Short Answer", html)
        self.assertIn("Question 3 (10 marks) &mdash; Essay", html)

    def test_lowercase_stored_type_is_still_labelled(self):
        # format_assignment_standard_html uppercases before lookup;
        # extracted assignments have carried "Essay" and "short-answer".
        for stored in ("essay", "Essay", "eSSaY"):
            with self.subTest(stored=stored):
                html = render(assignment(question(qtype=stored)))
                self.assertIn("Essay", html)


class StudentViewTest(SimpleTestCase):
    """The student's paper must be byte-identical to before this feature."""

    def test_label_is_absent_for_students(self):
        html = render(assignment(question(qtype="ESSAY")), include_rubric=False)
        self.assertNotIn("&mdash; Essay", html)
        self.assertIn("Question 1 (8 marks)", html)

    def test_no_type_label_leaks_for_any_type(self):
        for qtype, label in QUESTION_TYPE_LABELS.items():
            with self.subTest(qtype=qtype):
                html = render(
                    assignment(
                        question(
                            qtype=qtype,
                            options=["a", "b"] if qtype == "OBJECTIVE" else [],
                            rubric=[] if qtype == "OBJECTIVE" else question()["rubric"],
                        )
                    ),
                    include_rubric=False,
                )
                self.assertNotIn(f"&mdash; {label}", html)

    def test_student_heading_matches_the_pre_feature_shape(self):
        html = render(assignment(question(number=2, points=5)), include_rubric=False)
        self.assertIn("<strong>Question 2 (5 marks)</strong>", html)


class MalformedTypeTest(SimpleTestCase):
    """Unknown or missing types degrade to the old heading, never to a
    misleading label."""

    def test_unknown_type_gets_no_label(self):
        html = render(assignment(question(qtype="MULTIPART")))
        self.assertIn("<strong>Question 1 (8 marks)</strong>", html)

    def test_hybrid_gets_no_label(self):
        # HYBRID is an ASSIGNMENT-level type; a question carrying it is
        # malformed, and labelling it would assert something false.
        html = render(assignment(question(qtype="HYBRID")))
        self.assertIn("<strong>Question 1 (8 marks)</strong>", html)
        self.assertNotIn("Hybrid", html)

    def test_missing_type_gets_no_label(self):
        data = assignment(question())
        del data["questions"][0]["question_type"]
        html = render(data)
        self.assertIn("<strong>Question 1 (8 marks)</strong>", html)

    def test_null_type_gets_no_label(self):
        # question_type arrives from AI extraction and can be an explicit
        # null, which is not the same as an absent key.
        html = render(assignment(question(qtype=None)))
        self.assertIn("<strong>Question 1 (8 marks)</strong>", html)

    def test_empty_string_type_gets_no_label(self):
        html = render(assignment(question(qtype="")))
        self.assertIn("<strong>Question 1 (8 marks)</strong>", html)

    def test_html_in_a_type_cannot_inject(self):
        # question_type is AI-supplied and this document is rendered to PDF
        # by a real browser, so an unescaped value here would be a script
        # path. It is not a known label, so it must produce nothing at all.
        html = render(assignment(question(qtype="<script>alert(1)</script>")))
        self.assertNotIn("<script>", html)


class ProseMirrorRoundTripTest(SimpleTestCase):
    """
    A label the converter drops is a label the re-extraction never sees,
    which would make this whole feature a no-op.
    """

    def test_label_survives_conversion_to_prosemirror(self):
        html = render(assignment(question(qtype="SHORT-ANSWER", points=8)))
        document = json.loads(APS.html_to_prosemirror_text(html))
        self.assertIn("Short Answer", json.dumps(document))

    def test_label_stays_attached_to_its_question_heading(self):
        # Not merely present somewhere in the document: the extractor
        # associates it with the question by proximity in the heading run.
        html = render(assignment(question(number=2, qtype="ESSAY", points=10)))
        flat = json.dumps(json.loads(APS.html_to_prosemirror_text(html)))
        heading = "Question 2 (10 marks) \\u2014 Essay"
        self.assertIn(heading, flat.replace("—", "\\u2014"))

    def test_student_document_carries_no_label_after_conversion(self):
        html = render(assignment(question(qtype="ESSAY")), include_rubric=False)
        flat = json.dumps(json.loads(APS.html_to_prosemirror_text(html)))
        self.assertNotIn("Essay", flat)


class LabelTableTest(SimpleTestCase):
    def test_labels_cover_every_allowed_question_type(self):
        from assignments.serializers import QuestionSerializer

        self.assertEqual(
            set(QUESTION_TYPE_LABELS), set(QuestionSerializer.ALLOWED_QUESTION_TYPES)
        )

    def test_labels_are_keyed_uppercase(self):
        # The lookup uppercases first; a lowercase key would never match.
        for key in QUESTION_TYPE_LABELS:
            self.assertEqual(key, key.upper())
