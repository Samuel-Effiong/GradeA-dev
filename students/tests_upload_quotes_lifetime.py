"""Upload quote, owner rulings of 2026-11-20 (Q5, Q10, Q3).

Q5: the lifetime is a setting in minutes (default 10); the clock starts at
create and restarts when the quote becomes ready; a quote still measuring is
not expired; an unready quote older than the ceiling is purged; the purge runs
every 5 minutes; every reply and the 410 carry `expires_at` and
`lifetime_minutes`. Tests read the setting, never a hard-coded 10.

Q10: POST /submissions-credit-estimate/{id}/files adds files to an open quote.

Q3 = C: a teacher with nothing spendable gets the existing 402 before any
byte is stored, any row made or any measure asked for.
"""

import importlib
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from celery.schedules import crontab
from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from billing.tests.test_holds_entry import classroom
from students import kept_files as kept_files_module
from students.models import UploadQuote, UploadQuoteFile
from students.tests_upload_quotes_routes import (
    DETAIL_URL,
    LIST_URL,
    Base,
    data_of,
    pdf,
    png,
    run_the_store,
    text_file,
)

kept_files: Any = kept_files_module
conf: Any = settings
State = UploadQuoteFile.State


def minutes() -> int:
    return conf.UPLOAD_QUOTE_LIFETIME_MINUTES


def close(a, b, seconds=30):
    return abs((a - b).total_seconds()) <= seconds


class Settings(TestCase):
    def test_the_lifetime_is_in_minutes_and_the_hours_setting_is_gone(self):
        self.assertEqual(conf.UPLOAD_QUOTE_LIFETIME_MINUTES, 10)
        self.assertFalse(hasattr(settings, "UPLOAD_QUOTE_LIFETIME_HOURS"))
        self.assertEqual(conf.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES, 60)

    def test_the_purge_runs_every_five_minutes(self):
        entry = settings.CELERY_BEAT_SCHEDULE["purge-upload-quotes"]
        self.assertEqual(entry["task"], "students.tasks.purge_upload_quotes")
        self.assertEqual(entry["schedule"], crontab(minute="*/5"))
        self.assertNotIn("purge-upload-quotes-hourly", settings.CELERY_BEAT_SCHEDULE)


class Clock(Base):
    def test_create_sets_expiry_from_the_setting_and_replies_carry_it(self):
        response = self.create([pdf()])
        quote = data_of(response)
        self.assertEqual(quote["lifetime_minutes"], minutes())
        row = UploadQuote.objects.get()
        self.assertTrue(
            close(row.expires_at, timezone.now() + timedelta(minutes=minutes()))
        )
        self.assertEqual(data_of(self.get(row.pk))["lifetime_minutes"], minutes())
        self.client.force_authenticate(user=self.teacher)
        listed = data_of(self.client.get(reverse(LIST_URL)))
        self.assertEqual(listed[0]["lifetime_minutes"], minutes())
        self.assertIn("expires_at", listed[0])

    @override_settings(UPLOAD_QUOTE_LIFETIME_MINUTES=3)
    def test_a_different_setting_changes_the_clock(self):
        response = self.create([pdf()])
        self.assertEqual(data_of(response)["lifetime_minutes"], 3)
        row = UploadQuote.objects.get()
        self.assertTrue(close(row.expires_at, timezone.now() + timedelta(minutes=3)))

    def test_all_files_refused_is_ready_at_once_and_the_clock_restarts(self):
        UploadQuote.objects.all().delete()
        response = self.create([text_file("a.txt", b""), text_file("b.txt")])
        self.assertTrue(data_of(response)["ready"])
        row = UploadQuote.objects.get()
        self.assertTrue(
            close(row.expires_at, timezone.now() + timedelta(minutes=minutes()))
        )

    def test_the_last_measure_restarts_the_clock(self):
        from students.upload_quote_measure import do_measure

        self.create([pdf("a.pdf")])
        quote = UploadQuote.objects.get()
        UploadQuote.objects.filter(pk=quote.pk).update(
            expires_at=timezone.now() + timedelta(seconds=5)
        )
        do_measure(quote.files.get().pk)
        quote.refresh_from_db()
        self.assertTrue(
            close(quote.expires_at, timezone.now() + timedelta(minutes=minutes()))
        )

    def test_one_file_still_measuring_does_not_restart_the_clock(self):
        from students.upload_quote_measure import do_measure

        self.create([pdf("a.pdf"), pdf("b.pdf", pages=3)])
        quote = UploadQuote.objects.get()
        soon = timezone.now() + timedelta(seconds=5)
        UploadQuote.objects.filter(pk=quote.pk).update(expires_at=soon)
        do_measure(quote.files.order_by("position")[0].pk)
        quote.refresh_from_db()
        self.assertTrue(close(quote.expires_at, soon, seconds=1))


class Purge(Base):
    def run_purge(self):
        with self.captureOnCommitCallbacks(execute=True):
            return importlib.import_module("students.upload_quotes").purge_due()

    def quote_with(self, state):
        self.create([pdf()])
        quote = UploadQuote.objects.get()
        UploadQuoteFile.objects.filter(quote=quote).update(state=state)
        return quote

    def test_a_ready_quote_past_expiry_expires(self):
        quote = self.quote_with(State.MEASURED)
        UploadQuote.objects.filter(pk=quote.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        self.run_purge()
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.EXPIRED)

    def test_a_quote_still_measuring_past_expiry_is_not_expired(self):
        quote = self.quote_with(State.MEASURING)
        UploadQuote.objects.filter(pk=quote.pk).update(
            expires_at=timezone.now() - timedelta(minutes=5)
        )
        self.run_purge()
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.OPEN)

    def test_an_unready_quote_over_the_ceiling_is_purged(self):
        quote = self.quote_with(State.MEASURING)
        old = timezone.now() - timedelta(
            minutes=conf.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES + 1
        )
        UploadQuote.objects.filter(pk=quote.pk).update(created_at=old)
        self.run_purge()
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.EXPIRED)
        self.assertFalse(kept_files.MEMORY)


class AddFiles(Base):
    def add(self, quote_id, files, user=None):
        self.client.force_authenticate(user=user or self.teacher)
        with patch(
            "students.upload_quote_service.enqueue_measures"
        ) as self.enqueued, run_the_store(), self.captureOnCommitCallbacks(
            execute=True
        ):
            return self.client.post(
                reverse("upload-quote-files", args=[quote_id]),
                {"files": files},
                format="multipart",
            )

    def test_positions_continue_and_each_file_is_measured(self):
        quote_id = data_of(self.create([pdf("a.pdf"), pdf("b.pdf", pages=3)]))["id"]
        response = self.add(quote_id, [pdf("c.pdf", pages=4), png("d.png")])
        self.assertEqual(response.status_code, 201, response.content)
        body = data_of(response)
        self.assertEqual([f["position"] for f in body["files"]], [2, 3])
        self.assertEqual(body["total"], 4)
        self.assertEqual(body["lifetime_minutes"], minutes())
        self.assertEqual(self.enqueued.call_count, 1)

    def test_a_file_already_in_the_quote_is_not_stored_again(self):
        one = pdf("a.pdf")
        content = one.read()
        one.seek(0)
        quote_id = data_of(self.create([one]))["id"]
        before = len(kept_files.MEMORY)
        again = SimpleUploadedFile("again.pdf", content, content_type="application/pdf")
        response = self.add(quote_id, [again])
        entry = data_of(response)["files"][0]
        self.assertIsNotNone(entry["duplicate_of"])
        self.assertEqual(len(kept_files.MEMORY), before)

    def test_one_bad_file_does_not_fail_the_others(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        response = self.add(quote_id, [text_file("x.txt", b""), pdf("c.pdf", pages=3)])
        replied = [f["state"] for f in data_of(response)["files"]]
        self.assertEqual(replied, ["REFUSED", "STORING"])
        later = UploadQuoteFile.objects.filter(position__gte=1).order_by("position")
        self.assertEqual([r.state for r in later], ["REFUSED", "STORED"])

    @override_settings(UPLOAD_QUOTE_MAX_FILES=3)
    def test_over_the_file_limit_for_the_quote_is_a_400(self):
        quote_id = data_of(self.create([pdf("a.pdf"), pdf("b.pdf", pages=3)]))["id"]
        response = self.add(quote_id, [pdf("c.pdf", pages=4), png("d.png")])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(UploadQuote.objects.get().files.count(), 2)

    def test_a_closed_quote_is_refused(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        UploadQuote.objects.update(state=UploadQuote.State.CANCELLED)
        response = self.add(quote_id, [pdf("b.pdf", pages=3)])
        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "QUOTE_CLOSED"
        )

    def test_an_expired_quote_is_a_410_with_the_clock_in_the_params(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        UploadQuote.objects.update(state=UploadQuote.State.EXPIRED)
        response = self.add(quote_id, [pdf("b.pdf", pages=3)])
        self.assertEqual(response.status_code, 410)
        params = response.json()["error"]["field_errors"]["params"]
        self.assertEqual(params["lifetime_minutes"], minutes())
        self.assertIn("expires_at", params)

    @override_settings(UPLOAD_QUOTE_LIFETIME_MINUTES=7)
    def test_the_expired_sentence_says_the_configured_minutes(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        UploadQuote.objects.update(state=UploadQuote.State.EXPIRED)
        response = self.add(quote_id, [pdf("b.pdf", pages=3)])
        self.assertIn("more than 7 minutes", response.json()["message"])
        self.assertEqual(
            response.json()["error"]["field_errors"]["params"]["lifetime_minutes"], 7
        )

    def test_a_foreign_quote_is_the_house_404(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        response = self.add(quote_id, [pdf("b.pdf", pages=3)], user=self.other)
        self.assertEqual(response.status_code, 404)

    def test_a_request_with_no_files_is_a_400(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse("upload-quote-files", args=[quote_id]), {}, format="multipart"
        )
        self.assertEqual(response.status_code, 400)


class NothingSpendable(Base):
    """Q3 = C."""

    def setUp(self):
        super().setUp()
        self.poor, _, self.poor_course, self.poor_assignment, _ = classroom("poor", 0)

    def create_as_poor(self, files):
        self.client.force_authenticate(user=self.poor)
        with patch(
            "students.upload_quote_service.enqueue_measures"
        ) as self.enqueued, run_the_store(), self.captureOnCommitCallbacks(
            execute=True
        ):
            return self.client.post(
                reverse(LIST_URL),
                {
                    "kind": "answer_papers",
                    "assignment_id": str(self.poor_assignment.pk),
                    "files": files,
                },
                format="multipart",
            )

    def assert_nothing_happened(self, response):
        self.assertEqual(response.status_code, 402, response.content)
        body = response.json()
        self.assertEqual(body["error"]["field_errors"]["code"], "insufficient_credits")
        self.assertFalse(UploadQuote.objects.exists())
        self.assertFalse(kept_files.MEMORY)
        self.enqueued.assert_not_called()

    def test_zero_balance_gets_the_existing_402_and_nothing_is_stored(self):
        self.assert_nothing_happened(self.create_as_poor([pdf()]))

    @override_settings(BILLING_HOLDS_ENABLED=True)
    def test_a_balance_fully_held_by_running_work_gets_the_402(self):
        from billing.models import CreditWallet

        wallet = CreditWallet.objects.get(user=self.teacher)
        CreditWallet.objects.filter(pk=wallet.pk).update(
            reserved_credits=wallet.total_remaining_credits()
        )
        self.client.force_authenticate(user=self.teacher)
        with patch("students.upload_quote_service.enqueue_measures") as self.enqueued:
            response = self.client.post(
                reverse(LIST_URL),
                {
                    "kind": "answer_papers",
                    "assignment_id": str(self.assignment.pk),
                    "files": [pdf()],
                },
                format="multipart",
            )
        self.assert_nothing_happened(response)

    def test_one_spendable_credit_goes_through_to_the_measure(self):
        from billing.models import CreditWallet

        with patch.object(CreditWallet, "total_remaining_credits", return_value=1):
            response = self.create_as_poor([pdf()])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertTrue(UploadQuote.objects.exists())
        self.enqueued.assert_called_once()

    def test_adding_files_at_zero_is_the_same_402(self):
        quote_id = data_of(self.create([pdf("a.pdf")]))["id"]
        with patch("students.upload_quote_views.door_balance", return_value=0), patch(
            "students.upload_quote_service.enqueue_measures"
        ) as enq:
            self.client.force_authenticate(user=self.teacher)
            response = self.client.post(
                reverse("upload-quote-files", args=[quote_id]),
                {"files": [pdf("b.pdf", pages=3)]},
                format="multipart",
            )
        self.assertEqual(response.status_code, 402)
        enq.assert_not_called()
        self.assertEqual(UploadQuote.objects.get().files.count(), 1)

    def test_detail_url_name_is_unchanged(self):
        self.assertTrue(reverse(DETAIL_URL, args=["x"]))
