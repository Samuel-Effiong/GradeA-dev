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
import time
import tracemalloc
import zipfile
from typing import Any
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile, TemporaryUploadedFile
from django.test import override_settings
from django.urls import reverse
from PIL import Image

from AutoGrader import uploads
from students import upload_quote_service as service
from students.models import UploadQuote, UploadQuoteFile
from students.tests_upload_quotes_routes import Base, data_of, kept_files, pdf, png
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
    def test_files_are_stored_three_at_a_time_in_order(self):
        real = kept_files.store
        lock = threading.Lock()
        active = {"now": 0, "most": 0}

        def slow(content, key=None):
            with lock:
                active["now"] += 1
                active["most"] = max(active["most"], active["now"])
            time.sleep(0.05)
            try:
                return real(content, key=key)
            finally:
                with lock:
                    active["now"] -= 1

        files = [png(f"{n}.png", color=(n * 30, 0, 0)) for n in range(7)]
        digests = [sha(f.read()) for f in files]
        for f in files:
            f.seek(0)
        with patch.object(kept_files, "store", side_effect=slow):
            response = self.create(files)

        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(active["most"], 3)
        entries = data_of(response)["files"]
        self.assertEqual([e["position"] for e in entries], list(range(7)))
        self.assertEqual({e["state"] for e in entries}, {"STORED"})
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
        entries = data_of(response)["files"]
        self.assertEqual(
            [e["state"] for e in entries],
            ["STORED", "STORED", "NOT_KEPT", "STORED", "STORED"],
        )
        self.assertEqual(entries[2]["reason_code"], "QUOTE_FILE_NOT_KEPT")
        self.assertIsNone(UploadQuoteFile.objects.get(position=2).storage_key)
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

    def test_the_setting_defaults_to_three(self):
        from django.conf import settings

        self.assertEqual(settings.UPLOAD_QUOTE_STORE_CONCURRENCY, 3)

    def test_bytes_that_changed_before_the_store_are_not_kept(self):
        quote, _ = service.create_quote(
            self.teacher, kind="answer_papers", assignment=self.assignment
        )
        plans = service._prepare(quote, [as_upload("a.pdf", b"%PDF original")], [0])
        row, _ = plans[0]
        changed = [(row, as_upload("a.pdf", b"%PDF tampered"))]
        with patch("students.upload_quote_service.audit_metrics.count") as count:
            service._store(changed)
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
            UploadQuoteFile.objects.filter(quote=quote, state=State.STORED).count(), 20
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
