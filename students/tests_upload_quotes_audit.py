"""Upload quote, slice 6 (plan rev 3 section 5.7): a quote-ended audit event
for cancel and for expiry (ids and counts only, never a file name), and the
audit registry knows the new action."""

import importlib
from datetime import timedelta
from typing import Any

from django.test import SimpleTestCase
from django.urls import reverse
from django.utils import timezone

from audit.enums import AuditAction
from audit.metadata import METADATA_ALLOWLIST
from audit.models import AuditEvent
from students.models import UploadQuote, UploadQuoteFile
from students.tests_upload_quotes_confirm import Base, make_quote


class Registry(SimpleTestCase):
    def test_the_action_and_its_metadata_keys_are_registered(self):
        self.assertIn("UPLOAD_QUOTE_ENDED", AuditAction.values)
        keys = METADATA_ALLOWLIST[AuditAction("UPLOAD_QUOTE_ENDED")]
        self.assertEqual(set(keys), {"quote_id", "reason", "file_count"})
        self.assertIn("quote_id", METADATA_ALLOWLIST[AuditAction.SUBMISSION_UPLOAD])


class Ended(Base):
    def events(self):
        return list(AuditEvent.objects.filter(action="UPLOAD_QUOTE_ENDED"))

    def test_cancel_writes_one_event_and_a_repeat_none(self):
        quote = make_quote(self.teacher, self.assignment)
        self.client.force_authenticate(user=self.teacher)
        url = reverse("upload-quote-detail", args=[quote.pk])
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(url)
            self.client.delete(url)
        (event,) = self.events()
        self.assertEqual(event.metadata["reason"], "cancelled")
        self.assertEqual(event.metadata["quote_id"], str(quote.pk))
        self.assertEqual(event.metadata["file_count"], 2)
        self.assertNotIn("paper0", str(event.metadata))

    def test_expiry_writes_one_event_per_expired_quote(self):
        purge: Any = importlib.import_module("students.upload_quotes")
        old = make_quote(self.teacher, self.assignment)
        fresh = make_quote(self.teacher, self.assignment)
        UploadQuote.objects.filter(pk=old.pk).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            purge.purge_due()
            purge.purge_due()
        (event,) = self.events()
        self.assertEqual(event.metadata["reason"], "expired")
        self.assertEqual(event.metadata["quote_id"], str(old.pk))
        fresh.refresh_from_db()
        self.assertEqual(fresh.state, UploadQuote.State.OPEN)
        self.assertEqual(UploadQuoteFile.objects.filter(quote=old).count(), 2)

    def test_a_confirm_is_not_an_ended_event(self):
        quote = make_quote(self.teacher, self.assignment)
        self.confirm(quote)
        self.assertEqual(self.events(), [])
