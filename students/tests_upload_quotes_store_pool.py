"""Upload quote step 1 (owner, 2026-11-26): files are stored a few at a time,
read one file at a time, and stored exactly as they were uploaded.

* The stores run UPLOAD_QUOTE_STORE_CONCURRENCY (3) at a time; positions and
  per-file results are unchanged, and one failed store fails only its file.
* The bytes kept_files receives are the bytes the teacher sent, for any
  content (a PDF, images, a DOCX, NUL and high bytes, CRLF text, a file at
  the size limit). The type check reads the declared content type, so any
  bytes can arrive as a PDF; each payload is sent that way.
* No list holds every file's bytes: peak memory is a few files, not the
  request.
* A quote with no file is not ready, and confirm answers QUOTE_NOT_READY.
* open_bytes counts only rows that hold or will hold bytes.
"""

import hashlib
import io
import os
import threading
import tracemalloc
import zipfile
from typing import Any
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from AutoGrader import uploads
from students import quote_store_pool
from students import upload_quote_service as service
from students.models import UploadQuote, UploadQuoteFile, UploadQuoteStagedFile
from students.tests_upload_quotes_routes import (
    Base,
    data_of,
    kept_files,
    pdf,
    png,
    states,
)
from students.upload_quotes import open_bytes

State = UploadQuoteFile.State


def sha(data):
    return hashlib.sha256(data).hexdigest()


def jpeg_bytes():
    buffer = io.BytesIO()
    Image.new("RGB", (48, 32), (200, 10, 90)).save(buffer, format="JPEG")
    return buffer.getvalue()


def docx_bytes():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types/>')
        archive.writestr("word/document.xml", "<w:document>answer</w:document>")
    return buffer.getvalue()


def payloads():
    """(name, bytes) of every kind of content the owner listed."""
    return [
        ("paper.pdf", pdf("paper.pdf", pages=3).read()),
        ("scan.png", png("scan.png", color=(1, 2, 3)).read()),
        ("photo.jpg", jpeg_bytes()),
        ("answers.docx", docx_bytes()),
        ("binary.bin", bytes(range(256)) * 64 + b"\x00\x00\xff\xfe"),
        ("notes.txt", b"line one\r\nline two\r\n\r\n\tend\r\n"),
        ("limit.pdf", os.urandom(uploads.max_upload_size_bytes())),
    ]


def as_upload(name, data):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def on_disk(name, data):
    upload = TemporaryUploadedFile(name, "application/pdf", len(data), None)
    upload.write(data)
    upload.seek(0)
    return upload


class ParallelStore(Base):
    def test_files_are_stored_five_at_a_time_in_order(self):
        real = kept_files.store
        lock = threading.Lock()
        active = {"now": 0, "most": 0}
        # Each store waits until five are in flight: fewer than five at once
        # breaks the barrier (BrokenBarrierError, a 500), more never pass it.
        five = threading.Barrier(5, timeout=5)

        def slow(content, key=None):
            with lock:
                active["now"] += 1
                active["most"] = max(active["most"], active["now"])
            try:
                five.wait()
                return real(content, key=key)
            finally:
                with lock:
                    active["now"] -= 1

        files = [png(f"{n}.png", color=(n * 20, 0, 0)) for n in range(10)]
        digests = [sha(f.read()) for f in files]
        for f in files:
            f.seek(0)
        with patch.object(kept_files, "store", side_effect=slow):
            response = self.create(files)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertLessEqual(active["most"], 5)
        self.assertFalse(five.broken)
        entries = data_of(response)["files"]
        self.assertEqual([e["position"] for e in entries], list(range(10)))
        self.assertEqual({e["state"] for e in entries}, {"STORING"})
        self.assertEqual(set(states()), {"STORED"})
        rows = list(UploadQuoteFile.objects.order_by("position"))
        self.assertEqual([r.sha256 for r in rows], digests)
        for row, digest in zip(rows, digests, strict=True):
            self.assertEqual(sha(kept_files.MEMORY[row.storage_key]), digest)

    def test_one_failing_store_fails_only_its_file(self):
        real = kept_files.store
        files = [png(f"{n}.png", color=(0, n * 30, 0)) for n in range(5)]
        bad = sha(files[2].read())
        files[2].seek(0)

        def flaky(content, key=None):
            if sha(content) == bad:
                raise kept_files.KeptFileUnavailable("x")
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=flaky):
            response = self.create(files)

        self.assertEqual(response.status_code, 201, response.content)
        # The task retried the failing file to its last try, then gave up.
        self.assertEqual(states(), ["STORED", "STORED", "NOT_KEPT", "STORED", "STORED"])
        self.assertEqual(
            UploadQuoteFile.objects.get(position=2).reason_code, "QUOTE_FILE_NOT_KEPT"
        )
        # The last try timed out: the key stays so the purge deletes a late
        # asset when the quote ends; nothing is stored under it now.
        failed = UploadQuoteFile.objects.get(position=2)
        self.assertIsNotNone(failed.storage_key)
        self.assertNotIn(failed.storage_key, kept_files.MEMORY)
        self.assertEqual(len(kept_files.MEMORY), 4)

    @override_settings(UPLOAD_QUOTE_STORE_CONCURRENCY=1)
    def test_a_concurrency_of_one_stores_on_the_request_thread(self):
        real = kept_files.store
        threads = []

        def spy(content, key=None):
            threads.append(threading.current_thread())
            return real(content, key=key)

        with patch.object(kept_files, "store", side_effect=spy):
            self.create([png("a.png"), png("b.png", color="black")])
        self.assertEqual(threads, [threading.main_thread()] * 2)

    def test_the_setting_defaults_to_five(self):
        from django.conf import settings

        self.assertEqual(settings.UPLOAD_QUOTE_STORE_CONCURRENCY, 5)

    def test_bytes_that_changed_before_the_store_are_not_kept(self):
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        original, tampered = b"%PDF original", b"%PDF tampereD"
        # The same length: only the sha256 tells them apart.
        self.assertEqual(len(original), len(tampered))
        self.assert_not_kept(quote, original, tampered)

    def test_bytes_of_another_length_are_not_kept(self):
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        self.assert_not_kept(quote, b"%PDF original", b"%PDF original plus")

    def assert_not_kept(self, quote, original, tampered):
        """The staged bytes are altered in the database before the task runs:
        nothing is stored and the file is NOT_KEPT."""
        (row,) = service.add_files(quote, [as_upload("a.pdf", original)])
        UploadQuoteStagedFile.objects.filter(file_id=row.pk).update(content=tampered)
        with patch("students.upload_quote_service.audit_metrics.count") as count:
            self.assertEqual(service.store_staged(quote.pk, [row.pk]), [])
        self.assertFalse(UploadQuoteStagedFile.objects.filter(file_id=row.pk).exists())
        row.refresh_from_db()
        self.assertEqual(row.state, State.NOT_KEPT)
        self.assertEqual(row.reason_code, "QUOTE_FILE_NOT_KEPT")
        self.assertIsNone(row.storage_key)
        self.assertEqual(kept_files.MEMORY, {})
        self.assertIn(
            "upload_quote_bytes_changed", [c.args[0] for c in count.call_args_list]
        )


class SameBytesAsUploaded(Base):
    def test_every_kind_of_content_reaches_the_store_unchanged(self):
        real = kept_files.store
        received: dict[str | None, str] = {}

        def spy(content, key=None):
            received[key] = sha(content)
            return real(content, key=key)

        sent = payloads()
        with patch.object(kept_files, "store", side_effect=spy):
            response = self.create([as_upload(n, d) for n, d in sent])

        self.assertEqual(response.status_code, 201, response.content)
        rows = list(UploadQuoteFile.objects.order_by("position"))
        self.assertEqual(len(rows), len(sent))
        for row, (name, data) in zip(rows, sent, strict=True):
            with self.subTest(file=name):
                self.assertEqual(row.state, State.STORED)
                self.assertEqual(row.size_bytes, len(data))
                self.assertEqual(row.sha256, sha(data))
                self.assertEqual(received[row.storage_key], sha(data))
                self.assertEqual(kept_files.MEMORY[row.storage_key], data)

    def test_a_file_one_byte_over_the_limit_is_refused_not_stored(self):
        over = os.urandom(uploads.max_upload_size_bytes() + 1)
        response = self.create([as_upload("over.pdf", over)])
        (entry,) = data_of(response)["files"]
        self.assertEqual(
            (entry["state"], entry["reason_code"]), ("REFUSED", "FILE_TOO_LARGE")
        )
        self.assertEqual(kept_files.MEMORY, {})


class PeakMemory(Base):
    def test_the_request_never_holds_every_files_bytes(self):
        """Twenty 1 MB files: the old code kept all 20 MB in a list before the
        first store. Now at most a few files' bytes are held at once."""
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        mb = 1024 * 1024
        # On-disk uploads, as Django makes for files over 2.5 MB: reading one
        # allocates its bytes (an in-memory upload's read shares them).
        files = [on_disk(f"{n}.pdf", os.urandom(mb)) for n in range(20)]
        self.addCleanup(lambda: [f.close() for f in files])
        # A plain function, not a mock: a mock keeps every call's bytes.
        with patch.object(kept_files, "store", new=lambda content, key=None: None):
            tracemalloc.start()
            try:
                service.add_files(quote, files)
                _, peak = tracemalloc.get_traced_memory()
            finally:
                tracemalloc.stop()
        self.assertLess(peak, 10 * mb)
        self.assertEqual(
            UploadQuoteFile.objects.filter(quote=quote, state=State.STORING).count(), 20
        )


class AQuoteWithNoFile(Base):
    def make_empty(self):
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        return quote

    def test_is_not_ready(self):
        quote = self.make_empty()
        body = service.payload(quote)
        self.assertEqual((body["ready"], body["total"]), (False, 0))
        self.assertIsNone(body["estimate"])
        self.assertFalse(service.summary(quote)["ready"])

    def test_confirm_is_409_quote_not_ready(self):
        quote = self.make_empty()
        self.client.force_authenticate(user=self.teacher)
        response = self.client.post(
            reverse("upload-quote-confirm", args=[quote.pk]), {}, format="json"
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"], "QUOTE_NOT_READY"
        )
        quote.refresh_from_db()
        self.assertEqual(quote.state, UploadQuote.State.OPEN)


class OpenBytesCountsHeldFilesOnly(Base):
    def test_refused_and_not_kept_rows_do_not_count(self):
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        sizes: dict[Any, int] = {
            State.STORING: 1,
            State.STORED: 10,
            State.MEASURING: 100,
            State.MEASURED: 1000,
            State.REFUSED: 600 * 1024 * 1024,
            State.NOT_KEPT: 50_000,
        }
        for position, (state, size) in enumerate(sizes.items()):
            UploadQuoteFile.objects.create(
                quote=quote,
                position=position,
                state=state,
                size_bytes=size,
                sha256=f"{position:064d}" if state != State.REFUSED else "",
            )
        self.assertEqual(open_bytes(self.teacher), 1111)
        self.assertEqual(open_bytes(), 1111)

    def test_a_refused_oversize_file_does_not_fill_the_teachers_room(self):
        over = uploads.max_upload_size_bytes() + 1
        # Room for exactly the oversize file: it once counted, filling it.
        with override_settings(UPLOAD_QUOTE_OPEN_BYTES=over):
            first = self.create([as_upload("big.pdf", b"x" * over)])
            self.assertEqual(data_of(first)["files"][0]["state"], "REFUSED")
            second = self.create([pdf("small.pdf")])
        self.assertEqual(second.status_code, 201, second.content)


class ACancelDuringAMultiFileStore(Base):
    """Fixer #2 and its note: four files are being stored by the task (one
    store fails) when the teacher cancels, before or after the stores finish.
    Cancel's own deletes are left out (its after-commit callbacks are not
    run), so only the task's guard can delete: nothing it stored is left."""

    def test_the_task_deletes_what_it_stored(self):
        for order in ("cancel before the stores finish", "cancel after"):
            with self.subTest(order=order):
                kept_files.MEMORY.clear()
                quote, _ = service.create_quote(
                    self.teacher, kind="answer_papers", assignment=self.assignment
                )
                files = [
                    png(f"{n}.png", color=(len(order), n * 40, 0)) for n in range(4)
                ]
                bad = sha(files[1].read())
                files[1].seek(0)
                rows = service.add_files(quote, files)
                real_store = kept_files.store
                real_run_all = quote_store_pool.run_all

                def flaky(content, key=None, bad=bad, real_store=real_store):
                    if sha(content) == bad:
                        raise kept_files.KeptFileUnavailable("x")
                    return real_store(content, key=key)

                def cancel(quote=quote):
                    with self.captureOnCommitCallbacks(execute=False):
                        service.cancel(UploadQuote.objects.get(pk=quote.pk))

                def around(calls, order=order, real_run_all=real_run_all):
                    if order.startswith("cancel before"):
                        cancel()
                    outcomes = real_run_all(calls)
                    if not order.startswith("cancel before"):
                        cancel()
                    return outcomes

                with patch.object(kept_files, "store", side_effect=flaky), patch.object(
                    quote_store_pool, "run_all", side_effect=around
                ):
                    service.store_staged(quote.pk, [r.pk for r in rows], final=True)

                self.assertEqual(kept_files.MEMORY, {})
                self.assertFalse(
                    UploadQuoteStagedFile.objects.filter(file__quote=quote).exists()
                )
                quote.refresh_from_db()
                self.assertEqual(quote.state, UploadQuote.State.CANCELLED)


class NoSqlOnAStoreThread(Base):
    """Fixer #6: only the storage call runs in the pool. No query, and no
    database connection, is made on a quote-store thread during a create."""

    def test_a_three_file_create_runs_no_sql_in_the_pool(self):
        from django.db.backends.signals import connection_created
        from django.db.backends.utils import CursorWrapper

        on_pool: list = []
        seen_pool = []
        real_execute = CursorWrapper.execute
        real_executemany = CursorWrapper.executemany
        real_store = kept_files.store

        def note(*_args, **_kwargs):
            if threading.current_thread().name.startswith("quote-store"):
                on_pool.append(threading.current_thread().name)

        def execute(cursor, *args, **kwargs):
            note()
            return real_execute(cursor, *args, **kwargs)

        def executemany(cursor, *args, **kwargs):
            note()
            return real_executemany(cursor, *args, **kwargs)

        def store(content, key=None):
            seen_pool.append(threading.current_thread().name)
            return real_store(content, key=key)

        connection_created.connect(note)
        self.addCleanup(connection_created.disconnect, note)
        files = [png(f"{n}.png", color=(n * 60, 9, 9)) for n in range(3)]
        with patch.object(CursorWrapper, "execute", execute), patch.object(
            CursorWrapper, "executemany", executemany
        ), patch.object(kept_files, "store", side_effect=store):
            response = self.create(files)

        self.assertEqual(response.status_code, 201, response.content)
        # The guard looks where the stores ran: on pool threads.
        self.assertEqual(len(seen_pool), 3)
        self.assertTrue(all(n.startswith("quote-store") for n in seen_pool))
        self.assertEqual(on_pool, [])


class OneRetry(Base):
    """Owner, 2026-11-26: a store the storage did not answer is tried once
    more before the file counts as failed; changed bytes and a Cloudinary 400
    are never retried. Run on the task's last try, so its own retries stay
    out of the count."""

    def store_with(self, failures, exc=None):
        real = kept_files.store
        calls = []

        def flaky(content, key=None):
            calls.append(key)
            if len(calls) <= failures:
                raise exc or kept_files.KeptFileUnavailable("timeout")
            return real(content, key=key)

        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        (row,) = service.add_files(quote, [pdf("a.pdf")])
        with patch.object(kept_files, "store", side_effect=flaky), patch(
            "students.upload_quote_service.audit_metrics.count"
        ) as count:
            service.store_staged(quote.pk, [row.pk], final=True)
        row.refresh_from_db()
        metrics = [c.args[0] for c in count.call_args_list]
        return row, calls, metrics

    def test_one_failure_then_success_is_stored_with_two_calls(self):
        row, calls, metrics = self.store_with(1)
        self.assertEqual(row.state, State.STORED)
        self.assertEqual(len(calls), 2)
        self.assertEqual(len(set(calls)), 1)  # the same key both times
        self.assertEqual(metrics.count("upload_quote_store_retried"), 1)
        self.assertEqual(len(kept_files.MEMORY), 1)

    def test_two_failures_are_not_kept_after_two_calls(self):
        row, calls, metrics = self.store_with(2)
        self.assertEqual(
            (row.state, row.reason_code), (State.NOT_KEPT, "QUOTE_FILE_NOT_KEPT")
        )
        self.assertEqual(len(calls), 2)
        self.assertEqual(metrics.count("upload_quote_store_retried"), 1)
        self.assertEqual(kept_files.MEMORY, {})

    def test_a_cloudinary_400_is_not_retried(self):
        import cloudinary.exceptions

        try:
            raise cloudinary.exceptions.BadRequest("File size too large.")
        except cloudinary.exceptions.BadRequest as cause:
            refusal = kept_files.KeptFileUnavailable("BadRequest")
            refusal.__cause__ = cause
        row, calls, metrics = self.store_with(2, exc=refusal)
        self.assertEqual(row.state, State.NOT_KEPT)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("upload_quote_store_retried", metrics)

    def test_changed_bytes_are_checked_once_and_never_stored(self):
        original = b"%PDF original"
        digest = sha(original)
        with patch.object(kept_files, "store") as store, patch.object(
            service.hashlib, "sha256", wraps=hashlib.sha256
        ) as hashed:
            with self.assertRaises(service.BytesChanged):
                service._store_one(
                    b"%PDF tampereD", "kept_uploads/x/y", digest, len(original)
                )
        store.assert_not_called()
        self.assertEqual(hashed.call_count, 1)
