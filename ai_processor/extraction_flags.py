"""
H-242: what the answer reader noticed about a paper, as plain-language
flags a teacher and the student can read.

The reader reports two things beside each answer's status
(ai_processor/extraction_schemas.py): an `answer_flag` per answer, and a
verdict on the paper as a whole (`submission_matches_assignment`). Before
H-242 it had no field for either: the prompt asked for a warning the
strict schema could not carry, its overall comment was discarded, and an
upload for another subject passed as ordinary answers or as "not found".

This module turns those reports, and the statuses that already mean "a
human must look" (NOT_FOUND_IN_DOCUMENT, ILLEGIBLE), into one list of
flags. One place, so the upload, the typed-text edit and the grading run
cannot word or count them differently.

No Django imports: ai_processor and students both use it.
"""

from .extraction_schemas import (
    ANSWER_FLAGS,
    FLAG_NONE,
    FLAG_OFF_TOPIC,
    FLAG_READING_PROBLEM,
    FLAG_TYPE_MISMATCH,
    ILLEGIBLE,
    NOT_FOUND_IN_DOCUMENT,
)

# ── flag kinds, as stored on StudentSubmission.extraction_flags ─────────
SUBMISSION_MISMATCH = "SUBMISSION_MISMATCH"
OFF_TOPIC = FLAG_OFF_TOPIC
TYPE_MISMATCH = FLAG_TYPE_MISMATCH
READING_PROBLEM = FLAG_READING_PROBLEM
ANSWER_NOT_FOUND = "ANSWER_NOT_FOUND"

#: The review-queue tier of each kind. Work that may not be the student's
#: own answer to this paper, or may not have been read, is critical, as an
#: answer not found already is; an answer that is there but does not fit
#: is moderate.
TIER_OF = {
    SUBMISSION_MISMATCH: "critical",
    ANSWER_NOT_FOUND: "critical",
    READING_PROBLEM: "critical",
    OFF_TOPIC: "moderate",
    TYPE_MISMATCH: "moderate",
}

#: The headline of each kind. `{n}` is the question number.
MESSAGES = {
    SUBMISSION_MISMATCH: "This upload does not appear to be for this assignment.",
    OFF_TOPIC: "Question {n}: the answer does not appear to be about this question.",
    TYPE_MISMATCH: (
        "Question {n}: the answer is not in the form this question asks for."
    ),
    READING_PROBLEM: (
        "Question {n}: part or all of this answer could not be read clearly "
        "from the upload."
    ),
    ANSWER_NOT_FOUND: (
        "Question {n}: no answer to this question could be found in the upload."
    ),
}

#: The reason type a flag is filed under in StudentSubmission.review_reasons.
REVIEW_REASON_TYPE = "extraction_flag"


def _text(value):
    return " ".join(str(value).split()) if isinstance(value, str) else ""


def answer_flag(entry):
    """The reader's flag on one answer: NONE when absent or not one of the
    vocabulary (an older reply, or the schema switched off)."""
    flag = entry.get("answer_flag") if isinstance(entry, dict) else None
    return flag if flag in ANSWER_FLAGS else FLAG_NONE


def merge_answer_flags(existing, new):
    """The flag and detail of an answer joined from two chunks: a flag on
    either part stands, and when both carry one, both details are kept.
    Returns (flag, detail)."""
    flags = [
        (answer_flag(part), _text(part.get("answer_flag_detail")))
        for part in (existing, new)
    ]
    raised = [(flag, detail) for flag, detail in flags if flag != FLAG_NONE]
    if not raised:
        return FLAG_NONE, ""
    detail = " | ".join(detail for _, detail in raised if detail)
    return raised[0][0], detail


def paper_verdict(replies):
    """(matches, detail) for a paper read in one or more replies.

    The paper is a mismatch only when EVERY reply that gave a verdict said
    so: a chunk of continuation pages may hold nothing that answers the
    paper without the paper being for another assignment. A reply with no
    verdict (an older reply, the schema off) counts as a match."""
    verdicts = []
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        matches = reply.get("submission_matches_assignment")
        verdicts.append((matches is not False, _text(reply.get("mismatch_detail"))))
    if not verdicts or any(matches for matches, _ in verdicts):
        return True, ""
    return False, " | ".join(detail for _, detail in verdicts if detail)


def _flag(kind, question_number, detail):
    return {
        "kind": kind,
        "question_number": question_number,
        "message": MESSAGES[kind].format(n=question_number),
        "detail": detail,
    }


def _question_kind(entry):
    status = entry.get("answer_status")
    flag = answer_flag(entry)
    if status == NOT_FOUND_IN_DOCUMENT:
        return ANSWER_NOT_FOUND
    if status == ILLEGIBLE or flag == FLAG_READING_PROBLEM:
        return READING_PROBLEM
    if flag in (FLAG_OFF_TOPIC, FLAG_TYPE_MISMATCH):
        return flag
    return None


def submission_flags(answers, *, matches=True, mismatch_detail=""):
    """Every flag for a paper, the paper-level one first, then one per
    question at most, in the answers' order.

    The detail is the reader's own sentence when it gave one; for an answer
    it could not find or read, its note on what it saw on the page."""
    flags = []
    if matches is False:
        flags.append(_flag(SUBMISSION_MISMATCH, None, _text(mismatch_detail)))
    for entry in answers if isinstance(answers, list) else []:
        if not isinstance(entry, dict):
            continue
        kind = _question_kind(entry)
        if kind is None:
            continue
        detail = _text(entry.get("answer_flag_detail"))
        if not detail and kind in (ANSWER_NOT_FOUND, READING_PROBLEM):
            detail = _text(entry.get("transcription_notes"))
        flags.append(_flag(kind, entry.get("question_number"), detail))
    return flags


def flags_from_extraction(result):
    """The flags for one extraction result, as extract_answer_with_retry
    returns it."""
    result = result if isinstance(result, dict) else {}
    matches, detail = paper_verdict([result])
    return submission_flags(
        result.get("answers"), matches=matches, mismatch_detail=detail
    )
