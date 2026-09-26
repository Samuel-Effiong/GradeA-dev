"""
Structured-output (json_schema) contracts for the grading pipeline.

Grading is the highest-stakes model output in the platform, yet it
historically ran on free-form `{"type": "json_object"}` while assignment
generation already used a strict schema. A malformed grading response
costs a full retry — and every retry is a fresh billed call — so schema
enforcement removes an entire class of parse-retry-rebill loops at the
API layer, before our own validation even runs.

The wrapper shape matches ASSIGNMENT_GENERATION_RESPONSE_SCHEMA exactly
(`{"name", "strict", "schema"}`), which __ai_model wraps as
`{"type": "json_schema", "json_schema": <this>}` for OpenRouter.

Strict-mode constraints shape two deliberate contract changes from the
prose contract in GRADING_ASSIGNMENT_PROMPT_4:
- every field must be required, so `flag_for_review` — previously
  "omit the key when not needed" — becomes NULLABLE instead
  (GRADING_ASSIGNMENT_PROMPT_5 documents this);
- `evidence_quotes` is new and REQUIRED on every evaluation: verbatim
  spans from the student's answer justifying the selected level, checked
  mechanically afterwards (see ai_processor/evidence.py).

These schemas must stay faithful to the prompt's documented structure —
a schema the model can't naturally satisfy trades parse failures for
constraint failures and wins nothing.
"""

from typing import Any, Dict

# One evaluation object — shared verbatim by the batch and single-pass
# response schemas so the two paths can never drift apart.
# Explicitly typed (not left to inference): these are deeply nested,
# heterogeneous JSON-schema literals, so mypy narrows an unannotated `{}`
# to something far too specific for every value actually stored in it,
# and every subsequent index into a nested level then fails to type-check.
QUESTION_EVALUATION_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        # ── Context: echoed back from the input, no judgment yet. ────────
        # Echoed exactly from the input; extracted assignments sometimes
        # carry string question numbers, so both types are legal.
        "question_number": {"type": ["number", "string"]},
        "question_text": {"type": "string"},
        "question_type": {
            "type": "string",
            "enum": ["OBJECTIVE", "ESSAY", "SHORT-ANSWER"],
        },
        "max_points": {"type": "number"},
        "student_answer": {"type": "string"},
        "model_answer": {"type": "string"},
        # ── REASON FIRST, THEN SCORE. ────────────────────────────────────
        # KEY ORDER IS BEHAVIOUR, NOT STYLE. Under strict structured
        # output the model emits fields in the order declared here, and
        # each token it writes conditions everything after it. When
        # score_awarded came first (it did, until this ordering), the
        # model named a number on instinct and then wrote a rationale to
        # justify the number it had already committed to — the reasoning
        # served the score instead of producing it.
        #
        # The order below forces the causal chain the prompt's GRADING
        # PROCEDURE actually describes:
        #   quote the evidence -> argue the case -> declare how close the
        #   call was -> name the level -> emit that level's points.
        #
        # level_achieved before score_awarded matters for the same
        # reason: Policy rule 1 requires score_awarded to be exactly the
        # selected level's points, so the level must be chosen first for
        # the number to be a consequence of it rather than a guess the
        # level is then fitted to.
        #
        # Do not reorder these for tidiness.
        # The mechanical-verification hook: verbatim spans from the
        # student's answer. [] only for blank/not-attempted answers.
        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
        "evaluation_rationale": {"type": "string"},
        # PER-QUESTION uncertainty. The submission-level
        # grading_confidence proved useless as a routing signal (120 of
        # 124 answers came back >= 80 — no spread to select on), so this
        # asks the narrow question instead: did this answer sit cleanly
        # inside the level, or between two? "borderline" is what the
        # second-opinion selector escalates on.
        "level_decision": {"type": "string", "enum": ["clear", "borderline"]},
        "level_achieved": {
            "type": "string",
            "enum": [
                "excellent",
                "good",
                "fair",
                "poor",
                "correct",
                "incorrect",
                "not_attempted",
            ],
        },
        "score_awarded": {"type": "number"},
        # ── Feedback: written knowing the decision. ──────────────────────
        "strengths": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "improvement_suggestions": {"type": "array", "items": {"type": "string"}},
        "feedback_for_student": {"type": "string"},
        # Nullable, not omittable: strict mode requires every key.
        "flag_for_review": {
            "anyOf": [
                {"type": "null"},
                {
                    "type": "object",
                    "properties": {
                        "flag_type": {
                            "type": "string",
                            "enum": [
                                "BORDERLINE_SCORE",
                                "EXTRACTION_ERROR",
                                "PLAGIARISM_CONCERN",
                                "OFF_TOPIC_ANSWER",
                            ],
                        },
                        "description": {"type": "string"},
                        "recommendation": {"type": "string"},
                    },
                    "required": ["flag_type", "description", "recommendation"],
                    "additionalProperties": False,
                },
            ]
        },
    },
    # Same order as `properties` above, deliberately — some providers key
    # generation order off `required` rather than `properties`, so the two
    # must not drift.
    "required": [
        "question_number",
        "question_text",
        "question_type",
        "max_points",
        "student_answer",
        "model_answer",
        "evidence_quotes",
        "evaluation_rationale",
        "level_decision",
        "level_achieved",
        "score_awarded",
        "strengths",
        "weaknesses",
        "improvement_suggestions",
        "feedback_for_student",
        "flag_for_review",
    ],
    "additionalProperties": False,
}

_GRADING_SUMMARY_BLOCK = {
    "type": "object",
    "properties": {
        "total_score": {"type": "number"},
        "max_total_points": {"type": "number"},
        "percentage": {"type": "number"},
    },
    "required": ["total_score", "max_total_points", "percentage"],
    "additionalProperties": False,
}

_VERIFICATION_BLOCK = {
    "type": "object",
    "properties": {
        "manual_sum": {"type": "number"},
        "verification_status": {"type": "string", "enum": ["PASS", "FAIL"]},
        "calculation_notes": {"type": "string"},
    },
    "required": ["manual_sum", "verification_status", "calculation_notes"],
    "additionalProperties": False,
}

_PERFORMANCE_ANALYSIS_BLOCK = {
    "type": "object",
    "properties": {
        "score_breakdown": {"type": "string"},
        "strengths_summary": {
            "type": "object",
            "properties": {
                "overall_strengths": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["overall_strengths"],
            "additionalProperties": False,
        },
        "areas_for_improvement": {
            "type": "object",
            "properties": {
                "overall_weaknesses": {"type": "array", "items": {"type": "string"}}
            },
            "required": ["overall_weaknesses"],
            "additionalProperties": False,
        },
    },
    "required": ["score_breakdown", "strengths_summary", "areas_for_improvement"],
    "additionalProperties": False,
}

_RECOMMENDATIONS_BLOCK = {
    "type": "object",
    "properties": {
        "for_student": {"type": "array", "items": {"type": "string"}},
        "for_teacher": {"type": "array", "items": {"type": "string"}},
        "follow_up_actions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["for_student", "for_teacher", "follow_up_actions"],
    "additionalProperties": False,
}

# ── Batched path: _grade_question_batch asks for evaluations only. ───────
GRADING_BATCH_RESPONSE_SCHEMA: Dict[str, Any] = {
    "name": "grading_batch_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "question_evaluations": {
                "type": "array",
                "items": QUESTION_EVALUATION_SCHEMA,
            }
        },
        "required": ["question_evaluations"],
        "additionalProperties": False,
    },
}

# ── Single-pass path: the full grading report in one response. ───────────
GRADING_SINGLE_PASS_RESPONSE_SCHEMA: Dict[str, Any] = {
    "name": "grading_single_pass_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            # The per-question ordering rationale applies at the paper
            # level too. grading_summary carries total_score, and it used
            # to be emitted BEFORE any question had been evaluated — the
            # model announced a total and then graded toward it. Every
            # question is judged first; the totals, the analysis and the
            # confidence are all consequences of that work.
            "question_evaluations": {
                "type": "array",
                "items": QUESTION_EVALUATION_SCHEMA,
            },
            "grading_summary": _GRADING_SUMMARY_BLOCK,
            "score_calculation_verification": _VERIFICATION_BLOCK,
            "overall_performance_analysis": _PERFORMANCE_ANALYSIS_BLOCK,
            "recommendations": _RECOMMENDATIONS_BLOCK,
            # Last: how sure the grader is can only be judged once the
            # grading exists to be sure about.
            "grading_confidence": {"type": "number"},
        },
        "required": [
            "question_evaluations",
            "grading_summary",
            "score_calculation_verification",
            "overall_performance_analysis",
            "recommendations",
            "grading_confidence",
        ],
        "additionalProperties": False,
    },
}

# ── Summary call: _build_overall_grading_summary's requested keys. ───────
# grader_meta_analysis had no defined structure anywhere; it is pinned to
# a string here (the summary user-prompt says so too). Its numeric
# neighbors are overwritten by Python after the call regardless.
GRADING_SUMMARY_RESPONSE_SCHEMA: Dict[str, Any] = {
    "name": "grading_summary_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "grading_summary": _GRADING_SUMMARY_BLOCK,
            "overall_performance_analysis": _PERFORMANCE_ANALYSIS_BLOCK,
            "score_calculation_verification": _VERIFICATION_BLOCK,
            "grader_meta_analysis": {"type": "string"},
            "grading_confidence": {"type": "number"},
            "recommendations": _RECOMMENDATIONS_BLOCK,
        },
        "required": [
            "grading_summary",
            "overall_performance_analysis",
            "score_calculation_verification",
            "grader_meta_analysis",
            "grading_confidence",
            "recommendations",
        ],
        "additionalProperties": False,
    },
}
