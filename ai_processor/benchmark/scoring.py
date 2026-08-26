"""
Metrics over a completed benchmark run.

Pure functions on plain dicts — no Django, no model calls — so every
metric is unit-testable against hand-built inputs.

The central idea is the RUBRIC LEVEL, not the raw score. "Off by 5
points" means nothing on its own: 5 points is a whole grade band on a
10-point question and a rounding error on a 25-point essay. Every
accuracy number here is therefore computed in level-index space, where
0 = the top level, 1 = one level down, and so on.

Pass/fail rule (see dataset.py for the rationale):
    exact=True   -> the score must match exactly
    exact=False  -> the awarded level must be the expected level or an
                    adjacent one
"""

import math
import statistics
from collections import Counter, defaultdict

from ai_processor.benchmark.dataset import OBJECTIVE, allowed_scores


def level_ladder(question):
    """Reachable scores, highest first. Index 0 is the top grade."""
    return sorted(allowed_scores(question), reverse=True)


def level_index(question, score):
    """
    Where `score` sits on the ladder. None when the score is not a
    reachable value at all — which should be impossible after
    _finalize_grading_result's snapping, so it is reported rather than
    silently coerced.
    """
    ladder = level_ladder(question)
    try:
        return ladder.index(float(score))
    except (ValueError, TypeError):
        return None


def nearest_level_index(question, score):
    """
    Index of the closest reachable level. Used only for diagnostics on a
    score that is somehow off-ladder, so a snapping regression shows up
    as a number rather than as a crash.
    """
    ladder = level_ladder(question)
    if not ladder:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    return min(range(len(ladder)), key=lambda i: abs(ladder[i] - value))


def grade_one(question, spec, awarded):
    """
    Judge a single graded question against its expectation.

    Returns a dict; `verdict` is one of:
        exact      - awarded == expected
        adjacent   - one rubric level away, and the question allows it
        off        - outside the accepted band (a real failure)
        unreachable- awarded is not a value the grader should be able to
                     produce (indicates a snapping regression)
    """
    expected = float(spec.expected_points)
    actual = None if awarded is None else float(awarded)

    expected_idx = level_index(question, expected)
    actual_idx = level_index(question, actual) if actual is not None else None

    if actual is None:
        verdict = "off"
        distance = None
    elif actual == expected:
        verdict = "exact"
        distance = 0
    elif actual_idx is None:
        verdict = "unreachable"
        distance = None
    else:
        distance = actual_idx - expected_idx
        if spec.exact:
            verdict = "off"
        else:
            verdict = "adjacent" if abs(distance) == 1 else "off"

    return {
        "question_number": question["question_number"],
        "question_type": question["question_type"],
        "points": question["points"],
        "expected": expected,
        "awarded": actual,
        "expected_level": expected_idx,
        "awarded_level": actual_idx,
        # Positive = the grader was LENIENT is counter-intuitive here:
        # level 0 is the TOP grade, so a larger index is a lower grade.
        # Negate so the sign reads naturally.
        "level_error": None if distance is None else -distance,
        "exact_required": spec.exact,
        "verdict": verdict,
        "note": spec.note,
    }


def iter_graded_questions(result):
    """
    Yield (spec, question, evaluation, awarded) for one submission.

    This is the "match a question to the grade it received" join, extracted
    so that score_run() and the run-history recorder (benchmark/history.py)
    share ONE implementation. They were previously at risk of drifting
    apart, which would have made the stored history quietly disagree with
    the report printed from the same run.

    `evaluation` is None when the model returned nothing for that question,
    and `awarded` is then None too — callers must handle that rather than
    assume every question was graded.
    """
    assignment = result["assignment"]
    evaluations = {}
    for evaluation in (result.get("grading") or {}).get("question_evaluations") or []:
        if isinstance(evaluation, dict):
            evaluations[str(evaluation.get("question_number"))] = evaluation

    for spec in result["specs"]:
        question = assignment.question(spec.question_number)
        evaluation = evaluations.get(str(spec.question_number))
        awarded = evaluation.get("score_awarded") if evaluation else None
        yield spec, question, evaluation, awarded


def _second_opinion_index(grading):
    """
    Map question number (as str) -> what the second grader did with it.

    Flattens the `second_opinion` block, whose three parts are shaped
    differently: `selected` is {question: [reasons]}, `agreements` is a bare
    list of question numbers, and `disagreements` is a list of dicts
    carrying both graders' scores plus a severity tier.
    """
    block = grading.get("second_opinion") or {}
    index = {}

    for number, reasons in (block.get("selected") or {}).items():
        index[str(number)] = {
            "second_opinion_selected": True,
            "second_opinion_reasons": list(reasons or []),
            "second_opinion_disagreed": None,
            "second_opinion_tier": None,
            "second_opinion_b_score": None,
        }

    for number in block.get("agreements") or []:
        entry = index.setdefault(str(number), {"second_opinion_selected": True})
        entry["second_opinion_disagreed"] = False

    for disagreement in block.get("disagreements") or []:
        if not isinstance(disagreement, dict):
            continue
        entry = index.setdefault(
            str(disagreement.get("question_number")),
            {"second_opinion_selected": True},
        )
        entry["second_opinion_disagreed"] = True
        entry["second_opinion_tier"] = (disagreement.get("severity") or {}).get("tier")
        entry["second_opinion_b_score"] = (disagreement.get("b") or {}).get(
            "score_awarded"
        )

    return index


def iter_question_outcomes(run):
    """
    Yield one flat, JSON-safe row per graded question — the persisted form
    of what score_run() computes and then throws away.

    This is Tier 2 of the run history: enough per-question detail to ask
    "has this question's grade changed between runs?", which no aggregate
    metric can answer.

    Shares iter_graded_questions() with score_run(), and skips errored
    submissions exactly as score_run() does, so the row count always equals
    report["overall"]["questions"] for the same run. A test pins that.
    """
    for result in run["results"]:
        if result.get("error"):
            continue

        grading = result["grading"] or {}
        confidence = grading.get("grading_confidence")
        second_opinions = _second_opinion_index(grading)

        for spec, question, evaluation, awarded in iter_graded_questions(result):
            judged = grade_one(question, spec, awarded)
            evaluation = evaluation or {}
            quotes = evaluation.get("evidence_quotes")
            flag = evaluation.get("flag_for_review")

            row = {
                "assignment_key": result["assignment_key"],
                "student_key": result["student_key"],
                "subject": result["assignment"].subject,
                "question_number": question["question_number"],
                "question_type": question["question_type"],
                "points": question["points"],
                "expected_points": judged["expected"],
                "awarded_points": judged["awarded"],
                "expected_level": judged["expected_level"],
                "awarded_level": judged["awarded_level"],
                "level_error": judged["level_error"],
                "verdict": judged["verdict"],
                "exact_required": judged["exact_required"],
                "level_achieved": evaluation.get("level_achieved"),
                "level_decision": evaluation.get("level_decision"),
                "graded_by": evaluation.get("graded_by"),
                "snapped_from": evaluation.get("snapped_from"),
                "evidence_verified": evaluation.get("evidence_verified"),
                "evidence_quote_count": len(quotes) if quotes is not None else None,
                "unverified_evidence_count": evaluation.get(
                    "unverified_evidence_count"
                ),
                "flag_type": (
                    (flag or {}).get("flag_type") if isinstance(flag, dict) else None
                ),
                "grading_confidence": confidence,
                "second_opinion_selected": False,
                "second_opinion_reasons": [],
                "second_opinion_disagreed": None,
                "second_opinion_tier": None,
                "second_opinion_b_score": None,
            }
            row.update(second_opinions.get(str(question["question_number"]), {}))
            yield row


def _rate(numerator, denominator):
    return None if not denominator else round(numerator / denominator, 4)


def _mean(values):
    values = [v for v in values if v is not None]
    return None if not values else round(statistics.fmean(values), 4)


def _spearman(a, b):
    """
    Spearman rank correlation. Hand-rolled because scipy is not a
    dependency of this project and pulling it in for one statistic would
    be disproportionate. Handles ties via average ranks.
    """
    if len(a) != len(b) or len(a) < 2:
        return None

    def ranks(values):
        order = sorted(range(len(values)), key=lambda i: values[i])
        out = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            average = (i + j) / 2 + 1
            for k in range(i, j + 1):
                out[order[k]] = average
            i = j + 1
        return out

    ra, rb = ranks(a), ranks(b)
    mean_a, mean_b = statistics.fmean(ra), statistics.fmean(rb)
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb, strict=True))
    var_a = math.sqrt(sum((x - mean_a) ** 2 for x in ra))
    var_b = math.sqrt(sum((y - mean_b) ** 2 for y in rb))
    if not var_a or not var_b:
        return None
    return round(cov / (var_a * var_b), 4)


def score_run(run):
    """
    Turn a completed run into the full metric report.

    `run` is the structure produced by runner.execute_benchmark():
        {
          "results": [
             {"assignment_key", "student_key", "assignment", "specs",
              "grading" (the raw pipeline result), "elapsed_seconds",
              "tokens", "error"},
             ...
          ],
          "mode": "live"|"replay", ...
        }
    """
    per_question = []
    failures = []
    errors = []
    by_type = defaultdict(list)
    by_subject = defaultdict(list)
    student_totals = {}
    expected_totals = {}
    snap_counts = 0
    evidence_verified = 0
    evidence_total = 0
    deterministic_claimed = 0
    deterministic_correct = 0
    confidence_buckets = defaultdict(lambda: {"ok": 0, "total": 0})
    decision_buckets = defaultdict(lambda: {"exact": 0, "ok": 0, "total": 0})
    second_opinion = {"ran": 0, "skipped": 0, "error": 0, "not_run": 0}
    disagreement_tiers = Counter()
    disagreements = 0
    compared = 0
    tokens_total = 0
    elapsed_total = 0.0

    for result in run["results"]:
        akey = result["assignment_key"]
        skey = result["student_key"]
        assignment = result["assignment"]

        if result.get("error"):
            errors.append(
                {
                    "assignment_key": akey,
                    "student_key": skey,
                    "error": result["error"],
                }
            )
            continue

        grading = result["grading"] or {}
        tokens_total += result.get("tokens") or 0
        elapsed_total += result.get("elapsed_seconds") or 0.0

        verification = (
            (grading.get("grading_summary") or {}).get("score_calculation_verification")
            or grading.get("score_calculation_verification")
            or {}
        )
        snap_counts += verification.get("snapped_to_rubric_level_count") or 0

        confidence = grading.get("grading_confidence")

        awarded_total = 0.0
        for spec, question, evaluation, awarded in iter_graded_questions(result):
            judged = grade_one(question, spec, awarded)
            judged["assignment_key"] = akey
            judged["student_key"] = skey
            judged["subject"] = assignment.subject
            per_question.append(judged)

            if awarded is not None:
                awarded_total += float(awarded)
            if judged["verdict"] in ("off", "unreachable"):
                failures.append(judged)

            by_type[question["question_type"]].append(judged)
            by_subject[assignment.subject].append(judged)

            if evaluation:
                # level_decision calibration. The whole justification for
                # routing second opinions on "borderline" is that the
                # grader knows when a call was close — which is a claim
                # about the model, not something to take on faith. This
                # bucket answers it directly: of the questions the grader
                # called borderline, how many did it actually get wrong,
                # versus the ones it called clear? A trustworthy signal
                # has a visibly worse exact-match rate on "borderline".
                # If the two rates are the same, the field is noise and
                # the trigger is spending money for nothing — the same
                # verdict this benchmark already reached about
                # grading_confidence (see FINDINGS Finding 4).
                # Deterministic grades are excluded: tier 0 hardcodes
                # "clear" by construction, so counting them would dilute
                # the clear bucket with guaranteed-correct rows.
                if evaluation.get("graded_by") != "deterministic":
                    decision = evaluation.get("level_decision") or "clear"
                    decision_buckets[decision]["total"] += 1
                    if judged["verdict"] == "exact":
                        decision_buckets[decision]["exact"] += 1
                    if judged["verdict"] in ("exact", "adjacent"):
                        decision_buckets[decision]["ok"] += 1

                graded_by = evaluation.get("graded_by")
                if graded_by == "deterministic":
                    deterministic_claimed += 1
                    if judged["verdict"] == "exact":
                        deterministic_correct += 1
                if question["question_type"] != OBJECTIVE:
                    quotes = evaluation.get("evidence_quotes")
                    if quotes is not None:
                        evidence_total += 1
                        if evaluation.get("evidence_verified", True):
                            evidence_verified += 1

            if confidence is not None:
                confidence_band = (
                    "high (80+)"
                    if confidence >= 80
                    else "medium (60-79)" if confidence >= 60 else "low (<60)"
                )
                confidence_buckets[confidence_band]["total"] += 1
                if judged["verdict"] in ("exact", "adjacent"):
                    confidence_buckets[confidence_band]["ok"] += 1

        student_totals[(akey, skey)] = awarded_total
        expected_totals[(akey, skey)] = sum(
            float(s.expected_points) for s in result["specs"]
        )

        block = grading.get("second_opinion")
        if not block:
            second_opinion["not_run"] += 1
        elif block.get("error"):
            second_opinion["error"] += 1
        elif block.get("skipped") or block.get("skipped_reason"):
            second_opinion["skipped"] += 1
        else:
            second_opinion["ran"] += 1
            found = block.get("disagreements") or []
            compared += len(block.get("agreements") or []) + len(found)
            disagreements += len(found)
            for entry in found:
                disagreement_tiers[
                    (entry.get("severity") or {}).get("tier") or "unknown"
                ] += 1

    def band(entries):
        total = len(entries)
        exact = sum(1 for e in entries if e["verdict"] == "exact")
        within = sum(1 for e in entries if e["verdict"] in ("exact", "adjacent"))
        return {
            "questions": total,
            "exact": exact,
            "exact_rate": _rate(exact, total),
            "within_one_level": within,
            "within_one_level_rate": _rate(within, total),
            "mean_level_error": _mean([e["level_error"] for e in entries]),
        }

    # Ranking: does the grader order the students the way ground truth
    # does? A grader can be systematically miscalibrated yet still rank
    # correctly, and those are different problems with different fixes.
    ranking = {}
    for akey in {k[0] for k in student_totals}:
        students = sorted(k[1] for k in student_totals if k[0] == akey)
        actual = [student_totals[(akey, s)] for s in students]
        ideal = [expected_totals[(akey, s)] for s in students]
        ranking[akey] = {
            "students": students,
            "expected_totals": ideal,
            "awarded_totals": actual,
            "spearman": _spearman(ideal, actual),
        }

    return {
        "overall": band(per_question),
        "by_question_type": {k: band(v) for k, v in sorted(by_type.items())},
        "by_subject": {k: band(v) for k, v in sorted(by_subject.items())},
        "failures": failures,
        "errors": errors,
        "ranking": ranking,
        "deterministic": {
            "claimed": deterministic_claimed,
            "correct": deterministic_correct,
            # Must be 1.0. Tier 0 only claims unambiguous matches, so any
            # miss here is a real defect in objective_grading.py.
            "accuracy": _rate(deterministic_correct, deterministic_claimed),
        },
        "evidence": {
            "checked": evidence_total,
            "verified": evidence_verified,
            "verified_rate": _rate(evidence_verified, evidence_total),
        },
        "rubric_snapping": {
            "snapped_scores": snap_counts,
            "questions": len(per_question),
            "rate": _rate(snap_counts, len(per_question)),
        },
        "second_opinion": {
            "coverage": dict(second_opinion),
            "questions_compared": compared,
            "questions_disagreed": disagreements,
            "disagreement_rate": _rate(disagreements, compared),
            "tiers": dict(disagreement_tiers),
        },
        "confidence_calibration": {
            band_name: {
                "questions": counts["total"],
                "graded_acceptably": counts["ok"],
                "rate": _rate(counts["ok"], counts["total"]),
            }
            for band_name, counts in sorted(confidence_buckets.items())
        },
        # Does self-reported "borderline" actually predict being wrong?
        # Compare exact_rate across the two buckets: "borderline" should
        # be materially LOWER than "clear" for the second-opinion trigger
        # that reads this field to be worth its cost. LLM-graded
        # questions only.
        "level_decision_calibration": {
            decision: {
                "questions": counts["total"],
                "exact": counts["exact"],
                "exact_rate": _rate(counts["exact"], counts["total"]),
                "graded_acceptably": counts["ok"],
                "within_one_level_rate": _rate(counts["ok"], counts["total"]),
            }
            for decision, counts in sorted(decision_buckets.items())
        },
        "cost": {
            "total_tokens": tokens_total,
            "total_seconds": round(elapsed_total, 2),
            "submissions": len(run["results"]),
        },
    }


def score_reproducibility(runs):
    """
    How often the SAME question gets a different verdict across repeated
    runs of the same configuration.

    WHY THIS EXISTS

    Every other metric in this module measures SEVERITY — how wrong a
    grade is (exact vs adjacent, level error, within-one-level). None of
    them measures whether the grader is repeatable, and the two are
    genuinely independent: a change can leave severity untouched while
    making the grader markedly less stable.

    That is not hypothetical. It is exactly what the Run 8 prompt edit did
    (see benchmark/FINDINGS.md). Adding `answer_status` guidance left
    within_one_level_rate at ~1.0, mean_level_error flat, and the
    deterministic tier untouched — every severity check passed — while
    making 1.36x more questions change verdict between identical runs
    (19 of 168, against 14 for the previous prompt). Because this suite
    had no reproducibility metric, that regression was measured, written
    up, and ACCEPTED as benign before repeated runs caught it.

    It also matters more than a small accuracy dip, on the grading
    prompt's own terms: "a score that changes between runs is a wrong
    score." A student's answer drawing a different grade depending on
    when it happened to be marked is indefensible in a way that a
    defensible-but-borderline level choice is not.

    Args:
        runs: an iterable of at least two scored runs of the SAME
            configuration. Each is the dict returned by execute_benchmark;
            per-question verdicts come from iter_question_outcomes.

    Returns None when fewer than two runs are supplied — one run cannot
    exhibit variation, and returning a flattering 0.0 would read as
    "perfectly reproducible" rather than "not measured".
    """
    runs = list(runs)
    if len(runs) < 2:
        return None

    # question -> list of verdicts, one per run that graded it
    verdicts = defaultdict(list)
    for run in runs:
        for row in iter_question_outcomes(run):
            key = (row["assignment_key"], row["student_key"], row["question_number"])
            verdicts[key].append(row["verdict"])

    # Only questions present in EVERY run are comparable. A question
    # missing from one run (an errored submission) would otherwise look
    # stable purely because it was observed fewer times.
    comparable = {k: v for k, v in verdicts.items() if len(v) == len(runs)}
    if not comparable:
        return None

    unstable = [k for k, v in comparable.items() if len(set(v)) > 1]
    # Exact-match is the verdict the headline metric keys on, so an
    # exact <-> non-exact flip is tracked separately from any verdict
    # change: a question wobbling between two non-exact verdicts is less
    # consequential than one that stops being right.
    exactness_flips = [
        k
        for k, v in comparable.items()
        if len({verdict == "exact" for verdict in v}) > 1
    ]

    return {
        "runs": len(runs),
        "questions_compared": len(comparable),
        "questions_skipped": len(verdicts) - len(comparable),
        "unstable": len(unstable),
        "unstable_rate": _rate(len(unstable), len(comparable)),
        "exactness_flips": len(exactness_flips),
        "exactness_flip_rate": _rate(len(exactness_flips), len(comparable)),
        "unstable_questions": sorted(f"{a}/{s} Q{q}" for a, s, q in exactness_flips),
    }


def check_consistency(run, probes):
    """
    Cross-student consistency: byte-identical answers must score
    identically.

    Separate from score_run because this is a hard invariant rather than
    a quality metric — two identical answers receiving different scores
    is indefensible to a student regardless of which score is "right".
    """
    scores = defaultdict(dict)
    for result in run["results"]:
        if result.get("error"):
            continue
        for evaluation in (result["grading"] or {}).get("question_evaluations") or []:
            if not isinstance(evaluation, dict):
                continue
            key = (result["assignment_key"], evaluation.get("question_number"))
            scores[key][result["student_key"]] = evaluation.get("score_awarded")

    report = []
    for assignment_key, question_number in probes:
        awarded = scores.get((assignment_key, question_number), {})
        left = awarded.get("strong")
        right = awarded.get("twin")
        if left is None or right is None:
            # Both halves of the pair have to be in the run for the probe
            # to mean anything. A filtered run (--student excellent) must
            # report "not applicable", not a false inconsistency alarm.
            status = "skipped"
            consistent = None
        elif left == right:
            status = "consistent"
            consistent = True
        else:
            status = "INCONSISTENT"
            consistent = False
        report.append(
            {
                "assignment_key": assignment_key,
                "question_number": question_number,
                "strong": left,
                "twin": right,
                "status": status,
                "consistent": consistent,
            }
        )
    return report
