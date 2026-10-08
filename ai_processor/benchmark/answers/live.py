"""Live-provider answer-extraction corpus and call recorder.

BILLED. Only tests_answer_benchmark_live.py uses the billed half of this
module, and that test is skipped unless RUN_REAL_AI=1.

The deterministic suite proves the pipeline handles what a model returns.
This proves something different and weaker-sounding but just as necessary:
that the production model, on documents big enough to force chunking,
actually produces results the pipeline can turn into the right statuses.

WHAT IS RECORDED FOR EVERY DOCUMENT

document and page count, the production chunk size, the path taken, every
provider call (which pages, what range the note claimed, whether the schema
was sent, the model that served it, tokens, seconds, and the raw response),
the normalised extraction, the expected result, the discrepancies (computed
by the SAME harness.full_check the deterministic suite uses), the model
configuration, elapsed time, and the credit movement on the teacher's
wallet.

HOW PAGES ARE IDENTIFIED WITHOUT TOUCHING THE PAYLOAD

The deterministic harness tags page images with a marker; a real provider
must receive exactly what production sends, so nothing is added here.
Instead each rasterized page's data URL is mapped to its page number before
the run, and the recorder looks every image it sees back up in that map.
Every page of every corpus document has distinct text, so no two pages can
share a data URL.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile

from ai_processor import services as ai_services
from ai_processor.extraction_schemas import (
    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
    BLANK_VERIFICATION_RESPONSE_SCHEMA,
)
from assignments.services import AssignmentProcessingService

from .documents import Ink, build_document, expected_chunks
from .harness import RunResult, _expected_execution_path, full_check, page_digests
from .scenarios import (
    _answer_across_three_chunks,
    _continuation_shares_a_page,
    _cross_boundary_scenario,
    _simple,
)

_CLAIMED_RANGE = re.compile(r"pages (\d+) to (\d+)")
#: Raw responses are kept for diagnosis, bounded so a report stays readable.
RAW_RESPONSE_LIMIT = 6000


def live_corpus():
    """
    Documents chosen so that none of them can stay on the single-call path,
    covering every page count the spec names, with all three statuses,
    synthetic handwriting, noise, multi-question pages and a split answer.
    """
    return (
        _simple(
            "AE-903",
            "live-3-pages",
            "live",
            answered=("Q1", "Q2"),
            blank=("Q3",),
            per_page=1,
            tags=("live", "chunked", "blank"),
        ),
        _simple(
            "AE-906",
            "live-6-pages-mixed",
            "live",
            answered=("Q1", "Q2", "Q4", "Q5"),
            blank=("Q3",),
            not_found=("Q9",),
            per_page=1,
            pad_to=6,
            notes="Q3 is blank on the LAST page of chunk 1.",
            tags=("live", "chunked", "boundary", "blank", "not-found"),
        ),
        _simple(
            "AE-907",
            "live-7-pages-handwritten-synthetic",
            "live",
            answered=("Q1", "Q2", "Q3", "Q5", "Q6"),
            blank=("Q4",),
            not_found=("Q10",),
            per_page=1,
            pad_to=7,
            ink=Ink.HANDWRITTEN,
            notes="Q4 is blank on the FIRST page of chunk 2 - the live form "
            "of the AE-600 defect. Synthetic handwriting, not real scripts.",
            tags=("live", "chunked", "boundary", "handwritten-synthetic"),
        ),
        _simple(
            "AE-909",
            "live-9-pages-noisy",
            "live",
            answered=("Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"),
            blank=("Q8",),
            not_found=("Q11",),
            per_page=1,
            pad_to=9,
            noise=0.4,
            tags=("live", "chunked", "noisy", "blank", "not-found"),
        ),
        _simple(
            "AE-912",
            "live-12-pages-two-per-page",
            "live",
            answered=("Q1", "Q2", "Q3", "Q4", "Q5", "Q7", "Q8", "Q9", "Q10"),
            blank=("Q6",),
            per_page=2,
            pad_to=12,
            tags=("live", "chunked", "layout", "blank"),
        ),
        _simple(
            "AE-921",
            "live-21-pages",
            "live",
            answered=tuple(f"Q{n}" for n in range(1, 13) if n != 7),
            blank=("Q7",),
            per_page=1,
            pad_to=21,
            notes="Seven chunks. Past the blank re-read's page cap, so the "
            "extraction's own statuses stand alone.",
            tags=("live", "chunked", "large", "blank"),
        ),
        replace(
            _cross_boundary_scenario(),
            id="AE-905",
            name="live-answer-spans-chunk-seam",
            category="live",
            notes="The document that exposed the split-answer defect: before "
            "2026-09-14 the real model returned only 'The major component is'.",
            tags=("live", "chunked", "cross-chunk", "split-answer"),
        ),
        replace(
            _answer_across_three_chunks(),
            id="AE-915",
            name="live-answer-spans-three-chunks",
            category="live",
            model_quirks=(),
            tags=("live", "chunked", "cross-chunk", "split-answer"),
        ),
        replace(
            _continuation_shares_a_page(),
            id="AE-916",
            name="live-continuation-shares-a-page",
            category="live",
            model_quirks=(),
            tags=("live", "chunked", "cross-chunk", "split-answer", "status"),
        ),
    )


def rasterize_plain(scenario) -> list[dict]:
    """The production rasterizer's output, exactly as production sends it."""
    uploaded = SimpleUploadedFile(
        f"{scenario.id}.pdf",
        build_document(scenario.document),
        content_type="application/pdf",
    )
    return AssignmentProcessingService.prepare_ai_content(
        uploaded, "Extract the student's answers."
    )


class LiveRecorder:
    """Wraps the real, billed execute_graded_task and records every call."""

    def __init__(self, processor, content):
        self.original = processor.execute_graded_task
        self.page_of: dict[str, int] = {}
        page = 0
        for block in content:
            if block.get("type") == "image_url":
                page += 1
                self.page_of.setdefault(block["image_url"]["url"], page)
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        texts, pages = [], []
        for message in kwargs.get("messages") or []:
            body = message.get("content")
            if not isinstance(body, list):
                continue
            for block in body:
                if block.get("type") == "text":
                    texts.append(block.get("text", ""))
                elif block.get("type") == "image_url":
                    pages.append(self.page_of.get(block["image_url"]["url"]))
        claimed = _CLAIMED_RANGE.search("\n".join(texts))
        schema = kwargs.get("response_schema")
        record: dict[str, Any] = {
            "kind": (
                "blank_verification"
                if schema is BLANK_VERIFICATION_RESPONSE_SCHEMA
                else "extraction"
            ),
            "pages": pages,
            "claimed": (
                [int(claimed.group(1)), int(claimed.group(2))] if claimed else None
            ),
            "schema_sent": schema is ANSWER_EXTRACTION_RESPONSE_SCHEMA,
            "model": None,
            "total_tokens": None,
            "seconds": None,
            "raw_response": None,
            "error": None,
        }
        started = time.perf_counter()
        try:
            response = self.original(**kwargs)
        except BaseException as exc:  # noqa: B036 - recorded, then re-raised
            record["error"] = repr(exc)
            record["seconds"] = round(time.perf_counter() - started, 2)
            self.calls.append(record)
            raise
        usage = getattr(response, "usage", None)
        record.update(
            seconds=round(time.perf_counter() - started, 2),
            model=getattr(response, "model", None),
            total_tokens=getattr(usage, "total_tokens", None),
            raw_response=(response.choices[0].message.content or "")[
                :RAW_RESPONSE_LIMIT
            ],
        )
        self.calls.append(record)
        return response


def remaining_credits(user) -> float:
    from billing.models import CreditWallet

    return float(CreditWallet.objects.get(user=user).total_remaining_credits())


def run_live(scenario, teacher, course) -> dict:
    """Extract one corpus document with the real provider, and record it."""
    from assignments.models import Assignment

    questions = list(scenario.questions)
    assignment = Assignment.objects.create(
        title=f"{scenario.id} {scenario.name}",
        course=course,
        total_points=5 * len(questions),
        questions=questions,
    )
    content = rasterize_plain(scenario)
    page_count = sum(1 for block in content if block.get("type") == "image_url")
    size = ai_services.ANSWERS_EXTRACTION_PAGES_PER_CHUNK

    processor = ai_services.ai_processor
    recorder = LiveRecorder(processor, content)
    before = remaining_credits(teacher)
    started = time.perf_counter()
    result, error = None, None
    with patch.object(processor, "execute_graded_task", recorder):
        try:
            result = processor.extract_answer_with_retry(
                teacher,
                content,
                json.dumps(questions),
                assignment_model=assignment,
            )
        except Exception as exc:  # noqa: B902 - recorded, asserted by the test
            error = exc
    elapsed = time.perf_counter() - started
    after = remaining_credits(teacher)

    extraction = [call for call in recorder.calls if call["kind"] == "extraction"]
    layout = (
        expected_chunks(page_count, size)
        if page_count >= size
        else [[*range(1, page_count + 1)]]
    )
    # A completeness retry re-sends the whole document. Attribution is
    # checked on the first complete pass; the retries are reported, not hidden.
    first_pass = extraction[: len(layout)]
    run = RunResult(
        scenario_id=scenario.id,
        page_count=page_count,
        pages_per_chunk=size,
        execution_path=(
            "chunked"
            if any(call["claimed"] for call in extraction)
            else ("single-call" if extraction else "no-call")
        ),
        chunk_count=len(first_pass),
        pages_sent_per_chunk=[tuple(call["pages"]) for call in first_pass],
        pages_claimed_per_chunk=[
            tuple(call["claimed"]) if call["claimed"] else None for call in first_pass
        ],
        schemas_per_chunk=[
            ANSWER_EXTRACTION_RESPONSE_SCHEMA if call["schema_sent"] else None
            for call in first_pass
        ],
        result=result,
        error=error,
        elapsed_seconds=elapsed,
        extra={
            "expected_execution_path": _expected_execution_path(page_count, size),
            "page_digests": page_digests(content),
        },
    )
    discrepancies = full_check(run, scenario)
    tokens = sum(call["total_tokens"] or 0 for call in recorder.calls)
    answers = (result or {}).get("answers") or []

    return {
        "scenario": scenario.id,
        "name": scenario.name,
        "assignment_id": assignment.pk,
        "answers_payload": answers,
        "notes": scenario.notes,
        "tags": list(scenario.tags),
        "known_gap": scenario.known_gap,
        "pages": page_count,
        "pages_per_chunk": size,
        "expected_execution_path": run.extra["expected_execution_path"],
        "execution_path": run.execution_path,
        "chunk_count": run.chunk_count,
        "expected_chunks": layout,
        "retry_extraction_calls": len(extraction) - len(first_pass),
        "calls": recorder.calls,
        "expected": [
            {
                "question": e.question,
                "status": e.status,
                "answer": e.answer,
                "pages": list(e.pages),
                "fragments": [list(f) for f in e.fragments],
            }
            for e in scenario.expectations
        ],
        "actual": [
            {
                "question": str(a.get("question_number")),
                "status": a.get("answer_status"),
                "source_page": a.get("source_page"),
                "answer_html": a.get("answer_html"),
                "transcription_notes": a.get("transcription_notes"),
            }
            for a in answers
        ],
        "discrepancies": discrepancies,
        "error": repr(error) if error else None,
        "model_config": {
            "main_model": ai_services.MAIN_MODEL,
            "fallback_models": list(
                getattr(ai_services, "GRADING_FALLBACK_MODELS", [])
            ),
            "blank_verification_model": (
                getattr(settings, "ANSWER_BLANK_VERIFICATION_MODEL", "") or None
            ),
            "served_by": sorted(
                {call["model"] for call in recorder.calls if call["model"]}
            ),
        },
        "elapsed_seconds": round(elapsed, 2),
        "credits": {
            "before": before,
            "after": after,
            "consumed": before - after,
            "tokens_reported": tokens,
            # execute_graded_task charges response.usage.total_tokens per
            # call, so the wallet must move by exactly what was reported.
            "invariant_holds": abs((before - after) - tokens) < 1e-6,
        },
    }


def grade_live(scenario, extraction: dict, teacher) -> dict:
    """
    Grade a live extraction with the REAL grading pipeline, and record it.

    BILLED. The extraction's own answers payload is graded against the same
    Assignment it was extracted for, exactly as a submission would be.
    """
    from assignments.models import Assignment

    assignment = Assignment.objects.get(pk=extraction["assignment_id"])
    processor = ai_services.ai_processor
    recorder = LiveRecorder(processor, [])
    before = remaining_credits(teacher)
    started = time.perf_counter()
    grading, error = None, None
    with patch.object(processor, "execute_graded_task", recorder):
        try:
            grading = processor.extract_grade_with_retry(
                teacher,
                json.dumps(list(scenario.questions)),
                extraction["answers_payload"],
                assignment_model=assignment,
            )
        except Exception as exc:  # noqa: B902 - recorded, asserted by the test
            error = exc
    elapsed = time.perf_counter() - started
    after = remaining_credits(teacher)

    result = grading or {}
    evaluations = [
        ev for ev in result.get("question_evaluations") or [] if isinstance(ev, dict)
    ]
    tokens = sum(call["total_tokens"] or 0 for call in recorder.calls)
    return {
        "scenario": scenario.id,
        "error": repr(error) if error else None,
        "graded_statuses": {
            str(ev.get("question_number")): ev.get("answer_status")
            for ev in evaluations
        },
        "scores": {
            str(ev.get("question_number")): {
                "score_awarded": ev.get("score_awarded"),
                "max_points": ev.get("max_points"),
                "level_achieved": ev.get("level_achieved"),
                "student_answer": str(ev.get("student_answer") or "")[:500],
            }
            for ev in evaluations
        },
        "answers_not_found": sorted(
            str(item.get("question_number"))
            for item in result.get("answers_not_found") or []
        ),
        "summary": result.get("grading_summary"),
        "grading_model": result.get("grading_model"),
        "second_opinion": result.get("second_opinion"),
        "calls": [
            {
                "model": call["model"],
                "total_tokens": call["total_tokens"],
                "seconds": call["seconds"],
                "error": call["error"],
            }
            for call in recorder.calls
        ],
        "elapsed_seconds": round(elapsed, 2),
        "credits": {
            "before": before,
            "after": after,
            "consumed": before - after,
            "tokens_reported": tokens,
            "invariant_holds": abs((before - after) - tokens) < 1e-6,
        },
    }


def write_live_report(records, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "documents": len(records),
                "records": records,
            },
            indent=2,
            default=str,
        )
        + "\n",
        encoding="utf-8",
    )
