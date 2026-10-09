"""Tests for the one-time data-repair command that strips HTML tags baked
directly into Assignment.title from before title sanitization existed.
"""

from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_rigor import RigorFixtureMixin


class StripHtmlFromAssignmentTitlesCommandTest(RigorFixtureMixin, TestCase):
    def setUp(self):
        self.course = self.make_course()

    def _run(self, **options):
        out = StringIO()
        call_command(
            "strip_html_from_assignment_titles", stdout=out, stderr=out, **options
        )
        return out.getvalue()

    def _create_with_raw_title(self, title):
        """Bypass the pre_save sanitizer signal via bulk_create, so the
        row lands in the DB the way pre-fix data actually looked."""
        assignment = Assignment(
            title="placeholder",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )
        Assignment.objects.bulk_create([assignment])
        assignment.refresh_from_db()
        Assignment.objects.filter(pk=assignment.pk).update(title=title)
        assignment.refresh_from_db()
        return assignment

    def test_command_strips_tags_from_existing_titles(self):
        assignment = self._create_with_raw_title(
            "<p>Matrices, Bases and Matrix Multiplication Exam</p>"
        )

        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(
            assignment.title, "Matrices, Bases and Matrix Multiplication Exam"
        )
        self.assertIn("repaired : 1 assignment(s)", output)

    def test_command_leaves_clean_titles_untouched(self):
        assignment = self._create_with_raw_title("Already Clean Exam")

        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.title, "Already Clean Exam")
        self.assertIn("repaired : 0 assignment(s)", output)

    def test_dry_run_writes_nothing(self):
        assignment = self._create_with_raw_title("<h1>Final Exam</h1>")

        output = self._run(dry_run=True)
        assignment.refresh_from_db()

        self.assertEqual(assignment.title, "<h1>Final Exam</h1>")
        self.assertIn("would repair", output)
        self.assertIn("dry run", output)

    def test_command_is_idempotent(self):
        assignment = self._create_with_raw_title("<p>MCQ</p>")

        self._run()
        output = self._run()
        assignment.refresh_from_db()

        self.assertEqual(assignment.title, "MCQ")
        self.assertIn("repaired : 0 assignment(s)", output)

    def test_none_title_is_skipped_without_error(self):
        assignment = self._create_with_raw_title(None)

        output = self._run()
        assignment.refresh_from_db()

        self.assertIsNone(assignment.title)
        self.assertIn("repaired : 0 assignment(s)", output)
