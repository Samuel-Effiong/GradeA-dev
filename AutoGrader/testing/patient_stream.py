"""
The stream a test run reports on: a write that would block waits (H-107).
Test-only, never imported by production code.

WHY
---
A run's output often goes to a pipe: a timestamper, CI's log collector.
Every process that inherits the pipe shares one open file, and its mode
with it. Playwright starts its Node driver with the test process's own
stderr, and Node puts a pipe it is given into non-blocking mode: for every
holder while the driver lives, and for good if the driver is killed with
SIGKILL. When the reader is behind and the pipe is full, a plain write
raises BlockingIOError. In the parent of a parallel run that error left
Django's result loop while it was printing a test's name; its traceback
could not be printed either (same pipe), so the run ended with no output.

WHAT IT DOES
------------
`PatientStream` wraps the runner's stream (sys.stderr). It writes the
encoded text to the stream's file descriptor itself, so it always knows
how much went out: when the pipe is full it waits until the pipe can take
more and carries on from that byte. Nothing is lost, nothing is written
twice, and the order is kept. (Python's own text layer cannot promise
that: after a failed flush it does not know how much was written.)
Whatever the underlying stream still holds is flushed first, with the
same patience.

IT DOES NOT WAIT FOR EVER
-------------------------
A reader that has gone is an error at once (BrokenPipeError). A reader
that is there but takes nothing for PATIENCE seconds ends the write with
OutputNotRead, so a stuck reader fails the run instead of hanging it. The
patience is per wait: a slow reader that keeps reading is never given up
on.
"""

import io
import os
import select

#: Seconds one wait may last with the reader taking nothing. Longer than
#: any pause of a live reader (a timestamper on a loaded machine); shorter
#: than the five minutes of silence after which the gate's watchdog steps
#: in (rule 18), so the run ends itself, with a reason, first.
PATIENCE = 120


class OutputNotRead(OSError):
    """The test run's output has not been read for the whole patience."""


class PatientStream:
    def __init__(self, stream, patience=PATIENCE):
        self.stream = stream
        self.patience = patience

    def __getattr__(self, name):
        if name == "stream":  # not set yet (copying, unpickling)
            raise AttributeError(name)
        return getattr(self.stream, name)

    def _fd(self):
        try:
            return self.stream.fileno()
        except (AttributeError, OSError, ValueError, io.UnsupportedOperation):
            return None

    def _wait(self, fd):
        _, writable, _ = select.select([], [fd], [], self.patience)
        if not writable:
            raise OutputNotRead(
                f"The test run's output has not been read for {self.patience} "
                "seconds: whatever reads this run's output (a pipe to a "
                "timestamper or a log collector) has stopped taking it."
            )

    def flush(self):
        fd = self._fd()
        while True:
            try:
                return self.stream.flush()
            except BlockingIOError:
                if fd is None:
                    raise
                self._wait(fd)

    def write(self, text):
        fd = self._fd()
        if fd is None:
            return self.stream.write(text)
        # What the stream itself still holds goes out first.
        self.flush()
        data = memoryview(
            text.encode(
                getattr(self.stream, "encoding", None) or "utf-8",
                getattr(self.stream, "errors", None) or "backslashreplace",
            )
        )
        while data:
            try:
                written = os.write(fd, data)
            except BlockingIOError:
                self._wait(fd)
                continue
            data = data[written:]
        return len(text)
