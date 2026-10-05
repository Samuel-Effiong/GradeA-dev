"""H-110: Playwright's Node driver never gets this process's own stderr.

Node sets a pipe it is given as stderr non-blocking, and that setting
belongs to the pipe: every process sharing it then loses or cuts log lines
written while the pipe is full, with no error to the caller. Observed in a
real gunicorn and a real Celery worker after ONE render (2026-10-05). The
renderer now gives the driver a pipe of its own and reads it into the log.

Two layers here; the pipe and its reader thread on their own are in
tests_pdf_renderer_driver_stderr_reader.
  * TheProcessStderrStaysBlockingTest - the real renderer in a FRESH
    interpreter whose stderr is a pipe this test owns. The pipe's mode is
    read from the test's own end while the driver is alive, and again after
    the driver is SIGKILLed (the case that used to stay broken for good).
    A fresh interpreter, because replacing fd 2 of a test process would
    take that process's whole output with it.
  * PlaywrightStderrHookPinTest - the private Playwright function the fix
    relies on still exists and is still what the driver's stderr comes
    from. If this fails after an upgrade, re-check H-110 before anything.
"""

import inspect
import json
import os
import select
import signal
import subprocess
import sys
import time
import unittest

from django.conf import settings
from django.test import SimpleTestCase
from playwright._impl import _transport

from assignments.tests_pdf_renderer import _CHROMIUM_AVAILABLE

#: Runs in the fresh interpreter: one render with the real renderer and one
#: line of facts on stdout. Then it stays alive, so the driver does too,
#: and answers each line on stdin with whether the renderer's reader thread
#: is still reading. It ends when stdin is closed.
CHILD = r"""
import json, os, sys
from django.conf import settings
settings.configure(DEBUG=False)
from playwright._impl import _transport
original = _transport._get_stderr_fileno
from assignments import pdf_renderer
pdf = pdf_renderer.render_html_to_pdf(
    "<html><head><title>h110</title></head><body><p>one page</p></body></html>"
)
worker = pdf_renderer._get_worker()
driver = worker._playwright._impl_obj._connection._transport._proc
stderr_pipe = getattr(worker, "_driver_stderr", None)
print(json.dumps({
    "pdf_magic": pdf[:5].decode("latin1"),
    "driver_pid": driver.pid,
    "hook_is_the_original_again": _transport._get_stderr_fileno is original,
    "handed_over": getattr(stderr_pipe, "handed_over", None),
    "fd2_blocking_seen_inside": os.get_blocking(2),
}), flush=True)
for _ in sys.stdin:
    reading = stderr_pipe.is_reading() if stderr_pipe is not None else None
    print(json.dumps({"reader_is_reading": reading}), flush=True)
"""


def _parent_pid(pid):
    with open(f"/proc/{pid}/stat") as f:
        return int(f.read().rsplit(")", 1)[1].split()[1])


@unittest.skipUnless(
    _CHROMIUM_AVAILABLE,
    "Headless Chromium not available in this environment - install it "
    "with `playwright install chromium` to run these tests.",
)
class TheProcessStderrStaysBlockingTest(SimpleTestCase):
    def setUp(self):
        self.read_end, self.write_end = os.pipe()
        self.addCleanup(os.close, self.read_end)
        self.addCleanup(os.close, self.write_end)
        self.child = subprocess.Popen(
            [sys.executable, "-c", CHILD],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.write_end,
            cwd=str(settings.BASE_DIR),
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            text=True,
        )
        assert self.child.stdin and self.child.stdout  # narrows for mypy
        self.to_child, self.from_child = self.child.stdin, self.child.stdout
        self.addCleanup(self.end_child)
        ready, _, _ = select.select([self.from_child], [], [], 120)
        self.assertTrue(ready, "the fresh interpreter never reported its render")
        line = self.from_child.readline()
        self.assertTrue(line, "the fresh interpreter ended before reporting")
        self.facts = json.loads(line)

    def end_child(self):
        try:
            self.to_child.close()  # end of stdin ends the child
            self.child.wait(timeout=30)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.child.kill()  # this test's own child, by its Popen handle
            self.child.wait()
        finally:
            self.from_child.close()

    def ask_child(self):
        self.to_child.write("\n")
        self.to_child.flush()
        ready, _, _ = select.select([self.from_child], [], [], 30)
        self.assertTrue(ready, "the fresh interpreter stopped answering")
        return json.loads(self.from_child.readline())

    def stderr_is_blocking(self):
        # The mode belongs to the pipe's write end, which the test shares
        # with the child, so the test's own descriptor shows it.
        return os.get_blocking(self.write_end)

    def test_while_the_driver_is_alive_the_stderr_pipe_stays_blocking(self):
        self.assertEqual(self.facts["pdf_magic"], "%PDF-")
        self.assertIsNone(self.child.poll())
        self.assertTrue(
            self.stderr_is_blocking(),
            "the driver was given this process's stderr and made it non-blocking",
        )
        self.assertIs(self.facts["fd2_blocking_seen_inside"], True)

    def test_playwright_took_our_pipe_and_its_hook_is_put_back(self):
        self.assertIs(self.facts["handed_over"], True)
        self.assertIs(self.facts["hook_is_the_original_again"], True)

    def test_after_the_driver_is_killed_the_stderr_pipe_is_still_blocking(self):
        """Before the fix a SIGKILLed driver left the pipe non-blocking for
        as long as the service lived: Node never got to put it back."""
        driver_pid = self.facts["driver_pid"]
        self.assertEqual(_parent_pid(driver_pid), self.child.pid)
        self.assertIs(self.ask_child()["reader_is_reading"], True)
        os.kill(driver_pid, signal.SIGKILL)
        time.sleep(1.0)
        self.assertTrue(self.stderr_is_blocking())
        # The renderer closed its own copy of the pipe's write end after the
        # start, so the dead driver's was the last: the reader has ended
        # and is not left waiting for a process that is gone.
        self.assertIs(self.ask_child()["reader_is_reading"], False)


class PlaywrightStderrHookPinTest(SimpleTestCase):
    """The fix replaces a PRIVATE Playwright function for the length of one
    start. Playwright may rename or inline it in any release."""

    WHAT_TO_DO = (
        "Playwright no longer takes the driver's stderr from "
        "playwright._impl._transport._get_stderr_fileno(). The renderer "
        "then hands the driver this process's own stderr again (H-110: log "
        "lines lost while a render process lives). Find where the driver's "
        "stderr now comes from and update _DriverStderr.handed_to_playwright."
    )

    def test_the_hook_exists(self):
        self.assertTrue(
            callable(getattr(_transport, "_get_stderr_fileno", None)),
            self.WHAT_TO_DO,
        )

    def test_the_driver_is_started_with_what_the_hook_returns(self):
        source = inspect.getsource(_transport.PipeTransport.connect)
        self.assertIn("stderr=_get_stderr_fileno()", source, self.WHAT_TO_DO)
        self.assertIn("create_subprocess_exec", source, self.WHAT_TO_DO)
