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

# One evaluation object — shared verbatim by the batch and single-pass
# response schemas so the two paths can never drift apart.
QUESTION_EVALUATION_SCHEMA = {
    "type": "object",
    "properties": {
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
        "score_awarded": {"type": "number"},
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
        # The mechanical-verification hook: verbatim spans from the
        # student's answer. [] only for blank/not-attempted answers.
        "evidence_quotes": {"type": "array", "items": {"type": "string"}},
        "evaluation_rationale": {"type": "string"},
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
    "required": [
        "question_number",
        "question_text",
        "question_type",
        "max_points",
        "student_answer",
        "model_answer",
        "score_awarded",
        "level_achieved",
        "evidence_quotes",
        "evaluation_rationale",
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
GRADING_BATCH_RESPONSE_SCHEMA = {
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
GRADING_SINGLE_PASS_RESPONSE_SCHEMA = {
    "name": "grading_single_pass_response",
    "strict": True,
    "schema": {
        "type": "object",
        "properties": {
            "grading_summary": _GRADING_SUMMARY_BLOCK,
            "question_evaluations": {
                "type": "array",
                "items": QUESTION_EVALUATION_SCHEMA,
            },
            "score_calculation_verification": _VERIFICATION_BLOCK,
            "overall_performance_analysis": _PERFORMANCE_ANALYSIS_BLOCK,
            "grading_confidence": {"type": "number"},
            "recommendations": _RECOMMENDATIONS_BLOCK,
        },
        "required": [
            "grading_summary",
            "question_evaluations",
            "score_calculation_verification",
            "overall_performance_analysis",
            "grading_confidence",
            "recommendations",
        ],
        "additionalProperties": False,
    },
}

# ── Summary call: _build_overall_grading_summary's requested keys. ───────
# grader_meta_analysis had no defined structure anywhere; it is pinned to
# a string here (the summary user-prompt says so too). Its numeric
# neighbors are overwritten by Python after the call regardless.
GRADING_SUMMARY_RESPONSE_SCHEMA = {
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
