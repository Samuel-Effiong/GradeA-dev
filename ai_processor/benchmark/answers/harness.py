"""Runs a scenario through the REAL answer-extraction pipeline.

The point of this module is that nothing here reimplements extraction. A
scenario's PDF goes through the production rasterizer
(`AssignmentProcessingService.prepare_ai_content` -> `PDFService`), the
production entry point (`AIProcessor.extract_answer_with_retry`), the
production chunker and the production merge. Only the model is replaced,
and only in the deterministic mode.

If this file started doing its own chunking or its own merging, the
benchmark would be testing itself.

PAGE MARKERS

`prepare_ai_content` returns base64 JPEGs, which the fake provider cannot
map back to page numbers. So the harness tags each image block with a
`BENCHPAGE<n>` marker after rasterization and before extraction. The
marker rides in the block's `bytes` field and in the data URL's fragment,
both of which the pipeline passes through untouched - so the payload the
pipeline builds is what the provider reads, and a chunk carrying the wrong
pages is visible rather than inferred.
"""

from __future__ import annotations

import hashlib
import json
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from ai_processor import services as ai_services
from ai_processor.extraction_schemas import (
    ANSWER_EXTRACTION_RESPONSE_SCHEMA,
    ANSWER_STATUSES,
    NOT_FOUND_IN_DOCUMENT,
)
from assignments.services import AssignmentProcessingService

from .documents import build_document, expected_chunks
from .provider import FakeProvider, ProviderBehaviour


@dataclass
class RunResult:
    """Everything a failing scenario needs in order to be diagnosable."""

    scenario_id: str
    page_count: int
    pages_per_chunk: int
    execution_path: str  # "single-call" | "chunked"
    chunk_count: int
    pages_sent_per_chunk: list[tuple[int, ...]]
    pages_claimed_per_chunk: list[tuple[int, int] | None]
    schemas_per_chunk: list[object]
    result: dict | None
    error: BaseException | None = None
    #: The FakeProvider in deterministic runs; None for live runs, which
    #: record their own calls (live.LiveRecorder).
    provider: Any = None
    elapsed_seconds: float = 0.0
    extra: dict = field(default_factory=dict)

    # -- convenience views used by the suites ---------------------------

    @property
    def answers(self) -> list[dict]:
        return list((self.result or {}).get("answers") or [])

    @property
    def status_by_question(self) -> dict[str, str | None]:
        return {
            str(answer.get("question_number")): answer.get("answer_status")
            for answer in self.answers
        }

    @property
    def html_by_question(self) -> dict[str, str]:
        return {
            str(answer.get("question_number")): (answer.get("answer_html") or "")
            for answer in self.answers
        }

    @property
    def question_order(self) -> list[str]:
        return [str(answer.get("question_number")) for answer in self.answers]

    def as_report_dict(self) -> dict:
        return {
            "scenario": self.scenario_id,
            "pages": self.page_count,
            "pages_per_chunk": self.pages_per_chunk,
            "execution_path": self.execution_path,
            "chunk_count": self.chunk_count,
            "pages_sent_per_chunk": [list(p) for p in self.pages_sent_per_chunk],
            "pages_claimed_per_chunk": [
                list(p) if p else None for p in self.pages_claimed_per_chunk
            ],
            "statuses": self.status_by_question,
            "order": self.question_order,
            "error": repr(self.error) if self.error else None,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
        }


def rasterize(scenario) -> list[dict]:
    """
    The scenario's PDF through the PRODUCTION rasterizer, with page
    markers attached.
    """
    pdf_bytes = build_document(scenario.document)
    uploaded = SimpleUploadedFile(
        f"{scenario.id}.pdf", pdf_bytes, content_type="application/pdf"
    )
    content = AssignmentProcessingService.prepare_ai_content(
        uploaded, f"Extract the student's answers for {scenario.id}."
    )

    page_number = 0
    marked = []
    for block in content:
        if block.get("type") != "image_url":
            marked.append(block)
            continue
        page_number += 1
        url = block["image_url"]["url"]
        marked.append(
            {
                "type": "image_url",
                "image_url": {"url": f"{url}#BENCHPAGE{page_number}"},
                "bytes": f"BENCHPAGE{page_number}",
            }
        )
    return marked


def _expected_execution_path(page_count: int, threshold: int) -> str:
    """The path the production threshold rule says this document should take."""
    return "chunked" if page_count >= threshold else "single-call"


def _observed_execution_path(provider: FakeProvider) -> str:
    """
    The path the pipeline ACTUALLY took, read off the calls it made.

    Only the chunked path writes a "pages X to Y" note, so a call carrying
    one is proof of chunking. Deriving the path from the page count instead
    would restate the threshold rule rather than check it - exactly the
    caller-side assumption that let the wrong page ranges survive before.
    """
    calls = provider.extraction_calls
    if not calls:
        return "no-call"
    if any(call.pages_claimed is not None for call in calls):
        return "chunked"
    return "single-call"


def page_digests(content) -> list[str]:
    """
    A fingerprint of each rasterized page, with the benchmark marker removed.

    Pages are compared by digest rather than decoded. The same document must
    always produce the same digests, and a page rasterized from ANOTHER
    student's document can never match one of them - which is how the
    concurrency suite detects cross-student contamination without reading
    the images.
    """
    digests = []
    for block in content or []:
        if block.get("type") != "image_url":
            continue
        url = block["image_url"]["url"].split("#BENCHPAGE", 1)[0]
        digests.append(hashlib.sha256(url.encode("utf-8")).hexdigest())
    return digests


def run_scenario(scenario, **options) -> RunResult:
    """Rasterize the scenario's document, then extract it (see run_content)."""
    return run_content(scenario, rasterize(scenario), **options)


def run_content(
    scenario,
    content,
    *,
    behaviour: ProviderBehaviour | None = None,
    pages_per_chunk: int | None = None,
    assignment_model=None,
    user=None,
    processing_task_id=None,
    max_retries: int = 3,
) -> RunResult:
    """
    Drive already-rasterized content through the real extraction pipeline
    with the deterministic provider. Never raises: a failure is captured on
    the result so the failure-injection suites can assert on it.

    Separate from rasterization so the concurrency suite can force the
    interleaving "upload A, upload B, extract A, extract B".

    The chunk size is patched only when it differs from production. A
    module-level patch is not thread-safe, so concurrent callers must run
    at the production size - which is also the only size production has.
    """
    size = (
        pages_per_chunk
        or scenario.pages_per_chunk
        or ai_services.ANSWERS_EXTRACTION_PAGES_PER_CHUNK
    )
    page_count = len([b for b in content if b.get("type") == "image_url"])

    processor = ai_services.AIProcessor.__new__(ai_services.AIProcessor)
    provider = FakeProvider(scenario, behaviour)
    size_patch = (
        nullcontext()
        if size == ai_services.ANSWERS_EXTRACTION_PAGES_PER_CHUNK
        else patch("ai_processor.services.ANSWERS_EXTRACTION_PAGES_PER_CHUNK", size)
    )

    started = time.perf_counter()
    result = None
    error: BaseException | None = None
    with size_patch, patch.object(processor, "execute_graded_task", provider):
        try:
            result = processor.extract_answer_with_retry(
                user,
                content,
                json.dumps(list(scenario.questions)),
                assignment_model=assignment_model,
                max_retries=max_retries,
                processing_task_id=processing_task_id,
            )
        except BaseException as exc:  # noqa: B036 - captured deliberately
            error = exc
    elapsed = time.perf_counter() - started

    return RunResult(
        scenario_id=scenario.id,
        page_count=page_count,
        pages_per_chunk=size,
        execution_path=_observed_execution_path(provider),
        # Chunks only. The blank re-read is a separate billed call and
        # is reported separately, under provider_calls.
        chunk_count=len(provider.extraction_calls),
        pages_sent_per_chunk=provider.pages_sent_per_call,
        pages_claimed_per_chunk=[
            call.pages_claimed for call in provider.extraction_calls
        ],
        schemas_per_chunk=[call.response_schema for call in provider.extraction_calls],
        result=result,
        error=error,
        provider=provider,
        elapsed_seconds=elapsed,
        extra={
            "expected_execution_path": _expected_execution_path(page_count, size),
            "provider_calls_total": provider.call_count,
            "blank_verification_calls": len(provider.verification_calls),
            "page_digests": page_digests(content),
        },
    )


# --------------------------------------------------------------------------
# Shared assertions
# --------------------------------------------------------------------------


def check_execution_path(run: RunResult) -> list[str]:
    """The path taken must be the one the production threshold selects."""
    expected = run.extra.get("expected_execution_path")
    if expected and run.execution_path != expected:
        return [
            f"expected the {expected} path for {run.page_count} page(s) at "
            f"{run.pages_per_chunk} per chunk, but the pipeline took "
            f"{run.execution_path}"
        ]
    return []


def check_schema_sent(run: RunResult) -> list[str]:
    """
    Every extraction call carries the answer-extraction schema.

    Regression 2: the chunked path once sent none, so answer_status stopped
    being mandatory and a lost answer could come back indistinguishable
    from a blank. Checked on every call, on both paths.
    """
    problems = []
    for index, schema in enumerate(run.schemas_per_chunk, start=1):
        if schema is not ANSWER_EXTRACTION_RESPONSE_SCHEMA:
            what = "no" if schema is None else "a different"
            problems.append(
                f"extraction call {index} was sent {what} structured-output schema"
            )
    return problems


def check_page_coverage(run: RunResult) -> list[str]:
    """
    The hard chunking invariant: every source page submitted exactly once.

    Returns a list of human-readable problems; empty means clean. Compared
    against `expected_chunks`, which is computed independently of the
    production splitter.
    """
    problems: list[str] = []
    if run.execution_path == "single-call":
        return problems

    sent = [page for chunk in run.pages_sent_per_chunk for page in chunk]
    expected_pages = set(range(1, run.page_count + 1))

    missing = sorted(expected_pages - set(sent))
    if missing:
        problems.append(f"pages never submitted: {missing}")

    duplicates = sorted({page for page in sent if sent.count(page) > 1})
    if duplicates:
        problems.append(f"pages submitted more than once: {duplicates}")

    out_of_range = sorted({page for page in sent if page not in expected_pages})
    if out_of_range:
        problems.append(f"pages outside the document: {out_of_range}")

    wanted = expected_chunks(run.page_count, run.pages_per_chunk)
    got = [list(chunk) for chunk in run.pages_sent_per_chunk]
    if got != wanted:
        problems.append(f"chunk layout {got} != expected {wanted}")

    return problems


def check_claimed_ranges(run: RunResult) -> list[str]:
    """The page range each chunk CLAIMED must match what it was sent."""
    problems: list[str] = []
    if run.execution_path == "single-call":
        # No chunk note exists on this path - there is only one payload and
        # it is the whole document.
        return problems
    for index, (sent, claimed) in enumerate(
        zip(run.pages_sent_per_chunk, run.pages_claimed_per_chunk, strict=True),
        start=1,
    ):
        if not sent:
            continue
        if claimed is None:
            problems.append(f"chunk {index} sent {list(sent)} but claimed no range")
            continue
        want = (min(sent), max(sent))
        if claimed != want:
            problems.append(
                f"chunk {index} was sent pages {list(sent)} but told the model "
                f"it was seeing pages {claimed[0]} to {claimed[1]}"
            )
    return problems


def check_statuses(run: RunResult, scenario) -> list[str]:
    """Every expectation's status, exactly."""
    problems: list[str] = []
    actual = run.status_by_question
    for expectation in scenario.expectations:
        key = str(expectation.model_question_number)
        got = actual.get(key)
        if got is None:
            problems.append(f"{expectation.question}: no entry in the result at all")
        elif got != expectation.status:
            problems.append(
                f"{expectation.question}: expected {expectation.status}, got {got}"
            )
    return problems


def check_no_invented_answers(run: RunResult, scenario) -> list[str]:
    """A BLANK or NOT_FOUND question must carry no answer text."""
    problems: list[str] = []
    html = run.html_by_question
    for expectation in scenario.expectations:
        if expectation.status in ("ANSWERED",):
            continue
        text = strip_markup(html.get(str(expectation.model_question_number), ""))
        if text:
            problems.append(
                f"{expectation.question} is {expectation.status} but carries "
                f"answer text {text!r}"
            )
    return problems


def check_answer_content(run: RunResult, scenario) -> list[str]:
    """
    Semantic content for answered questions, normalised.

    Deliberately not an exact-string comparison: a real model may wrap,
    punctuate or capitalise differently and still be correct. What must
    hold is that the expected answer is present.
    """
    problems: list[str] = []
    html = run.html_by_question
    for expectation in scenario.expectations:
        if expectation.status != "ANSWERED" or not expectation.answer:
            continue
        got = normalise(html.get(str(expectation.model_question_number), ""))
        if normalise(expectation.answer) not in got:
            problems.append(
                f"{expectation.question}: expected an answer containing "
                f"{expectation.answer!r}, got {got!r}"
            )
        # An answer written across pages must keep EVERY part, in order.
        # Checking only the final answer text would pass a merge that kept
        # the conclusion and silently dropped the working before it.
        position = 0
        for page, fragment in expectation.fragments:
            found_at = got.find(normalise(fragment), position)
            if found_at < 0:
                problems.append(
                    f"{expectation.question}: the part written on page {page} "
                    f"({fragment!r}) is missing or out of order in {got!r}"
                )
                break
            position = found_at + len(normalise(fragment))
    return problems


def check_no_cross_contamination(run: RunResult, scenario) -> list[str]:
    """
    No question's answer may carry another question's answer.

    Joining the parts of a split answer is only safe if the parts are
    joined onto the RIGHT question. A continuation attached to its
    neighbour, or a neighbour's answer swallowed into a continuation, would
    still pass a containment check on each question's own answer - so this
    looks for the other answers. One- and two-character answers (e.g. "W")
    are skipped: they occur inside ordinary words.
    """
    import re

    others = [
        (e.question, normalise(e.answer))
        for e in scenario.expectations
        if e.status == "ANSWERED" and e.answer and len(normalise(e.answer)) >= 3
    ]
    problems = []
    html = run.html_by_question
    for expectation in scenario.expectations:
        got = normalise(html.get(str(expectation.model_question_number), ""))
        if not got:
            continue
        for question, answer in others:
            if question == expectation.question:
                continue
            if re.search(rf"\b{re.escape(answer)}\b", got):
                problems.append(
                    f"{expectation.question}'s answer contains {question}'s "
                    f"answer {answer!r}: {got!r}"
                )
    return problems


def check_statuses_are_valid(run: RunResult) -> list[str]:
    problems = []
    for answer in run.answers:
        status = answer.get("answer_status")
        if status not in ANSWER_STATUSES:
            problems.append(
                f"question {answer.get('question_number')} has invalid "
                f"answer_status {status!r}"
            )
    return problems


def check_no_duplicates(run: RunResult) -> list[str]:
    order = run.question_order
    duplicates = sorted({q for q in order if order.count(q) > 1})
    return (
        [f"duplicated question entries in the result: {duplicates}"]
        if duplicates
        else []
    )


def check_completeness(run: RunResult, scenario) -> list[str]:
    """
    Every declared question is accounted for. An omission must never be
    silently absent - the downstream pairing would fabricate a placeholder
    and grade it as not attempted.
    """
    declared = {str(q["question_number"]) for q in scenario.questions}
    present = set(run.question_order)
    missing = sorted(declared - present)
    return [f"declared questions missing from the result: {missing}"] if missing else []


def strip_markup(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", value or "").strip()


def normalise(value: str) -> str:
    """Lowercase, tag-free, whitespace- and punctuation-insensitive."""
    import re

    text = strip_markup(value).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def full_check(run: RunResult, scenario) -> list[str]:
    """Every invariant that should hold for a clean scenario run."""
    problems: list[str] = []
    if run.error is not None:
        return [f"pipeline raised {run.error!r}"]
    problems += check_execution_path(run)
    problems += check_schema_sent(run)
    problems += check_page_coverage(run)
    problems += check_claimed_ranges(run)
    problems += check_statuses(run, scenario)
    problems += check_statuses_are_valid(run)
    problems += check_no_invented_answers(run, scenario)
    problems += check_answer_content(run, scenario)
    problems += check_no_cross_contamination(run, scenario)
    problems += check_no_duplicates(run)
    problems += check_completeness(run, scenario)
    return problems


def describe(run: RunResult, scenario, problems) -> str:
    """A failure message that does not require re-running the benchmark."""
    lines = [
        "",
        f"scenario        : {scenario.id} {scenario.name} [{scenario.category}]",
        f"notes           : {scenario.notes}",
        f"model quirks    : {list(scenario.model_quirks) or 'none'}",
        f"known gap       : {scenario.known_gap or 'none'}",
        f"pages           : {run.page_count}",
        f"pages_per_chunk : {run.pages_per_chunk}",
        f"execution path  : {run.execution_path}",
        f"chunks          : {run.chunk_count}",
        f"pages sent      : {[list(p) for p in run.pages_sent_per_chunk]}",
        f"pages claimed   : {[list(p) if p else None for p in run.pages_claimed_per_chunk]}",
        "already found   : "
        f"{[sorted(c.already_found) for c in run.provider.extraction_calls] if run.provider else '?'}",
        f"schema per chunk: {['set' if s else 'NONE' for s in run.schemas_per_chunk]}",
        "expected statuses:",
    ]
    for expectation in scenario.expectations:
        lines.append(
            f"    {expectation.question:<7} {expectation.status:<22} "
            f"pages={list(expectation.pages)}"
        )
    lines.append("actual statuses:")
    for question, status in run.status_by_question.items():
        lines.append(f"    {question:<7} {status}")
    if run.error:
        lines.append(f"error           : {run.error!r}")
    lines.append("problems:")
    lines.extend(f"    - {problem}" for problem in problems)
    return "\n".join(lines)


NOT_FOUND = NOT_FOUND_IN_DOCUMENT
