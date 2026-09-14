"""PDFService must not share per-upload state between concurrent requests.

THE BUG THIS PINS. `ai_processor.services` creates one module-level
`pdf_service = PDFService()` and `PDFService` carries the file it is
working on as instance state (`self.uploaded_file`, set by
`set_uploaded_file`). The PDF branch of
`AssignmentProcessingService.prepare_ai_content` used that singleton as
set-then-use:

    pdf_service.set_uploaded_file(uploaded_file)   # assignments/services.py
    images = pdf_service.extract()

Both call sites of `prepare_ai_content` are synchronous DRF view handlers -
`students/views.py` (a student uploading their submission) and
`assignments/views.py` (a teacher bulk-uploading assignments) - and the
web tier runs `gunicorn --worker-class gthread --threads 4` (Dockerfile).
Four request threads therefore share that one object.

Two uploads landing on the same worker interleave as:

    thread A: set_uploaded_file(A.pdf)
    thread B: set_uploaded_file(B.pdf)      <- overwrites A's
    thread A: extract()                     -> rasterizes B.pdf

Reproduced before the fix with two real threads: the thread that uploaded
`alpha.pdf` read `bravo.pdf`.

WHY IT IS A BLOCKER, not a tidiness point. The extracted pages become the
student's answers. A student's submission can be graded from a DIFFERENT
student's uploaded paper - a silently wrong grade - and that other
student's handwritten work is then stored on, and shown for, a submission
belonging to someone who may be in another class at another school
entirely. It is simultaneously a wrong-grade bug and a cross-tenant
disclosure, and nothing in the pipeline would flag either.

The tests below use page COUNT as the discriminator: each thread uploads a
PDF with a distinct number of pages, and `prepare_ai_content` returns one
content block per page (plus the prompt). A thread that reads another
thread's file gets the wrong number of blocks back. That is cheap,
deterministic, and does not depend on decoding the rendered images.
"""

import threading
from unittest.mock import patch

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from ai_processor.services import PDFService
from assignments.services import AssignmentProcessingService


def make_pdf_bytes(label: str, pages: int) -> bytes:
    """A real, rasterizable PDF with a known page count."""
    document = fitz.open()
    for index in range(pages):
        page = document.new_page()
        page.insert_text((72, 100), f"{label} page {index + 1}", fontsize=36)
    data = document.tobytes()
    document.close()
    return data


def uploaded_pdf(label: str, pages: int) -> SimpleUploadedFile:
    return SimpleUploadedFile(
        f"{label.lower()}.pdf",
        make_pdf_bytes(label, pages),
        content_type="application/pdf",
    )


class PdfServiceIsNotSharedBetweenThreadsTest(SimpleTestCase):
    """
    The real caller, under real thread contention, with the interleaving
    FORCED rather than hoped for.

    Left to chance this proves nothing: the window between
    `set_uploaded_file()` and `extract()` reading `self.uploaded_file` is a
    couple of bytecode operations, and three unassisted runs against the
    known-broken code all passed. That is precisely why the bug survived -
    it is rare per-upload and certain at volume.

    So each thread is held at the top of `extract()` until every thread has
    arrived. The real `extract` still does the real work; only its start is
    synchronised. On the shared-singleton code every thread has completed
    its `set_uploaded_file` before any of them reads the file, so they all
    rasterize whichever upload happened to be last - which is exactly the
    production failure, made reproducible.
    """

    # Distinct page counts are the discriminator - see the module docstring.
    UPLOADS = {"ALPHA": 1, "BRAVO": 3, "CHARLIE": 2, "DELTA": 4}

    def _run_burst(self):
        results: dict = {}
        errors: dict = {}
        started = threading.Barrier(len(self.UPLOADS))
        original_extract = PDFService.extract

        def synchronised_extract(pdf_self, *args, **kwargs):
            # Every thread has now chosen its file; none has read one yet.
            started.wait(timeout=60)
            return original_extract(pdf_self, *args, **kwargs)

        def prepare(label, pages):
            try:
                content = AssignmentProcessingService.prepare_ai_content(
                    uploaded_pdf(label, pages), f"prompt for {label}"
                )
                results[label] = len(
                    [b for b in content if b.get("type") == "image_url"]
                )
            except Exception as exc:  # pragma: no cover - a real failure
                errors[label] = exc

        with patch.object(PDFService, "extract", synchronised_extract):
            threads = [
                threading.Thread(target=prepare, args=(label, pages))
                for label, pages in self.UPLOADS.items()
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=180)

        return results, errors

    def test_concurrent_uploads_each_extract_their_own_file(self):
        results, errors = self._run_burst()

        self.assertEqual(errors, {}, f"threads raised: {errors}")
        self.assertEqual(
            results,
            dict(self.UPLOADS),
            "a request extracted a different request's uploaded PDF - the "
            "student would be graded on someone else's paper, and that "
            "other student's work would be stored against this submission",
        )

    def test_repeated_bursts_stay_correct(self):
        """Three forced interleavings, so one lucky ordering cannot pass."""
        for round_index in range(3):
            with self.subTest(round=round_index):
                results, errors = self._run_burst()
                self.assertEqual(errors, {}, f"threads raised: {errors}")
                self.assertEqual(results, dict(self.UPLOADS))


class PdfServiceInstancesAreIndependentTest(SimpleTestCase):
    """
    The property that makes the above safe, asserted directly so the
    reason survives even if the caller is refactored again.
    """

    def test_two_instances_do_not_share_the_file_being_extracted(self):
        first = PDFService(uploaded_pdf("ALPHA", 1))
        second = PDFService(uploaded_pdf("BRAVO", 3))

        self.assertEqual(len(first.extract()), 1)
        self.assertEqual(len(second.extract()), 3)
        # And the first is still usable afterwards - constructing the
        # second must not have disturbed it.
        first.uploaded_file.seek(0)
        self.assertEqual(len(first.extract()), 1)

    def test_prepare_ai_content_does_not_mutate_the_shared_singleton(self):
        """
        The shared `pdf_service` object still exists (its stateless
        get_pdf_page_count is used for token estimation). What must never
        happen again is a request writing its own file onto it.
        """
        from ai_processor.services import pdf_service

        pdf_service.set_uploaded_file(None)

        AssignmentProcessingService.prepare_ai_content(
            uploaded_pdf("ALPHA", 1), "prompt"
        )

        self.assertIsNone(
            pdf_service.uploaded_file,
            "prepare_ai_content wrote per-request state onto the shared "
            "module-level PDFService again",
        )
