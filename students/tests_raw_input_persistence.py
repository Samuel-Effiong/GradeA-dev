"""
Regression coverage for how ProseMirror documents reach the `raw_input` column.

Two defects are covered here:

- The submission pipeline assigned the converter's *dict* to `raw_input`, a
  TextField. Django coerced it with str(), so the column held a Python repr
  ("{'type': 'doc', ...}") that no JSON parser can read back - and both the
  editor and the answer-extraction prompt read that column.

- student_submission_to_html() interpolated `question_text` and `answer_html`
  raw. Those come from the AI extractor reading a student-uploaded PDF or
  image, so they are attacker-influenced, and the resulting document is stored
  and later rendered in a teacher's browser.

Run with:
    python manage.py test students.tests_raw_input_persistence
"""

import json

from django.test import TestCase
from django.utils import timezone

from assignments.models import Assignment
from assignments.services import AssignmentProcessingService
from classrooms.models import Course, Session
from students.models import StudentSubmission
from students.services import student_submission_to_html
from users.models import CustomUser, UserTypes


class RawInputFixtureMixin:
    def _fixture(self, tag, answers=None, title="A"):
        teacher = CustomUser.objects.create_user(
            email=f"{tag}-t-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        student = CustomUser.objects.create_user(
            email=f"{tag}-s-{timezone.now().timestamp()}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Ada",
            last_name="Lovelace",
        )
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title=title,
            course=course,
            questions=[{"question_number": 1, "question_text": "Q1?", "points": 10}],
        )
        submission = StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers=(
                answers
                if answers is not None
                else [{"question_number": 1, "answer_html": "<p>An answer.</p>"}]
            ),
        )
        return submission


class RawInputIsStoredAsJsonTest(RawInputFixtureMixin, TestCase):
    def test_persisted_raw_input_round_trips_through_json(self):
        submission = self._fixture("json")
        html = student_submission_to_html(submission)

        submission.raw_input = AssignmentProcessingService.html_to_prosemirror_text(
            html
        )
        submission.save()
        submission.refresh_from_db()

        document = json.loads(submission.raw_input)
        self.assertEqual(document["type"], "doc")

    def test_a_dict_assigned_to_the_textfield_would_not_be_json(self):
        """
        Pins down *why* html_to_prosemirror_text exists. If this ever starts
        passing, TextField has gained dict handling and the guidance in
        html_to_prosemirror_text's docstring needs revisiting.
        """
        submission = self._fixture("repr")
        submission.raw_input = {"type": "doc", "content": []}
        submission.save()
        submission.refresh_from_db()

        self.assertIsInstance(submission.raw_input, str)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(submission.raw_input.replace('"', "'"))

    def test_stored_document_contains_no_python_literals(self):
        submission = self._fixture("literals")
        payload = AssignmentProcessingService.html_to_prosemirror_text(
            student_submission_to_html(submission)
        )

        self.assertNotIn("None", payload)
        self.assertNotIn("True", payload)
        self.assertNotIn("'", payload)
        self.assertIn("null", payload)


class SubmissionHtmlSanitisationTest(RawInputFixtureMixin, TestCase):
    HOSTILE = (
        "<script>alert(1)</script>"
        '<img src="x" onerror="alert(2)">'
        '<a href="javascript:alert(3)">click</a>'
        '<p onclick="alert(4)">text</p>'
    )

    def test_hostile_extractor_output_is_stripped_from_the_html(self):
        submission = self._fixture(
            "xss",
            answers=[{"question_number": 1, "answer_html": self.HOSTILE}],
        )
        html = student_submission_to_html(submission)

        self.assertNotIn("<script", html)
        self.assertNotIn("onerror", html)
        self.assertNotIn("onclick", html)
        self.assertNotIn("javascript:", html)

    def test_hostile_extractor_output_never_reaches_the_stored_document(self):
        submission = self._fixture(
            "xssdoc",
            answers=[{"question_number": 1, "answer_html": self.HOSTILE}],
        )
        payload = AssignmentProcessingService.html_to_prosemirror_text(
            student_submission_to_html(submission)
        )

        for fragment in ("script", "onerror", "onclick", "javascript:", "alert("):
            self.assertNotIn(fragment, payload, fragment)

    def test_hostile_question_text_is_stripped(self):
        submission = self._fixture(
            "qtext",
            answers=[
                {
                    "question_number": 1,
                    "question_text": '<img src=x onerror="alert(1)">Q',
                    "answer_html": "<p>a</p>",
                }
            ],
        )
        html = student_submission_to_html(submission)

        self.assertNotIn("onerror", html)
        self.assertIn("Q", html)

    def test_hostile_assignment_title_is_stripped(self):
        submission = self._fixture("title", title='<img src=x onerror="alert(1)">Test')
        html = student_submission_to_html(submission)

        self.assertNotIn("onerror", html)
        self.assertIn("Test", html)

    def test_legitimate_answer_formatting_still_survives(self):
        submission = self._fixture(
            "keep",
            answers=[
                {
                    "question_number": 1,
                    "answer_html": "<p>H<sub>2</sub>O and x<sup>2</sup> "
                    "with <strong>emphasis</strong></p>",
                }
            ],
        )
        payload = json.loads(
            AssignmentProcessingService.html_to_prosemirror_text(
                student_submission_to_html(submission)
            )
        )

        found = set()

        def walk(node):
            for mark in node.get("marks", []):
                found.add(mark["type"])
            for child in node.get("content", []):
                walk(child)

        walk(payload)
        self.assertIn("subscript", found)
        self.assertIn("superscript", found)
        self.assertIn("strong", found)

    def test_submission_with_no_answers_still_converts(self):
        submission = self._fixture("noanswers", answers=[])
        payload = AssignmentProcessingService.html_to_prosemirror_text(
            student_submission_to_html(submission)
        )
        self.assertEqual(json.loads(payload)["type"], "doc")

    def test_submission_with_a_skipped_answer_still_converts(self):
        submission = self._fixture(
            "skipped",
            answers=[{"question_number": 1, "answer_html": ""}],
        )
        html = student_submission_to_html(submission)
        payload = AssignmentProcessingService.html_to_prosemirror_text(html)

        self.assertIn("No answer submitted", html)
        self.assertEqual(json.loads(payload)["type"], "doc")


class RawInputRepairMigrationTest(TestCase):
    """The migration's classifier, exercised directly on representative rows."""

    def setUp(self):
        module = __import__(
            "students.migrations.0024_repair_raw_input_json",
            fromlist=["_repaired"],
        )
        self.repaired = module._repaired

    def test_python_repr_is_converted_to_json(self):
        result = self.repaired("{'type': 'doc', 'content': [], 'x': None}")
        self.assertEqual(json.loads(result), {"type": "doc", "content": [], "x": None})

    def test_valid_json_is_left_alone(self):
        self.assertIsNone(self.repaired('{"type": "doc", "content": []}'))

    def test_free_text_is_left_alone(self):
        self.assertIsNone(self.repaired("Unprocessed uploaded text"))

    def test_html_is_left_alone(self):
        self.assertIsNone(self.repaired("<p>some raw html</p>"))

    def test_null_and_blank_are_left_alone(self):
        for value in (None, "", "   ", 42):
            with self.subTest(value=value):
                self.assertIsNone(self.repaired(value))

    def test_brace_prefixed_nonsense_is_left_alone(self):
        self.assertIsNone(self.repaired("{not a literal at all"))

    def test_a_scalar_literal_is_left_alone(self):
        self.assertIsNone(self.repaired("'just a string'"))

    def test_real_converter_output_round_trips(self):
        document = AssignmentProcessingService.html_to_prosemirror_json(
            "<h2>t</h2><p>a<sup>1</sup></p>"
        )
        repaired = self.repaired(str(document))

        self.assertIsNotNone(repaired)
        self.assertEqual(json.loads(repaired), document)


class RawInputRepairMigrationRunTest(RawInputFixtureMixin, TestCase):
    """The migration's data pass, executed against real rows."""

    def setUp(self):
        module = __import__(
            "students.migrations.0024_repair_raw_input_json",
            fromlist=["repair"],
        )
        self.repair = module.repair

    def _run(self):
        from django.apps import apps

        self.repair(apps, None)

    def test_corrupted_rows_are_repaired_and_others_left_alone(self):
        document = AssignmentProcessingService.html_to_prosemirror_json("<p>x</p>")

        corrupted = self._fixture("mig-bad")
        StudentSubmission.objects.filter(pk=corrupted.pk).update(
            raw_input=str(document)
        )
        already_json = self._fixture("mig-json")
        StudentSubmission.objects.filter(pk=already_json.pk).update(
            raw_input=json.dumps(document)
        )
        free_text = self._fixture("mig-text")
        StudentSubmission.objects.filter(pk=free_text.pk).update(
            raw_input="Unprocessed uploaded text"
        )
        blank = self._fixture("mig-blank")

        self._run()

        corrupted.refresh_from_db()
        already_json.refresh_from_db()
        free_text.refresh_from_db()
        blank.refresh_from_db()

        self.assertEqual(json.loads(corrupted.raw_input), document)
        self.assertEqual(json.loads(already_json.raw_input), document)
        self.assertEqual(free_text.raw_input, "Unprocessed uploaded text")
        self.assertFalse(blank.raw_input)

    def test_running_twice_is_idempotent(self):
        document = AssignmentProcessingService.html_to_prosemirror_json("<p>y</p>")
        submission = self._fixture("mig-twice")
        StudentSubmission.objects.filter(pk=submission.pk).update(
            raw_input=str(document)
        )

        self._run()
        submission.refresh_from_db()
        once = submission.raw_input

        self._run()
        submission.refresh_from_db()

        self.assertEqual(submission.raw_input, once)
        self.assertEqual(json.loads(submission.raw_input), document)
