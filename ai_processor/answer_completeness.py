"""
Mechanical completeness checking for extracted student answers.

WHAT THIS DEFENDS AGAINST

Grading already refuses a model response that grades fewer questions than
it was asked to (services.py::_missing_question_numbers): an ungraded
question still counts toward max_total_points while contributing nothing
to total_score, which silently deflates the grade. Answer EXTRACTION had
no equivalent, even though the same arithmetic applies one step earlier
and with worse consequences — a question whose answer never made it out
of extraction is paired with a fabricated empty answer
(services.py::_pair_question_with_answers) and scored 0 as
`not_attempted`, indistinguishable from a student who chose to skip it.

This module closes that gap. It is deliberately pure — plain data in,
plain data out, no Django, no ORM, no network — so every rule below is
unit-testable without a database, exactly like objective_grading.py and
evidence.py.

THE TWO MODES, AND WHY THE DEGRADE EXISTS

MODE_STRICT returns violations for the caller to raise on and retry. That
is right on the early attempts: a re-ask usually produces the missing
answer.

MODE_LOG repairs instead of rejecting, and the LAST extraction attempt
uses it. This mirrors the deliberate degrade already proven in
services.py::_grade_question_batch for the evidence check, and for the
same reason: failing on the final attempt destroys the whole submission,
and the student gets no grade at all. A repaired payload — with the
missing questions explicitly marked NOT_FOUND_IN_DOCUMENT and routed to a
human — is strictly better for that student than nothing, and unlike the
old behaviour it is not silent.

WHAT REPAIR MUST NEVER DO

Repair may only ever ADD information a human will act on. It must never
invent an answer, never upgrade a status toward ANSWERED, and never drop
a transcription that was actually produced. Every repair path below is
written so that the worst case is "a real answer is additionally flagged
for review", not "a real answer is discarded".
"""

import logging

from ai_processor.extraction_schemas import (
    ANSWER_STATUSES,
    ANSWERED,
    BLANK,
    EMPTY_ANSWER_STATUSES,
    NOT_FOUND_IN_DOCUMENT,
)

logger = logging.getLogger(__name__)

MODE_STRICT = "strict"  # violations are returned for the caller to raise on
MODE_LOG = "log"  # repair + annotate, never reject
MODE_OFF = "off"  # do not inspect at all
MODES = (MODE_STRICT, MODE_LOG, MODE_OFF)


class AnswerExtractionCompletenessError(ValueError):
    """
    An extracted answer payload does not account for every question.

    Subclasses ValueError so the existing broad retry handlers in
    extract_answer_with_retry treat it as a retryable extraction failure,
    matching how GradingCompletenessError is handled one layer down.
    """


def _default_key(value):
    """
    Normalize a question_number for cross-type matching.

    Assignment questions store ints; extracted answers are free-form JSON
    and can quote the same number as a string ("3" vs 3). An exact-type
    lookup between the two reports a present answer as missing — which
    here would mean fabricating a NOT_FOUND_IN_DOCUMENT flag for an answer
    we actually have, sending a correctly graded submission to review for
    no reason. Callers normally pass
    AIProcessor._question_number_key; this mirrors it so the module stays
    usable (and testable) standalone.
    """
    text = str(value).strip()
    return int(text) if text.isdigit() else text


def infer_answer_status(entry):
    """
    The status for an entry that carries none.

    Reachable whenever the strict schema is disabled
    (ANSWER_EXTRACTION_SCHEMA_ENABLED=False) or a payload predates it, so
    it must degrade to something safe rather than raise.

    The inference is deliberately LOSSY in one direction: an empty answer
    becomes BLANK, never NOT_FOUND_IN_DOCUMENT, because without the
    model's own account of the page there is no evidence to distinguish
    them — and asserting NOT_FOUND on a guess would flood the review queue
    with every genuinely skipped question in the class. That is precisely
    the pre-schema behaviour, so turning the schema off returns the system
    to exactly where it was, no worse.
    """
    html = (entry.get("answer_html") or "").strip()
    return ANSWERED if html else BLANK


def _normalize_entry(entry):
    """A shallow copy with a guaranteed-valid answer_status."""
    normalized = dict(entry)
    status = normalized.get("answer_status")
    if status not in ANSWER_STATUSES:
        if status not in (None, ""):
            logger.warning(
                "[AnswerCompleteness] Unrecognised answer_status %r on question "
                "%r; re-deriving from the transcription.",
                status,
                normalized.get("question_number"),
            )
        normalized["answer_status"] = infer_answer_status(normalized)
    return normalized


def _missing_entry(question, key_fn):
    """
    The placeholder inserted for a question extraction never accounted
    for. Carries the flag, never a fabricated answer.
    """
    return {
        "question_number": question.get("question_number"),
        "question_text": question.get("question_text", ""),
        "source_page": None,
        "transcription_notes": (
            "Extraction returned no entry for this question. Inserted by "
            "answer-completeness repair; the document was never confirmed "
            "to be blank here."
        ),
        "answer_html": "",
        "answer_status": NOT_FOUND_IN_DOCUMENT,
        "confidence": 0.0,
    }


def _pick_survivor(entries):
    """
    Which of several entries sharing one question_number to keep.

    Prefers a real transcription over an empty one, because the failure
    this whole module exists to prevent is losing a real answer. A tie
    between two DIFFERENT transcriptions is genuinely ambiguous and is
    reported as a violation by the caller; this only decides what to carry
    forward so the repaired payload stays well-formed.
    """
    answered = [
        entry
        for entry in entries
        if entry.get("answer_status") == ANSWERED
        and (entry.get("answer_html") or "").strip()
    ]
    return (answered or entries)[0]


def check_answer_completeness(answers, questions, *, key_fn=_default_key):
    """
    Inspect an extracted answer payload against the assignment's questions.

    Returns (repaired_answers, violations). `violations` is a list of
    human-readable strings — the same contract as
    evidence.enforce_evidence — so a caller in strict mode can join them
    into one retryable error, and a caller in log mode can annotate.

    `repaired_answers` is always well-formed and always accounts for every
    question exactly once, whether or not there were violations. Callers
    in strict mode discard it and retry; callers in log mode persist it.
    """
    violations = []

    if not isinstance(answers, list):
        violations.append(f"`answers` must be a list, got {type(answers).__name__}.")
        answers = []

    question_keys = []
    questions_by_key = {}
    for question in questions or []:
        if not isinstance(question, dict):
            continue
        key = key_fn(question.get("question_number"))
        if key in questions_by_key:
            # The assignment itself is malformed. Not this module's
            # problem to fix, but silently collapsing the duplicate would
            # under-count the expected answers, so it is surfaced.
            violations.append(
                f"Assignment has more than one question numbered {key!r}."
            )
            continue
        questions_by_key[key] = question
        question_keys.append(key)

    grouped = {}
    for entry in answers:
        if not isinstance(entry, dict):
            violations.append(f"Answer entry is not an object: {entry!r}.")
            continue
        grouped.setdefault(key_fn(entry.get("question_number")), []).append(
            _normalize_entry(entry)
        )

    # Extras first: an answer numbered outside the assignment is the
    # signature of numbering drift, which usually means some OTHER answer
    # is attached to the wrong question. It is never safe to just ignore.
    for key in grouped:
        if key not in questions_by_key:
            violations.append(
                f"Answer for question {key!r} does not correspond to any "
                f"question in this assignment (numbering drift)."
            )

    repaired = []
    for key in question_keys:
        entries = grouped.get(key)

        if not entries:
            violations.append(f"No answer entry for question {key!r}.")
            repaired.append(_missing_entry(questions_by_key[key], key_fn))
            continue

        if len(entries) > 1:
            distinct = {(e.get("answer_html") or "").strip() for e in entries}
            violations.append(
                f"Question {key!r} has {len(entries)} answer entries "
                f"({len(distinct)} distinct)."
            )

        entry = _pick_survivor(entries)
        status = entry.get("answer_status")
        html = (entry.get("answer_html") or "").strip()

        # Cross-field rules a JSON schema cannot express. Both directions
        # are self-contradictions, and both are repaired toward the
        # TRANSCRIPTION rather than the label: the text on the page is
        # evidence, the label is the model's opinion about it.
        if status == ANSWERED and not html:
            violations.append(
                f"Question {key!r} is marked {ANSWERED} but its answer_html "
                f"is empty."
            )
            entry = dict(entry, answer_status=NOT_FOUND_IN_DOCUMENT)
        elif status in EMPTY_ANSWER_STATUSES and html:
            violations.append(
                f"Question {key!r} is marked {status} but carries a "
                f"non-empty answer_html."
            )
            entry = dict(entry, answer_status=ANSWERED)

        repaired.append(entry)

    return repaired, violations


def enforce_answer_completeness(
    answers, questions, *, mode=MODE_STRICT, key_fn=_default_key
):
    """
    check_answer_completeness plus the mode policy.

    MODE_OFF    -> returns the payload untouched, no inspection.
    MODE_STRICT -> raises AnswerExtractionCompletenessError on any
                   violation, for the caller's retry loop to catch.
    MODE_LOG    -> logs, and returns the REPAIRED payload so the
                   submission survives with its problems made explicit.
    """
    if mode == MODE_OFF:
        return answers

    repaired, violations = check_answer_completeness(answers, questions, key_fn=key_fn)

    if not violations:
        return repaired

    if mode == MODE_STRICT:
        raise AnswerExtractionCompletenessError(
            "Extracted answers are incomplete: " + "; ".join(violations)
        )

    logger.warning(
        "[AnswerCompleteness] Repaired %s violation(s) on the final "
        "extraction attempt; affected questions are flagged for review. %s",
        len(violations),
        "; ".join(violations),
    )
    return repaired
