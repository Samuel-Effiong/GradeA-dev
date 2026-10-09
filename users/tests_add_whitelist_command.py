"""
`manage.py add_whitelist` - the only code in users/ with 0% coverage.

It writes real rows to BetaWhitelist from operator input, so the things
worth pinning are the ones an operator would be bitten by: that re-running
it is safe (get_or_create, not create), that addresses are normalised to
lowercase before storage, and that a bad --file path reports an error
instead of a traceback.
"""

import tempfile
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from users.models import BetaWhitelist


class AddWhitelistCommandTests(TestCase):
    def run_command(self, *args, **kwargs):
        out, err = StringIO(), StringIO()
        call_command("add_whitelist", *args, stdout=out, stderr=err, **kwargs)
        return out.getvalue(), err.getvalue()

    def test_adds_emails_passed_as_arguments(self):
        out, _ = self.run_command("first@example.com", "second@example.com")

        self.assertTrue(
            BetaWhitelist.objects.filter(email="first@example.com").exists()
        )
        self.assertTrue(
            BetaWhitelist.objects.filter(email="second@example.com").exists()
        )
        self.assertIn("Added: 2", out)

    def test_new_entries_are_active(self):
        self.run_command("active@example.com")

        self.assertTrue(BetaWhitelist.objects.get(email="active@example.com").is_active)

    def test_emails_are_normalised_to_lowercase(self):
        """Everything else in the stack looks these up lowercased."""
        self.run_command("MiXeD.Case@Example.COM")

        self.assertTrue(
            BetaWhitelist.objects.filter(email="mixed.case@example.com").exists()
        )

    def test_rerunning_is_idempotent_and_reports_the_skip(self):
        self.run_command("repeat@example.com")
        out, _ = self.run_command("repeat@example.com")

        self.assertEqual(
            BetaWhitelist.objects.filter(email="repeat@example.com").count(), 1
        )
        self.assertIn("Already exists", out)
        self.assertIn("Skipped: 1", out)

    def test_reads_one_email_per_line_from_a_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emails.txt"
            path.write_text("from.file.a@example.com\nfrom.file.b@example.com\n")

            out, _ = self.run_command(file=str(path))

        self.assertTrue(
            BetaWhitelist.objects.filter(email="from.file.a@example.com").exists()
        )
        self.assertTrue(
            BetaWhitelist.objects.filter(email="from.file.b@example.com").exists()
        )
        self.assertIn("Added: 2", out)

    def test_blank_lines_in_a_file_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emails.txt"
            path.write_text("\n\nonly.one@example.com\n\n   \n")

            self.run_command(file=str(path))

        self.assertEqual(BetaWhitelist.objects.count(), 1)

    def test_file_and_arguments_combine(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emails.txt"
            path.write_text("in.file@example.com\n")

            self.run_command("in.args@example.com", file=str(path))

        self.assertTrue(
            BetaWhitelist.objects.filter(email="in.file@example.com").exists()
        )
        self.assertTrue(
            BetaWhitelist.objects.filter(email="in.args@example.com").exists()
        )

    def test_a_missing_file_reports_an_error_and_writes_nothing(self):
        _, err = self.run_command(file="/nonexistent/path/emails.txt")

        self.assertIn("File not found", err)
        self.assertFalse(BetaWhitelist.objects.exists())

    def test_no_input_is_a_warning_not_a_crash(self):
        out, _ = self.run_command()

        self.assertIn("Nothing to do", out)
        self.assertFalse(BetaWhitelist.objects.exists())
