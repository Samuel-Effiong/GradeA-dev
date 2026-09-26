"""Live-provider answer-extraction benchmark.

BILLED AND SLOW: skipped unless RUN_REAL_AI=1, the same opt-in the other
real-AI tests use. Normal CI runs only the free structural checks at the
top of this file.

    RUN_REAL_AI=1 python manage.py test ai_processor.tests_answer_benchmark_live

    # validate one change on one document before spending the corpus
    RUN_REAL_AI=1 ANSWER_LIVE_ONLY=AE-905 ANSWER_LIVE_REPORT_SUFFIX=_targeted \\
        python manage.py test ai_processor.tests_answer_benchmark_live

Every run's full record - pages per chunk, raw responses, statuses,
discrepancies, model, timing and credits - is written to
ai_processor/benchmark/answers/reports/ BEFORE anything is asserted, so a
failing run still leaves its evidence behind.

Asserted for every extracted document: no pipeline error; the chunked path
really ran (more than one call whenever the document is longer than a
chunk); the schema was sent; every page was submitted exactly once; every
status is exactly right; every answer - including every part of an answer
that crosses a chunk boundary - is present after normalisation, and on its
own question; and the wallet moved by exactly the tokens reported.

For the documents also taken through REAL grading: the graded statuses are
the extracted ones, exactly the not-found questions are flagged for review,
the total is the sum of the per-question scores, a blank or absent answer
scores zero, a real answer scores above zero, and the grader saw every part
of a split answer.
"""

import json
import os
import unittest
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from ai_processor.benchmark.answers.harness import normalise
from ai_processor.benchmark.answers.live import (
    grade_live,
    live_corpus,
    run_live,
    write_live_report,
)
from ai_processor.extraction_schemas import ANSWERED, REVIEW_REQUIRED_STATUSES
from ai_processor.services import ANSWERS_EXTRACTION_PAGES_PER_CHUNK
from ai_processor.tests_real_chunked_extraction import (
    RUN_REAL_AI,
    SKIP_REASON,
    make_teacher_with_credits,
)

REPORTS = Path(settings.BASE_DIR) / "ai_processor" / "benchmark" / "answers" / "reports"
#: Comma-separated scenario ids to run, for a targeted validation.
ONLY = frozenset(
    part.strip()
    for part in os.environ.get("ANSWER_LIVE_ONLY", "").split(",")
    if part.strip()
)
#: Keeps a targeted run from overwriting the full-run reports.
SUFFIX = os.environ.get("ANSWER_LIVE_REPORT_SUFFIX", "")
#: Documents taken all the way through real grading as well.
GRADING_SCENARIOS = ("AE-905", "AE-906", "AE-916")


def selected(ids=None):
    return [
        scenario
        for scenario in live_corpus()
        if (not ONLY or scenario.id in ONLY) and (ids is None or scenario.id in ids)
    ]


class LiveCorpusShapeTest(SimpleTestCase):
    """Free, always run: the live corpus cannot stay on the single-call path."""

    def test_every_live_document_takes_the_chunked_path(self):
        size = ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        for scenario in live_corpus():
            with self.subTest(scenario=scenario.id):
                self.assertGreaterEqual(scenario.page_count, size)

    def test_the_corpus_covers_the_page_counts_the_spec_names(self):
        pages = {scenario.page_count for scenario in live_corpus()}
        self.assertTrue({3, 6, 7, 9, 12} <= pages, sorted(pages))
        self.assertGreaterEqual(max(pages), 20)

    def test_the_corpus_covers_every_status(self):
        statuses = {e.status for s in live_corpus() for e in s.expectations}
        self.assertEqual(statuses, {"ANSWERED", "BLANK", "NOT_FOUND_IN_DOCUMENT"})

    def test_the_corpus_asserts_split_answers_across_two_and_three_chunks(self):
        split = {
            s.id: max(len(e.fragments) for e in s.expectations)
            for s in live_corpus()
            if "split-answer" in s.tags
        }
        self.assertIn(2, split.values(), split)
        self.assertIn(3, split.values(), split)
        self.assertFalse([s.id for s in live_corpus() if s.known_gap])


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class LiveAnswerExtractionBenchmarkTest(TestCase):
    maxDiff = None

    def setUp(self):
        from classrooms.models import Course, Session

        self.teacher = make_teacher_with_credits("answer-benchmark-live@example.com")
        session = Session.objects.create(name="Live", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Answer Benchmark Live", teacher=self.teacher, session=session
        )

    def test_live_corpus(self):
        scenarios = selected()
        if not scenarios:
            self.skipTest(f"ANSWER_LIVE_ONLY={sorted(ONLY)} selects no document")
        records = []
        try:
            for scenario in scenarios:
                records.append(run_live(scenario, self.teacher, self.course))
        finally:
            write_live_report(records, REPORTS / f"live_run{SUFFIX}.json")

        size = ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        for record in records:
            context = json.dumps(record, indent=2, default=str)[:8000]
            with self.subTest(scenario=record["scenario"]):
                self.assertIsNone(record["error"], context)
                self.assertEqual(record["execution_path"], "chunked", context)
                if record["pages"] > size:
                    self.assertGreater(record["chunk_count"], 1, context)
                self.assertTrue(record["credits"]["invariant_holds"], context)
                self.assertEqual(record["discrepancies"], [], context)


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class LiveEndToEndGradingTest(TestCase):
    maxDiff = None

    def setUp(self):
        from classrooms.models import Course, Session

        self.teacher = make_teacher_with_credits("answer-benchmark-grade@example.com")
        session = Session.objects.create(name="Live grading", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Answer Benchmark Grading", teacher=self.teacher, session=session
        )

    def test_extraction_through_real_grading(self):
        scenarios = selected(GRADING_SCENARIOS)
        if not scenarios:
            self.skipTest(
                f"ANSWER_LIVE_ONLY={sorted(ONLY)} selects no grading document"
            )
        records = []
        try:
            for scenario in scenarios:
                extraction = run_live(scenario, self.teacher, self.course)
                grading = (
                    grade_live(scenario, extraction, self.teacher)
                    if extraction["error"] is None
                    else None
                )
                records.append(
                    {
                        "scenario": scenario.id,
                        "extraction": extraction,
                        "grading": grading,
                    }
                )
        finally:
            write_live_report(records, REPORTS / f"live_grading_run{SUFFIX}.json")

        by_id = {scenario.id: scenario for scenario in scenarios}
        for record in records:
            scenario = by_id[record["scenario"]]
            extraction, grading = record["extraction"], record["grading"]
            context = json.dumps(record, indent=2, default=str)[:8000]
            with self.subTest(scenario=scenario.id):
                self.assertIsNone(extraction["error"], context)
                self.assertEqual(extraction["discrepancies"], [], context)
                self.assertIsNotNone(grading, context)
                self.assertIsNone(grading["error"], context)

                expected = {e.question: e.status for e in scenario.expectations}
                self.assertEqual(grading["graded_statuses"], expected, context)
                self.assertEqual(
                    set(grading["answers_not_found"]),
                    {
                        q
                        for q, status in expected.items()
                        if status in REVIEW_REQUIRED_STATUSES
                    },
                    context,
                )

                scores = grading["scores"]
                self.assertAlmostEqual(
                    float(grading["summary"]["total_score"]),
                    sum(float(item["score_awarded"] or 0) for item in scores.values()),
                    places=2,
                    msg=context,
                )
                for expectation in scenario.expectations:
                    score = scores[expectation.question]
                    if expectation.status == ANSWERED:
                        self.assertGreater(
                            float(score["score_awarded"] or 0), 0, context
                        )
                        seen = normalise(score["student_answer"])
                        for _page, fragment in expectation.fragments:
                            self.assertIn(normalise(fragment), seen, context)
                    else:
                        self.assertEqual(float(score["score_awarded"] or 0), 0, context)

                self.assertTrue(extraction["credits"]["invariant_holds"], context)
                self.assertTrue(grading["credits"]["invariant_holds"], context)
