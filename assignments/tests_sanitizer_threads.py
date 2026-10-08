"""H-191: the HTML sanitizer must give each caller its own text when many
threads call it at once.

`sanitize_editor_html` used ONE module-level bleach.Cleaner for every call.
Bleach documents that a Cleaner is not thread-safe: it keeps one html5lib
parser, whose tree builder (open elements, the document) is replaced at the
start of every parse and read again at the end. Two threads inside the parser
at once can therefore (a) raise html5lib's own `assert False # We should never
reach this point` (seen on CI, in students.tests_grading_redelivery_live), and
(b) hand one caller the other's text. The cleaner runs on student answers,
teacher assignment text and graded documents, on gunicorn's four threads per
worker and on the task workers.

These tests call the real functions from many threads at once, with the thread
switch interval made tiny so that the threads really interleave inside the
parser, and compare every output with the same input's single-thread output.
They are pure Python (no database).
"""

import sys
import threading
from concurrent.futures import ThreadPoolExecutor

from django.test import SimpleTestCase

from assignments.prosemirror_converter import (
    html_to_prosemirror_json,
    sanitize_editor_html,
)

INPUTS = 12
ROUNDS = 60
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


def run_in_threads(function):
    """(results, errors): every thread calls `function` on every input,
    ROUNDS times, starting at a different input; all start together and the
    interpreter switches threads as often as it can."""
    results, errors = [], []
    lock = threading.Lock()
    barrier = threading.Barrier(THREADS)

    def work(offset):
        barrier.wait()
        for round_number in range(ROUNDS):
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

    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        with ThreadPoolExecutor(max_workers=THREADS) as pool:
            list(pool.map(work, range(THREADS)))
    finally:
        sys.setswitchinterval(previous)
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

    def test_the_hostile_inputs_stay_clean_in_every_thread(self):
        results, errors = run_in_threads(sanitize_editor_html)

        self.assertEqual(errors, [])
        hostile = [(i, got) for i, got in results if i % 3 == 0]
        self.assertTrue(hostile)
        for i, got in hostile:
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

        results, errors = run_in_threads(html_to_prosemirror_json)

        self.assertEqual(errors, [])
        self.assertEqual(len(results), THREADS * ROUNDS * INPUTS)
        wrong = [i for i, got in results if got != expected[i]]
        self.assertEqual(wrong, [])
