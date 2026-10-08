"""
Metrics for the assignment-extraction benchmark.

WHAT IS SCORED, AND WHAT DELIBERATELY IS NOT

Extraction legitimately REWRITES prose: `question_text` is re-emitted as
HTML, rubric descriptions are reformatted, whitespace moves. Scoring those
byte-for-byte would produce a benchmark that fails constantly for reasons
nobody would ever act on, and a benchmark people learn to ignore is worse
than none.

So the metrics here are structural. They measure the things a teacher
would notice and could not fix (the frontend editor is free-form, so
there is no per-question repair path - see
ai_processor/benchmark/extraction_dataset.py):

    question_count      did we get every question back?
    question_type       is it still an essay / an MCQ?
    points              is it still worth what it was worth?
    option_count        did a six-option MCQ come back with six?
    option_text         are the choices still the same choices?
    rubric_levels       did a six-level rubric come back with six?
    rubric_level_names  are they still the teacher's OWN level names?
    rubric_points       is the points ladder unchanged?
    blooms_level        did the cognitive-demand signal survive?

`option_text` and rubric descriptions are compared after normalisation
(tags stripped, entities unescaped, whitespace collapsed, casefolded),
reusing the same canonical form objective_grading.normalize_text applies
when matching a student's answer against an option - so the benchmark
holds extraction to exactly the standard the grader will hold it to
later, not a stricter invented one.
"""

from ai_processor.benchmark.extraction_dataset import EXTRACTION_CASES_BY_KEY
from ai_processor.objective_grading import normalize_text

#: Metric keys, in report order.
METRICS = (
    "question_count",
    "question_type",
    "points",
    "option_count",
    "option_text",
    "rubric_levels",
    "rubric_level_names",
    "rubric_points",
    "blooms_level",
)


def _normalized_options(options):
    return [normalize_text(option) for option in options or []]


def _rubric_level_names(rubric):
    return [
        normalize_text(level.get("level"))
        for level in rubric or []
        if isinstance(level, dict)
    ]


def _rubric_points(rubric) -> list:
    values: list = []
    for level in rubric or []:
        if not isinstance(level, dict):
            continue
        raw = level.get("points")
        if raw is None:
            values.append(None)
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            values.append(None)
    return values


def _coerce_points(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _question_key(value):
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def score_question(expected, actual):
    """
    Compare one expected question against what extraction returned.

    Returns {metric: True/False/None}. None means "not applicable to this
    question" (option metrics on an essay, rubric metrics on an MCQ) and
    is excluded from rates rather than counted as a pass — otherwise an
    assignment of MCQs would post a perfect rubric score having never
    been asked to preserve a rubric.
    """
    result = {}

    result["question_type"] = expected.get("question_type") == actual.get(
        "question_type"
    )
    result["points"] = _coerce_points(expected.get("points")) == _coerce_points(
        actual.get("points")
    )

    expected_blooms = (expected.get("blooms_level") or "").strip().lower()
    actual_blooms = (actual.get("blooms_level") or "").strip().lower()
    result["blooms_level"] = (
        expected_blooms == actual_blooms if expected_blooms else None
    )

    expected_options = expected.get("options") or []
    actual_options = actual.get("options") or []
    if expected_options:
        result["option_count"] = len(expected_options) == len(actual_options)
        # Set comparison, not sequence: the renderer derives the A/B/C
        # letters from position, so a reordering is visible to a student
        # but is not a LOSS of content. Order is caught by option_count
        # plus the letter-label check in the dataset validator.
        result["option_text"] = set(_normalized_options(expected_options)) == set(
            _normalized_options(actual_options)
        )
    else:
        # An open-ended question must come back with NO options. Extraction
        # inventing them is a real failure, so this is False, not None.
        result["option_count"] = not actual_options
        result["option_text"] = None

    expected_rubric = expected.get("rubric") or []
    actual_rubric = actual.get("rubric") or []
    if expected_rubric:
        result["rubric_levels"] = len(expected_rubric) == len(actual_rubric)
        result["rubric_level_names"] = _rubric_level_names(
            expected_rubric
        ) == _rubric_level_names(actual_rubric)
        result["rubric_points"] = _rubric_points(expected_rubric) == _rubric_points(
            actual_rubric
        )
    elif expected.get("question_type") == "OBJECTIVE":
        # OBJECTIVE questions are scored full-or-zero and must carry an
        # empty rubric; one appearing is a contract violation.
        result["rubric_levels"] = not actual_rubric
        result["rubric_level_names"] = None
        result["rubric_points"] = None
    else:
        # Open-ended with no supplied rubric: generation is CORRECT here,
        # and the prompt's four-level framework is what it should produce.
        result["rubric_levels"] = len(actual_rubric) == 4
        result["rubric_level_names"] = None
        result["rubric_points"] = None

    return result


def score_case(case, extracted):
    """
    Score one extraction against its ExtractionCase.

    `extracted` is the raw AI payload (the dict with "questions").
    """
    actual_questions = (extracted or {}).get("questions") or []
    actual_by_key = {
        _question_key(q.get("question_number")): q
        for q in actual_questions
        if isinstance(q, dict)
    }

    per_question = []
    for expected in case.questions:
        key = _question_key(expected.get("question_number"))
        actual = actual_by_key.get(key)
        if actual is None:
            # A dropped question fails every applicable metric rather than
            # being skipped: silently excluding it would let an extraction
            # that returned ONE question out of six post a perfect score.
            per_question.append(
                {
                    "question_number": expected.get("question_number"),
                    "found": False,
                    "metrics": {
                        metric: False
                        for metric in METRICS
                        if metric != "question_count"
                    },
                }
            )
            continue
        per_question.append(
            {
                "question_number": expected.get("question_number"),
                "found": True,
                "metrics": score_question(expected, actual),
            }
        )

    counts = {metric: {"passed": 0, "total": 0} for metric in METRICS}

    expected_count = len(case.questions)
    counts["question_count"]["total"] = 1
    counts["question_count"]["passed"] = int(len(actual_questions) == expected_count)

    for entry in per_question:
        for metric, value in entry["metrics"].items():
            if value is None:
                continue
            counts[metric]["total"] += 1
            counts[metric]["passed"] += int(bool(value))

    # Strict fields are what GATE the run; everything else is reported.
    strict_failures = []
    for entry in per_question:
        for metric in case.strict_fields:
            if entry["metrics"].get(metric) is False:
                strict_failures.append(
                    f"{case.key} Q{entry['question_number']}: {metric}"
                )
    if counts["question_count"]["passed"] == 0:
        strict_failures.append(
            f"{case.key}: expected {expected_count} question(s), "
            f"got {len(actual_questions)}"
        )

    return {
        "case": case.key,
        "expected_questions": expected_count,
        "actual_questions": len(actual_questions),
        "per_question": per_question,
        "counts": counts,
        "strict_failures": strict_failures,
        "passed": not strict_failures,
    }


def _rate(passed, total):
    return None if not total else round(passed / total, 4)


def score_run(case_results):
    """Aggregate per-case results into the reported run summary."""
    totals = {metric: {"passed": 0, "total": 0} for metric in METRICS}
    for result in case_results:
        for metric, count in result["counts"].items():
            totals[metric]["passed"] += count["passed"]
            totals[metric]["total"] += count["total"]

    rates = {
        metric: _rate(count["passed"], count["total"])
        for metric, count in totals.items()
    }

    strict_failures = [
        failure for result in case_results for failure in result["strict_failures"]
    ]

    measured = [rate for rate in rates.values() if rate is not None]

    return {
        "cases": len(case_results),
        "cases_passed": sum(1 for r in case_results if r["passed"]),
        "counts": totals,
        "rates": rates,
        "overall": _rate(
            sum(c["passed"] for c in totals.values()),
            sum(c["total"] for c in totals.values()),
        ),
        "weakest_metric": (
            min(
                (m for m in METRICS if rates[m] is not None),
                key=lambda m: rates[m],
                default=None,
            )
            if measured
            else None
        ),
        "strict_failures": strict_failures,
        "passed": not strict_failures,
        "results": case_results,
    }


def case_for(key):
    return EXTRACTION_CASES_BY_KEY[key]
