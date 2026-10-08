"""H-191: the HTML sanitizer must give each caller its own text when many
threads call it at once.

`sanitize_editor_html` used ONE module-level bleach.Cleaner for every call.
Bleach documents that a Cleaner is not thread-safe: it keeps one html5lib
parser, whose tree builder (open elements, the document) is replaced at the
start of every parse and read again at the end. Two threads inside the parser
at once can therefore (a) raise html5lib's own `assert False # We should never
reach this point` (seen on CI, in students.tests_grading_redelivery_live), and
(b) hand one caller the other's text (by reading of the library; not
observed). The cleaner runs on student answers,
teacher assignment text and graded documents, on gunicorn's four threads per
worker and on the task workers.

These tests call the real functions from many threads at once, with the thread
switch interval made tiny so that the threads really interleave inside the
parser, and compare every output with the same input's single-thread output.
They are pure Python (no database).
"""

import faulthandler
import json
import sys
import threading
import time

from django.test import SimpleTestCase

from assignments.prosemirror_converter import (
    _cached_prosemirror_text,
    html_to_prosemirror_json,
    html_to_prosemirror_text,
    sanitize_editor_html,
)

INPUTS = 12
ROUNDS = 60
#: The end-to-end conversions (lxml + ProseMirror on top of the sanitizer) cost
#: about ten times as much per call, so they run fewer rounds: on the cured
#: code the 60-round version needed ~110 s of the 120 s deadline and once
#: failed it (rate run, tip 1678fa6a, run 4). Fewer rounds still interleave:
#: the barrier starts all threads together and the switch interval is 1 us.
SLOW_ROUNDS = 12
THREADS = 8


def one_input(i):
    """A distinct, structured input; every third one is hostile."""
    body = (
        f"<p>MARK-{i:02d}-alpha <b>bold {i}</b> <em>it {i}</em></p>"
        f"<ul><li>item {i}a</li><li>item {i}b</li></ul>"
        f"<table><tr><td>cell {i}</td><td>cell {i}x</td></tr></table>"
    )
    if i % 3 == 0:
        body += (
            f"<p>HOSTILE-{i:02d} <img src=x onerror=alert({i})>"
            f"<script>evil{i}()</script></p>"
        )
    return body


ALL_INPUTS = [one_input(i) for i in range(INPUTS)]


#: How long the threads may take before the run is called HUNG. On the shared
#: Cleaner the corrupted parser has been seen to leave threads spinning for
#: ever (11 minutes at 178% CPU in the first measured run, H-191): the test
#: must fail, not wait. The threads are daemons so the process can still exit.
HUNG_AFTER_SECONDS = 120
_threads_left_spinning = []


def run_in_threads(function, rounds=ROUNDS):
    """(results, errors): every thread calls `function` on every input,
    ROUNDS times, starting at a different input; all start together and the
    interpreter switches threads as often as it can. A thread that is still
    running after HUNG_AFTER_SECONDS is reported as an error, not waited for."""
    if _threads_left_spinning:
        raise AssertionError(
            "an earlier test in this process left %d thread(s) spinning"
            % len(_threads_left_spinning)
        )
    results: list = []
    errors: list = []
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def work(offset):
        barrier.wait()
        for round_number in range(rounds):
            for step in range(INPUTS):
                index = (offset + round_number + step) % INPUTS
                try:
                    got = function(ALL_INPUTS[index])
                except Exception as exc:  # noqa: BLE001 - recorded, asserted on
                    with lock:
                        errors.append((index, type(exc).__name__))
                else:
                    with lock:
                        results.append((index, got))

    threads = [
        threading.Thread(target=work, args=(offset,), daemon=True)
        for offset in range(THREADS)
    ]
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + HUNG_AFTER_SECONDS
        for thread in threads:
            thread.join(max(0.0, deadline - time.monotonic()))
    finally:
        sys.setswitchinterval(previous)
    alive = [thread for thread in threads if thread.is_alive()]
    if alive:
        # Where are they? Every thread's stack goes to stderr (the run's log),
        # so a hang shows whether a thread is inside bleach or html5lib.
        faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
        _threads_left_spinning.extend(alive)
        with lock:
            errors.append(("hung", len(alive)))
    return results, errors


class TheSanitizerKeepsEachCallersTextToItself(SimpleTestCase):
    def setUp(self):
        self.expected = [sanitize_editor_html(text) for text in ALL_INPUTS]

    def test_the_single_thread_outputs_are_what_the_comparison_needs(self):
        """The deciding values: every expected output exists and carries its own
        marker, so a mixed or empty output cannot equal it by accident."""
        for i, text in enumerate(self.expected):
            with self.subTest(input=i):
                self.assertTrue(text)
                self.assertIn(f"MARK-{i:02d}-alpha", text)
                for j in range(INPUTS):
                    if j != i:
                        self.assertNotIn(f"MARK-{j:02d}-alpha", text)

    def test_many_threads_raise_nothing_and_every_output_is_its_own(self):
        results, errors = run_in_threads(sanitize_editor_html)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), THREADS * ROUNDS * INPUTS)
        wrong = [i for i, got in results if got != self.expected[i]]
        self.assertEqual(wrong, [])
        # And, separately, nobody's output carries another input's marker
        # (the markers are non-empty and distinct: the first test proves it).
        foreign = [
            (i, j)
            for i, got in results
            for j in range(INPUTS)
            if j != i and f"MARK-{j:02d}-alpha" in got
        ]
        self.assertEqual(foreign, [])

    def test_the_hostile_inputs_stay_clean_in_every_thread(self):
        results, errors = run_in_threads(sanitize_editor_html)

        self.assertEqual(errors, [])
        hostile = [(i, got) for i, got in results if i % 3 == 0]
        self.assertTrue(hostile)
        for i, got in hostile:
            self.assertEqual(got, self.expected[i])
            self.assertIn(f"HOSTILE-{i:02d}", got)
            self.assertNotIn("<script", got.lower())
            self.assertNotIn("onerror", got.lower())
            self.assertNotIn("evil", got.lower())

    def test_the_whole_conversion_gives_each_thread_its_own_document(self):
        """The real function the writers call, end to end (sanitize, parse,
        ProseMirror document), compared document with document."""
        expected = [html_to_prosemirror_json(text) for text in ALL_INPUTS]
        for document in expected:
            self.assertTrue(document.get("content"))

        results, errors = run_in_threads(html_to_prosemirror_json, SLOW_ROUNDS)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), THREADS * SLOW_ROUNDS * INPUTS)
        wrong = [i for i, got in results if got != expected[i]]
        self.assertEqual(wrong, [])

    def test_a_wrong_result_is_never_kept_in_the_conversion_cache(self):
        """html_to_prosemirror_text keeps the last 64 conversions in a
        process-local cache keyed on the exact HTML, ABOVE the sanitizer: a
        mixed result made by a race would be kept under its victim's input
        and served again to every later request for that text until the
        process restarts. Run the threads on the cached function, then read
        every input back, single-threaded, from the cache the threads
        filled: each must be its own document."""
        _cached_prosemirror_text.cache_clear()
        self.addCleanup(_cached_prosemirror_text.cache_clear)
        expected = {text: html_to_prosemirror_json(text) for text in ALL_INPUTS}
        _cached_prosemirror_text.cache_clear()

        results, errors = run_in_threads(html_to_prosemirror_text, SLOW_ROUNDS)

        self.assertEqual(errors, [])
        self.assertTrue(results)
        info = _cached_prosemirror_text.cache_info()
        self.assertGreater(info.currsize, 0)
        for text in ALL_INPUTS:
            with self.subTest(text=text[:20]):
                self.assertEqual(
                    json.loads(html_to_prosemirror_text(text)), expected[text]
                )
