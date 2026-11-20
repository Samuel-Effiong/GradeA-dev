"""Upload quote, slice 6 (BE-B-03 doc guard): the notes name the constants the
figure depends on, so the page cannot drift from the code."""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ai_processor import call_estimates
from billing import estimates

NOTES = Path(settings.BASE_DIR) / "docs" / "evidence" / "upload-quotes" / "NOTES.md"


class NotesNameTheConstants(SimpleTestCase):
    def setUp(self):
        self.text = NOTES.read_text()

    def test_the_headroom_the_divisor_and_the_tolerance_date_are_named(self):
        self.assertIn("CALL_HEADROOM", self.text)
        self.assertIn("UPLOAD_DIVISOR_FALLBACK", self.text)
        self.assertIn(estimates.CALL_HEADROOM_SINCE.strftime("%Y-%m-%d"), self.text)
        self.assertIn(str(estimates.UPLOAD_DIVISOR_FALLBACK), self.text)
        self.assertTrue(call_estimates.CALL_HEADROOM)

    def test_the_settings_and_the_frontend_facts_are_named(self):
        for name in (
            "UPLOAD_QUOTES_ENABLED",
            "UPLOAD_QUOTE_LIFETIME_MINUTES",
            "UPLOAD_QUOTE_UNREADY_CEILING_MINUTES",
            "Idempotency-Key",
            "lifetime_minutes",
            "expires_at",
            "BE-B-03",
            "BE-B-05",
        ):
            with self.subTest(name=name):
                self.assertIn(name, self.text)
