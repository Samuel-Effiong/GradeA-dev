"""Deterministic answer-extraction benchmark: the catalogued scenarios.

Every scenario in ai_processor/benchmark/answers/scenarios.py runs through
the REAL pipeline - the production rasterizer, extract_answer_with_retry,
the production chunker and the production merge - with only the model
replaced by a deterministic provider. No network, no credits, no database.

What a pass proves, and what it does not, is set out in
ai_processor/benchmark/answers/README.md. In one line: this validates OUR
pipeline's chunking, attribution, merging and status handling. It says
nothing about whether the real model can read a page; the live suite
(tests_answer_benchmark_live.py, RUN_REAL_AI=1) exists for that.
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from ai_processor.benchmark.answers import SCENARIOS, SCENARIOS_BY_ID
from ai_processor.benchmark.answers.documents import expected_chunks
from ai_processor.benchmark.answers.harness import (
    check_page_coverage,
    describe,
    full_check,
    run_scenario,
)
from ai_processor.benchmark.answers.scenarios import _simple
from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK

#: Scenarios that encode the correct behaviour and are known not to meet it
#: yet. Pinned here so adding one is a visible, reviewed edit rather than a
#: quiet way to make a failing scenario stop failing.
#: Empty since 2026-09-14: AE-803 (a split answer lost at a chunk boundary)
#: was closed by the chunk note asking for continuations.
KNOWN_GAPS: set[str] = set()


class CatalogueTest(SimpleTestCase):
    def test_every_scenario_has_a_stable_well_formed_id(self):
        for scenario in SCENARIOS:
            self.assertRegex(scenario.id, r"^AE-\d{3}$")
        self.assertEqual(len(SCENARIOS), len(SCENARIOS_BY_ID))

    def test_known_gaps_are_exactly_the_declared_ones(self):
        self.assertEqual({s.id for s in SCENARIOS if s.known_gap}, KNOWN_GAPS)

    def test_every_status_is_represented(self):
        statuses = {e.status for s in SCENARIOS for e in s.expectations}
        self.assertTrue(
            {"ANSWERED", "BLANK", "NOT_FOUND_IN_DOCUMENT"} <= statuses, statuses
        )


class ScenarioBenchmarkTest(SimpleTestCase):
    """One subTest per catalogued scenario; failures carry full diagnostics."""

    maxDiff = None

    def test_every_catalogued_scenario(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.id):
                run = run_scenario(scenario)
                problems = full_check(run, scenario)
                if scenario.known_gap:
                    # A known gap that starts passing must be noticed: its
                    # note is now false and it should be held to the
                    # normal standard.
                    self.assertTrue(
                        problems,
                        f"{scenario.id} is recorded as a known gap but now "
                        "PASSES. Remove its known_gap note and its entry in "
                        "KNOWN_GAPS.",
                    )
                else:
                    self.assertEqual(problems, [], describe(run, scenario, problems))

    def test_scenarios_tagged_chunked_really_chunk(self):
        """
        A scenario named "chunked" that silently ran as one call would
        prove nothing about chunking. Where the document is larger than one
        chunk, more than one provider call must actually have been made.
        """
        for scenario in SCENARIOS:
            if "chunked" not in scenario.tags:
                continue
            size = scenario.pages_per_chunk or ANSWERS_EXTRACTION_PAGES_PER_CHUNK
            with self.subTest(scenario=scenario.id):
                run = run_scenario(scenario)
                self.assertEqual(
                    run.execution_path, "chunked", describe(run, scenario, [])
                )
                if scenario.page_count > size:
                    self.assertGreater(run.chunk_count, 1, describe(run, scenario, []))


def _answered_document(pages, size=None):
    """
    A document of `pages` pages, one answered question per page (up to the
    size of the question bank) padded with continuation sheets.
    """
    numbers = tuple(f"Q{index}" for index in range(1, min(pages, 12) + 1))
    return _simple(
        f"AE-T{pages:03d}",
        f"threshold-derived-{pages}-pages",
        "boundary",
        answered=numbers,
        per_page=1,
        pad_to=pages,
        pages_per_chunk=size,
    )


class ThresholdDerivedBoundaryTest(SimpleTestCase):
    """
    Page counts DERIVED from the production chunk size, never assumed.

    If ANSWERS_EXTRACTION_PAGES_PER_CHUNK changes, these cases move with it,
    so the threshold stays covered on both sides rather than quietly
    drifting away from the fixed page counts in the catalogue.
    """

    maxDiff = None

    def test_the_path_taken_at_every_boundary(self):
        threshold = ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        page_counts = sorted(
            {1, 2, threshold - 1, threshold, threshold + 1}
            | {2 * threshold, 2 * threshold + 1}
        )
        for pages in (count for count in page_counts if count >= 1):
            scenario = _answered_document(pages)
            with self.subTest(pages=pages):
                run = run_scenario(scenario)
                if pages >= threshold:
                    self.assertEqual(run.execution_path, "chunked")
                    self.assertEqual(
                        [list(chunk) for chunk in run.pages_sent_per_chunk],
                        expected_chunks(pages, threshold),
                    )
                else:
                    self.assertEqual(run.execution_path, "single-call")
                    self.assertEqual(run.chunk_count, 1)
                problems = full_check(run, scenario)
                self.assertEqual(problems, [], describe(run, scenario, problems))

    def test_every_chunk_size_on_one_ten_page_document(self):
        """
        Sizes below, at and above the document length. Every page must be
        submitted exactly once and the layout must be exactly the one an
        independent split produces.
        """
        pages = 10
        for size in (1, 2, 3, 4, 5, 9, 10, 11, 50):
            scenario = _answered_document(pages, size=size)
            with self.subTest(pages_per_chunk=size):
                run = run_scenario(scenario)
                if pages >= size:
                    self.assertEqual(run.execution_path, "chunked")
                    sent = [
                        page for chunk in run.pages_sent_per_chunk for page in chunk
                    ]
                    self.assertEqual(sorted(sent), list(range(1, pages + 1)))
                    self.assertEqual(len(sent), len(set(sent)))
                    self.assertEqual(
                        [list(chunk) for chunk in run.pages_sent_per_chunk],
                        expected_chunks(pages, size),
                    )
                else:
                    self.assertEqual(run.execution_path, "single-call")
                problems = full_check(run, scenario)
                self.assertEqual(problems, [], describe(run, scenario, problems))

    def test_a_document_large_enough_for_more_than_ten_chunks(self):
        threshold = ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        pages = 10 * threshold + 1
        scenario = _answered_document(pages)
        run = run_scenario(scenario)
        self.assertGreaterEqual(run.chunk_count, 11, describe(run, scenario, []))
        self.assertEqual(check_page_coverage(run), [])
        problems = full_check(run, scenario)
        self.assertEqual(problems, [], describe(run, scenario, problems))


class RunsFromAnyWorkingDirectoryTest(SimpleTestCase):
    """
    Prompt files were once read relative to the current working directory,
    so the service only imported when started from the project root. The
    benchmark must not depend on that either.
    """

    def test_services_and_benchmark_import_outside_the_project_root(self):
        project_root = Path(settings.BASE_DIR)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(project_root), env.get("PYTHONPATH", "")) if part
        )
        code = (
            "import django; django.setup(); "
            "from ai_processor import services; "
            "from ai_processor.benchmark.answers import SCENARIOS; "
            "assert services.ANSWERS_EXTRACTION_PROMPT.strip(); "
            "print('scenarios', len(SCENARIOS))"
        )
        with tempfile.TemporaryDirectory() as elsewhere:
            completed = subprocess.run(
                [sys.executable, "-c", code],
                cwd=elsewhere,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
        self.assertEqual(completed.returncode, 0, completed.stderr[-3000:])
        self.assertIn(f"scenarios {len(SCENARIOS)}", completed.stdout)
