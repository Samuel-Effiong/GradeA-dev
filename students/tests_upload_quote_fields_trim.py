"""Estimate fields trim (owner, 2026-11-25): the upload quote's estimate,
in the GET reply and in the create's reply, carries no source, sample_size
or shape_basis; each file's own figure is "estimated_cost" (it was "high").

    python manage.py test students.tests_upload_quote_fields_trim
"""

from django.core.files.uploadedfile import SimpleUploadedFile

from students import tests_upload_quotes_routes as routes
from students.models import UploadQuoteFile

HIDDEN = {"source", "sample_size", "shape_basis"}
SHOWN = {
    "p50",
    "p90",
    "verdict",
    "spendable",
    "projected_remaining_p50",
    "projected_remaining_p90",
    "n",
}
KEY = "22222222-2222-2222-2222-222222222222"


class QuoteEstimateTrim(routes.Base):
    def ready_quote(self):
        """A quote made by POST, its file then measured, so it is ready."""
        first = self.create([routes.pdf()], key=KEY)
        self.assertEqual(first.status_code, 201, first.content)
        UploadQuoteFile.objects.update(
            state=UploadQuoteFile.State.MEASURED,
            pages=2,
            page_tokens=[765, 765],
            readable=True,
        )
        return routes.data_of(first)["id"]

    def assert_trimmed(self, body):
        self.assertTrue(body["ready"])
        self.assertEqual(set(body["estimate"]), SHOWN)
        self.assertFalse(HIDDEN & set(body["estimate"]))

    def test_get_estimate_has_no_hidden_field(self):
        response = self.get(self.ready_quote())
        self.assertEqual(response.status_code, 200, response.content)
        self.assert_trimmed(routes.data_of(response))

    def test_create_reply_estimate_has_no_hidden_field(self):
        self.ready_quote()
        # The same key returns the quote, now ready, through POST.
        second = self.create([routes.pdf()], key=KEY)
        self.assertEqual(second.status_code, 200, second.content)
        self.assert_trimmed(routes.data_of(second))

    def test_get_names_each_files_figure_estimated_cost(self):
        body = routes.data_of(self.get(self.ready_quote()))
        (entry,) = body["files"]
        self.assertNotIn("high", entry)
        self.assertIsInstance(entry["estimated_cost"], int)
        self.assertGreater(entry["estimated_cost"], 0)

    def test_an_unmeasured_file_has_a_null_estimated_cost(self):
        body = routes.data_of(self.create([routes.pdf()]))
        (entry,) = body["files"]
        self.assertNotIn("high", entry)
        self.assertIsNone(entry["estimated_cost"])

    def test_a_duplicate_entry_carries_estimated_cost_too(self):
        one = routes.pdf("a.pdf")
        two = SimpleUploadedFile("copy.pdf", one.read(), "application/pdf")
        one.seek(0)
        first, second = routes.data_of(self.create([one, two]))["files"]
        self.assertEqual(second["duplicate_of"], first["id"])
        for entry in (first, second):
            self.assertNotIn("high", entry)
            self.assertIn("estimated_cost", entry)
