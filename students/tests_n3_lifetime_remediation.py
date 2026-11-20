"""N3 (owner-approved wording, 2026-11-19): an upload item whose kept file the
lifetime purge removed reads "Upload the file again." as its remediation,
in the retry's 409 and in session-results alike. session-results also
carries resolution "replace_file", matching the 409's params.resolution.
The main message (item_retry.deleted_after_days) is unchanged, and so is
every other item."""

from django.test import TestCase
from django.utils import timezone

from students.item_retry import deleted_after_days
from students.models import KeptUpload
from students.tests_parked_files_kept import ParkedFixture

UPLOAD_AGAIN = "Upload the file again."
OLD_REMEDIATION = "Fix what its failure message describes first, then try again."


class LifetimePurgedRemediation(ParkedFixture, TestCase):
    def setUp(self):
        self.build(10_000, tag="n3")

    def purged_item(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        KeptUpload.objects.filter(processing_task=parked).update(
            purged_at=timezone.now(), purge_reason=KeptUpload.PurgeReason.LIFETIME
        )
        return data, parked

    def entry(self, data, item):
        return next(
            e
            for e in self.session_results(data["session_id"])["failure_list"]
            if e["item_id"] == str(item.id)
        )

    def test_the_retry_409_says_upload_the_file_again(self):
        _, parked = self.purged_item()
        response = self.retry(parked)
        self.assertEqual(response.status_code, 409)
        body = response.json()["error"]["field_errors"]
        self.assertEqual(body["error"], deleted_after_days())
        self.assertEqual(body["remediation"], UPLOAD_AGAIN)
        self.assertEqual(body["params"]["resolution"], "replace_file")
        self.assertEqual(body["reason_code"], "NOT_RETRYABLE")

    def test_session_results_says_the_same_with_the_resolution(self):
        data, parked = self.purged_item()
        entry = self.entry(data, parked)
        self.assertEqual(entry["message"], deleted_after_days())
        self.assertEqual(entry["remediation"], UPLOAD_AGAIN)
        self.assertEqual(entry["resolution"], "replace_file")
        self.assertIs(entry["retryable"], False)

    def test_a_still_kept_parked_item_has_no_resolution(self):
        data = self.upload(1_000, 20_000)
        parked = self.items(data)[1]
        entry = self.entry(data, parked)
        self.assertIsNone(entry["resolution"])
        self.assertNotEqual(entry["remediation"], UPLOAD_AGAIN)
        self.assertIs(entry["retryable"], True)

    def test_a_not_saved_upload_keeps_the_general_remediation(self):
        # No kept row at all (#16b): its own why, the catalogue remediation.
        data, parked = self.purged_item()
        KeptUpload.objects.filter(processing_task=parked).delete()
        body = self.retry(parked).json()["error"]["field_errors"]
        self.assertEqual(body["remediation"], OLD_REMEDIATION)
