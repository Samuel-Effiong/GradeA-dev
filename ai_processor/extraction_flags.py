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

H-253 (the owner's ruling): each answer carries its own flag
(`review_flag`, answer_review_flag), and the one flag about the whole
paper is a field of its own (upload_mismatch_flag). The full list is still
kept on the submission, for the review queue.

No Django imports: ai_processor and students both use it.
"""

from .extraction_schemas import (
    ANSWER_FLAGS,
    ANSWERED,
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

#: H-253: the key each stored answer carries its own flag under.
REVIEW_FLAG = "review_flag"


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


def _writing_seen(entry):
    """The re-check found writing in this question's place (H-253)."""
    check = entry.get("blank_verification")
    return isinstance(check, dict) and bool(check.get("content_found"))


def _question_kind(entry):
    status = entry.get("answer_status")
    flag = answer_flag(entry)
    if status == NOT_FOUND_IN_DOCUMENT:
        # H-253: writing the re-check saw and nothing could copy out is a
        # reading problem, never "no answer could be found".
        return READING_PROBLEM if _writing_seen(entry) else ANSWER_NOT_FOUND
    if status == ILLEGIBLE or flag == FLAG_READING_PROBLEM:
        return READING_PROBLEM
    if flag in (FLAG_OFF_TOPIC, FLAG_TYPE_MISMATCH):
        return flag
    return None


def _on_topic(entry):
    """An answer the reader transcribed and did not call off topic: work
    for this question, whatever its form."""
    return entry.get("answer_status") in (ANSWERED, ILLEGIBLE) and (
        answer_flag(entry) != FLAG_OFF_TOPIC
    )


def _question_flag(entry):
    """The flag for one answer, with its question number, or None."""
    kind = _question_kind(entry)
    if kind is None:
        return None
    detail = _text(entry.get("answer_flag_detail"))
    if not detail and kind in (ANSWER_NOT_FOUND, READING_PROBLEM):
        detail = _text(entry.get("transcription_notes"))
    return _flag(kind, entry.get("question_number"), detail)


def _without_number(flag):
    return {key: value for key, value in flag.items() if key != "question_number"}


def answer_review_flag(entry):
    """H-253: what one stored answer carries as its `review_flag`:
    {kind, message, detail}, or None when there is nothing to check."""
    if not isinstance(entry, dict):
        return None
    flag = _question_flag(entry)
    return _without_number(flag) if flag is not None else None


def upload_mismatch_flag(answers, *, matches=True, detail=""):
    """H-253: the flag about the paper as a whole, {kind, message, detail},
    or None: the paper's own field, as it belongs to no question."""
    flags = submission_flags(answers, matches=matches, mismatch_detail=detail)
    if flags and flags[0]["kind"] == SUBMISSION_MISMATCH:
        return _without_number(flags[0])
    return None


def mismatch_in(flags):
    """The paper-level flag in a stored list of flags, or None."""
    for flag in flags if isinstance(flags, list) else []:
        if isinstance(flag, dict) and flag.get("kind") == SUBMISSION_MISMATCH:
            return _without_number(flag)
    return None


def _question_key(number):
    return " ".join(str(number).split()).lower()


def answers_with_flags(answers, flags):
    """H-253: the answers as they are sent, each carrying its own
    `review_flag` from the paper's stored flags (null when it has none).

    Added when an answer is shown, never stored in it: the stored answers
    stay exactly what the reader returned, a teacher's edit of an answer
    cannot lose its flag, and a paper read since H-242 needs no rewrite.
    Not a list of answers: returned as it is."""
    if not isinstance(answers, list):
        return answers
    by_question = {}
    for flag in flags if isinstance(flags, list) else []:
        if isinstance(flag, dict) and flag.get("question_number") is not None:
            by_question.setdefault(
                _question_key(flag["question_number"]), _without_number(flag)
            )
    return [
        (
            dict(
                entry,
                **{
                    REVIEW_FLAG: by_question.get(
                        _question_key(entry.get("question_number"))
                    )
                },
            )
            if isinstance(entry, dict)
            else entry
        )
        for entry in answers
    ]


def submission_flags(answers, *, matches=True, mismatch_detail=""):
    """Every flag for a paper, the paper-level one first, then one per
    question at most, in the answers' order.

    The detail is the reader's own sentence when it gave one; for an answer
    it could not find or read, its note on what it saw on the page.

    The paper-level verdict is held against the reader's own answers: a
    paper with even one answer on topic is work for this assignment, so a
    "not for this assignment" verdict that contradicts it is not raised
    (seen live: one maths paper with a right answer to Q3 was called
    another subject's). The per-question flags still stand."""
    answers = answers if isinstance(answers, list) else []
    entries = [entry for entry in answers if isinstance(entry, dict)]
    flags = []
    if matches is False and not any(_on_topic(entry) for entry in entries):
        flags.append(_flag(SUBMISSION_MISMATCH, None, _text(mismatch_detail)))
    for entry in entries:
        flag = _question_flag(entry)
        if flag is not None:
            flags.append(flag)
    return flags


def flags_from_extraction(result):
    """The flags for one extraction result, as extract_answer_with_retry
    returns it."""
    result = result if isinstance(result, dict) else {}
    matches, detail = paper_verdict([result])
    return submission_flags(
        result.get("answers"), matches=matches, mismatch_detail=detail
    )
