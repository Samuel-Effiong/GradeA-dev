"""
Teacher multi-file upload at scale: a large, mixed batch, and many identical
copies of it racing, still charge each good file exactly once.

tests_upload_batch_billing.py proves the per-file rules on 3-file batches
and a 4-way race. These tests push the same rules to a size where lock
contention, connection pressure and ordering effects would show: a 30-file
batch with unreadable files spread from first to last position, then six
concurrent replays of it. Billing is real (wallet, buckets, usage log,
refunds on PostgreSQL); only the model provider call is replaced, and each
good file is charged a distinct amount so every charge traces to its file.
"""

import threading
import time

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from assignments.file_uploads import IN_PROGRESS_MESSAGE
from assignments.models import Assignment, AssignmentUploadFingerprint
from assignments.tests_upload_batch_billing import (
    BatchUploadBillingFixture,
    ai_response,
    charges,
    png,
)

GOOD_COUNT = 24
BAD_POSITIONS = (0, 5, 11, 17, 23, 29)  # first, spread through, last
WIDTHS = [300 + index for index in range(GOOD_COUNT)]
TOKENS = {width: width * 7 for width in WIDTHS}


def batch():
    """30 (name, bytes, content_type) entries: 24 good PNGs of distinct
    widths and 6 unreadable files, half labelled PDF and half image."""
    widths = iter(WIDTHS)
    entries = []
    for position in range(GOOD_COUNT + len(BAD_POSITIONS)):
        if position in BAD_POSITIONS:
            if position % 2:
                entries.append((f"bad-{position}.pdf", b"not a pdf", "application/pdf"))
            else:
                entries.append((f"bad-{position}.png", b"not an image", "image/png"))
        else:
            width = next(widths)
            entries.append((f"good-{width}.png", png(width), "image/png"))
    return entries


class LargeBatchUploadTest(BatchUploadBillingFixture):
    def setUp(self):
        super().setUp()
        # The fixture's provider stand-in reads each file's width back out of
        # the image it is sent, records it, and routes it to `behaviour`.
        self.delay = 0.0
        self.provider.behaviour.update({width: self._answer for width in WIDTHS})

    def _answer(self, width):
        if self.delay:
            time.sleep(self.delay)
        return ai_response(TOKENS[width])

    def _post(self, entries):
        client = APIClient()
        client.force_authenticate(user=self.teacher)
        return client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.course.id),
                "assignments": [
                    SimpleUploadedFile(name, data, content_type=content_type)
                    for name, data, content_type in entries
                ],
            },
            format="multipart",
        )

    def _assert_every_good_file_charged_exactly_once(self):
        self.assertEqual(
            sorted(self.provider.calls),
            WIDTHS,
            "a good file was extracted more or fewer than once",
        )
        self.assertEqual(charges(self.teacher), (sorted(TOKENS.values()), []))
        self.assertEqual(
            Assignment.objects.filter(course=self.course).count(), GOOD_COUNT
        )
        self.assertEqual(
            AssignmentUploadFingerprint.objects.filter(
                course=self.course,
                status=AssignmentUploadFingerprint.Status.COMPLETED,
            ).count(),
            GOOD_COUNT,
        )
        self.assert_no_open_claims()

    def test_a_large_mixed_batch_charges_each_good_file_exactly_once(self):
        entries = batch()
        started = time.monotonic()

        response = self._post(entries)

        elapsed = time.monotonic() - started
        print(f"\n[scale] 30-file batch (24 good, 6 bad) took {elapsed:.2f}s")
        self.assertEqual(
            response.status_code, status.HTTP_207_MULTI_STATUS, response.content[:300]
        )
        successful, failed = self.outcomes(response)
        self.assertEqual(len(successful), GOOD_COUNT)
        self.assertTrue(all(entry["already_uploaded"] is False for entry in successful))
        self.assertEqual(
            sorted(entry["file_name"] for entry in failed),
            sorted(entries[position][0] for position in BAD_POSITIONS),
        )
        for entry in failed:
            self.assertTrue(entry["error"], entry)
            self.assertNotEqual(entry["error"], IN_PROGRESS_MESSAGE)
        self._assert_every_good_file_charged_exactly_once()

    def test_six_concurrent_replays_of_a_large_batch_extract_each_file_once(self):
        entries = batch()
        self.delay = 0.05  # widens the windows in which requests overlap
        replicas = 6
        gate = threading.Barrier(replicas)
        responses = []
        errors = []
        lock = threading.Lock()

        def run():
            try:
                gate.wait(60)
                response = self._post(entries)
                with lock:
                    responses.append(response)
            except Exception as exc:  # surfaced by the assertion below
                with lock:
                    errors.append(exc)
            finally:
                connection.close()

        started = time.monotonic()
        threads = [threading.Thread(target=run) for _ in range(replicas)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(300)
        elapsed = time.monotonic() - started
        print(
            f"\n[scale] {replicas} concurrent 30-file batches took {elapsed:.2f}s; "
            f"provider calls={len(self.provider.calls)}"
        )

        self.assertEqual(errors, [])
        self.assertEqual(len(responses), replicas)
        bad_names = {entries[position][0] for position in BAD_POSITIONS}
        for response in responses:
            self.assertIn(
                response.status_code,
                (status.HTTP_201_CREATED, status.HTTP_207_MULTI_STATUS),
                response.content[:300],
            )
            successful, failed = self.outcomes(response)
            self.assertEqual(len(successful) + len(failed), len(entries))
            for entry in failed:
                if entry["file_name"] not in bad_names:
                    # A good file may only be refused because another
                    # replica was extracting it at that moment.
                    self.assertEqual(entry["error"], IN_PROGRESS_MESSAGE)

        self._assert_every_good_file_charged_exactly_once()

        # Once the race has settled, one more replay returns every good file
        # as already uploaded and charges nothing.
        final = self._post(entries)
        successful, failed = self.outcomes(final)
        self.assertEqual(len(successful), GOOD_COUNT)
        self.assertTrue(all(entry["already_uploaded"] is True for entry in successful))
        self.assertEqual(
            sorted(entry["file_name"] for entry in failed), sorted(bad_names)
        )
        self._assert_every_good_file_charged_exactly_once()
