"""Upload quote, fixes from the local end-to-end run
(docs/evidence/upload-quotes/E2E.md).

F1: more files in one request than Django's DATA_UPLOAD_MAX_NUMBER_FILES made
Django raise TooManyFilesSent while the body was read, before any view check,
and the route answered 500. Every count over the limit now gets the
owner-approved "You can upload up to {limit} files at a time." 400, on the
quote routes and on any other multipart route.

F3: a ready quote past expires_at read OPEN until the purge (every 5 minutes)
marked it EXPIRED. It now reads EXPIRED at once, by the rule confirm's 410
and the purge already use (ready and past expires_at; a quote still
measuring is not expired by its clock).

F4: the quote routes' audit events have the approved target label
"Upload quote".
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from audit.serializers import _TARGET_LABEL
from students import kept_files as kept_files_module
from students.models import UploadQuote, UploadQuoteFile
from students.tests_upload_quotes_routes import LIST_URL, Base, data_of, pdf

kept_files: Any = kept_files_module

TOO_MANY = (
    "You can upload up to {limit} files at a time. Remove some files, then try again."
)


def tiny(count):
    return [
        SimpleUploadedFile(f"f{i}.txt", b"x", content_type="text/plain")
        for i in range(count)
    ]


class TooManyFilesInOneRequest(Base):
    def assert_too_many(self, response, limit):
        self.assertEqual(response.status_code, 400, response.content[:500])
        self.assertIn(TOO_MANY.format(limit=limit), response.content.decode())

    def test_one_over_the_default_limit_on_create(self):
        limit = settings.UPLOAD_QUOTE_MAX_FILES
        response = self.create(tiny(limit + 1))
        self.assert_too_many(response, limit)
        self.assertFalse(UploadQuote.objects.exists())
        self.assertEqual(kept_files.MEMORY, {})

    def test_thousands_of_files_on_create(self):
        response = self.create(tiny(5000))
        self.assert_too_many(response, settings.UPLOAD_QUOTE_MAX_FILES)
        self.assertFalse(UploadQuote.objects.exists())

    def test_one_over_the_default_limit_on_add(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        stored = dict(kept_files.MEMORY)
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse("upload-quote-files", args=[quote_id]),
            {"files": tiny(settings.UPLOAD_QUOTE_MAX_FILES + 1)},
            format="multipart",
        )
        self.assert_too_many(response, settings.UPLOAD_QUOTE_MAX_FILES)
        self.assertEqual(UploadQuoteFile.objects.filter(quote_id=quote_id).count(), 1)
        self.assertEqual(kept_files.MEMORY, stored)

    def test_another_multipart_route_gets_the_same_400(self):
        # The batch upload route: same exception while its body is read.
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse(
                "student-submission-batch-upload",
                kwargs={"assignment_id": self.assignment.pk},
            ),
            {"answers": tiny(settings.DATA_UPLOAD_MAX_NUMBER_FILES + 1)},
            format="multipart",
        )
        self.assert_too_many(response, settings.DATA_UPLOAD_MAX_NUMBER_FILES)


class FileCountLimits(SimpleTestCase):
    def test_django_never_refuses_a_count_the_quote_allows(self):
        # Otherwise a raised UPLOAD_QUOTE_MAX_FILES would be cut short by
        # Django's own count, under the quote's larger number.
        self.assertGreaterEqual(
            settings.DATA_UPLOAD_MAX_NUMBER_FILES, settings.UPLOAD_QUOTE_MAX_FILES
        )


class PastExpiryReadsExpired(Base):
    def ready_quote_past_expiry(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        UploadQuoteFile.objects.filter(quote_id=quote_id).update(
            state=UploadQuoteFile.State.MEASURED, pages=2
        )
        UploadQuote.objects.filter(pk=quote_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        return quote_id

    def test_get_reads_expired_at_once(self):
        quote_id = self.ready_quote_past_expiry()
        response = self.get(quote_id)
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(data_of(response)["state"], "EXPIRED")

    def test_the_open_list_leaves_it_out(self):
        quote_id = self.ready_quote_past_expiry()
        self.client.force_authenticate(user=self.teacher)
        rows = data_of(self.client.get(reverse(LIST_URL)))
        self.assertNotIn(quote_id, [r["id"] for r in rows])

    def test_add_is_the_410(self):
        quote_id = self.ready_quote_past_expiry()
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse("upload-quote-files", args=[quote_id]),
            {"files": [pdf("b.pdf")]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 410, response.content)
        self.assertIn("QUOTE_EXPIRED", response.content.decode())

    def test_a_quote_still_measuring_is_not_expired_by_its_clock(self):
        # Same as confirm (409 not ready) and the purge (never expires it).
        quote_id = data_of(self.create([pdf()]))["id"]
        UploadQuote.objects.filter(pk=quote_id).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.assertEqual(data_of(self.get(quote_id))["state"], "OPEN")
        self.client.force_authenticate(user=self.teacher)
        rows = data_of(self.client.get(reverse(LIST_URL)))
        self.assertIn(quote_id, [r["id"] for r in rows])

    def test_a_ready_quote_before_expiry_is_open(self):
        quote_id = data_of(self.create([pdf()]))["id"]
        UploadQuoteFile.objects.filter(quote_id=quote_id).update(
            state=UploadQuoteFile.State.MEASURED, pages=2
        )
        self.assertEqual(data_of(self.get(quote_id))["state"], "OPEN")


class RouteTargetLabel(SimpleTestCase):
    def test_each_quote_route_has_the_upload_quote_label(self):
        for view in (
            "UploadQuoteCollection",
            "UploadQuoteDetail",
            "UploadQuoteFiles",
            "UploadQuoteConfirm",
        ):
            with self.subTest(view=view):
                self.assertEqual(_TARGET_LABEL.get(view), "Upload quote")


class ExpiryWordingSingular(SimpleTestCase):
    """F2 (owner Q19 A, 2026-11-23): "1 minute" when the lifetime is 1, every
    other value "{n} minutes"."""

    def message(self, minutes):
        from AutoGrader.reason_codes import CodedError

        return CodedError(
            "QUOTE_EXPIRED",
            params={
                "lifetime_minutes": minutes,
                "expires_at": "2026-11-23T00:00:00+00:00",
            },
        ).message

    def test_one_minute_is_singular(self):
        self.assertEqual(
            self.message(1),
            "This upload waited more than 1 minute, so we removed the files.",
        )

    def test_other_values_stay_plural(self):
        for minutes in (2, 10, 60):
            with self.subTest(minutes=minutes):
                self.assertEqual(
                    self.message(minutes),
                    f"This upload waited more than {minutes} minutes, so we"
                    " removed the files.",
                )
