"""
Trend analysis over the benchmark run history.

THE QUESTION THIS ANSWERS

Every run write-up in FINDINGS.md has had to hedge in the same way:

    "a single run cannot distinguish 'the fix didn't help' from 'noise'"

With several runs stored, that stops being a judgement call. If exact-match
has ranged 82.7-86.5% across five runs with no relevant change, then a
two-point move is ordinary wobble; a seven-point move in evidence-verified is
four times that spread and is therefore real. This module computes that
comparison instead of leaving it to the eye.

WHAT COUNTS AS A COMPARABLE RUN

Two kinds of row are excluded from variation statistics by default, and both
would badly distort the result if they were not:

  - REPLAY runs re-read fixed recorded responses, so their numbers are
    identical every time. Averaging in 365 identical nightly rows would
    collapse the apparent spread towards zero, after which every genuine run
    would look like a dramatic anomaly.
  - PARTIAL runs (--assignment maths) grade a subset, so their rates are not
    comparable with a full run's.

Runs are also grouped by prompt fingerprint, because a prompt change alters
what the model was asked; the caller is warned when a span mixes versions.

STATISTICS

stdlib `statistics` only, matching the convention set in scoring.py ("scipy
is not a dependency of this project and pulling it in for one statistic would
be disproportionate"). numpy and pandas are installed but deliberately unused
by the grading code.
"""

import statistics
from collections import defaultdict

# The numbers worth watching over time. Order matters — it is the order they
# are reported in.
TRENDED_METRICS = (
    ("exact_rate", "exact match", "rate"),
    ("within_one_level_rate", "within one level", "rate"),
    ("mean_level_error", "bias (+ = lenient)", "number"),
    ("evidence_verified_rate", "evidence verified", "rate"),
    ("deterministic_accuracy", "tier 0 accuracy", "rate"),
    ("second_opinion_disagreement_rate", "2nd-opinion disagreement", "rate"),
    ("total_tokens", "tokens per run", "count"),
)

PAID_MODES = ("live", "record")

# Below this many runs, a spread is not meaningful and the report says so
# rather than printing a number that invites false confidence.
MIN_RUNS_FOR_BAND = 3

# How many standard deviations from the mean before a run is called unusual.
# 2 is the conventional "outside normal variation" threshold.
OUTLIER_SIGMAS = 2.0


def comparable_runs(runs, include_replay=False, include_partial=False):
    """
    The runs that may legitimately be compared with each other.

    Sorted chronologically by run_id, which is safe because run_id starts
    with a UTC timestamp.
    """
    selected = []
    for run in runs:
        if not include_replay and run.get("mode") not in PAID_MODES:
            continue
        if not include_partial and not run.get("is_full_run", True):
            continue
        selected.append(run)
    return sorted(selected, key=lambda r: r.get("run_id") or "")


def metric_values(runs, metric):
    """(run_id, value) for every run that has this metric, in order."""
    series = []
    for run in runs:
        value = (run.get("metrics") or {}).get(metric)
        if value is not None:
            series.append((run.get("run_id"), value))
    return series


def clamp_for_kind(value, kind):
    """
    Keep a displayed bound inside what the metric can actually be.

    mean +/- 2 spread is the right calculation but can land outside a rate's
    real limits — a "normal range" topping out at 103.2% reads as a mistake
    and undermines trust in every other number on the page. The underlying
    arithmetic is untouched; only what is shown is bounded.
    """
    if value is None or kind != "rate":
        return value
    return min(1.0, max(0.0, value))


def noise_band(values, kind=None):
    """
    What "normal" looks like for a metric.

    Returns None when there are too few runs — an honest "we don't know yet"
    is far more useful than a spread computed from two points, which would
    read as authoritative and be meaningless.
    """
    values = [v for v in values if v is not None]
    if len(values) < MIN_RUNS_FOR_BAND:
        return None
    mean = statistics.fmean(values)
    # Population stdev: these runs are the whole history, not a sample drawn
    # from a larger set of runs we could have done.
    spread = statistics.pstdev(values)
    return {
        "runs": len(values),
        "mean": round(mean, 4),
        "spread": round(spread, 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "normal_low": round(clamp_for_kind(mean - OUTLIER_SIGMAS * spread, kind), 4),
        "normal_high": round(clamp_for_kind(mean + OUTLIER_SIGMAS * spread, kind), 4),
    }


def assess_latest(values):
    """
    Is the most recent value unusual compared with the ones before it?

    The band is built from the EARLIER runs only. Including the value being
    judged would drag the mean towards it and mask exactly the change we are
    looking for.

    Returns None when there is not enough history to say.
    """
    values = [v for v in values if v is not None]
    if len(values) < MIN_RUNS_FOR_BAND + 1:
        return None

    latest, earlier = values[-1], values[:-1]
    band = noise_band(earlier)
    if band is None:
        return None

    spread = band["spread"]
    change = latest - band["mean"]
    # A zero spread means every earlier run agreed exactly. Any movement at
    # all is then meaningful, but dividing by zero is not, so report the
    # direction without a multiple.
    sigmas = None if spread == 0 else round(change / spread, 2)

    if spread == 0:
        unusual = change != 0
    else:
        unusual = abs(change) > OUTLIER_SIGMAS * spread

    return {
        "latest": round(latest, 4),
        "baseline_mean": band["mean"],
        "baseline_spread": spread,
        "change": round(change, 4),
        "sigmas": sigmas,
        "unusual": unusual,
        "direction": "up" if change > 0 else ("down" if change < 0 else "flat"),
        "baseline_runs": band["runs"],
    }


def trends(runs, include_replay=False, include_partial=False):
    """The full per-metric trend report."""
    selected = comparable_runs(runs, include_replay, include_partial)
    fingerprints = {
        r.get("prompt_fingerprint") for r in selected if r.get("prompt_fingerprint")
    }

    # Built separately and attached at the end: assigning into
    # report["metrics"] directly makes the dict's value type infer as
    # `object`, which mypy then refuses to index.
    metrics = {}
    for metric, label, kind in TRENDED_METRICS:
        series = metric_values(selected, metric)
        values = [value for _run_id, value in series]
        metrics[metric] = {
            "label": label,
            "kind": kind,
            "series": series,
            "band": noise_band(values, kind),
            "assessment": assess_latest(values),
        }

    return {
        "runs_considered": len(selected),
        "run_ids": [r.get("run_id") for r in selected],
        "mixed_prompt_versions": len(fingerprints) > 1,
        "prompt_fingerprints": sorted(fingerprints),
        "metrics": metrics,
    }


def _question_key(row):
    return (
        row.get("assignment_key"),
        row.get("student_key"),
        row.get("question_number"),
    )


def question_stability(question_rows, run_ids=None):
    """
    Which questions does the grader mark inconsistently?

    THE CAPABILITY THAT DID NOT EXIST BEFORE. Every existing metric is an
    average over one run; none can say "this same answer got a different
    grade last time". The `twin` probe is close but different — it checks
    that two identical answers agree WITHIN a single run, not that one answer
    is graded the same way ACROSS runs.

    A question whose awarded level moves between runs, with the answer and
    the rubric unchanged, is a question the grader is unreliable on.
    """
    allowed = set(run_ids) if run_ids is not None else None
    grouped = defaultdict(list)
    for row in question_rows:
        if allowed is not None and row.get("run_id") not in allowed:
            continue
        grouped[_question_key(row)].append(row)

    findings = []
    for key, rows in grouped.items():
        rows = sorted(rows, key=lambda r: r.get("run_id") or "")
        levels = [r.get("awarded_level") for r in rows]
        distinct = {level for level in levels if level is not None}
        if len(rows) < 2:
            continue
        findings.append(
            {
                "assignment_key": key[0],
                "student_key": key[1],
                "question_number": key[2],
                "runs": len(rows),
                "distinct_levels": len(distinct),
                "levels": levels,
                "scores": [r.get("awarded_points") for r in rows],
                "expected_level": rows[-1].get("expected_level"),
                "question_type": rows[-1].get("question_type"),
                "unstable": len(distinct) > 1,
            }
        )

    # Most-unstable first, then most-observed, so the worst offenders lead.
    return sorted(
        findings,
        key=lambda f: (-f["distinct_levels"], -f["runs"], str(f["assignment_key"])),
    )


def question_history(question_rows, assignment_key, student_key, question_number):
    """Every recorded grade for one question, oldest first."""
    rows = [
        row
        for row in question_rows
        if row.get("assignment_key") == assignment_key
        and row.get("student_key") == student_key
        and str(row.get("question_number")) == str(question_number)
    ]
    return sorted(rows, key=lambda r: r.get("run_id") or "")
