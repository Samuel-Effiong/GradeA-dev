"""Cross-student isolation, concurrent chunked extraction, and determinism.

The shared-PDFService defect (Regression 1, pinned at unit level by
tests_pdf_service_concurrency.py) let one student's submission be graded
from another student's pages. These tests hold the same property at the
level of the whole extraction pipeline, with real threads.

HOW CONTAMINATION IS DETECTED

Every page of every student's document carries a token unique to that
student AND that page, so no two pages anywhere render alike. Each
document is rasterized once on its own to record the digest of every
page (harness.page_digests). Under concurrency, a thread that received
even one page from another student's document produces a digest that is
not in its own baseline - no OCR needed, and no way for two genuinely
different pages to collide.

WHAT IS NOT VARIED

The spec asks for different chunk sizes per student "where the production
API permits it". It does not: ANSWERS_EXTRACTION_PAGES_PER_CHUNK is a
module constant, one value per process. Every thread therefore runs at the
production size, which is also the only configuration production can be in.
"""

import json
import threading
from dataclasses import replace

from django.test import SimpleTestCase

from ai_processor.benchmark.answers import SCENARIOS, build_document
from ai_processor.benchmark.answers.documents import Line, expected_chunks
from ai_processor.benchmark.answers.harness import (
    describe,
    full_check,
    page_digests,
    rasterize,
    run_content,
    run_scenario,
)
from ai_processor.benchmark.answers.scenarios import _simple
from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK

#: Repetitions for each concurrent test. Races are probabilistic; a single
#: clean round proves little.
ROUNDS = 5
#: Upper bound on any one barrier wait, so a deadlock fails instead of hangs.
BARRIER_TIMEOUT = 120


def student_scenario(token, pages):
    """
    A scenario for one student, with `token` and the page number written on
    every page so that every page of every student is visually unique.
    """
    numbers = tuple(f"Q{index}" for index in range(1, min(pages, 12) + 1))
    base = _simple(
        f"AE-{token[:5]}",
        f"student-{token.lower()}-{pages}-pages",
        "concurrency",
        answered=numbers,
        per_page=1,
        pad_to=pages,
    )
    tokened_pages = tuple(
        replace(page, lines=(Line(f"{token} - page {index}"),) + page.lines)
        for index, page in enumerate(base.document.pages, start=1)
    )
    return replace(
        base,
        student_name=token,
        document=replace(base.document, pages=tokened_pages),
    )


def run_concurrently(scenarios, *, split_upload_and_extract):
    """
    Run each scenario on its own thread, all starting together.

    With `split_upload_and_extract`, every thread finishes rasterizing
    before ANY thread starts extracting: the "upload A, upload B, extract A,
    extract B" interleaving, forced rather than hoped for.
    """
    start = threading.Barrier(len(scenarios))
    uploaded = threading.Barrier(len(scenarios))
    runs, errors = {}, []

    def worker(scenario):
        try:
            start.wait(timeout=BARRIER_TIMEOUT)
            content = rasterize(scenario)
            if split_upload_and_extract:
                uploaded.wait(timeout=BARRIER_TIMEOUT)
            runs[scenario.id] = run_content(scenario, content)
        except BaseException as exc:  # noqa: B036 - reported by the caller
            errors.append((scenario.id, exc))
            start.abort()
            uploaded.abort()

    threads = [threading.Thread(target=worker, args=(s,)) for s in scenarios]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return runs, errors


class ConcurrencyAssertions(SimpleTestCase):
    maxDiff = None

    def baselines(self, scenarios):
        digests = {s.id: page_digests(rasterize(s)) for s in scenarios}
        every_page = [d for page_list in digests.values() for d in page_list]
        # Precondition: if two pages could render identically, a foreign
        # page could hide behind a matching digest and prove nothing.
        self.assertEqual(len(every_page), len(set(every_page)))
        return digests

    def assertOwnWorkOnly(self, scenario, run, baseline):
        context = describe(run, scenario, [])
        self.assertIsNone(run.error, context)
        self.assertEqual(run.extra["page_digests"], baseline, context)
        self.assertEqual(run.page_count, scenario.page_count, context)
        self.assertEqual(
            [list(chunk) for chunk in run.pages_sent_per_chunk],
            expected_chunks(scenario.page_count, ANSWERS_EXTRACTION_PAGES_PER_CHUNK),
            context,
        )
        self.assertEqual(run.result["student_name"], scenario.student_name, context)
        problems = full_check(run, scenario)
        self.assertEqual(problems, [], describe(run, scenario, problems))


class CrossStudentIsolationTest(ConcurrencyAssertions):
    def test_upload_upload_extract_extract_never_crosses_students(self):
        alpha = student_scenario("ALPHA_STUDENT_UNIQUE_TOKEN", 4)
        bravo = student_scenario("BRAVO_STUDENT_UNIQUE_TOKEN", 7)
        baseline = self.baselines((alpha, bravo))

        for round_number in range(ROUNDS):
            with self.subTest(round=round_number):
                runs, errors = run_concurrently(
                    (alpha, bravo), split_upload_and_extract=True
                )
                self.assertEqual(errors, [])
                for scenario in (alpha, bravo):
                    self.assertOwnWorkOnly(
                        scenario, runs[scenario.id], baseline[scenario.id]
                    )


class ConcurrentChunkedExtractionTest(ConcurrencyAssertions):
    def test_four_multi_chunk_documents_at_once(self):
        students = tuple(
            student_scenario(token, pages)
            for token, pages in (
                ("CHARLIE_STUDENT_UNIQUE_TOKEN", 9),
                ("DELTA_STUDENT_UNIQUE_TOKEN", 12),
                ("ECHO_STUDENT_UNIQUE_TOKEN", 6),
                ("FOXTROT_STUDENT_UNIQUE_TOKEN", 15),
            )
        )
        baseline = self.baselines(students)

        for round_number in range(ROUNDS):
            for split in (False, True):
                with self.subTest(round=round_number, split=split):
                    runs, errors = run_concurrently(
                        students, split_upload_and_extract=split
                    )
                    self.assertEqual(errors, [])
                    for scenario in students:
                        self.assertOwnWorkOnly(
                            scenario, runs[scenario.id], baseline[scenario.id]
                        )


def normalised(run):
    """Everything a run produces that must not vary between identical runs."""
    return json.dumps(
        {
            "result": run.result,
            "error": repr(run.error) if run.error else None,
            "execution_path": run.execution_path,
            "pages_sent": [list(chunk) for chunk in run.pages_sent_per_chunk],
            "pages_claimed": [
                list(claim) if claim else None for claim in run.pages_claimed_per_chunk
            ],
            "page_digests": run.extra["page_digests"],
            "notes": [call.note for call in run.provider.calls],
        },
        sort_keys=True,
    )


class DeterminismTest(SimpleTestCase):
    RUNS = 3

    def test_every_document_builds_to_identical_bytes(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.id):
                builds = {build_document(scenario.document) for _ in range(self.RUNS)}
                self.assertEqual(len(builds), 1)

    def test_every_scenario_produces_identical_normalised_output(self):
        for scenario in SCENARIOS:
            with self.subTest(scenario=scenario.id):
                outputs = {normalised(run_scenario(scenario)) for _ in range(self.RUNS)}
                self.assertEqual(len(outputs), 1)
