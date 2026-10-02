"""
Executes the extraction dataset through the real extraction pipeline.

Same three modes, same tape, same guarantees as the grading benchmark
(ai_processor/benchmark/runner.py) — this deliberately REUSES `_Tape`
rather than growing a parallel recorder, so a fix to the record/replay
seam lands in both benchmarks at once.

## What a case actually exercises

Each ExtractionCase is rendered to HTML and then to ProseMirror by the
pipeline's OWN renderer, and that document is what the model is asked to
extract. That is byte-for-byte the loop a teacher's edit takes: the
frontend editor is free-form and resends the whole document, so every
edit is a full AI re-extraction. The benchmark therefore measures the
question that loop raises — *if a teacher opens this assignment and saves
it, do they get the same assignment back?*

## Why replay matters here specifically

The extraction prompt is over 11,000 tokens and is edited by hand. Before
this existed, the only way to know whether an edit helped or hurt was to
pay for a live run and eyeball the JSON. Replay makes the regression
check free and deterministic, which is the difference between a check
that runs on every change and one that runs when somebody remembers.
"""

import time

from django.test import override_settings

from ai_processor.benchmark.extraction_dataset import EXTRACTION_CASES, ExtractionCase
from ai_processor.benchmark.extraction_scoring import score_case, score_run
from ai_processor.benchmark.runner import (
    MODE_RECORD,
    MODE_REPLAY,
    MODES,
    RECORDINGS_DIR,
    _Tape,
)

#: Kept separate from the grading recordings. The two benchmarks have
#: independent lifecycles - re-recording extraction after a prompt edit
#: must not force a re-record of the (far more expensive) grading set.
EXTRACTION_RECORDINGS_DIR = RECORDINGS_DIR.parent / "extraction_recordings"


def _extraction_settings():
    """
    Settings pinned for every extraction benchmark run.

    Currently empty, and deliberately still here rather than inlined: the
    grading benchmark has a real list (_benchmark_settings in runner.py)
    and this is where extraction's equivalents go the moment any of its
    behaviour becomes settings-driven.

    NOTE what CANNOT be pinned here.
    ai_processor.services.PROSEMIRROR_CHUNK_THRESHOLD is a module
    constant, not a Django setting, and it decides whether a document is
    extracted in one call or several. Changing it therefore changes the
    number of model calls and so the replay key set, silently invalidating
    every recording — with no override_settings hook to protect against
    it. The dataset is sized well under the threshold so no case chunks
    today (asserted by
    tests_extraction_benchmark_runner.ChunkThresholdGuardTest), which is
    what keeps that fragility theoretical.
    """
    return {}


def iter_cases(case_keys=None):
    """Cases to run, in a stable order."""
    for case in EXTRACTION_CASES:
        if case_keys and case.key not in case_keys:
            continue
        yield case


def render_case_document(case: ExtractionCase) -> str:
    """
    The ProseMirror document a teacher's editor would hand back for this
    case, produced by the pipeline's own renderer.

    include_rubric=True because this is the TEACHER document - the one
    `raw_input` holds and the one re-extraction reads (see
    AssignmentDetailSerializer.get_raw_input). A rubric-free render would
    measure the student view, which is never re-extracted.
    """
    from assignments.services import AssignmentProcessingService

    html = AssignmentProcessingService.format_assignment_standard_html(
        case.as_assignment(), include_rubric=True
    )
    return AssignmentProcessingService.html_to_prosemirror_text(html)


def execute_extraction_benchmark(
    user,
    mode=MODE_REPLAY,
    case_keys=None,
    recordings_dir=EXTRACTION_RECORDINGS_DIR,
    progress=None,
):
    """
    Re-extract every case and score the result.

    `user` must be a TEACHER with an active subscription and a credit
    wallet, exactly as the grading benchmark requires - extraction goes
    through execute_graded_task and is billed like any other call.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    from ai_processor.services import AIProcessor, ai_processor

    tape = _Tape(mode, recordings_dir)
    original = AIProcessor._AIProcessor__ai_model  # type: ignore[attr-defined]
    records = []

    with override_settings(**_extraction_settings()):
        with patch_ai_model(AIProcessor, tape, original):
            for case in iter_cases(case_keys):
                if progress:
                    progress(case)

                started = time.monotonic()
                before = tape.tokens
                record = {
                    "case_key": case.key,
                    "case": case,
                    "extracted": None,
                    "error": None,
                }
                try:
                    document = render_case_document(case)
                    content = [{"type": "text", "text": document}]
                    record["extracted"] = ai_processor.extract_assignment_with_retry(
                        user, content, max_retries=1
                    )
                except Exception as exc:  # surfaced per-case, never fatal
                    record["error"] = f"{type(exc).__name__}: {exc}"
                record["elapsed_seconds"] = round(time.monotonic() - started, 2)
                record["tokens"] = tape.tokens - before
                records.append(record)

    if mode == MODE_RECORD:
        tape.save()

    case_results = []
    for record in records:
        result = score_case(record["case"], record["extracted"])
        result["error"] = record["error"]
        result["tokens"] = record["tokens"]
        result["elapsed_seconds"] = record["elapsed_seconds"]
        if record["error"]:
            # An extraction that raised has no scoreable output. It must
            # fail the run rather than be excluded, or a prompt edit that
            # makes extraction CRASH would look like a clean benchmark.
            result["passed"] = False
            result["strict_failures"].append(
                f"{record['case_key']}: extraction failed - {record['error']}"
            )
        case_results.append(result)

    run = score_run(case_results)
    run.update(
        {
            "mode": mode,
            "model_calls": tape.calls,
            "total_tokens": tape.tokens,
            "recordings": str(recordings_dir),
            "responses": {
                key: tape.entries[key] for key in tape.used_keys if key in tape.entries
            },
        }
    )
    return run


def patch_ai_model(processor_cls, tape, original):
    """The transport seam, isolated so both benchmarks patch it the same
    way and a change to the mangled-name detail lands in one place."""
    from unittest.mock import patch

    return patch.object(processor_cls, "_AIProcessor__ai_model", tape.wrap(original))
