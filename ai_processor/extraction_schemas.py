"""
Structured-output (json_schema) contracts for the extraction pipeline.

WHY THIS EXISTS

Answer extraction was the least defended model call in the platform and
the most consequential. It ran on free-form `{"type": "json_object"}`,
its result was `json.loads`-ed and assigned straight into a JSONField
(students/services.py), and nothing anywhere checked its shape. The
failure that mattered was not a crash — it was silence:

    ai_processor/services.py::_pair_question_with_answers fabricates an
    empty answer for any question it cannot find a match for, and grading
    then scores that as `not_attempted` = 0. That fabricated entry is
    byte-identical, downstream, to a student who genuinely left the
    question blank.

So a student who ANSWERED a question, whose answer extraction dropped,
received a silent zero, and no human was ever told. `answer_status`
below exists to make those two cases structurally distinguishable for
the first time.

The wrapper shape matches ASSIGNMENT_GENERATION_RESPONSE_SCHEMA and the
grading schemas exactly (`{"name", "strict", "schema"}`), which
__ai_model wraps as `{"type": "json_schema", "json_schema": <this>}`.

STRICT MODE CONSEQUENCES

Strict structured output requires every declared key to be present, so
two fields the prose contract described as "omit when not applicable"
become NULLABLE here instead — `student_name_raw` (documented in
ANSWERS_EXTRACTION_PROMPT_HTML_4 as "ONLY included when a roster match
was made") and `source_page`. This is the same accommodation
grading_schemas.py made for `flag_for_review`, for the same reason.
"""

from typing import Any, Dict

# ── answer_status vocabulary ─────────────────────────────────────────────
# Exposed as constants because Python code (completeness enforcement, the
# review-flagging path, the benchmark scorer) branches on these, and a
# typo'd string literal in any one of those places would fail open —
# silently skipping the very flag this module exists to raise.
ANSWERED = "ANSWERED"
BLANK = "BLANK"
ILLEGIBLE = "ILLEGIBLE"
NOT_FOUND_IN_DOCUMENT = "NOT_FOUND_IN_DOCUMENT"

ANSWER_STATUSES = (ANSWERED, BLANK, ILLEGIBLE, NOT_FOUND_IN_DOCUMENT)

#: Statuses that mean "we do not have this student's work" as opposed to
#: "this student produced no work". Every one of these must reach a human:
#: scoring them 0 may well be correct, but it is not something the system
#: is entitled to decide on its own.
REVIEW_REQUIRED_STATUSES = frozenset({ILLEGIBLE, NOT_FOUND_IN_DOCUMENT})

#: Statuses under which a non-empty answer_html is a self-contradiction,
#: and vice versa. Enforced in Python (schemas cannot express cross-field
#: rules) by ai_processor.answer_completeness.
EMPTY_ANSWER_STATUSES = frozenset({BLANK, NOT_FOUND_IN_DOCUMENT})


# One extracted answer. Explicitly typed for the same reason the grading
# schemas are: mypy narrows an unannotated nested literal far too tightly
# for the heterogeneous values actually stored in it.
EXTRACTED_ANSWER_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        # ── Context: echoed from the assignment, no judgment yet. ────────
        # Assignments extracted by AI sometimes carry string question
        # numbers, so both types are legal here — exactly as
        # QUESTION_EVALUATION_SCHEMA allows. Python normalises via
        # _question_number_key before any join.
        "question_number": {"type": ["number", "string"]},
        "question_text": {"type": "string"},
        # ── TRANSCRIBE FIRST, THEN CLASSIFY. ─────────────────────────────
        # KEY ORDER IS BEHAVIOUR, NOT STYLE — the same hard-won lesson as
        # QUESTION_EVALUATION_SCHEMA's reason-before-score ordering. Under
        # strict structured output the model emits fields in the order
        # declared here, and every token it writes conditions the ones
        # after it.
        #
        # If `answer_status` came first, the model would commit to the
        # word "BLANK" and then write `answer_html: ""` to stay
        # consistent with itself — manufacturing exactly the silent zero
        # this schema was built to prevent. Declaring the observation
        # (`source_page`, `transcription_notes`) and the transcription
        # (`answer_html`) BEFORE the classification forces the status to
        # be a CONSEQUENCE of what was actually read off the page, rather
        # than a prior guess the transcription is then fitted to.
        #
        # Do not reorder these for tidiness.
        "source_page": {"type": ["integer", "null"]},
        "transcription_notes": {"type": "string"},
        "answer_html": {"type": "string"},
        "answer_status": {"type": "string", "enum": list(ANSWER_STATUSES)},
        # PER-ANSWER confidence. The document-level extraction_confidence
        # is too coarse to route on — it is one number for a whole
        # submission, so one unreadable question cannot lower it without
        # slandering the other nineteen.
        "confidence": {"type": "number"},
    },
    "required": [
        "question_number",
        "question_text",
        "source_page",
        "transcription_notes",
        "answer_html",
        "answer_status",
        "confidence",
    ],
    "additionalProperties": False,
}


ANSWER_EXTRACTION_RESPONSE_SCHEMA: Dict[str, Any] = {
    "name": "answer_extraction_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            # Identity first: it is read off the page independently of the
            # answers, and a wrong student attribution invalidates every
            # answer under it no matter how well transcribed.
            "student_name": {"type": "string"},
            # Nullable rather than omittable — see the module docstring.
            "student_name_raw": {"type": ["string", "null"]},
            "student_id": {"type": "string"},
            "answers": {"type": "array", "items": EXTRACTED_ANSWER_SCHEMA},
            "extraction_confidence": {"type": "number"},
            "feedback": {"type": "string"},
        },
        "required": [
            "student_name",
            "student_name_raw",
            "student_id",
            "answers",
            "extraction_confidence",
            "feedback",
        ],
        "additionalProperties": False,
    },
}


# ── Blank re-verification (Phase A4) ─────────────────────────────────────
# A claimed blank is the ONLY shape a lost answer can hide in: an answer
# that was transcribed is, by definition, not lost. So rather than trying
# to verify every answer against a source text we do not have (submissions
# are read from page images; ocr_processor is an empty stub, so there is no
# independent transcript to diff against), this re-reads the pages asking
# one narrow question about the specific questions that came back empty.
#
# Cost is bounded and self-limiting: zero extra calls on a fully answered
# submission, and the more blanks there are — i.e. the higher the risk that
# one of them is a miss — the more the single extra call earns its keep.
BLANK_VERIFICATION_RESPONSE_SCHEMA: Dict[str, Any] = {
    "name": "blank_verification_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "question_number": {"type": ["number", "string"]},
                        # Observation before verdict, same ordering rule as
                        # above: the model must say what it can see before
                        # it rules on whether anything is there.
                        "observed": {"type": "string"},
                        "page": {"type": ["integer", "null"]},
                        # A verbatim fragment is the mechanical proof that
                        # `content_found` is true — checked in Python
                        # against nothing (there is no source text), but
                        # carried to the teacher so the review is actionable
                        # rather than a bare assertion.
                        "verbatim_fragment": {"type": ["string", "null"]},
                        "content_found": {"type": "boolean"},
                    },
                    "required": [
                        "question_number",
                        "observed",
                        "page",
                        "verbatim_fragment",
                        "content_found",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["findings"],
        "additionalProperties": False,
    },
}
