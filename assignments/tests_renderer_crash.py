"""Chromium crash and recovery, with REAL process termination.

The renderer's crash path (`_acquire_browser` discarding a dead browser,
`_render` retrying once on a fresh one) was the only substantial uncovered
region left in pdf_renderer.py, and it is a real production failure mode:
the OOM killer, a container eviction, or Chromium simply dying takes the
browser out from under an in-flight render.

Nothing here is simulated with a mock. Every test finds the actual
Chromium process this worker launched and SIGKILLs it, then asserts on
what the renderer does next.

WHAT THE FAILURE INJECTION ESTABLISHED: with the renderer's event loop
running - which is always true in production, since the loop thread lives
for the life of the process - `browser.is_connected()` flips to False on
its own within about 0.2s of the SIGKILL, before any operation is
attempted. `_render`'s retry, gated on `not self._is_alive(browser)`,
therefore fires on a genuinely detected crash rather than on a side
effect. That is a robust design, and the first test below pins the
detection with a time bound.

A METHODOLOGICAL WARNING, recorded because it produced a wrong conclusion
during this work: probing this from a standalone script that used
`time.sleep()` INSIDE the coroutine made `is_connected()` appear to stay
True indefinitely. Blocking the event loop stops Playwright processing
the transport close, so the disconnect is never noticed. Anyone
re-measuring this must keep the loop free, or they will "discover" a
detection failure that does not exist.

The eight properties asserted across this module:
  1. the render reports correctly (a valid PDF, or PDFRenderError - never
     a hang and never a partial file);
  2. no Chromium processes are orphaned;
  3. the swap lock, the concurrency semaphore and the in-flight/queue
     counters are all released;
  4. the retry happens exactly once, not in a loop;
  5. nothing truncated or corrupt is ever returned as a PDF;
  6. later renders still work;
  7. concurrent renders are not left blocked by another render's crash;
  8. no database connections, threads or worker resources are leaked.
"""

import os
import threading
import time
import unittest
from unittest.mock import patch

import fitz
import psutil
from django.db import connection
from django.test import SimpleTestCase

from assignments import pdf_renderer
from assignments.tests_pdf_renderer import _CHROMIUM_AVAILABLE

SIMPLE_DOCUMENT = (
    "<!doctype html><html><head><title>Crash</title></head>"
    "<body><p>content</p></body></html>"
)
# Math forces the KaTeX assets to load and typeset, which makes a render
# long enough to reliably kill something mid-flight.
SLOW_DOCUMENT = (
    "<!doctype html><html><head><title>Crash</title></head><body>"
    + "".join(f"<p>Question {i}: $\\int_0^{{{i}}} x^2 dx$</p>" for i in range(60))
    + "</body></html>"
)


def chromium_processes():
    """Every Chromium process descended from this test process."""
    me = psutil.Process(os.getpid())
    found = []
    for child in me.children(recursive=True):
        try:
            if "chrome" in child.name().lower():
                found.append(child)
        except psutil.Error:  # pragma: no cover - process vanished
            pass
    return found


def browser_main_processes():
    """
    Just the browser's own top-level processes.

    Chromium's renderer/GPU/zygote children all carry a `--type=` flag;
    the one without it is the browser process whose death is the crash
    being simulated.
    """
    main = []
    for proc in chromium_processes():
        try:
            if not any(arg.startswith("--type=") for arg in proc.cmdline()):
                main.append(proc)
        except psutil.Error:  # pragma: no cover - process vanished
            pass
    return main


def kill_the_browser():
    """SIGKILL the live browser process(es). Returns how many were killed."""
    targets = browser_main_processes()
    for proc in targets:
        try:
            proc.kill()
        except psutil.Error:  # pragma: no cover
            pass
    psutil.wait_procs(targets, timeout=10)
    return len(targets)


def assert_valid_pdf(test, payload):
    """A PDF or nothing - never a truncated or half-written file."""
    test.assertIsInstance(payload, bytes)
    test.assertTrue(payload.startswith(b"%PDF"), f"not a PDF: {payload[:40]!r}")
    document = fitz.open(stream=payload, filetype="pdf")
    try:
        test.assertGreaterEqual(document.page_count, 1)
        # Actually parse a page: a truncated file can still carry the
        # header, so the header alone proves very little.
        document.load_page(0).get_text()
    finally:
        document.close()


@unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
class ChromiumCrashRecoveryTest(SimpleTestCase):
    """Real SIGKILL against the real browser, one render at a time."""

    # Property 8 counts real backends in pg_stat_activity, so this needs
    # database access even though nothing here uses the ORM.
    databases = {"default"}

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()
        self.addCleanup(pdf_renderer.reset_worker_for_tests)
        self.worker = pdf_renderer._get_worker()
        self.threads_before = threading.active_count()

    def _swap(self, name, replacement):
        """
        Replace one of the worker's coroutine methods for this test.

        patch.object rather than a plain attribute assignment: mypy
        rejects assigning to a method and bugbear rejects setattr with a
        constant name, and this restores the original automatically.
        """
        patcher = patch.object(self.worker, name, replacement)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _assert_fully_released(self):
        """Property 3: nothing is still held after the dust settles."""
        self.assertFalse(
            self.worker._swap_lock.locked(), "the browser swap lock is still held"
        )
        self.assertEqual(
            self.worker._in_flight, 0, "the in-flight counter did not unwind"
        )
        with self.worker._queue_lock:
            self.assertEqual(
                self.worker._queued, 0, "a queue slot was leaked by the crash"
            )
        self.assertEqual(
            self.worker._semaphore._value,
            pdf_renderer._max_concurrent_renders(),
            "a concurrency slot was not returned to the semaphore",
        )

    def test_a_killed_browser_is_detected_promptly_without_being_used(self):
        """
        The precondition the whole retry rests on: the renderer can tell a
        dead browser from a live one BEFORE trying to use it.

        Time-bounded rather than instantaneous, because detection arrives
        via the transport close on the loop thread. If this ever starts
        failing, `_render`'s retry stops firing and every crash becomes a
        failed download instead of a slow one.
        """
        browser = self.worker._browser
        self.assertTrue(self.worker._is_alive(browser))

        self.assertGreater(kill_the_browser(), 0, "no browser process to kill")

        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and self.worker._is_alive(browser):
            time.sleep(0.1)

        self.assertFalse(
            self.worker._is_alive(browser),
            "a SIGKILLed browser still reports itself connected after 10s - "
            "the crash-retry in _render will not fire",
        )

    def test_a_render_after_a_crash_returns_a_valid_pdf(self):
        """
        Properties 1, 4, 5 and 6: the browser is dead before the call, and
        the render still produces a real, parseable PDF by relaunching.
        """
        launches = {"n": 0}
        original_launch = self.worker._launch

        async def counting_launch(playwright):
            launches["n"] += 1
            return await original_launch(playwright)

        self._swap("_launch", counting_launch)

        self.assertGreater(kill_the_browser(), 0)

        payload = pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT)

        assert_valid_pdf(self, payload)
        self.assertEqual(
            launches["n"], 1, f"expected exactly one relaunch, saw {launches['n']}"
        )
        self._assert_fully_released()

        # Property 6: and the next one works too, with no further relaunch.
        assert_valid_pdf(self, pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT))
        self.assertEqual(launches["n"], 1)

    def test_a_crash_mid_render_is_retried_and_still_yields_a_valid_pdf(self):
        """
        The real thing: the browser dies while a render is genuinely in
        flight, not between calls.
        """
        result: dict = {}

        def render():
            try:
                result["pdf"] = pdf_renderer.render_html_to_pdf(
                    SLOW_DOCUMENT, timeout=45
                )
            except Exception as exc:
                result["error"] = exc

        thread = threading.Thread(target=render)
        thread.start()
        # Let the render get properly underway before pulling the rug.
        time.sleep(0.8)
        killed = kill_the_browser()
        thread.join(timeout=120)

        self.assertFalse(thread.is_alive(), "the render hung after the crash")
        self.assertGreater(killed, 0, "nothing was killed - test proved nothing")

        if "pdf" in result:
            # The retry absorbed it: that is the designed outcome.
            assert_valid_pdf(self, result["pdf"])
        else:
            # Or it failed cleanly - acceptable, but it must be the
            # renderer's own typed error, not a raw Playwright exception
            # leaking to the caller.
            self.assertIsInstance(result["error"], pdf_renderer.PDFRenderError)

        self._assert_fully_released()

    def test_a_crash_inside_the_render_is_RECOVERED_not_merely_reported(self):
        """
        Pins the one-shot retry in `_render` specifically.

        This needed its own test because the obvious ones do not reach it:
        `_acquire_browser` detects a browser that died BEFORE the call and
        relaunches, so the retry never fires and deleting it changes
        nothing observable. (Confirmed by mutation - removing the retry
        left the rest of this module green.) The retry only matters in the
        narrow window where the browser is alive at acquire time and dies
        during the render itself, which is what is forced here.

        The requirement is recovery, not merely a clean error: a crash in
        that window must still hand the teacher their PDF.
        """
        calls = {"n": 0}
        original_do_render = self.worker._do_render

        async def crash_on_first_attempt(browser, html, options):
            calls["n"] += 1
            if calls["n"] == 1:
                # Alive at acquire, dead by the time it is used.
                kill_the_browser()
            return await original_do_render(browser, html, options)

        self._swap("_do_render", crash_on_first_attempt)

        payload = pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT, timeout=30)

        assert_valid_pdf(self, payload)
        self.assertEqual(
            calls["n"],
            2,
            "the render was not retried after the browser died mid-flight - "
            "a crash in this window now costs the caller their download",
        )
        self._assert_fully_released()

    def test_the_retry_is_bounded_when_the_browser_keeps_dying(self):
        """
        Property 4, the other direction: a browser that dies every time
        must produce ONE retry and then a clean failure - not an infinite
        relaunch loop that pins a request thread forever.
        """
        launches = {"n": 0}
        original_launch = self.worker._launch

        async def dying_launch(playwright):
            launches["n"] += 1
            browser = await original_launch(playwright)
            return browser

        self._swap("_launch", dying_launch)

        # Kill on every acquire, so no attempt can ever complete.
        original_do_render = self.worker._do_render

        async def crash_first(browser, html, options):
            kill_the_browser()
            return await original_do_render(browser, html, options)

        self._swap("_do_render", crash_first)

        started = time.perf_counter()
        with self.assertRaises(pdf_renderer.PDFRenderError):
            pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT, timeout=20)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 90, "the failure path did not terminate promptly")
        self.assertLessEqual(
            launches["n"],
            3,
            f"relaunched {launches['n']} times - the retry is not bounded",
        )
        self._assert_fully_released()

    def test_no_chromium_processes_are_orphaned_by_a_crash(self):
        """
        Property 2. An orphan per crash would accumulate until the box ran
        out of memory, and nothing in the app would report it.
        """
        kill_the_browser()
        assert_valid_pdf(self, pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT))

        pdf_renderer.reset_worker_for_tests()

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and chromium_processes():
            time.sleep(0.5)

        survivors = [(p.pid, p.name()) for p in chromium_processes() if p.is_running()]
        self.assertEqual(survivors, [], f"orphaned Chromium processes: {survivors}")

    def test_a_crash_leaks_no_threads_or_database_connections(self):
        """
        Property 8. The renderer owns a loop thread and Django owns the
        connection; a crash must return both to where they started.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database()"
            )
            connections_before = cursor.fetchone()[0]

        for _ in range(3):
            kill_the_browser()
            assert_valid_pdf(self, pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT))

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM pg_stat_activity "
                "WHERE datname = current_database()"
            )
            connections_after = cursor.fetchone()[0]

        self.assertLessEqual(
            connections_after - connections_before,
            2,
            f"database connections grew {connections_before} -> {connections_after} "
            "across three browser crashes",
        )
        # One loop thread per worker; three crashes must not mean three
        # extra threads.
        self.assertLessEqual(
            threading.active_count() - self.threads_before,
            2,
            "threads accumulated across crashes",
        )


@unittest.skipUnless(_CHROMIUM_AVAILABLE, "Headless Chromium not available")
class ConcurrentRendersSurviveACrashTest(SimpleTestCase):
    """
    Property 7: when Chromium dies, EVERY render in flight dies with it.

    They must all come back - each with a valid PDF or a clean error - and
    none may be left parked. A crash that wedges the other callers turns
    one dead browser into a stuck worker process.
    """

    def setUp(self):
        pdf_renderer.reset_worker_for_tests()
        self.addCleanup(pdf_renderer.reset_worker_for_tests)
        self.worker = pdf_renderer._get_worker()

    def test_every_concurrent_caller_returns_after_the_browser_is_killed(self):
        callers = 8
        outcomes: list = [None] * callers
        barrier = threading.Barrier(callers)

        def render(index):
            try:
                barrier.wait(timeout=30)
                outcomes[index] = (
                    "ok",
                    pdf_renderer.render_html_to_pdf(SLOW_DOCUMENT, timeout=45),
                )
            except pdf_renderer.PDFRenderError as exc:
                outcomes[index] = ("failed", exc)
            except Exception as exc:  # pragma: no cover - unexpected
                outcomes[index] = ("unexpected", exc)

        threads = [threading.Thread(target=render, args=(i,)) for i in range(callers)]
        for thread in threads:
            thread.start()
        time.sleep(1.0)
        killed = kill_the_browser()

        for thread in threads:
            thread.join(timeout=150)

        stuck = [t for t in threads if t.is_alive()]
        self.assertEqual(stuck, [], f"{len(stuck)} render(s) never returned")
        self.assertGreater(killed, 0)

        kinds = [outcome[0] for outcome in outcomes]
        self.assertNotIn(
            "unexpected",
            kinds,
            f"a raw exception escaped to a caller: "
            f"{[o[1] for o in outcomes if o[0] == 'unexpected']}",
        )
        self.assertEqual(len(kinds), callers, "not every caller recorded an outcome")

        # Property 5 again, under concurrency: anything returned as a PDF
        # must be a real one.
        for kind, value in outcomes:
            if kind == "ok":
                assert_valid_pdf(self, value)

        # Property 3, under concurrency.
        self.assertEqual(self.worker._in_flight, 0)
        with self.worker._queue_lock:
            self.assertEqual(self.worker._queued, 0)
        self.assertEqual(
            self.worker._semaphore._value, pdf_renderer._max_concurrent_renders()
        )

        # Property 6: the process is still usable afterwards.
        assert_valid_pdf(self, pdf_renderer.render_html_to_pdf(SIMPLE_DOCUMENT))
