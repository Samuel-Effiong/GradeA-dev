"""The renderer must refuse to start in a gevent-patched process.

This is the test that was missing when a browser in the gevent Celery
worker took production down. Every other renderer test runs under real
OS threads (gunicorn's gthread pool), where the event loop stays
confined to its own thread and nothing leaks - so none of them could
have caught it.

Under gevent, threading.Thread becomes a greenlet sharing one OS thread
with everything else, and asyncio records its running loop per-OS-thread.
run_forever() therefore makes that loop visible to every other greenlet,
and Django's async_unsafe check rejects any ORM call it can see a running
loop from. The result was every unrelated Celery task failing with
SynchronousOnlyOperation until the worker was restarted.

The real monkey-patching is done in a subprocess: patching threading in
the test runner's own process would corrupt every test after it.
"""

import subprocess
import sys
import textwrap

from django.test import SimpleTestCase

from assignments import pdf_renderer


class GeventDetectionTest(SimpleTestCase):
    def test_reports_unpatched_in_a_normal_process(self):
        # The test runner uses real threads, so the renderer is allowed.
        self.assertFalse(pdf_renderer._gevent_patched())

    def test_unavailable_is_a_render_error_but_not_busy(self):
        """
        Distinct from PDFRendererBusy on purpose: Busy means "try again
        later", Unavailable means "never here". Existing handlers that
        catch PDFRenderError still work; retry logic keyed on Busy must
        not fire for it.
        """
        self.assertTrue(
            issubclass(pdf_renderer.PDFRendererUnavailable, pdf_renderer.PDFRenderError)
        )
        self.assertFalse(
            issubclass(
                pdf_renderer.PDFRendererUnavailable, pdf_renderer.PDFRendererBusy
            )
        )


class GeventGuardSubprocessTest(SimpleTestCase):
    """
    Runs a real gevent-patched process and asserts the guard holds.

    Anything less than real monkey-patching would be testing a mock of
    the exact mechanism that caused the outage.
    """

    def _run(self, body: str) -> str:
        script = (
            textwrap.dedent(
                """
            from gevent import monkey
            monkey.patch_all()

            import os, sys
            sys.path.insert(0, ".")
            os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
            import django
            django.setup()

            from assignments import pdf_renderer
            """
            )
            + textwrap.dedent(body)
        )

        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=180,
        )
        return result.stdout + result.stderr

    def test_threading_really_is_patched_in_the_subprocess(self):
        # Guards the test itself: if patching silently stopped working,
        # the assertions below would pass for the wrong reason.
        out = self._run(
            """
            print("PATCHED:", pdf_renderer._gevent_patched())
            """
        )
        self.assertIn("PATCHED: True", out)

    def test_the_renderer_refuses_to_start_under_gevent(self):
        out = self._run(
            """
            try:
                pdf_renderer._get_worker()
                print("RESULT: started (BAD - should have refused)")
            except pdf_renderer.PDFRendererUnavailable as exc:
                print("RESULT: refused")
                print("MENTIONS_GEVENT:", "gevent" in str(exc))
            """
        )
        self.assertIn("RESULT: refused", out)
        self.assertIn("MENTIONS_GEVENT: True", out)

    def test_no_event_loop_leaks_after_the_refusal(self):
        """
        The actual production symptom: a loop visible from other
        greenlets, which makes Django reject every ORM call. After the
        guard refuses, no loop may be running.
        """
        out = self._run(
            """
            import asyncio
            try:
                pdf_renderer._get_worker()
            except pdf_renderer.PDFRendererUnavailable:
                pass
            try:
                asyncio.get_running_loop()
                print("LOOP: LEAKED")
            except RuntimeError:
                print("LOOP: none")
            """
        )
        self.assertIn("LOOP: none", out)

    def test_orm_still_works_after_the_refusal(self):
        """
        End to end, in the shape the outage actually took: an unrelated
        ORM read in the same process must be unaffected.
        """
        out = self._run(
            """
            try:
                pdf_renderer._get_worker()
            except pdf_renderer.PDFRendererUnavailable:
                pass
            from django.contrib.contenttypes.models import ContentType
            try:
                ContentType.objects.exists()
                print("ORM: ok")
            except Exception as exc:
                print("ORM: FAILED", type(exc).__name__)
            """
        )
        self.assertIn("ORM: ok", out)

    def test_the_singleton_is_not_left_half_initialised(self):
        # A refused start must leave nothing behind for the next caller.
        out = self._run(
            """
            for _ in range(3):
                try:
                    pdf_renderer._get_worker()
                except pdf_renderer.PDFRendererUnavailable:
                    pass
            print("WORKER_IS_NONE:", pdf_renderer._worker is None)
            """
        )
        self.assertIn("WORKER_IS_NONE: True", out)
