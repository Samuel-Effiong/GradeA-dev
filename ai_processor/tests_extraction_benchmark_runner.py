"""
Tests for the extraction benchmark's runner and command wiring.

ai_processor/tests_extraction_benchmark.py covers the dataset and the
scorer as pure logic. This file covers the harness around them — the bits
that decide whether the benchmark is honest about what it is doing:

  * the document a case is measured against is the TEACHER document, the
    one re-extraction actually reads;
  * no case is large enough to trigger chunked extraction, which is what
    keeps the recordings stable;
  * the command refuses to run on a malformed dataset, and refuses to
    guess a user.

Run with:
    python manage.py test ai_processor.tests_extraction_benchmark_runner
"""

import json

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from ai_processor.benchmark.extraction_dataset import (
    EXTRACTION_CASES,
    MIXED_HYBRID,
    SIX_LEVEL_RUBRIC,
)
from ai_processor.benchmark.extraction_runner import (
    EXTRACTION_RECORDINGS_DIR,
    iter_cases,
    render_case_document,
)
from ai_processor.benchmark.runner import RECORDINGS_DIR


class CaseSelectionTest(SimpleTestCase):
    def test_no_filter_yields_every_case(self):
        self.assertEqual(list(iter_cases()), EXTRACTION_CASES)

    def test_filter_selects_one_case(self):
        self.assertEqual(
            [c.key for c in iter_cases(["six_level_rubric"])], ["six_level_rubric"]
        )

    def test_order_is_stable(self):
        # Recording keys are per-prompt, not per-position, but a stable
        # order keeps run-to-run diffs readable.
        self.assertEqual([c.key for c in iter_cases()], [c.key for c in iter_cases()])

    def test_unknown_filter_yields_nothing(self):
        self.assertEqual(list(iter_cases(["nope"])), [])


class RenderedDocumentTest(SimpleTestCase):
    """
    The benchmark is only meaningful if it feeds the model the document a
    real edit would feed it.
    """

    def test_document_is_valid_prosemirror(self):
        document = json.loads(render_case_document(SIX_LEVEL_RUBRIC))
        self.assertEqual(document["type"], "doc")

    def test_document_includes_the_rubric(self):
        # include_rubric=True: this is the TEACHER document, which is what
        # raw_input holds and what re-extraction reads. A rubric-free
        # render would measure the student view, which is never
        # re-extracted — and every rubric metric would score zero.
        flat = render_case_document(SIX_LEVEL_RUBRIC)
        self.assertIn("table_row", flat)
        # Title-cased: format_assignment_standard_html renders level names
        # through .title() for display. The scorer normalises casing on
        # both sides (extraction_scoring uses objective_grading's
        # normalize_text), so this is presentation, not a lost name.
        self.assertIn("Very Good", flat)

    def test_document_includes_the_question_type_label(self):
        # The label only helps if it reaches the document the model sees.
        flat = render_case_document(MIXED_HYBRID)
        self.assertIn("Multiple Choice", flat)
        self.assertIn("Short Answer", flat)

    def test_every_rubric_level_reaches_the_document(self):
        flat = render_case_document(SIX_LEVEL_RUBRIC)
        for level in SIX_LEVEL_RUBRIC.question(1)["rubric"]:
            with self.subTest(level=level["level"]):
                self.assertIn(level["level"].title(), flat)

    def test_every_option_reaches_the_document(self):
        from ai_processor.benchmark.extraction_dataset import SIX_OPTION_MCQ

        flat = render_case_document(SIX_OPTION_MCQ)
        for option in SIX_OPTION_MCQ.question(1)["options"]:
            with self.subTest(option=option):
                self.assertIn(option, flat)

    def test_rendering_is_deterministic(self):
        # Non-determinism here would change the recording key on every run
        # and make replay permanently miss.
        self.assertEqual(
            render_case_document(MIXED_HYBRID), render_case_document(MIXED_HYBRID)
        )


class ChunkThresholdGuardTest(SimpleTestCase):
    """
    ai_processor.services.PROSEMIRROR_CHUNK_THRESHOLD is a module
    constant, not a Django setting, so the benchmark cannot pin it with
    override_settings. It decides whether a document is extracted in one
    call or several — which changes the number of model calls and so the
    replay key set, silently invalidating every recording.

    The dataset is sized well under it so no case chunks today. This test
    is where that stops being true loudly rather than quietly.
    """

    def _tokens(self, text):
        import tiktoken

        return len(tiktoken.get_encoding("cl100k_base").encode(text))

    def test_no_case_document_approaches_the_chunk_threshold(self):
        from ai_processor.services import PROSEMIRROR_CHUNK_THRESHOLD

        for case in EXTRACTION_CASES:
            with self.subTest(case=case.key):
                tokens = self._tokens(render_case_document(case))
                self.assertLess(
                    tokens,
                    PROSEMIRROR_CHUNK_THRESHOLD * 0.8,
                    f"{case.key} renders to {tokens} tokens, within 20% of the "
                    f"{PROSEMIRROR_CHUNK_THRESHOLD}-token chunking threshold. "
                    "Chunking would change the model-call count and invalidate "
                    "the committed recordings — shrink the case or re-record "
                    "deliberately.",
                )


class RecordingsLocationTest(SimpleTestCase):
    def test_extraction_recordings_are_separate_from_grading(self):
        # Re-recording extraction after a prompt edit must never force a
        # re-record of the far more expensive grading set.
        self.assertNotEqual(EXTRACTION_RECORDINGS_DIR, RECORDINGS_DIR)

    def test_missing_recording_error_names_the_extraction_command(self):
        # The tape is shared, and naming the wrong command sends whoever
        # hits this to re-record the wrong dataset.
        from ai_processor.benchmark.runner import (
            MODE_REPLAY,
            MissingRecordingError,
            _Tape,
        )

        with self.assertRaises(MissingRecordingError) as ctx:
            _Tape(
                MODE_REPLAY,
                EXTRACTION_RECORDINGS_DIR.parent / "does_not_exist_extraction",
            )
        self.assertIn("extraction_benchmark", str(ctx.exception))


class CommandGuardTest(SimpleTestCase):
    def test_unknown_case_key_is_rejected(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("extraction_benchmark", "--case", "nope", "--mode", "replay")
        self.assertIn("Unknown case key", str(ctx.exception))

    def test_missing_teacher_email_is_rejected_even_in_replay(self):
        # Replay spends nothing but still runs the billing path around the
        # model call, so it needs a real user. The error has to say so, or
        # the next person assumes replay is broken.
        with self.assertRaises(CommandError) as ctx:
            call_command("extraction_benchmark", "--mode", "replay")
        message = str(ctx.exception)
        self.assertIn("--teacher-email", message)
        self.assertIn("tests_extraction_benchmark_golden", message)

    def test_live_mode_requires_a_teacher(self):
        with self.assertRaises(CommandError) as ctx:
            call_command("extraction_benchmark", "--mode", "live")
        self.assertIn("--teacher-email", str(ctx.exception))
