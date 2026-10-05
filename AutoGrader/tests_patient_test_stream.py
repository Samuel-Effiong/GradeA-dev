"""
H-107 (finding B): the test run's result stream waits when a write would
block; it does not raise.

A run's output often goes to a pipe (a timestamper, CI's log collector).
Every process that inherits the pipe shares one open file, mode included.
Playwright starts its Node driver with the test process's own stderr, and
Node puts a pipe it is given into non-blocking mode: for every holder,
while the driver lives, and for good if the driver is killed with SIGKILL
(shown outside the suite, 2026-10-05). When the reader is behind and the
pipe is full, the parent's next write of a test's name raised
BlockingIOError (observed, 2026-10-05). It left Django's parallel result
loop, its traceback could not be printed (same pipe), and the run ended
with no output; before H-107's worker fix it then hung.

AutoGrader/testing/patient_stream.py wraps the stream the runner reports
on. A write that would block waits until the pipe can take more, and
carries on where it stopped: nothing lost, nothing twice, order kept. A
reader that takes nothing for PATIENCE seconds ends the run with
OutputNotRead instead of a new hang.
"""

import importlib
import io
import os
import sys
import threading
import time

from django.test import SimpleTestCase

PIPE_AT_LEAST = 70_000  # more than a Linux pipe holds (65,536 bytes)


def non_blocking_pipe():
    """(read fd, a text stream on the write end) with the write end left
    non-blocking, as a Node child leaves the run's stderr. The stream is
    built like sys.stderr: line-buffered text over a buffered writer."""
    read_fd, write_fd = os.pipe()
    os.set_blocking(write_fd, False)
    stream = io.TextIOWrapper(
        io.BufferedWriter(io.FileIO(write_fd, "w")),
        encoding="utf-8",
        errors="backslashreplace",
        line_buffering=True,
    )
    return read_fd, stream


class Reader(threading.Thread):
    """Reads the pipe to its end, starting late and in small pieces: a
    reader that is behind.

    It reads through a descriptor of its own (a dup). The test closes its
    read end when it finishes, and the next test's pipe may be given the
    same number: a reader still running on the test's number would then
    read the next test's pipe (seen once, 2026-10-05: the test with no
    reader had one)."""

    def __init__(self, read_fd, start_after=0.3):
        super().__init__(daemon=True)
        self.read_fd = os.dup(read_fd)
        self.start_after = start_after
        self.data = b""

    def run(self):
        try:
            time.sleep(self.start_after)
            while True:
                piece = os.read(self.read_fd, 4096)
                if not piece:
                    return
                self.data += piece
        finally:
            os.close(self.read_fd)


class PatientStreamTestCase(SimpleTestCase):
    def setUp(self):
        self.module = importlib.import_module("AutoGrader.testing.patient_stream")

    def pipe(self):
        read_fd, stream = non_blocking_pipe()
        self.addCleanup(self.close_quietly, stream)
        self.addCleanup(self.close_fd_quietly, read_fd)
        return read_fd, stream

    @staticmethod
    def close_quietly(stream):
        try:
            stream.close()
        except (OSError, ValueError):
            pass

    @staticmethod
    def close_fd_quietly(fd):
        try:
            os.close(fd)
        except OSError:
            pass


class AWriteThatWouldBlockWaitsTests(PatientStreamTestCase):
    LINES = [f"test_number_{n} (some.module.SomeTests) ... ok\n" for n in range(2000)]

    def test_the_condition_is_real_a_plain_stream_raises(self):
        """The control: without the wrapper, the same writes to the same
        kind of pipe raise, as the run's parent did."""
        self.assertGreater(sum(map(len, self.LINES)), PIPE_AT_LEAST)
        _read_fd, stream = self.pipe()

        with self.assertRaises(BlockingIOError):
            for line in self.LINES:
                stream.write(line)

    def test_everything_arrives_once_and_in_order(self):
        read_fd, stream = self.pipe()
        reader = Reader(read_fd)
        reader.start()
        patient = self.module.PatientStream(stream)

        for line in self.LINES:
            patient.write(line)
        patient.flush()
        stream.close()
        reader.join(timeout=30)

        self.assertFalse(reader.is_alive(), "the reader never saw the end")
        self.assertEqual(reader.data.decode("utf-8"), "".join(self.LINES))

    def test_one_write_larger_than_the_pipe_arrives_whole(self):
        read_fd, stream = self.pipe()
        reader = Reader(read_fd)
        reader.start()
        text = "".join(self.LINES)

        written = self.module.PatientStream(stream).write(text)
        stream.close()
        reader.join(timeout=30)

        self.assertEqual(written, len(text))
        self.assertEqual(reader.data.decode("utf-8"), text)

    def test_what_the_stream_itself_still_holds_goes_out_first(self):
        """Something written through the underlying stream and still in
        its buffer (a write that could not be flushed) must come out
        before what the wrapper writes next."""
        read_fd, stream = self.pipe()
        fill = b"." * 65_536
        self.assertEqual(os.write(stream.fileno(), fill), len(fill))  # full
        stream.buffer.write(b"FIRST\n")  # accepted into the buffer, not sent
        reader = Reader(read_fd)
        reader.start()

        self.module.PatientStream(stream).write("SECOND\n")
        stream.close()
        reader.join(timeout=30)

        self.assertEqual(reader.data[len(fill) :], b"FIRST\nSECOND\n")

    def test_text_that_cannot_be_encoded_is_written_as_the_stream_would(self):
        read_fd, stream = self.pipe()
        reader = Reader(read_fd, start_after=0)
        reader.start()

        self.module.PatientStream(stream).write("caf\u00e9 \udcff\n")
        stream.close()
        reader.join(timeout=30)

        self.assertEqual(reader.data, "caf\u00e9 \\udcff\n".encode("utf-8"))


class AReaderThatTakesNothingTests(PatientStreamTestCase):
    def write_in_a_thread(self, patient, text):
        outcome: dict = {}

        def write():
            try:
                patient.write(text)
                outcome["returned"] = True
            except BaseException as exc:  # noqa: BLE001 - reported below
                outcome["raised"] = exc

        thread = threading.Thread(target=write, daemon=True)
        started = time.monotonic()
        thread.start()
        thread.join(timeout=10)
        outcome["seconds"] = time.monotonic() - started
        outcome["still_waiting"] = thread.is_alive()
        return outcome

    def test_it_gives_up_loudly_after_its_patience_not_never(self):
        _read_fd, stream = self.pipe()  # nobody reads
        patient = self.module.PatientStream(stream, patience=0.3)

        outcome = self.write_in_a_thread(patient, "x" * (PIPE_AT_LEAST * 2))

        self.assertFalse(outcome["still_waiting"], "it waits for ever: a new hang")
        self.assertIsInstance(outcome.get("raised"), self.module.OutputNotRead)
        self.assertIsInstance(outcome["raised"], OSError)
        self.assertIn("has not been read", str(outcome["raised"]))
        self.assertLess(outcome["seconds"], 5)

    def test_a_reader_that_is_slow_but_reading_is_waited_for(self):
        """The patience is per wait, not for the whole write: a reader
        that keeps taking a little is never given up on."""
        read_fd, stream = self.pipe()
        reader = Reader(read_fd, start_after=0.2)
        reader.start()
        patient = self.module.PatientStream(stream, patience=1.0)

        outcome = self.write_in_a_thread(patient, "y" * (PIPE_AT_LEAST * 3))
        stream.close()
        reader.join(timeout=30)

        self.assertTrue(outcome.get("returned"), outcome)
        self.assertFalse(reader.is_alive(), "the reader never saw the end")
        self.assertEqual(len(reader.data), PIPE_AT_LEAST * 3)

    def test_a_reader_that_has_gone_is_an_error_at_once(self):
        read_fd, stream = self.pipe()
        os.close(read_fd)
        patient = self.module.PatientStream(stream, patience=5)

        outcome = self.write_in_a_thread(patient, "z\n")

        self.assertIsInstance(outcome.get("raised"), BrokenPipeError)
        self.assertLess(outcome["seconds"], 2)

    def test_the_default_patience(self):
        """Longer than any pause of a live reader; shorter than the five
        minutes of silence after which the watchdog (rule 18) steps in, so
        the run ends itself first."""
        self.assertEqual(self.module.PATIENCE, 120)
        _read_fd, stream = self.pipe()
        self.assertEqual(self.module.PatientStream(stream).patience, 120)


class OtherStreamsTests(PatientStreamTestCase):
    def test_a_stream_with_no_file_descriptor_is_written_to_as_it_is(self):
        target = io.StringIO()
        patient = self.module.PatientStream(target)

        patient.write("one\n")
        patient.flush()

        self.assertEqual(target.getvalue(), "one\n")

    def test_everything_else_is_the_streams_own(self):
        _read_fd, stream = self.pipe()
        patient = self.module.PatientStream(stream)

        self.assertEqual(patient.fileno(), stream.fileno())
        self.assertEqual(patient.encoding, "utf-8")
        self.assertFalse(patient.isatty())


class WiringTests(PatientStreamTestCase):
    def test_the_runner_reports_on_a_patient_stream_over_stderr(self):
        from AutoGrader.redis_test_runner import RedisHygieneRunner

        stream = RedisHygieneRunner().get_test_runner_kwargs().get("stream")

        self.assertIsInstance(stream, self.module.PatientStream)
        assert stream is not None
        self.assertIs(stream.stream, sys.stderr)

    def test_the_stream_is_whatever_stderr_is_when_the_run_is_set_up(self):
        """Not the interpreter's original stderr. A run whose sys.stderr
        has been replaced (a wrapper, a capture) must report on the
        replacement; the test above cannot tell the two apart, because in
        an ordinary run they are the same object (1a's mutant, H-107)."""
        from AutoGrader.redis_test_runner import RedisHygieneRunner

        replacement = io.StringIO()
        original = sys.stderr
        sys.stderr = replacement
        try:
            # enable_faulthandler=False: the runner's constructor would
            # otherwise hand faulthandler the replacement's descriptor,
            # and this one has none.
            runner = RedisHygieneRunner(enable_faulthandler=False)
            stream = runner.get_test_runner_kwargs().get("stream")
        finally:
            sys.stderr = original

        self.assertIsNot(replacement, sys.__stderr__)
        self.assertIsInstance(stream, self.module.PatientStream)
        assert stream is not None
        self.assertIs(stream.stream, replacement)

    def test_unittests_runner_writes_through_it(self):
        import unittest

        from AutoGrader.redis_test_runner import RedisHygieneRunner

        read_fd, stream = self.pipe()
        reader = Reader(read_fd, start_after=0)
        reader.start()
        kwargs = RedisHygieneRunner(verbosity=0).get_test_runner_kwargs()
        kwargs["stream"] = self.module.PatientStream(stream)
        kwargs.pop("resultclass", None)

        unittest.TextTestRunner(**kwargs).stream.writeln("a line from the runner")
        stream.close()
        reader.join(timeout=30)

        self.assertEqual(reader.data, b"a line from the runner\n")
