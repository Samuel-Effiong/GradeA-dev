"""
Selective second-opinion grading: pure trigger/selection/comparison logic.

One AI's grade is one opinion. This module decides WHICH questions
deserve a second, independent opinion (a different model, graded blind —
see AIProcessor._maybe_run_second_opinion), and compares the two graders'
results afterwards. Policy, per the design decisions:

- Triggered only, never a blanket second pass: the run's own low
  confidence, the grader's own flag_for_review markers (emitted today
  and previously discarded), high-point questions, and a small random QA
  sample of everything else.
- Agreement finalizes silently. Disagreement between two independent
  graders is the strongest error signal available — it escalates to the
  teacher (needs_review), never to a third AI.
- Grader A's score always stands in the stored grade. A second opinion
  can only flag; it never changes a number.

Pure functions, no Django imports; the rng is injected so selection is
deterministic under test.
"""

import math
import random as _random_module

# Reason labels (stable strings — persisted into review_reasons and read
# by the future eval loop; do not rename casually).
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_HIGH_STAKES = "high_stakes"
REASON_BORDERLINE_LEVEL = "borderline_level"
REASON_QA_SAMPLE = "qa_sample"
REASON_FLAGGED_PREFIX = "flagged:"


def _coerce_number(value, default=0.0):
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(number) or math.isinf(number):
        return default
    return number


def _is_borderline(evaluation):
    """
    True only for a literal "borderline" level_decision.

    Anything else — missing key, null, "Borderline " with stray case or
    spacing already normalized upstream, an unexpected value — reads as
    "clear". The asymmetry is deliberate and points at the safe side:
    an unrecognized value must never manufacture a billed second grading
    call, whereas failing to escalate one genuinely close question costs
    nothing beyond the status quo before this trigger existed.
    """
    return str(evaluation.get("level_decision", "")).strip().casefold() == "borderline"


def _eligible_evaluations(result, key_fn):
    """
    LLM-graded evaluations only, keyed by normalized question_number.

    Deterministic evaluations are exact — second-guessing them buys
    nothing. not_attempted evaluations have no award to dispute (and the
    evidence rules already forbid awarding points to a blank), so a
    second read of them is wasted spend. Cache-served evaluations
    (ai_processor/grading_cache.py) are excluded the same way: they were
    already fully graded (and, if triggered, already survived second
    opinion) on a previous submission before ever being cached — and the
    caller's `questions`/`llm_questions` for THIS submission no longer
    carries their rubric content at all (they were partitioned out before
    grading even started), so a fresh comparison here would have nothing
    correct to compare against.
    """
    eligible = {}
    for evaluation in result.get("question_evaluations", []):
        if not isinstance(evaluation, dict):
            continue
        if evaluation.get("graded_by") == "deterministic":
            continue
        if evaluation.get("from_cache"):
            continue
        if evaluation.get("level_achieved") == "not_attempted":
            continue
        eligible[key_fn(evaluation.get("question_number"))] = evaluation
    return eligible


def select_second_opinion_targets(
    result,
    questions,
    *,
    key_fn,
    min_confidence,
    high_points_threshold,
    sample_rate,
    borderline_enabled=True,
    rng=None,
):
    """
    Decide which questions get a second opinion.

    Returns {normalized_question_number: [reason, ...]} — empty when
    nothing triggers. A question can carry several reasons; the reasons
    are persisted so threshold tuning has data to work from.

    Triggers:
    - run grading_confidence < min_confidence (strict <): the grader
      itself wasn't sure, so EVERY eligible question is re-read.
    - a non-null flag_for_review on an evaluation: that question only.
    - level_decision == "borderline": the grader says THIS answer sat
      between two rubric levels and it had to choose. See below.
    - question points >= high_points_threshold: the expensive-to-get-
      wrong questions.
    - one rng draw per submission < sample_rate: full re-read as QA
      sampling — this is what keeps the "easy" cases measured.

    On the borderline trigger. The submission-level grading_confidence
    turned out to be useless for routing — a live benchmark run had 120
    of 124 questions at confidence >= 80, so the low_confidence trigger
    effectively never fires and there is no spread to threshold on. That
    left high_points as the de-facto only deterministic trigger, which
    selects for *expensive* questions rather than *doubtful* ones — the
    two are unrelated, and the second-opinion budget was being spent on
    the wrong questions.

    level_decision asks the narrow, per-question version of the same
    question, and a between-levels call is exactly where an independent
    reader earns its cost: on a discrete ladder, one rung is the
    difference between two adjacent grades. It is self-reported and so
    could be gamed by a lazy grader answering "clear" to everything —
    which is why the benchmark scores it against ground truth rather
    than trusting it (see scoring.py's level_decision calibration block).
    """
    if rng is None:
        rng = _random_module
    eligible = _eligible_evaluations(result, key_fn)
    if not eligible:
        return {}

    reasons_by_key = {}

    def add(key, reason):
        reasons_by_key.setdefault(key, []).append(reason)

    confidence = _coerce_number(result.get("grading_confidence"), default=100.0)
    if confidence < min_confidence:
        for key in eligible:
            add(key, REASON_LOW_CONFIDENCE)

    points_by_key = {
        key_fn(q.get("question_number")): _coerce_number(q.get("points"))
        for q in questions
        if isinstance(q, dict)
    }

    for key, evaluation in eligible.items():
        flag = evaluation.get("flag_for_review")
        if isinstance(flag, dict):
            flag_type = flag.get("flag_type") or "UNSPECIFIED"
            add(key, f"{REASON_FLAGGED_PREFIX}{flag_type}")
        if borderline_enabled and _is_borderline(evaluation):
            add(key, REASON_BORDERLINE_LEVEL)
        if points_by_key.get(key, 0) >= high_points_threshold:
            add(key, REASON_HIGH_STAKES)

    # One draw per submission (not per question): a sampled submission
    # gets a FULL second read, which is what makes the sample usable as a
    # measurement of overall grader quality.
    if sample_rate > 0 and rng.random() < sample_rate:
        for key in eligible:
            add(key, REASON_QA_SAMPLE)

    return reasons_by_key


def pick_second_model(first_model, candidates):
    """
    The first candidate that differs from the model that ACTUALLY graded
    (grader A's result records its routed model, fallbacks included), or
    None. A "second opinion" from the same model shares every blind spot
    of the first — independence is the whole point, so same-model is a
    skip, never a fallback.
    """
    if not candidates:
        return None
    for candidate in candidates:
        if candidate and candidate != first_model:
            return candidate
    return None


def _side(evaluation):
    """The fields the teacher needs to judge one grader's position."""
    return {
        "score_awarded": evaluation.get("score_awarded"),
        "level_achieved": evaluation.get("level_achieved"),
        # Whether each grader thought the call was close is exactly the
        # context a teacher needs when two graders disagree: two "clear"
        # verdicts that differ is a genuine conflict, whereas one side
        # saying "borderline" explains the split on its own.
        "level_decision": evaluation.get("level_decision", "clear"),
        "evaluation_rationale": evaluation.get("evaluation_rationale", ""),
        "evidence_quotes": evaluation.get("evidence_quotes", []),
    }


# Severity tier labels (stable strings — persisted into review_reasons
# and segmented on by the grading_eval command).
TIER_CRITICAL = "critical"
TIER_MODERATE = "moderate"
TIER_BORDERLINE = "borderline"


def _severity(
    score_a,
    score_b,
    points,
    rubric_level_points,
    *,
    critical_fraction,
    moderate_fraction,
):
    """
    Classify how bad one disagreement is. Severity never decides WHETHER
    something is a disagreement (equality already did); it grades the
    disagreement for teacher triage:

    - gap_fraction: |a-b| as a share of the question's points. An
      OBJECTIVE full-vs-zero split is 1.0 — correctly critical, since the
      graders disagree on correct/incorrect outright.
    - levels_apart: how many rubric rungs separate the two choices, when
      both scores are recognizable rubric level values. Two graders two
      rungs apart isn't a borderline judgment call — someone is wrong.
    - tier: critical (levels_apart >= 2 OR gap_fraction >= critical),
      else moderate (gap_fraction >= moderate, or unknowable points —
      never downgrade what we can't measure), else borderline.
    """
    gap_points = abs(score_a - score_b)

    gap_fraction = None
    if points and points > 0:
        gap_fraction = round(gap_points / points, 4)

    levels_apart = None
    if rubric_level_points:
        ordered = sorted(set(rubric_level_points))
        if score_a in ordered and score_b in ordered:
            levels_apart = abs(ordered.index(score_a) - ordered.index(score_b))

    if (levels_apart is not None and levels_apart >= 2) or (
        gap_fraction is not None and gap_fraction >= critical_fraction
    ):
        tier = TIER_CRITICAL
    elif gap_fraction is None or gap_fraction >= moderate_fraction:
        tier = TIER_MODERATE
    else:
        tier = TIER_BORDERLINE

    return {
        "gap_points": gap_points,
        "gap_fraction": gap_fraction,
        "levels_apart": levels_apart,
        "tier": tier,
    }


def compare_evaluations(
    evals_a,
    evals_b,
    *,
    key_fn,
    questions=None,
    critical_fraction=0.5,
    moderate_fraction=0.25,
):
    """
    Compare two graders' (already clamped) evaluations per question.

    Rubric levels are discrete point values and both sides pass through
    _finalize_grading_result before comparison, so equality of
    score_awarded IS agreement on the level — deliberately no tolerance:
    on a discrete ladder, any tolerance wide enough to ever fire would
    swallow one-level disagreements, the most informative kind. Severity
    (below) grades disagreements AFTER detection instead of hiding them.

    Only keys present on both sides are compared (grader B's completeness
    over its selected set is enforced upstream by _grade_question_batch).

    When `questions` is provided, each disagreement carries a `severity`
    block ({gap_points, gap_fraction, levels_apart, tier}) computed from
    that question's points and rubric ladder. Without `questions` the
    output shape is unchanged (backward compatible).

    Returns {"agreements": [question_number...],
             "disagreements": [{question_number, a, b[, severity]}, ...]}.
    """
    b_by_key = {
        key_fn(ev.get("question_number")): ev for ev in evals_b if isinstance(ev, dict)
    }

    points_by_key = {}
    rubric_by_key = {}
    if questions:
        for question in questions:
            if not isinstance(question, dict):
                continue
            key = key_fn(question.get("question_number"))
            points_by_key[key] = _coerce_number(question.get("points"))
            rubric = question.get("rubric")
            if isinstance(rubric, list):
                rubric_by_key[key] = [
                    _coerce_number(level.get("points"))
                    for level in rubric
                    if isinstance(level, dict)
                ]

    agreements = []
    disagreements = []
    for eval_a in evals_a:
        if not isinstance(eval_a, dict):
            continue
        key = key_fn(eval_a.get("question_number"))
        eval_b = b_by_key.get(key)
        if eval_b is None:
            continue
        score_a = _coerce_number(eval_a.get("score_awarded"))
        score_b = _coerce_number(eval_b.get("score_awarded"))
        if score_a == score_b:
            agreements.append(eval_a.get("question_number"))
        else:
            disagreement = {
                "question_number": eval_a.get("question_number"),
                "a": _side(eval_a),
                "b": _side(eval_b),
            }
            if questions:
                disagreement["severity"] = _severity(
                    score_a,
                    score_b,
                    points_by_key.get(key),
                    rubric_by_key.get(key),
                    critical_fraction=critical_fraction,
                    moderate_fraction=moderate_fraction,
                )
            disagreements.append(disagreement)
    return {"agreements": agreements, "disagreements": disagreements}
