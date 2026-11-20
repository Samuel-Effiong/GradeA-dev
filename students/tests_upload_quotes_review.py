"""Independent review of the upload quote (the Fixer's findings, SM -72
2026-11-20), one test group per finding. Plants adapted from the reviewer's."""

import importlib
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from AutoGrader.reason_codes import CodedError
from students import kept_files as kept_files_module
from students import upload_quote_service as service
from students.models import UploadQuote, UploadQuoteFile
from students.tests_upload_quotes_confirm import Base, make_quote

kept_files: Any = kept_files_module
State = UploadQuoteFile.State
measure: Any = importlib.import_module("students.upload_quote_measure")
quotes: Any = importlib.import_module("students.upload_quotes")


def upload(name="a.pdf", data=b"%PDF-1.4 same bytes" * 10):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class F1AddRacesTheEnd(Base):
    """Finding 1: an add that loses a race with cancel, expiry or confirm never
    answers 201 and never leaves a stored file nobody will delete."""

    def test_a_cancel_during_the_store_closes_the_add_and_deletes_the_bytes(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        real = kept_files.store

        def store_then_cancel(content, key=None):
            put = real(content, key=key)
            UploadQuote.objects.filter(pk=quote.pk).update(
                state=UploadQuote.State.CANCELLED
            )
            return put

        before = set(kept_files.MEMORY)
        with patch.object(
            service.kept_files, "store", side_effect=store_then_cancel
        ), self.captureOnCommitCallbacks(execute=True):
            with self.assertRaises(CodedError) as caught:
                service.add_to_quote(quote, [upload()])
        self.assertEqual(caught.exception.reason_code, "QUOTE_CLOSED")
        self.assertEqual(set(kept_files.MEMORY), before)

    def test_an_add_to_a_confirmed_quote_writes_no_row(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        UploadQuote.objects.filter(pk=quote.pk).update(
            state=UploadQuote.State.CONFIRMED
        )
        with self.assertRaises(CodedError):
            service.add_to_quote(quote, [upload()])
        self.assertEqual(quote.files.count(), 1)

    def test_the_purge_clears_a_file_an_ended_quote_still_holds(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        row = quote.files.get()
        key = row.storage_key
        UploadQuote.objects.filter(pk=quote.pk).update(
            state=UploadQuote.State.CONFIRMED, confirmed_at=timezone.now()
        )
        quotes.purge_due()
        row.refresh_from_db()
        self.assertNotIn(key, kept_files.MEMORY)
        self.assertIsNone(row.storage_key)
        self.assertIsNotNone(row.purged_at)


class F2ReaddAfterNotKept(Base):
    """Finding 2: 'Add that file again' works."""

    def test_adding_a_not_kept_file_again_stores_and_measures_it(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        with patch.object(
            service.kept_files,
            "store",
            side_effect=kept_files.KeptFileUnavailable("down"),
        ):
            first = service.add_to_quote(quote, [upload()])
        self.assertEqual(first[0].state, State.NOT_KEPT)
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            second = service.add_to_quote(quote, [upload()])
        entry = second[0]
        self.assertFalse(isinstance(entry, tuple), "treated as a duplicate")
        self.assertEqual(entry.state, State.STORED)
        self.assertEqual(len(callbacks), 1)  # the measure is asked for


class F3DropEndedQuote(Base):
    """Finding 3: an ended quote with a refused file is still dropped."""

    def ended(self, states):
        quote = make_quote(self.teacher, self.assignment, pages=(2, 3), states=states)
        UploadQuote.objects.filter(pk=quote.pk).update(
            state=UploadQuote.State.EXPIRED,
            closed_at=timezone.now() - timedelta(days=8),
        )
        return quote

    def test_control_all_stored_is_dropped(self):
        quote = self.ended(None)
        quotes.purge_due()
        self.assertFalse(UploadQuote.objects.filter(pk=quote.pk).exists())

    def test_a_refused_file_does_not_keep_the_row_forever(self):
        quote = self.ended({1: State.REFUSED})
        quotes.purge_due()
        quotes.purge_due()
        self.assertFalse(UploadQuote.objects.filter(pk=quote.pk).exists())

    def test_a_file_still_stored_keeps_the_row(self):
        quote = make_quote(
            self.teacher, self.assignment, pages=(2, 3), states={1: State.REFUSED}
        )
        UploadQuote.objects.filter(pk=quote.pk).update(
            state=UploadQuote.State.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=8),
        )
        with patch.object(
            kept_files, "delete", side_effect=kept_files.KeptFileUnavailable("down")
        ):
            quotes.purge_due()
        self.assertTrue(UploadQuote.objects.filter(pk=quote.pk).exists())


class F4MeasureNotKept(Base):
    """Finding 4: a file the measure gives up on does not keep its asset."""

    def test_a_checksum_mismatch_deletes_the_asset_and_nulls_the_key(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        stored = kept_files.store(b"real bytes")
        row = UploadQuoteFile.objects.create(
            quote=quote,
            position=1,
            state=State.STORED,
            storage_key=stored.storage_key,
            original_name="x.pdf",
            content_type="application/pdf",
            size_bytes=stored.size_bytes,
            sha256="0" * 64,
        )
        measure._measure(row.pk)
        row.refresh_from_db()
        self.assertEqual(row.state, State.NOT_KEPT)
        self.assertIsNone(row.storage_key)
        self.assertNotIn(stored.storage_key, kept_files.MEMORY)


class F5LateMeasure(Base):
    """Finding 5: a late or duplicate measure does not restart the clock."""

    def test_a_measure_of_a_settled_file_leaves_the_expiry_alone(self):
        quote = make_quote(self.teacher, self.assignment, pages=(2,))
        soon = timezone.now() + timedelta(minutes=1)
        UploadQuote.objects.filter(pk=quote.pk).update(expires_at=soon)
        measure.do_measure(str(quote.files.get().pk))
        quote.refresh_from_db()
        self.assertEqual(quote.expires_at, soon)
