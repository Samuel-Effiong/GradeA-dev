"""Upload quote step 2 (owner, 2026-11-26): the create and add-files reply at
once; the bytes wait in Postgres (UploadQuoteStagedFile) until the store task
sends them to the file storage.

The owner's main ask is that a file's bytes are never changed on the way:
intake -> database -> storage -> what the measure reads. These tests run on
the real test database (nothing about the database is mocked): the staged
bytes are read back from Postgres, and the storage is the memory backend or
a fake Cloudinary whose reply the store task checks.
"""

import hashlib
from datetime import timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from assignments.models import Assignment
from billing.tests.test_holds_entry import classroom
from students import quote_store_pool
from students import upload_quote_measure as measure
from students import upload_quote_service as service
from students import upload_quotes
from students.models import UploadQuote, UploadQuoteFile, UploadQuoteStagedFile
from students.tasks import store_quote_files
from students.tests_upload_quotes_routes import (
    LIST_URL,
    Base,
    data_of,
    kept_files,
    pdf,
    png,
)
from students.tests_upload_quotes_store_pool import as_upload, payloads, sha

State = UploadQuoteFile.State


def md5(data):
    return hashlib.md5(data).hexdigest()  # nosec B324 - Cloudinary's etag


class FakeCloud:
    """Cloudinary's upload and destroy, in memory, with a reply the test can
    get wrong on purpose."""

    def __init__(self, reply=None):
        self.assets: dict[str, bytes] = {}
        self.destroyed: list[str] = []
        self.reply = reply

    def upload(self, stream, public_id=None, overwrite=None, **_kwargs):
        """As Cloudinary answers (Sonnet worker's live check, 2026-11-26): an
        existing key with overwrite=False raises nothing and replies with the
        STORED asset's bytes and etag, existing=True."""
        data = stream.getvalue()
        existing = public_id in self.assets
        if not existing or overwrite:
            self.assets[public_id] = data
        if self.reply is not None:
            return self.reply(data)
        kept = self.assets[public_id]
        return {
            "bytes": len(kept),
            "etag": md5(kept),
            "public_id": public_id,
            "existing": existing and not overwrite,
        }

    def destroy(self, public_id, **_kwargs):
        self.destroyed.append(public_id)
        found = self.assets.pop(public_id, None)
        return {"result": "ok" if found is not None else "not found"}

    def patches(self):
        return (
            patch.object(kept_files, "_memory", return_value=False),
            patch("students.kept_files.cloudinary.uploader.upload", self.upload),
            patch("students.kept_files.cloudinary.uploader.destroy", self.destroy),
        )


class Staged(Base):
    """A create whose store task is held back: the test runs it."""

    def create_staged(self, files, key=None):
        self.client.force_authenticate(user=self.teacher)
        body: dict[str, Any] = {
            "kind": "answer_papers",
            "assignment_id": str(self.assignment.pk),
            "files": files,
        }
        headers = {"HTTP_IDEMPOTENCY_KEY": key} if key else {}
        with patch(
            "students.upload_quote_service.enqueue_store"
        ) as self.store_asked, self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse(LIST_URL), body, format="multipart", **headers
            )
        return response

    def run_store(self, quote_id, final=False):
        ids = list(
            UploadQuoteFile.objects.filter(
                quote_id=quote_id, state=State.STORING
            ).values_list("pk", flat=True)
        )
        with patch(
            "students.upload_quote_service.enqueue_measures"
        ) as self.measure_asked, self.captureOnCommitCallbacks(execute=True):
            return service.store_staged(quote_id, ids, final=final)

    def rows(self, quote_id):
        return list(
            UploadQuoteFile.objects.filter(quote_id=quote_id).order_by("position")
        )


class SameBytesAllTheWay(Staged):
    def test_intake_database_storage_and_measure_see_the_same_bytes(self):
        sent = payloads()
        response = self.create_staged([as_upload(n, d) for n, d in sent])
        self.assertEqual(response.status_code, 201, response.content)
        quote_id = data_of(response)["id"]
        self.assertEqual({f["state"] for f in data_of(response)["files"]}, {"STORING"})
        rows = self.rows(quote_id)

        # The database: the staged bytes read back from Postgres.
        for row, (name, data) in zip(rows, sent, strict=True):
            with self.subTest(at="database", file=name):
                staged = UploadQuoteStagedFile.objects.get(file_id=row.pk)
                self.assertEqual(bytes(staged.content), data)
                self.assertEqual(staged.sha256, sha(data))
                self.assertEqual(staged.size_bytes, len(data))
                self.assertEqual(row.sha256, sha(data))

        # The storage: what kept_files.store received.
        received: dict[Any, str] = {}
        real = kept_files.store

        def spy(content, key=None):
            received[key] = sha(content)
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=spy):
            self.assertEqual(self.run_store(quote_id), [])

        # The measure: what it reads back with kept_files.load.
        loaded: dict[Any, str] = {}
        real_load = kept_files.load

        def load_spy(key):
            data = real_load(key)
            loaded[key] = sha(data)
            return data

        with patch.object(kept_files, "load", side_effect=load_spy):
            for row in rows:
                measure.do_measure(row.pk)

        for row, (name, data) in zip(self.rows(quote_id), sent, strict=True):
            with self.subTest(at="storage and measure", file=name):
                self.assertEqual(received[row.storage_key], sha(data))
                self.assertEqual(sha(kept_files.load(row.storage_key)), sha(data))
                self.assertEqual(loaded[row.storage_key], sha(data))
                # A changed file would read NOT_KEPT at the measure's check.
                self.assertEqual(row.state, State.MEASURED)
        self.assertFalse(UploadQuoteStagedFile.objects.exists())


class TheReplyComesFirst(Staged):
    def test_storing_then_stored_then_measured_and_ready(self):
        response = self.create_staged([pdf("a.pdf"), png("b.png")])
        self.assertEqual(response.status_code, 201, response.content)
        body = data_of(response)
        quote_id = body["id"]
        self.assertEqual([f["state"] for f in body["files"]], ["STORING"] * 2)
        self.assertFalse(body["ready"])
        self.assertEqual(kept_files.MEMORY, {})
        (call,) = self.store_asked.call_args_list
        self.assertEqual(str(call.args[0]), quote_id)
        self.assertEqual(
            {str(i) for i in call.args[1]}, {f["id"] for f in body["files"]}
        )

        self.run_store(quote_id)
        polled = data_of(self.get(quote_id))
        self.assertEqual([f["state"] for f in polled["files"]], ["STORED"] * 2)
        self.assertFalse(polled["ready"])
        (asked,) = self.measure_asked.call_args_list
        self.assertEqual(
            {str(i) for i in asked.args[0]}, {f["id"] for f in body["files"]}
        )
        for row in self.rows(quote_id):
            self.assertIsNotNone(row.stored_at)

        for row in self.rows(quote_id):
            measure.do_measure(row.pk)
        ready = data_of(self.get(quote_id))
        self.assertTrue(ready["ready"])
        self.assertEqual([f["state"] for f in ready["files"]], ["MEASURED"] * 2)
        self.assertIsNotNone(ready["estimate"])

    def test_a_duplicate_entry_repeats_a_storing_file(self):
        one = pdf("a.pdf")
        data = one.read()
        response = self.create_staged(
            [as_upload("a.pdf", data), as_upload("b.pdf", data)]
        )
        first, second = data_of(response)["files"]
        self.assertEqual(second["duplicate_of"], first["id"])
        self.assertEqual((first["state"], second["state"]), ("STORING", "STORING"))
        self.assertEqual(UploadQuoteStagedFile.objects.count(), 1)

    def test_a_replay_while_storing_shows_every_row(self):
        files = [png(f"{n}.png", color=(n * 70, 1, 1)) for n in range(3)]
        first = self.create_staged(files, key="replay-1")
        self.assertEqual(first.status_code, 201)
        again = self.create_staged([pdf("other.pdf")], key="replay-1")
        self.assertEqual(again.status_code, 200, again.content)
        body = data_of(again)
        self.assertEqual(body["id"], data_of(first)["id"])
        self.assertEqual(body["total"], 3)
        self.assertEqual([f["state"] for f in body["files"]], ["STORING"] * 3)
        self.assertEqual(UploadQuoteFile.objects.count(), 3)
        self.assertEqual(UploadQuoteStagedFile.objects.count(), 3)
        self.store_asked.assert_not_called()

    def test_a_single_oversize_file_gets_its_own_entry_not_storage_full(self):
        over = b"x" * (settings.MAX_UPLOAD_FILE_SIZE_BYTES + 1)
        with override_settings(UPLOAD_QUOTE_OPEN_BYTES=1000):
            response = self.create_staged([as_upload("big.pdf", over)])
        self.assertEqual(response.status_code, 201, response.content)
        (entry,) = data_of(response)["files"]
        self.assertEqual(
            (entry["state"], entry["reason_code"]), ("REFUSED", "FILE_TOO_LARGE")
        )


class AllNotKept(Staged):
    def test_the_clock_restarts_and_the_quote_does_not_expire_at_once(self):
        response = self.create_staged([pdf("a.pdf")])
        quote_id = data_of(response)["id"]
        # The store took longer than the lifetime.
        UploadQuote.objects.filter(pk=quote_id).update(
            expires_at=timezone.now() - timedelta(minutes=1)
        )
        with patch.object(
            kept_files, "store", side_effect=kept_files.KeptFileUnavailable("down")
        ):
            self.run_store(quote_id, final=True)
        body = data_of(self.get(quote_id))
        self.assertEqual(body["files"][0]["state"], "NOT_KEPT")
        self.assertTrue(body["ready"])
        self.assertEqual(body["state"], "OPEN")
        expires = UploadQuote.objects.get(pk=quote_id).expires_at
        self.assertGreater(
            expires,
            timezone.now()
            + timedelta(minutes=settings.UPLOAD_QUOTE_LIFETIME_MINUTES - 1),
        )


class TheStorageReplyIsChecked(Staged):
    def store_with(self, cloud):
        response = self.create_staged([pdf("a.pdf")])
        quote_id = data_of(response)["id"]
        first, second, third = cloud.patches()
        with first, second, third:
            self.run_store(quote_id, final=True)
        return self.rows(quote_id)[0]

    def test_a_right_reply_is_stored(self):
        cloud = FakeCloud()
        row = self.store_with(cloud)
        self.assertEqual(row.state, State.STORED)
        self.assertIn(row.storage_key, cloud.assets)

    def test_a_wrong_size_deletes_the_asset_and_is_not_kept(self):
        cloud = FakeCloud(reply=lambda d: {"bytes": len(d) + 1, "etag": md5(d)})
        row = self.store_with(cloud)
        self.assertEqual(
            (row.state, row.reason_code), (State.NOT_KEPT, "QUOTE_FILE_NOT_KEPT")
        )
        self.assertIsNone(row.storage_key)
        self.assertEqual(cloud.assets, {})
        self.assertTrue(cloud.destroyed)

    def test_a_wrong_etag_deletes_the_asset_and_is_not_kept(self):
        cloud = FakeCloud(reply=lambda d: {"bytes": len(d), "etag": md5(d + b"!")})
        row = self.store_with(cloud)
        self.assertEqual(row.state, State.NOT_KEPT)
        self.assertEqual(cloud.assets, {})

    @override_settings(UPLOAD_QUOTE_VERIFY_ETAG=False)
    def test_the_etag_check_can_be_switched_off(self):
        cloud = FakeCloud(reply=lambda d: {"bytes": len(d), "etag": md5(d + b"!")})
        self.assertEqual(self.store_with(cloud).state, State.STORED)

    def test_a_reply_without_size_or_etag_is_stored(self):
        cloud = FakeCloud(reply=lambda d: {})
        self.assertEqual(self.store_with(cloud).state, State.STORED)


class EndingWhileStoring(Staged):
    def test_a_cancel_before_the_task_leaves_nothing(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf"), png("b.png")]))["id"]
        self.client.force_authenticate(user=self.teacher)
        with self.captureOnCommitCallbacks(execute=True):
            self.client.delete(reverse("upload-quote-detail", args=[quote_id]))
        self.assertFalse(UploadQuoteStagedFile.objects.exists())
        self.assertEqual(self.run_store(quote_id), [])
        self.assertEqual(kept_files.MEMORY, {})
        # The keys stay on the rows until no store can still be running.
        for row in self.rows(quote_id):
            self.assertIsNotNone(row.storage_key)
        UploadQuoteFile.objects.update(created_at=timezone.now() - timedelta(hours=1))
        with self.captureOnCommitCallbacks(execute=True):
            upload_quotes.purge_due()
        for row in self.rows(quote_id):
            self.assertIsNotNone(row.purged_at)

    def test_a_cancel_while_the_bytes_land_leaves_nothing(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf"), png("b.png")]))["id"]
        real_run_all = quote_store_pool.run_all

        def cancel_then_store(calls):
            with self.captureOnCommitCallbacks(execute=True):
                service.cancel(UploadQuote.objects.get(pk=quote_id))
            return real_run_all(calls)

        with patch.object(quote_store_pool, "run_all", side_effect=cancel_then_store):
            self.run_store(quote_id)
        self.assertEqual(kept_files.MEMORY, {})
        self.assertFalse(UploadQuoteStagedFile.objects.exists())

    def test_an_erasure_during_the_store_deletes_the_assets(self):
        """The assignment is erased (its quotes and files go by cascade) while
        the bytes are on their way: the task finds the quote gone and deletes
        what it stored."""
        quote_id = data_of(self.create_staged([pdf("a.pdf"), png("b.png")]))["id"]
        real_run_all = quote_store_pool.run_all

        def erase_then_store(calls):
            with self.captureOnCommitCallbacks(execute=True):
                Assignment.objects.filter(pk=self.assignment.pk).delete()
            return real_run_all(calls)

        with patch.object(quote_store_pool, "run_all", side_effect=erase_then_store):
            self.run_store(quote_id)
        self.assertFalse(UploadQuote.objects.filter(pk=quote_id).exists())
        self.assertFalse(UploadQuoteStagedFile.objects.exists())
        self.assertEqual(kept_files.MEMORY, {})

    def test_an_erasure_before_the_task_takes_the_staged_bytes(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        with self.captureOnCommitCallbacks(execute=True):
            Assignment.objects.filter(pk=self.assignment.pk).delete()
        self.assertFalse(UploadQuoteStagedFile.objects.exists())
        self.assertEqual(self.run_store(quote_id), [])
        self.assertEqual(kept_files.MEMORY, {})

    def test_the_purge_of_an_ended_quote_takes_its_staged_bytes(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        UploadQuote.objects.filter(pk=quote_id).update(
            created_at=timezone.now() - timedelta(days=1)
        )
        with self.captureOnCommitCallbacks(execute=True):
            upload_quotes.purge_due()
        self.assertEqual(UploadQuote.objects.get(pk=quote_id).state, "EXPIRED")
        self.assertFalse(UploadQuoteStagedFile.objects.exists())


class Retries(Staged):
    def test_a_second_run_stores_nothing_again(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        real = kept_files.store
        calls = []

        def spy(content, key=None):
            calls.append(key)
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=spy):
            self.run_store(quote_id)
            row = self.rows(quote_id)[0]
            service.store_staged(quote_id, [row.pk])
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.rows(quote_id)[0].state, State.STORED)

    def test_a_run_that_died_before_settling_stores_under_the_same_key(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        row = self.rows(quote_id)[0]
        with patch.object(service, "_settle_batch", side_effect=RuntimeError("killed")):
            with self.assertRaises(RuntimeError):
                service.store_staged(quote_id, [row.pk])
        self.assertTrue(UploadQuoteStagedFile.objects.filter(file_id=row.pk).exists())
        self.run_store(quote_id)
        self.assertEqual(list(kept_files.MEMORY), [row.storage_key])
        self.assertEqual(self.rows(quote_id)[0].state, State.STORED)

    def test_a_storage_fault_keeps_the_bytes_staged_then_ends_not_kept(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        row = self.rows(quote_id)[0]
        calls = []

        def down(content, key=None):
            calls.append(key)
            raise kept_files.KeptFileUnavailable("down")

        with patch.object(kept_files, "store", side_effect=down):
            self.assertEqual(service.store_staged(quote_id, [row.pk]), [str(row.pk)])
            self.assertTrue(
                UploadQuoteStagedFile.objects.filter(file_id=row.pk).exists()
            )
            self.assertEqual(self.rows(quote_id)[0].state, State.STORING)
            calls.clear()
            with patch(
                "students.upload_quote_service.enqueue_measures"
            ), self.captureOnCommitCallbacks(execute=True):
                store_quote_files.apply(args=(quote_id, [str(row.pk)]))
        # Five task retries after the first run, two attempts each.
        self.assertEqual(len(calls), (1 + 5) * service.STORE_ATTEMPTS)
        after = self.rows(quote_id)[0]
        self.assertEqual(after.state, State.NOT_KEPT)
        self.assertFalse(UploadQuoteStagedFile.objects.exists())
        # The last try timed out: the upload may still land, so the key stays
        # on the row and the purge deletes a late asset when the quote ends.
        self.assertEqual(after.storage_key, row.storage_key)
        kept_files.MEMORY[row.storage_key] = b"landed late"
        with self.captureOnCommitCallbacks(execute=True):
            service.cancel(UploadQuote.objects.get(pk=quote_id))
            upload_quotes.purge_due()
        self.assertNotIn(row.storage_key, kept_files.MEMORY)
        after.refresh_from_db()
        self.assertIsNotNone(after.purged_at)

    def test_the_task_runs_on_the_light_queue(self):
        self.assertEqual(
            settings.CELERY_TASK_ROUTES["students.tasks.store_quote_files"],
            {"queue": "quote_measure"},
        )
        self.assertIn("quote_measure", [q.name for q in settings.CELERY_TASK_QUEUES])
        with patch.object(store_quote_files, "apply_async") as sent:
            service.enqueue_store("q", ["f"])
        sent.assert_called_once_with(args=("q", ["f"]), queue="quote_measure")


class Sweeper(Staged):
    def staged_row(self, minutes_old, staged=True):
        quote_id = data_of(
            self.create_staged([png(f"{minutes_old}.png", color=(minutes_old, 2, 2))])
        )["id"]
        row = self.rows(quote_id)[0]
        kept_files.MEMORY[row.storage_key] = b"bytes that landed"
        UploadQuoteFile.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=minutes_old)
        )
        if not staged:
            UploadQuoteStagedFile.objects.filter(file_id=row.pk).delete()
        return row

    def sweep(self):
        with patch("students.upload_quote_service.enqueue_store") as asked, patch(
            "students.upload_quote_service.audit_metrics.count"
        ) as count, self.captureOnCommitCallbacks(execute=True):
            result = service.sweep_storing()
        return result, asked, [c.args[0] for c in count.call_args_list]

    def test_a_stale_file_with_staged_bytes_is_asked_for_again(self):
        row = self.staged_row(11)
        result, asked, _ = self.sweep()
        self.assertEqual(result, {"store_requeued": 1, "store_gave_up": 0})
        asked.assert_called_once_with(row.quote_id, [row.pk])
        self.assertEqual(self.rows(row.quote_id)[0].state, State.STORING)

    def test_a_fresh_file_is_left_alone(self):
        self.staged_row(5)
        result, asked, _ = self.sweep()
        self.assertEqual(result, {"store_requeued": 0, "store_gave_up": 0})
        asked.assert_not_called()

    def test_a_file_with_staged_bytes_is_asked_again_until_the_ceiling(self):
        row = self.staged_row(31)
        result, asked, _ = self.sweep()
        self.assertEqual(result, {"store_requeued": 1, "store_gave_up": 0})
        asked.assert_called_once_with(row.quote_id, [row.pk])

    def test_a_re_ask_is_not_sent_again_within_ten_minutes(self):
        self.staged_row(11)
        first, _, _ = self.sweep()
        second, asked, _ = self.sweep()
        self.assertEqual(first["store_requeued"], 1)
        self.assertEqual(second["store_requeued"], 0)
        asked.assert_not_called()

    def test_an_old_file_is_not_kept_and_its_key_deleted(self):
        row = self.staged_row(settings.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES + 1)
        result, asked, metrics = self.sweep()
        self.assertEqual(result, {"store_requeued": 0, "store_gave_up": 1})
        asked.assert_not_called()
        after = self.rows(row.quote_id)[0]
        self.assertEqual(
            (after.state, after.reason_code), (State.NOT_KEPT, "QUOTE_FILE_NOT_KEPT")
        )
        self.assertIsNone(after.storage_key)
        self.assertNotIn(row.storage_key, kept_files.MEMORY)
        self.assertFalse(UploadQuoteStagedFile.objects.exists())
        self.assertIn("upload_quote_store_gave_up", metrics)
        quote = UploadQuote.objects.get(pk=row.quote_id)
        self.assertGreater(quote.expires_at, timezone.now())

    def test_a_file_with_no_staged_bytes_is_not_kept_after_thirty_minutes(self):
        young = self.staged_row(11, staged=False)
        result, _, _ = self.sweep()
        self.assertEqual(result, {"store_requeued": 0, "store_gave_up": 0})
        self.assertEqual(self.rows(young.quote_id)[0].state, State.STORING)
        old = self.staged_row(31, staged=False)
        result, _, _ = self.sweep()
        self.assertEqual(result["store_gave_up"], 1)
        self.assertEqual(self.rows(old.quote_id)[0].state, State.NOT_KEPT)

    def test_the_beat_task_runs_both_nets(self):
        from students.tasks import requeue_stuck_quote_measures

        self.staged_row(11)
        with patch("students.upload_quote_service.enqueue_store"), patch(
            "students.upload_quote_measure.enqueue"
        ):
            result = requeue_stuck_quote_measures.run()
        self.assertEqual(result["store_requeued"], 1)
        self.assertIn("requeued", result)


class TheMeasureClockRunsFromStoredAt(Staged):
    def stored(self, stored_minutes_ago):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        self.run_store(quote_id)
        row = self.rows(quote_id)[0]
        UploadQuoteFile.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(minutes=40),
            stored_at=timezone.now() - timedelta(minutes=stored_minutes_ago),
        )
        return row

    def test_a_file_stored_two_minutes_ago_is_not_stale(self):
        self.stored(2)
        with patch.object(measure, "enqueue") as asked:
            result = measure.requeue_stuck()
        self.assertEqual(result["requeued"], 0)
        asked.assert_not_called()

    def test_a_file_stored_eleven_minutes_ago_is_asked_for_again(self):
        row = self.stored(11)
        with patch.object(measure, "enqueue") as asked:
            result = measure.requeue_stuck()
        self.assertEqual(result["requeued"], 1)
        asked.assert_called_once_with([row.pk])


@override_settings(UPLOAD_QUOTES_ENABLED=True)
class DuplicateDelivery(TransactionTestCase):
    """Two runs of the store task for the same files at once (a worker that
    dies after the upload, then acks_late redelivery), on real threads and
    real commits: both store under the row's one key, one settles, and the
    other must not delete the asset the row now holds."""

    def setUp(self):
        kept_files.MEMORY.clear()
        self.teacher, _, _, self.assignment, _ = classroom("qdup", 10**9)

    def test_the_settled_asset_survives_the_second_run(self):
        import threading

        from AutoGrader.testing.concurrency import run_concurrently

        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        (row,) = service.add_files(quote, [pdf("a.pdf")])
        both_stored = threading.Barrier(2, timeout=10)
        real = kept_files.store

        def store(content, key=None):
            put = real(content, key=key)
            both_stored.wait()  # both runs hold the bytes before either settles
            return put

        def run(_index):
            try:
                return service.store_staged(str(quote.pk), [str(row.pk)])
            finally:
                connection.close()

        with patch.object(kept_files, "store", side_effect=store), patch(
            "students.upload_quote_service.enqueue_measures", MagicMock()
        ):
            results, errors = run_concurrently(run, 2, test=self)

        self.assertEqual(errors, [])
        self.assertEqual(results, [[], []])
        row.refresh_from_db()
        self.assertEqual(row.state, State.STORED)
        self.assertIn(row.storage_key, kept_files.MEMORY)
        self.assertFalse(UploadQuoteStagedFile.objects.exists())


class StepTwoReview(Staged):
    """Fixer's review of step 2 (2026-11-26)."""

    def test_1_the_staged_bytes_cap_is_storage_full(self):
        self.create_staged([pdf("a.pdf")])
        staged = service.staged_bytes()
        self.assertGreater(staged, 0)
        with override_settings(UPLOAD_QUOTE_STAGED_BYTES_MAX=staged + 10), patch(
            "students.upload_quote_service.audit_metrics.count"
        ) as count:
            response = self.create_staged([pdf("b.pdf", pages=4)])
        self.assertEqual(response.status_code, 413, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"],
            "QUOTE_STORAGE_FULL",
        )
        self.assertIn(
            "upload_quote_staged_full", [c.args[0] for c in count.call_args_list]
        )
        self.assertEqual(UploadQuote.objects.count(), 1)

    def test_2_a_full_set_of_slots_waits_and_is_not_a_fault(self):
        with patch.object(
            service, "acquire_store_slot", side_effect=[False, False, True]
        ) as acquire, patch.object(
            service, "release_store_slot"
        ) as release, patch.object(
            service, "store_staged", return_value=[]
        ) as stored:
            store_quote_files.apply(args=("q", ["f"]), kwargs={"faults": 4})
        self.assertEqual(acquire.call_count, 3)
        release.assert_called_once()
        # Two waits did not move the fault count: still not the last try.
        stored.assert_called_once_with("q", ["f"], final=False)

    @override_settings(UPLOAD_QUOTE_STORE_SLOTS=2)
    def test_2_the_slots_count_to_the_setting(self):
        from django.core.cache import cache

        cache.delete(service.STORE_SLOTS_KEY)
        self.addCleanup(cache.delete, service.STORE_SLOTS_KEY)
        self.assertTrue(service.acquire_store_slot())
        self.assertTrue(service.acquire_store_slot())
        self.assertFalse(service.acquire_store_slot())
        service.release_store_slot()
        self.assertTrue(service.acquire_store_slot())

    def test_3_a_failed_staging_leaves_no_quote_and_a_replay_starts_fresh(self):
        self.client.raise_request_exception = False
        with patch.object(
            UploadQuoteStagedFile.objects, "create", side_effect=RuntimeError("db")
        ):
            failed = self.create_staged([pdf("a.pdf")], key="fresh-1")
        self.assertEqual(failed.status_code, 500)
        self.assertFalse(UploadQuote.objects.exists())
        self.assertFalse(UploadQuoteFile.objects.exists())
        again = self.create_staged([pdf("a.pdf")], key="fresh-1")
        self.assertEqual(again.status_code, 201, again.content)
        self.assertEqual(UploadQuote.objects.count(), 1)

    def test_4_a_duplicate_runs_wrong_reply_leaves_the_settled_asset(self):
        """B loads the file, A stores and settles it STORED, then B's own
        store gets a wrong reply: B must not delete the row's asset."""
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        row = self.rows(quote_id)[0]
        cloud = FakeCloud()
        real_run_all = quote_store_pool.run_all
        state = {"nested": False}

        def a_first_then_b(calls):
            if not state["nested"]:
                state["nested"] = True
                service.store_staged(quote_id, [row.pk])  # run A, in full
                cloud.reply = lambda d: {"bytes": len(d) + 1, "etag": md5(d)}
            return real_run_all(calls)

        first, second, third = cloud.patches()
        with first, second, third, patch.object(
            quote_store_pool, "run_all", side_effect=a_first_then_b
        ), patch("students.upload_quote_service.enqueue_measures"):
            service.store_staged(quote_id, [row.pk])  # run B
        row.refresh_from_db()
        self.assertEqual(row.state, State.STORED)
        self.assertIn(row.storage_key, cloud.assets)
        self.assertNotIn(row.storage_key, cloud.destroyed)

    def test_4_a_wrong_reply_is_not_deleted_in_the_pool_thread(self):
        cloud = FakeCloud(reply=lambda d: {"bytes": len(d) + 1})
        with patch.object(kept_files, "delete") as deleted:
            first, second, third = cloud.patches()
            with first, second, third, self.assertRaises(service.ReplyMismatch):
                service._store_one(b"abc", "kept_uploads/x/k", sha(b"abc"), 3)
        deleted.assert_not_called()

    def test_5_the_sweeper_ends_a_row_while_its_store_is_uploading(self):
        quote_id = data_of(self.create_staged([pdf("a.pdf")]))["id"]
        row = self.rows(quote_id)[0]
        UploadQuoteFile.objects.filter(pk=row.pk).update(
            created_at=timezone.now()
            - timedelta(minutes=settings.UPLOAD_QUOTE_UNREADY_CEILING_MINUTES + 1)
        )
        real_run_all = quote_store_pool.run_all

        def sweep_then_store(calls):
            with self.captureOnCommitCallbacks(execute=True):
                self.assertEqual(service.sweep_storing()["store_gave_up"], 1)
            return real_run_all(calls)

        with patch.object(quote_store_pool, "run_all", side_effect=sweep_then_store):
            service.store_staged(quote_id, [row.pk])
        after = self.rows(quote_id)[0]
        self.assertEqual((after.state, after.storage_key), (State.NOT_KEPT, None))
        self.assertEqual(kept_files.MEMORY, {})

    def test_8_a_broker_that_does_not_answer_still_replies_201(self):
        self.client.force_authenticate(user=self.teacher)
        with patch.object(
            store_quote_files, "apply_async", side_effect=OSError("broker down")
        ), patch(
            "students.upload_quote_service.audit_metrics.count"
        ) as count, self.captureOnCommitCallbacks(
            execute=True
        ):
            response = self.client.post(
                reverse(LIST_URL),
                {
                    "kind": "answer_papers",
                    "assignment_id": str(self.assignment.pk),
                    "files": [pdf("a.pdf")],
                },
                format="multipart",
            )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIn(
            "upload_quote_store_enqueue_failed",
            [c.args[0] for c in count.call_args_list],
        )
        self.assertEqual(UploadQuoteStagedFile.objects.count(), 1)

    def test_10_staged_bytes_are_stored_out_of_line_uncompressed(self):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT attstorage FROM pg_attribute "
                "WHERE attrelid = 'students_uploadquotestagedfile'::regclass "
                "AND attname = 'content'"
            )
            (storage,) = cursor.fetchone()
        self.assertEqual(storage, "e")

    @override_settings(UPLOAD_QUOTE_STORE_CONCURRENCY=2)
    def test_11_each_batch_goes_to_the_measure_when_it_settles(self):
        files = [png(f"{n}.png", color=(n * 50, 3, 3)) for n in range(3)]
        quote_id = data_of(self.create_staged(files))["id"]
        self.run_store(quote_id)
        sizes = [len(c.args[0]) for c in self.measure_asked.call_args_list]
        self.assertEqual(sizes, [2, 1])


class TheCloudinaryPoolFitsTheStores(Staged):
    """The live load test logged urllib3 "Connection pool is full" from 3
    parallel stores up: the SDK's pool held one connection per host."""

    @override_settings(UPLOAD_QUOTE_STORE_CONCURRENCY=5, UPLOAD_QUOTE_STORE_SLOTS=3)
    def test_the_pool_holds_every_store_running_at_once(self):
        import cloudinary.uploader

        before = cloudinary.uploader._http
        self.addCleanup(setattr, cloudinary.uploader, "_http", before)
        self.addCleanup(setattr, kept_files, "_pool_sized", kept_files._pool_sized)
        kept_files._pool_sized = False
        kept_files.size_cloudinary_pool()
        pool = cloudinary.uploader._http
        self.assertIsNot(pool, before)
        self.assertEqual(pool.connection_pool_kw["maxsize"], 15)
        # The SDK's own options are kept (certificates).
        self.assertEqual(pool.connection_pool_kw.get("cert_reqs"), "CERT_REQUIRED")
        # Once only.
        kept_files.size_cloudinary_pool()
        self.assertIs(cloudinary.uploader._http, pool)
