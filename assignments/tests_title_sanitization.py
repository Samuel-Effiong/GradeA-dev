"""Tests locking in title HTML-stripping behaviour.

Root cause: AI extraction wraps assignment titles in heading/paragraph
tags meant for the rich editor/PDF body (see
assignments/services.py::format_assignment_standard_html), but nothing
stripped that markup before it reached plain-text contexts - notification
emails, PDF headers/filenames, list views - where it showed up as literal
text (e.g. "<p>Matrices Exam</p>").

Two independent layers now guard against this, and both are covered here:

  * assignments.signals.sanitize_assignment_title - a pre_save hook that
    strips tags from `Assignment.title` on every write path (DRF
    serializers, direct .create()/.save(), admin, shell).
  * format_assignment_standard_html - always re-wraps a plain-text title
    in its own <h1>, so the editor/PDF body still shows it as a prominent,
    first-in-document heading regardless of what the caller passed in.
"""

from django.test import SimpleTestCase, TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.services import AssignmentProcessingService as A
from assignments.services import _strip_html_from_title
from assignments.tests_rigor import RigorFixtureMixin


class StripHtmlFromTitleHelperTest(SimpleTestCase):
    def test_strips_wrapping_paragraph_tag(self):
        self.assertEqual(
            _strip_html_from_title("<p>Matrices, Bases and Matrix Exam</p>"),
            "Matrices, Bases and Matrix Exam",
        )

    def test_strips_wrapping_heading_tag(self):
        self.assertEqual(_strip_html_from_title("<h1>Final Exam</h1>"), "Final Exam")

    def test_collapses_whitespace_left_by_stripped_tags(self):
        self.assertEqual(
            _strip_html_from_title("<p>Part A</p>\n<p>Part B</p>"),
            "Part A Part B",
        )

    def test_plain_title_is_left_untouched(self):
        self.assertEqual(_strip_html_from_title("Already Clean"), "Already Clean")

    def test_none_is_passed_through(self):
        self.assertIsNone(_strip_html_from_title(None))

    def test_empty_string_is_passed_through(self):
        self.assertEqual(_strip_html_from_title(""), "")

    def test_is_idempotent(self):
        once = _strip_html_from_title("<p>Exam</p>")
        twice = _strip_html_from_title(once)
        self.assertEqual(once, twice)

    def test_script_tag_content_does_not_leak_as_visible_text(self):
        # Tag stripping alone would leave the inner text visible; the
        # helper only needs to guarantee no *tag syntax* survives here -
        # script/style bodies are not expected in a title field, but the
        # result must still be inert plain text either way.
        result = _strip_html_from_title("<script>alert(1)</script>Title")
        self.assertNotIn("<script", result)
        self.assertIn("Title", result)


class AssignmentTitleSignalTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        self.course = self.make_course()

    def test_create_strips_tags_from_title(self):
        assignment = Assignment.objects.create(
            title="<p>Matrices, Bases and Matrix Multiplication Exam</p>",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        assignment.refresh_from_db()
        self.assertEqual(
            assignment.title, "Matrices, Bases and Matrix Multiplication Exam"
        )

    def test_full_save_after_mutating_title_strips_tags(self):
        assignment = Assignment.objects.create(
            title="Clean Title",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        assignment.title = "<h1>Renamed Exam</h1>"
        assignment.save()
        assignment.refresh_from_db()
        self.assertEqual(assignment.title, "Renamed Exam")

    def test_partial_save_with_update_fields_still_strips_tags(self):
        """The rigor signal skips work when `questions` isn't in
        update_fields; title sanitization must NOT copy that gating, since
        a save() call that only touches title must still be sanitized."""
        assignment = Assignment.objects.create(
            title="Clean Title",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        assignment.title = "<p>Retitled</p>"
        assignment.save(update_fields=["title"])
        assignment.refresh_from_db()
        self.assertEqual(assignment.title, "Retitled")

    def test_title_without_tags_is_unaffected(self):
        assignment = Assignment.objects.create(
            title="Already Clean Exam",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        assignment.refresh_from_db()
        self.assertEqual(assignment.title, "Already Clean Exam")

    def test_none_title_does_not_raise(self):
        assignment = Assignment.objects.create(
            title=None,
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        assignment.refresh_from_db()
        self.assertIsNone(assignment.title)


class FormatAssignmentStandardHtmlTitleHeadingTest(SimpleTestCase):
    """Locks in that the editor/PDF body always gets a real <h1> title,
    first in the document, regardless of what markup (if any) the caller's
    `data["title"]` already contains."""

    def _base_data(self, title):
        return {
            "title": title,
            "instructions": "Read carefully.",
            "total_points": 10,
            "questions": [],
        }

    def test_tag_laden_title_is_rewrapped_as_a_clean_h1(self):
        html = A.format_assignment_standard_html(
            self._base_data("<p>Matrices, Bases and Matrix Multiplication Exam</p>")
        )
        self.assertIn("<h1>Matrices, Bases and Matrix Multiplication Exam</h1>", html)
        # No leftover literal <p> wrapping the title text.
        self.assertNotIn("<p>Matrices", html)

    def test_plain_title_is_wrapped_in_h1(self):
        html = A.format_assignment_standard_html(self._base_data("Final Exam"))
        self.assertIn("<h1>Final Exam</h1>", html)

    def test_title_heading_is_the_first_content_in_the_document(self):
        html = A.format_assignment_standard_html(self._base_data("Final Exam"))
        title_pos = html.index("<h1>Final Exam</h1>")
        instructions_pos = html.index("Read carefully.")
        questions_header_pos = html.index("Assignment Questions")
        self.assertLess(title_pos, instructions_pos)
        self.assertLess(title_pos, questions_header_pos)

    def test_empty_title_renders_no_heading(self):
        html = A.format_assignment_standard_html(self._base_data(""))
        self.assertNotIn("<h1>", html)

    def test_none_title_renders_no_heading(self):
        html = A.format_assignment_standard_html(self._base_data(None))
        self.assertNotIn("<h1>", html)

    def test_title_is_escaped_against_injection(self):
        html = A.format_assignment_standard_html(
            self._base_data("<script>alert(1)</script>Title")
        )
        self.assertNotIn("<script", html)
        self.assertIn("Title", html)

    def test_omitting_document_header_still_skips_title(self):
        html = A.format_assignment_standard_html(
            self._base_data("Should not appear"), include_document_header=False
        )
        self.assertNotIn("Should not appear", html)
