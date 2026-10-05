"""H-110: the Node driver's own stderr pipe and the thread that reads it
into the log, without a browser. (The real renderer in a fresh interpreter,
and the pin on Playwright's private hook, are in
tests_pdf_renderer_driver_stderr.)

The reader is on the render path: if it stopped reading, the pipe would
fill and the driver's writes would stall. So these tests give it what a
driver and a Chromium can write - bytes that are not UTF-8, a last line
with no newline, a line longer than the pipe, a burst while the log is
slow, a log that raises - and require it to keep reading until the stream
ends, and only then to end.
"""

import os
import threading
import time
from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from playwright._impl import _transport

from assignments import pdf_renderer
from assignments.pdf_renderer import _DriverStderr

LOGGER = "assignments.pdf_renderer"
PREFIX = "[PDF] Playwright driver stderr: "


class HookHandOverTest(SimpleTestCase):
    def setUp(self):
        self.original = _transport._get_stderr_fileno
        self.pipe = _DriverStderr()
        self.addCleanup(self.pipe.close)

    def test_inside_playwright_is_given_our_write_end(self):
        self.assertIs(self.pipe.handed_over, False)
        with self.pipe.handed_to_playwright():
            self.assertEqual(_transport._get_stderr_fileno(), self.pipe.write_fd)
        self.assertIs(self.pipe.handed_over, True)
        self.assertIs(_transport._get_stderr_fileno, self.original)

    def test_the_hook_is_put_back_when_the_start_fails(self):
        with self.assertRaises(RuntimeError):
            with self.pipe.handed_to_playwright():
                raise RuntimeError("the driver did not start")
        self.assertIs(_transport._get_stderr_fileno, self.original)
        self.assertIs(self.pipe.handed_over, False)

    def test_a_second_start_waits_and_restores_the_real_hook(self):
        """Two starts must not overlap: the second would save the first's
        replacement as "the original" and put THAT back."""
        other = _DriverStderr()
        self.addCleanup(other.close)
        entered = threading.Event()
        seen = []

        def second():
            with other.handed_to_playwright():
                entered.set()
                seen.append(_transport._get_stderr_fileno())

        with self.pipe.handed_to_playwright():
            thread = threading.Thread(target=second, daemon=True)
            thread.start()
            self.assertFalse(entered.wait(0.3), "two starts overlapped")
            self.assertEqual(_transport._get_stderr_fileno(), self.pipe.write_fd)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(seen, [other.write_fd])
        self.assertIs(_transport._get_stderr_fileno, self.original)

    def test_without_the_hook_the_start_still_goes_ahead(self):
        with patch.object(_transport, "_get_stderr_fileno", None):
            with self.pipe.handed_to_playwright():
                self.assertIsNone(_transport._get_stderr_fileno)
        self.assertIs(self.pipe.handed_over, False)
        self.assertIs(_transport._get_stderr_fileno, self.original)


class DriverStderrReaderTest(SimpleTestCase):
    def read(self, *chunks, close_timeout=10.0):
        """The log messages the reader makes of `chunks` written to the
        driver's end of the pipe, in order."""
        pipe = _DriverStderr()
        with self.assertLogs(LOGGER, level="WARNING") as caught:
            pdf_renderer.logger.warning("sentinel, so that an empty read passes")
            for chunk in chunks:
                os.write(pipe.write_fd, chunk)
            pipe.close(timeout=close_timeout)
        self.assertFalse(pipe.is_reading(), "the reader did not end with the stream")
        return [record.getMessage() for record in caught.records[1:]]

    def test_each_line_becomes_one_warning(self):
        self.assertEqual(
            self.read(b"first line\nsecond line\n"),
            [PREFIX + "first line", PREFIX + "second line"],
        )

    def test_a_line_split_across_writes_is_one_warning(self):
        self.assertEqual(self.read(b"half a ", b"line\n"), [PREFIX + "half a line"])

    def test_a_last_line_with_no_newline_is_not_lost(self):
        self.assertEqual(
            self.read(b"done\nlast words"), [PREFIX + "done", PREFIX + "last words"]
        )

    def test_blank_lines_are_not_logged(self):
        self.assertEqual(self.read(b"\n\n   \nreal\n\n"), [PREFIX + "real"])

    def test_bytes_that_are_not_utf8_do_not_stop_the_reader(self):
        messages = self.read(b"\xff\xfe broken \x80 text\n", b"after it\n")
        self.assertEqual(len(messages), 2)
        self.assertIn("broken", messages[0])
        self.assertEqual(messages[1], PREFIX + "after it")

    def test_control_characters_are_replaced(self):
        [message] = self.read(b"red \x1b[31malert\x07\n")
        self.assertNotIn("\x1b", message)
        self.assertNotIn("\x07", message)
        self.assertEqual(message, PREFIX + "red ?[31malert?")

    def test_a_long_line_is_cut(self):
        [message, after] = self.read(b"x" * 5000 + b"\n", b"after\n")
        self.assertEqual(
            message, PREFIX + "x" * pdf_renderer.DRIVER_STDERR_MAX_LINE + " [cut]"
        )
        self.assertEqual(after, PREFIX + "after")

    def test_a_line_longer_than_the_pipe_is_logged_once_and_the_rest_dropped(self):
        messages = self.read(b"y" * 200_000 + b" tail of it\n", b"next\n")
        self.assertEqual(
            messages,
            [
                PREFIX + "y" * pdf_renderer.DRIVER_STDERR_MAX_LINE + " [cut]",
                PREFIX + "next",
            ],
        )

    @override_settings(LOG_SCRUB_ADDRESSES=True)
    def test_a_line_is_scrubbed_like_any_log_line(self):
        """The driver can print URLs and page text. Its lines go through
        the same record factory as every other log line (H-89)."""
        address = "pupil.name" + "@" + "school.example"
        # Built here so that no URL with a password is written in the repo.
        dsn = "postgres" + "://" + "render" + ":" + "hunter2" + "@" + "db.internal/app"
        [message] = self.read(f"navigation failed for {address} via {dsn}\n".encode())
        self.assertNotIn(address, message)
        self.assertNotIn("hunter2", message)
        self.assertIn("[email]", message)
        self.assertIn("[credentials]", message)

    def test_a_burst_larger_than_the_pipe_arrives_whole_with_a_slow_logger(self):
        """The reader is on the render path: while the log is slow the
        driver's writes wait, and nothing is dropped."""
        lines = [f"burst line {i:05d} ".encode() + b"z" * 40 for i in range(3000)]
        self.assertGreater(sum(len(line) + 1 for line in lines), 2 * 65536)
        got = []

        def slow_warning(_template, text):
            time.sleep(0.0005)
            got.append(text)

        pipe = _DriverStderr()
        with patch.object(pdf_renderer.logger, "warning", slow_warning):
            writer = threading.Thread(
                target=lambda: [
                    os.write(pipe.write_fd, line + b"\n") for line in lines
                ],
                daemon=True,
            )
            writer.start()
            writer.join(timeout=60)
            self.assertFalse(writer.is_alive(), "the reader stopped draining the pipe")
            pipe.close(timeout=60)
        self.assertFalse(pipe.is_reading())
        self.assertEqual(got, [line.decode() for line in lines])

    def test_a_logger_that_raises_does_not_stop_the_reader(self):
        calls = []

        def failing_warning(_template, text):
            calls.append(text)
            raise OSError("the log is unavailable")

        pipe = _DriverStderr()
        with patch.object(pdf_renderer.logger, "warning", failing_warning):
            os.write(pipe.write_fd, b"one\ntwo\nthree\n")
            pipe.close(timeout=10)
        self.assertFalse(pipe.is_reading())
        self.assertEqual(calls, ["one", "two", "three"])


class DriverStderrDescriptorsTest(SimpleTestCase):
    def open_descriptors(self):
        return len(os.listdir("/proc/self/fd"))

    def test_close_ends_the_reader_and_closes_both_ends(self):
        pipe = _DriverStderr()
        read_fd, write_fd = pipe._read_fd, pipe.write_fd
        self.assertTrue(pipe.is_reading())
        pipe.close(timeout=10)
        self.assertFalse(pipe.is_reading())
        for fd in (read_fd, write_fd):
            with self.assertRaises(OSError):
                os.fstat(fd)

    def test_close_twice_is_harmless(self):
        pipe = _DriverStderr()
        pipe.close(timeout=10)
        # A descriptor number is reused: a second os.close would close
        # whatever was opened in between.
        reused, other_end = os.pipe()
        self.addCleanup(os.close, reused)
        self.addCleanup(os.close, other_end)
        pipe.close(timeout=10)
        os.fstat(reused)
        os.fstat(other_end)

    def test_repeated_starts_leak_no_descriptor(self):
        with self.assertLogs(LOGGER, level="WARNING") as caught:
            _DriverStderr().close(timeout=10)  # anything opened lazily is open now
            pdf_renderer.logger.warning("sentinel")
            before = self.open_descriptors()
            for _ in range(25):
                pipe = _DriverStderr()
                os.write(pipe.write_fd, b"a line\n")
                pipe.close(timeout=10)
                self.assertFalse(pipe.is_reading())
            self.assertEqual(self.open_descriptors(), before)
        self.assertEqual(len(caught.records), 26)

    def test_the_reader_outlives_our_write_end_while_the_driver_has_one(self):
        """close_write_end() is what the renderer calls right after the
        start: the driver's own copy keeps the stream open."""
        pipe = _DriverStderr()
        drivers_copy = os.dup(pipe.write_fd)
        pipe.close_write_end()
        with self.assertLogs(LOGGER, level="WARNING") as caught:
            os.write(drivers_copy, b"still here\n")
            deadline = time.time() + 10
            while not caught.records and time.time() < deadline:
                time.sleep(0.01)
            self.assertTrue(pipe.is_reading())
            os.close(drivers_copy)
            pipe.close(timeout=10)
        self.assertFalse(pipe.is_reading())
        self.assertEqual(
            [r.getMessage() for r in caught.records], [PREFIX + "still here"]
        )
