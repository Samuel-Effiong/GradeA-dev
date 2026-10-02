"""Academic rigor scoring.
Love God with all your heart

Rigor is modelled as a composite of three independently-meaningful signals,
each on a 0-5 scale:

    demand     What level of thinking the assignment asks for, read from the
               per-question ``blooms_level`` that the AI extraction pipeline
               already produces (ai_processor/services.py's response schema
               requires it on every question). Points-weighted, so a 40-point
               "Create" essay counts for more than a 1-point "Remember" MCQ.
               This is the definitional core of rigor: cognitive demand.

    evidence   Whether the work actually stretched anyone, derived from
               achieved ``score_percentage``. A class averaging 95% was not
               stretched, whatever the questions claimed to be. This is the
               reality check on `demand`, which is otherwise self-reported by
               the question author.

    standards  Whether open-ended questions define what "good" looks like,
               measured by rubric depth. Demanding work without stated
               standards is not rigor, it is just harshness -- and this is the
               component a school admin can most directly act on.

`demand` and `standards` are properties of the assignment itself and never
change once the questions are set, so they are denormalized onto
Assignment.rigor_demand / .rigor_standards (see the pre_save hook in
assignments/signals.py) rather than re-parsed out of JSON on every dashboard
request. `evidence` moves every time something is graded, so it is always
aggregated live from StudentSubmission.score_percentage (an indexed column).

Everything in this module is a pure function over plain data: no model
imports, no database access, no Django settings. That keeps the scoring rules
testable in isolation and safe to call from migrations.
"""

BLOOMS_SCALE = {
    "remember": 0.0,
    "understand": 1.0,
    "apply": 2.0,
    "analyze": 3.0,
    "analyse": 3.0,  # tolerate the en-GB spelling; the enum emits en-US
    "evaluate": 4.0,
    "create": 5.0,
}

#: Question types whose quality is a judgement call, and which therefore need
#: a rubric for the grade to mean anything. OBJECTIVE questions are excluded:
#: a multiple-choice answer is right or wrong and a rubric adds nothing.
OPEN_ENDED_QUESTION_TYPES = frozenset({"ESSAY", "SHORT-ANSWER"})

#: A rubric with fewer than this many levels does not meaningfully discriminate
#: between performances (a single "full marks" row is not a rubric).
MIN_RUBRIC_LEVELS = 3

#: Below this share of Bloom's coverage the demand score is guesswork, so we
#: report nothing rather than a confident number derived from a minority of
#: the assignment. Expressed as a fraction of total question points.
MIN_BLOOMS_COVERAGE = 0.5

#: Relative contribution of each component to the blended score. These are
#: renormalized over whichever components are actually available, so an
#: all-objective assignment (no `standards`) is not penalised for it.
COMPONENT_WEIGHTS = {
    "demand": 0.6,
    "evidence": 0.25,
    "standards": 0.15,
}

RIGOR_SCALE_MAX = 5.0


def _clamp(value):
    """Pin a score into the 0-5 reporting scale."""
    return max(0.0, min(RIGOR_SCALE_MAX, float(value)))


def _coerce_points(raw):
    """Question point values arrive from JSON and from a FloatField, and from
    hand-edited payloads in between. Anything non-numeric or negative is
    treated as zero weight rather than blowing up the whole assignment."""
    try:
        points = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if points != points or points in (float("inf"), float("-inf")):  # NaN / inf
        return 0.0
    return max(0.0, points)


def _blooms_value(raw):
    """Map a stored blooms_level onto the 0-5 scale, or None if absent/unknown."""
    if not isinstance(raw, str):
        return None
    return BLOOMS_SCALE.get(raw.strip().lower())


def _iter_questions(questions):
    """Yield only the dict-shaped entries of a questions payload.

    Assignment.questions is a free-form JSONField, so it may legitimately be
    None, and may be a malformed list from an older extraction run. Callers
    should never have to care."""
    if not isinstance(questions, (list, tuple)):
        return
    for question in questions:
        if isinstance(question, dict):
            yield question


def compute_demand(questions):
    """Points-weighted mean Bloom's level across an assignment's questions.

    Returns ``(demand, coverage)`` where `demand` is 0-5 (or None when the
    Bloom's data is too sparse to trust) and `coverage` is the fraction of
    question points that carried a recognised Bloom's level, 0.0-1.0.

    Weighting is by points because that is how the assignment itself weights
    the work. When no question carries usable points -- an assignment scored
    purely by rubric, or one where points were never filled in -- we fall back
    to an unweighted mean so the assignment still gets a score instead of
    silently disappearing from the metric.
    """
    total_points = 0.0
    rated_points = 0.0
    weighted_sum = 0.0

    total_count = 0
    rated_count = 0
    level_sum = 0.0

    for question in _iter_questions(questions):
        points = _coerce_points(question.get("points"))
        level = _blooms_value(question.get("blooms_level"))

        total_points += points
        total_count += 1

        if level is None:
            continue

        rated_points += points
        weighted_sum += points * level
        rated_count += 1
        level_sum += level

    if total_count == 0:
        return None, 0.0

    if total_points > 0 and rated_points > 0:
        coverage = rated_points / total_points
        demand = weighted_sum / rated_points
    elif rated_count > 0:
        # No usable point values anywhere: weight every question equally.
        coverage = rated_count / total_count
        demand = level_sum / rated_count
    else:
        return None, 0.0

    if coverage < MIN_BLOOMS_COVERAGE:
        return None, coverage

    return _clamp(demand), coverage


def compute_standards(questions):
    """Share of open-ended questions carrying a usable rubric, scaled to 0-5.

    Returns None when the assignment has no open-ended questions at all --
    an all-multiple-choice quiz is not failing at rubric design, the question
    simply does not apply to it, and scoring it 0 would be a false negative.
    """
    open_ended = 0
    with_rubric = 0

    for question in _iter_questions(questions):
        question_type = question.get("question_type")
        if not isinstance(question_type, str):
            continue
        if question_type.strip().upper() not in OPEN_ENDED_QUESTION_TYPES:
            continue

        open_ended += 1

        rubric = question.get("rubric")
        if isinstance(rubric, (list, tuple)):
            levels = sum(1 for row in rubric if isinstance(row, dict))
            if levels >= MIN_RUBRIC_LEVELS:
                with_rubric += 1

    if open_ended == 0:
        return None

    return _clamp(RIGOR_SCALE_MAX * (with_rubric / open_ended))


def compute_evidence(average_score_percentage):
    """Invert an achieved average percentage into a 0-5 difficulty signal.

    100% average -> 0 (nobody was stretched); 0% average -> 5. Callers are
    responsible for only passing an average drawn from a large enough sample
    (see dashboard/rigor.py's MIN_GRADED_SUBMISSIONS) -- this function has no
    way to know how many submissions produced the number it is given.
    """
    if average_score_percentage is None:
        return None
    try:
        percentage = float(average_score_percentage)
    except (TypeError, ValueError):
        return None
    if percentage != percentage:  # NaN
        return None

    percentage = max(0.0, min(100.0, percentage))
    return _clamp(RIGOR_SCALE_MAX * (1.0 - percentage / 100.0))


def compose_rigor(demand, evidence=None, standards=None):
    """Blend the available components into a single 0-5 headline score.

    `demand` is required: it is the only component that measures rigor
    directly, and a score built purely from outcomes would answer a different
    question while wearing the same label. When it is missing we return None
    rather than quietly substituting a proxy.

    Weights are renormalized across whichever of the optional components are
    present, so an assignment with no rubric-bearing questions and no graded
    submissions still scores exactly its demand.
    """
    if demand is None:
        return None

    components = {"demand": _clamp(demand)}
    if evidence is not None:
        components["evidence"] = _clamp(evidence)
    if standards is not None:
        components["standards"] = _clamp(standards)

    total_weight = sum(COMPONENT_WEIGHTS[name] for name in components)
    if total_weight <= 0:  # pragma: no cover - weights are constants > 0
        return None

    blended = sum(COMPONENT_WEIGHTS[name] * value for name, value in components.items())
    return _clamp(blended / total_weight)


def score_assignment(questions):
    """Compute the assignment-intrinsic rigor fields from a questions payload.

    Returns ``(demand, standards, coverage)``, matching the denormalized
    columns on Assignment. Deliberately excludes `evidence`, which depends on
    submissions and would go stale the moment anything was graded.
    """
    demand, coverage = compute_demand(questions)
    standards = compute_standards(questions)
    return demand, standards, coverage
