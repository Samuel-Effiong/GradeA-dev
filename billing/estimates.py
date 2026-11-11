"""The credit estimate (Epic B1b-4; plan 08 §3.6 and §9 item 6, 08b §1.3).

Before a batch runs, the teacher can ask what it will likely cost (p50) and
what it could cost (p90), and whether their spendable balance covers it.
The estimate is advice only: nothing refuses a teacher because of it.

THE ONLY READER OF AICallRecord outside ai_processor (SM ruling 2026-11-08,
`ai_processor.tests_call_record_static.NothingReadsOrExposesTheTable`). It
reads aggregates only (sums and counts per paper) and every public function
returns numbers and plain dicts or lists, never rows or querysets.

A paper's cost is the sum of `charged_credits` of its charged grading calls
(grouped by `processing_task_id`), over the last 90 days, for papers whose
task row ended SUCCESS. Calls whose usage row was refunded are left out.

The paper estimate uses the first source with enough history:
  1. this assignment's papers (at least 10);
  2. the teacher's papers of the same shape (at least 30);
  3. all papers of the same shape (at least 30);
  3b. all papers of any shape, scaled per question (at least 30);
  4. "rough": one representative prompt's estimate x
     BillingSettings.cold_start_cost_factor, with p90 x 1.5.
A shape is the question-count bucket and whether any question carries an
image. For grading, images come only from the assignment's own
question_image fields (AIProcessor._question_image_content_blocks), so a
target assignment's shape is read exactly from its questions.
"""

import json
import math
import statistics
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Exists, Max, OuterRef, Sum, TextField
from django.db.models.functions import Cast
from django.utils import timezone

from ai_processor.models import AICallRecord
from audit import metrics
from billing.models import BillingSettings, CreditUsageLog, CreditWallet
from students.models import (
    BackgroundProcessingTask,
    BackgroundTaskStatus,
    BackgroundTaskType,
)

HISTORY_DAYS = 90
ASSIGNMENT_MIN = 10
SHAPE_MIN = 30
Z90 = 1.28
ROUGH_P90_FACTOR = 1.5

SOURCE_ASSIGNMENT = "assignment"
SOURCE_TEACHER_SHAPE = "teacher_shape"
SOURCE_SHAPE = "shape"
SOURCE_PER_QUESTION = "per_question"
SOURCE_ROUGH = "rough"
SHAPE_BASIS = "assignment_questions"

SUFFICIENT = "SUFFICIENT"
MARGINAL = "MARGINAL"
INSUFFICIENT = "INSUFFICIENT"

DRIFT_OVERALL = 0.85
DRIFT_CELL = 0.80
DRIFT_CELL_MIN = 30

GRADING_TASK_TYPE = "grade_assignment"
CHARGED = "charged"

# Approved text #17 (owner, 2026-11-09; team/approved_wording_b1b_2026-11-09.md).
# Ships False; switched on at B1b-8 with the other _APPROVED constants.
DRIFT_EMAIL_APPROVED = True
DRIFT_EMAIL_SUBJECT = "Credit estimates need a look"
DRIFT_EMAIL_PARAGRAPHS = (
    "This month's check of credit estimates found that fewer batches than "
    "expected stayed within the high estimate.",
    "Overall: {coverage}% of {batches} batches stayed within the high "
    "estimate. The target is at least 85%.",
    "Groups below 80% (only groups with at least 30 batches are checked):\n" "{groups}",
    "Suggested cold-start factor: {suggested_factor} (now {current_factor}).",
    "Nothing has been changed. Estimates only change when someone changes "
    "the factor.",
)
DRIFT_EMAIL_GROUP = (
    "{questions} questions, {with_or_without} images, batches of {size_band}: "
    "{coverage}% of {batches} batches"
)
SIZE_BAND_WORDS = {"1": "1", "2-9": "2 to 9", "10-29": "10 to 29", "30+": "30 or more"}


# ----------------------------------------------------------------- shapes


def question_bucket(questions):
    count = questions or 0
    if count <= 5:
        return "1-5"
    if count <= 10:
        return "6-10"
    if count <= 20:
        return "11-20"
    return "21+"


def size_band(n):
    if n <= 1:
        return "1"
    if n <= 9:
        return "2-9"
    if n <= 29:
        return "10-29"
    return "30+"


def _question_count(assignment):
    questions = assignment.questions or []
    return len(questions) if isinstance(questions, list) else 0


def _has_question_image(assignment):
    for question in assignment.questions or []:
        if not isinstance(question, dict):
            continue
        url = (question.get("question_image") or "").strip().lower()
        if url.startswith(("http://", "https://")):
            return True
    return False


def shape_of(assignment):
    """(question bucket, has_images) of the assignment's papers."""
    return (
        question_bucket(_question_count(assignment)),
        _has_question_image(assignment),
    )


# ----------------------------------------------------------- the history


def _paper_queryset(*, since, until=None, assignment_id=None, billed_user_id=None):
    calls = AICallRecord.objects.filter(
        task_type=GRADING_TASK_TYPE,
        outcome=CHARGED,
        created_at__gte=since,
        processing_task_id__isnull=False,
    )
    if until is not None:
        calls = calls.filter(created_at__lt=until)
    if assignment_id is not None:
        calls = calls.filter(assignment_id=assignment_id)
    if billed_user_id is not None:
        calls = calls.filter(billed_user_id=billed_user_id)
    refunded = CreditUsageLog.objects.filter(
        task_id=OuterRef("task_id"), is_refunded=True
    )
    succeeded = BackgroundProcessingTask.objects.filter(
        pk=OuterRef("processing_task_id"), status=BackgroundTaskStatus.SUCCESS
    )
    return (
        calls.exclude(Exists(refunded))
        .filter(Exists(succeeded))
        .values("processing_task_id")
        .annotate(
            cost=Sum("charged_credits"),
            estimate_sum=Sum("estimate"),
            questions=Max("questions"),
            images=Max("images"),
            started_at=Max("created_at"),
            # PostgreSQL has no max(uuid); every call of one paper carries
            # the same ids, so the text max is that id.
            assignment_id=Max(Cast("assignment_id", TextField())),
            billed_user_id=Max(Cast("billed_user_id", TextField())),
        )
        .order_by("processing_task_id")
    )


def _since(days, now=None):
    return (now or timezone.now()) - timedelta(days=days)


def paper_costs(
    *, assignment_id=None, billed_user_id=None, days=HISTORY_DAYS, now=None, until=None
):
    """One plain dict per paper: its cost and shape. Never rows."""
    rows = _paper_queryset(
        since=_since(days, now),
        until=until,
        assignment_id=assignment_id,
        billed_user_id=billed_user_id,
    )
    return [
        {
            "processing_task_id": str(row["processing_task_id"]),
            "cost": int(row["cost"] or 0),
            "estimate": int(row["estimate_sum"] or 0),
            "questions": int(row["questions"] or 0),
            "has_images": bool(row["images"]),
            "started_at": row["started_at"].isoformat(),
            "assignment_id": (
                str(row["assignment_id"]) if row["assignment_id"] else None
            ),
            "billed_user_id": (
                str(row["billed_user_id"]) if row["billed_user_id"] else None
            ),
        }
        for row in rows
    ]


def paper_costs_sql(*, assignment_id=None, billed_user_id=None):
    """The SQL and params of paper_costs' query (Gate 6 EXPLAINs it).

    The params come back as text (timestamps in ISO form), so like every
    public function here it returns plain values; PostgreSQL reads each as
    its column's type."""
    query = _paper_queryset(
        since=_since(HISTORY_DAYS),
        assignment_id=assignment_id,
        billed_user_id=billed_user_id,
    ).query
    sql, params = query.sql_with_params()
    return sql, [
        param.isoformat() if hasattr(param, "isoformat") else str(param)
        for param in params
    ]


# --------------------------------------------------------------- the band


def _nearest_rank(ordered, quantile):
    return ordered[max(0, math.ceil(quantile * len(ordered)) - 1)]


def band(costs, n):
    """The band of a batch of n papers from per-paper costs."""
    if n <= 0 or not costs:
        return {
            "p50": 0,
            "p90": 0,
            "p50_paper": 0,
            "p90_paper": 0,
            "mean": 0.0,
            "sigma": 0.0,
        }
    ordered = sorted(costs)
    p50_paper = _nearest_rank(ordered, 0.5)
    p90_paper = _nearest_rank(ordered, 0.9)
    mean = statistics.fmean(ordered)
    sigma = statistics.pstdev(ordered)
    p50 = n * p50_paper
    if n >= 10:
        p90 = math.ceil(n * mean + Z90 * sigma * math.sqrt(n))
    else:
        p90 = n * p90_paper
    return {
        "p50": int(p50),
        "p90": int(p90),
        "p50_paper": int(p50_paper),
        "p90_paper": int(p90_paper),
        "mean": float(mean),
        "sigma": float(sigma),
    }


def verdict(p50, p90, spendable):
    """FR-B-04: S >= p90 SUFFICIENT; p50 <= S < p90 MARGINAL; else
    INSUFFICIENT."""
    if spendable >= p90:
        return SUFFICIENT
    if spendable >= p50:
        return MARGINAL
    return INSUFFICIENT


def _rough_paper_cost(assignment, representative):
    from ai_processor.services import GRADING_ASSIGNMENT_PROMPT, ai_processor

    answers = getattr(representative, "answers", None) if representative else None
    user_prompt = (
        "### Rubric JSON\n"
        + json.dumps(assignment.questions or [], default=str)
        + "\n### Student Answers JSON\n"
        + json.dumps(answers or [], default=str)
    )
    return int(
        ai_processor.estimate_messages_cost(
            [{"type": "text", "text": user_prompt}],
            [{"type": "text", "text": str(GRADING_ASSIGNMENT_PROMPT)}],
            [],
        )
    )


def _rough(per_paper, n, factor=None):
    if factor is None:
        factor = BillingSettings.load().cold_start_cost_factor
    p50 = math.ceil(n * per_paper * factor)
    return {
        "p50": int(p50),
        "p90": int(math.ceil(p50 * ROUGH_P90_FACTOR)),
        "source": SOURCE_ROUGH,
        "shape_basis": SHAPE_BASIS,
        "sample_size": 0,
    }


def _from_history(
    history, assignment_id, teacher_id, target_bucket, target_images, target_q, n
):
    """The first tier with enough papers, or None."""
    own = [p["cost"] for p in history if p["assignment_id"] == assignment_id]
    if len(own) >= ASSIGNMENT_MIN:
        return SOURCE_ASSIGNMENT, own

    def same_shape(p):
        return (
            question_bucket(p["questions"]) == target_bucket
            and p["has_images"] == target_images
        )

    teachers = [
        p["cost"]
        for p in history
        if p["billed_user_id"] == teacher_id and same_shape(p)
    ]
    if len(teachers) >= SHAPE_MIN:
        return SOURCE_TEACHER_SHAPE, teachers
    shaped = [p["cost"] for p in history if same_shape(p)]
    if len(shaped) >= SHAPE_MIN:
        return SOURCE_SHAPE, shaped
    scaled = [
        p["cost"] / p["questions"] * max(target_q, 1)
        for p in history
        if p["questions"] >= 1
    ]
    if len(scaled) >= SHAPE_MIN:
        return SOURCE_PER_QUESTION, scaled
    return None


def estimate(assignment, n, *, teacher, representative=None, now=None):
    """The batch estimate for n papers of `assignment` by `teacher`."""
    history = paper_costs(now=now, until=now)
    return _estimate_from(
        history,
        assignment,
        n,
        teacher_id=str(teacher.pk),
        rough_per_paper=lambda: _rough_paper_cost(assignment, representative),
    )


def paper_hold_estimate(assignment, n, *, teacher, representative=None, now=None):
    """Epic B1b-6 (Q6): estimate() for n papers, plus the per-paper p90 that
    a batch holds for each paper it launches (at least 1)."""
    history = paper_costs(now=now, until=now)
    result, p90_paper = _estimate_and_paper(
        history,
        assignment,
        n,
        teacher_id=str(teacher.pk),
        rough_per_paper=lambda: _rough_paper_cost(assignment, representative),
    )
    return result, max(int(p90_paper), 1)


def _estimate_from(history, assignment, n, *, teacher_id, rough_per_paper):
    result, _ = _estimate_and_paper(
        history,
        assignment,
        n,
        teacher_id=teacher_id,
        rough_per_paper=rough_per_paper,
    )
    return result


def _estimate_and_paper(history, assignment, n, *, teacher_id, rough_per_paper):
    target_bucket, target_images = shape_of(assignment)
    chosen = _from_history(
        history,
        str(assignment.pk),
        teacher_id,
        target_bucket,
        target_images,
        _question_count(assignment),
        n,
    )
    if chosen is None:
        per_paper = rough_per_paper()
        factor = BillingSettings.load().cold_start_cost_factor
        return _rough(per_paper, n, factor), math.ceil(
            per_paper * factor * ROUGH_P90_FACTOR
        )
    source, costs = chosen
    result = band(costs, n)
    return {
        "p50": result["p50"],
        "p90": result["p90"],
        "source": source,
        "shape_basis": SHAPE_BASIS,
        "sample_size": len(costs),
    }, result["p90_paper"]


def spendable_of(user):
    """The wallet's free balance (holds.spendable_credits), 0 without one."""
    from billing.holds import spendable_credits

    wallet = CreditWallet.objects.filter(user_id=user.pk).first() if user.pk else None
    if wallet is None:
        return 0
    return int(spendable_credits(wallet))


def advise(teacher, assignment, n, *, representative=None):
    """The endpoint's payload: codes and numbers only."""
    result = estimate(assignment, n, teacher=teacher, representative=representative)
    spendable = spendable_of(teacher)
    return {
        "p50": result["p50"],
        "p90": result["p90"],
        "verdict": verdict(result["p50"], result["p90"], spendable),
        "spendable": spendable,
        "projected_remaining_p50": spendable - result["p50"],
        "projected_remaining_p90": spendable - result["p90"],
        "source": result["source"],
        "shape_basis": result["shape_basis"],
        "sample_size": result["sample_size"],
        "n": n,
    }


# ----------------------------------------------- the backtest and drift


def _batch_keys(task_ids):
    rows = BackgroundProcessingTask.objects.filter(pk__in=task_ids).values_list(
        "pk", "task_type", "batch_session_id"
    )
    keys = {}
    for pk, task_type, session_id in rows:
        if task_type == BackgroundTaskType.BATCH_SUBMISSION_GRADING and session_id:
            keys[str(pk)] = f"session:{session_id}"
        else:
            keys[str(pk)] = f"paper:{pk}"
    return keys


def past_batches(*, days=HISTORY_DAYS, now=None):
    """Q1: the batch-grading task rows of one batch_session are one batch;
    a paper graded on its own is a batch of 1. Successful papers only."""
    history = paper_costs(days=days, now=now)
    keys = _batch_keys([p["processing_task_id"] for p in history])
    grouped = {}
    for p in history:
        grouped.setdefault(
            keys.get(p["processing_task_id"], p["processing_task_id"]), []
        ).append(p)
    batches = []
    for key, members in sorted(grouped.items()):
        first = min(members, key=lambda p: p["started_at"])
        batches.append(
            {
                "batch": key,
                "n": len(members),
                "actual": sum(p["cost"] for p in members),
                "assignment_id": first["assignment_id"],
                "billed_user_id": first["billed_user_id"],
                "started_at": first["started_at"],
                "bucket": question_bucket(first["questions"]),
                "has_images": first["has_images"],
                "questions": first["questions"],
                "first_paper_estimate": first["estimate"],
                "papers": sorted(p["processing_task_id"] for p in members),
            }
        )
    return batches


class _Shape:
    """The target shape of a replayed batch (no Assignment row needed)."""

    def __init__(self, batch):
        self.pk = batch["assignment_id"]
        self.questions = [{}] * batch["questions"]
        if batch["has_images"]:
            self.questions = [{"question_image": "https://replay"}] + self.questions[1:]


def backtest(*, days=HISTORY_DAYS, now=None):
    """Replay each past batch against the history before it: the share of
    batches whose actual cost stayed within p90, per shape x size cell."""
    now = now or timezone.now()
    batches = past_batches(days=days, now=now)
    # Each batch is estimated from the HISTORY_DAYS before its own start, so
    # the history reaches back up to `days` further than the batches do.
    history = paper_costs(days=days + HISTORY_DAYS, now=now)
    cells = {}
    ratios = []
    for batch in batches:
        started = batch["started_at"]
        cutoff = (
            datetime.fromisoformat(started) - timedelta(days=HISTORY_DAYS)
        ).isoformat()
        before = [
            p
            for p in history
            if cutoff <= p["started_at"] < started
            and p["processing_task_id"] not in batch["papers"]
        ]
        result = _estimate_from(
            before,
            _Shape(batch),
            batch["n"],
            teacher_id=batch["billed_user_id"],
            rough_per_paper=lambda b=batch: _rough_figure_of_paper(b["papers"][0]),
        )
        covered = batch["actual"] <= result["p90"]
        key = (batch["bucket"], batch["has_images"], size_band(batch["n"]))
        cell = cells.setdefault(key, {"batches": 0, "covered": 0, "ratios": []})
        cell["batches"] += 1
        cell["covered"] += int(covered)
        if result["p50"]:
            cell["ratios"].append(batch["actual"] / result["p50"])
            ratios.append(batch["actual"] / result["p50"])
    out_cells = []
    for (bucket, has_images, band_name), cell in sorted(cells.items()):
        out_cells.append(
            {
                "bucket": bucket,
                "has_images": has_images,
                "size_band": band_name,
                "batches": cell["batches"],
                "coverage": round(cell["covered"] / cell["batches"], 4),
                "ratio_quantiles": _quantiles(cell["ratios"]),
            }
        )
    total = sum(c["batches"] for c in cells.values())
    covered = sum(c["covered"] for c in cells.values())
    return {
        "batches": total,
        "coverage": round(covered / total, 4) if total else None,
        "cells": out_cells,
        "ratio_quantiles": _quantiles(ratios),
        "suggested_factor": rough_replay_ratio(days=days, now=now),
    }


def _quantiles(values):
    if not values:
        return {}
    ordered = sorted(values)
    return {f"p{q}": round(_nearest_rank(ordered, q / 100), 3) for q in (10, 50, 90)}


def _rough_figure_of_paper(processing_task_id):
    """The rough tier's own unscaled figure for a past paper: the same
    prompt estimate the endpoint makes, from the paper's own assignment and
    submission (0 when the assignment is gone)."""
    task = (
        BackgroundProcessingTask.objects.select_related("assignment", "submission")
        .filter(pk=processing_task_id)
        .first()
    )
    if task is None or task.assignment is None:
        return 0
    return _rough_paper_cost(task.assignment, task.submission)


def rough_replay_ratio(*, days=HISTORY_DAYS, now=None):
    """The suggested cold_start_cost_factor (SM ruling 2026-11-09, D-f):
    the median over papers of actual / the rough tier's own figure, so the
    factor closes the rough tier's own gap. None without data."""
    ratios = []
    for p in paper_costs(days=days, now=now):
        figure = _rough_figure_of_paper(p["processing_task_id"])
        if figure:
            ratios.append(p["cost"] / figure)
    return round(statistics.median(ratios), 3) if ratios else None


def provider_gap_ratio(*, days=HISTORY_DAYS, now=None):
    """For information only: the median over papers of actual / the real
    calls' recorded estimate (AICallRecord.estimate). It covers the
    provider's gap, not the rough prompt's, so it is never the factor."""
    ratios = [
        p["cost"] / p["estimate"]
        for p in paper_costs(days=days, now=now)
        if p["estimate"]
    ]
    return round(statistics.median(ratios), 3) if ratios else None


def drift_reasons(report):
    """Below 85% overall, or below 80% in a cell with at least 30 batches."""
    reasons = []
    coverage = report.get("coverage")
    if coverage is not None and coverage < DRIFT_OVERALL:
        reasons.append(f"overall coverage {coverage}")
    for cell in report.get("cells", []):
        if cell["batches"] >= DRIFT_CELL_MIN and cell["coverage"] < DRIFT_CELL:
            reasons.append(
                f"cell {cell['bucket']}/{cell['has_images']}/{cell['size_band']} "
                f"coverage {cell['coverage']}"
            )
    return reasons


def emit_drift_metric(reasons):
    """NFR-OBS-03's credit_estimate_drift (no-op without Sentry)."""
    metrics.count("credit_estimate_drift", tags={"reasons": len(reasons)})


def _percent(value):
    return int(round((value or 0) * 100))


def _drift_email(report, current_factor):
    groups = "\n".join(
        DRIFT_EMAIL_GROUP.format(
            questions=cell["bucket"],
            with_or_without="with" if cell["has_images"] else "without",
            size_band=SIZE_BAND_WORDS[cell["size_band"]],
            coverage=_percent(cell["coverage"]),
            batches=cell["batches"],
        )
        for cell in report.get("cells", [])
        if cell["batches"] >= DRIFT_CELL_MIN and cell["coverage"] < DRIFT_CELL
    )
    values = {
        "coverage": _percent(report.get("coverage")),
        "batches": report.get("batches", 0),
        "groups": groups,
        "suggested_factor": report.get("suggested_factor"),
        "current_factor": current_factor,
    }
    return "\n\n".join(p.format(**values) for p in DRIFT_EMAIL_PARAGRAPHS)


def drift_check(now=None):
    """The monthly check: one summary row every run; on drift the metric
    and, once text #17 is switched on, the email. It never changes the
    factor."""
    from billing.models import EstimateDriftRun

    report = backtest(now=now)
    reasons = drift_reasons(report)
    current_factor = BillingSettings.load().cold_start_cost_factor
    row = EstimateDriftRun.objects.create(
        window_days=HISTORY_DAYS,
        batches=report.get("batches", 0),
        coverage=report.get("coverage"),
        cells=report.get("cells", []),
        suggested_factor=report.get("suggested_factor"),
        current_factor=current_factor,
        drifted=bool(reasons),
    )
    if reasons:
        emit_drift_metric(reasons)
        if DRIFT_EMAIL_APPROVED:
            _send_drift_email(report, current_factor)
    return {"run_id": str(row.pk), "drifted": bool(reasons), "reasons": reasons}


def _send_drift_email(report, current_factor):
    from AutoGrader.tasks import send_email_task
    from users.models import CustomUser, UserTypes

    recipients = sorted(
        CustomUser.objects.filter(
            is_active=True, is_superuser=True, user_type=UserTypes.SUPER_ADMIN
        ).values_list("email", flat=True)
    )
    if not recipients:
        return
    send_email_task.delay(
        subject=DRIFT_EMAIL_SUBJECT,
        message=_drift_email(report, current_factor),
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=recipients,
    )
