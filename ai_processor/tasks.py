"""
Scheduled grading-quality checks.

Two jobs, deliberately split by what they can and cannot detect:

nightly_grading_benchmark_replay
    Free and deterministic. Replays recorded model responses through the
    real pipeline and diffs the metrics against the committed baseline.
    Catches regressions in OUR code — a change to snapping, evidence
    enforcement, batching or arithmetic shows up immediately. By
    construction it CANNOT notice the model itself changing.

weekly_grading_benchmark_live
    Costs real credits. Grades the same fixed dataset against the live
    model, so it is the only thing that can detect the provider silently
    changing behaviour underneath us. Off unless ENABLE_AI_LIVE_QA is
    set, so a normal production worker no-ops at DEBUG.

Both follow billing/tasks.py's live-QA conventions: max_retries=0
(a failure here is a signal to investigate, not a transient to paper
over) and ERROR-level logging naming the reproduction command.

There is no CI in this repo, so Celery Beat is the only scheduler
available. If CI is ever added, the nightly replay belongs there
instead — it needs no credentials and no database of its own.
"""

import json
import logging
from pathlib import Path

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)

BASELINE_PATH = (
    Path(__file__).resolve().parent / "benchmark" / "baselines" / ("baseline.json")
)


def live_qa_enabled():
    """Mirrors billing.stripe_live_qa.live_qa_enabled's posture."""
    return bool(getattr(settings, "ENABLE_AI_LIVE_QA", False))


def _run(mode):
    """Execute the benchmark and return (report, diff-or-None)."""
    from ai_processor.benchmark import runner, scoring
    from ai_processor.benchmark.dataset import IDENTICAL_ANSWER_PROBES, iter_all_errors
    from ai_processor.management.commands.grading_benchmark import (
        Command,
        compare_to_baseline,
    )

    problems = list(iter_all_errors())
    if problems:
        raise RuntimeError(
            "Benchmark dataset is internally inconsistent: " + "; ".join(problems)
        )

    user = Command()._resolve_user(mode, None)
    run = runner.execute_benchmark(user, mode=mode)
    report = scoring.score_run(run)
    report["consistency"] = scoring.check_consistency(run, IDENTICAL_ANSWER_PROBES)

    diff = None
    if BASELINE_PATH.exists():
        diff = compare_to_baseline(report, json.loads(BASELINE_PATH.read_text()))
    return report, diff


def _escalate(mode, report, diff):
    """Log the outcome at a level matching how bad it is."""
    overall = report["overall"]
    summary = (
        f"{overall['questions']} questions, "
        f"exact={overall['exact_rate']}, "
        f"within-one-level={overall['within_one_level_rate']}, "
        f"bias={overall['mean_level_error']}"
    )

    if diff and diff["regressed"]:
        logger.error(
            "Grading benchmark (%s) REGRESSED against baseline: %s. Details: %s. "
            "Reproduce with: manage.py grading_benchmark --mode %s --baseline %s",
            mode,
            "; ".join(diff["lines"]),
            summary,
            mode,
            BASELINE_PATH,
        )
        return f"Grading benchmark {mode}: REGRESSION — {summary}"

    if report["errors"]:
        logger.error(
            "Grading benchmark (%s) had %d submission(s) fail outright: %s",
            mode,
            len(report["errors"]),
            report["errors"],
        )
        return f"Grading benchmark {mode}: {len(report['errors'])} run error(s)"

    logger.info("Grading benchmark (%s) OK: %s", mode, summary)
    return f"Grading benchmark {mode}: {summary}"


@shared_task(bind=True, max_retries=0)
def nightly_grading_benchmark_replay(self):
    """
    Replay the benchmark against recorded responses. Free, deterministic,
    safe to run anywhere — makes no model calls and writes no submission
    rows.
    """
    from ai_processor.benchmark.runner import MODE_REPLAY, MissingRecordingError

    try:
        report, diff = _run(MODE_REPLAY)
    except MissingRecordingError as exc:
        # Recordings go stale whenever the dataset or a prompt file
        # changes. That is a maintenance task, not a grading defect, so
        # it must not read as a quality regression.
        logger.warning(
            "Grading benchmark replay skipped — recordings are stale or "
            "missing (%s). Re-record with: manage.py grading_benchmark "
            "--mode record  (this makes real, billed calls).",
            exc,
        )
        return "Grading benchmark replay skipped: recordings stale."

    return _escalate(MODE_REPLAY, report, diff)


@shared_task(bind=True, max_retries=0)
def weekly_grading_benchmark_live(self):
    """
    Grade the benchmark against the LIVE model and diff against baseline.

    This is the only check that can see the provider changing behaviour.
    It bills a real wallet, so it stays off unless explicitly enabled.
    """
    from ai_processor.benchmark.runner import MODE_LIVE

    if not live_qa_enabled():
        logger.debug(
            "AI live QA is not enabled in this environment; skipping the "
            "weekly grading benchmark. (Set ENABLE_AI_LIVE_QA=True on a "
            "QA/staging worker.)"
        )
        return "Grading benchmark live skipped: not enabled in this environment."

    report, diff = _run(MODE_LIVE)
    return _escalate(MODE_LIVE, report, diff)
