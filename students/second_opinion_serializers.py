"""
Schema-only serializers for the teacher-facing grading detail view.

None of these are ever instantiated to serialize real data — the pipeline
JSON they describe (ai_processor/grading_schemas.py's
QUESTION_EVALUATION_SCHEMA, and ai_processor/second_opinion.py's
_side()/_severity()/compare_evaluations() output) is already valid and is
passed straight through by StudentSubmissionDetailSerializer's
get_question_breakdown/get_second_opinion. These classes exist purely so
drf-spectacular renders real nested objects in the OpenAPI schema instead
of an undocumented JSON blob — same pattern as
billing/serializers.py::CancellationInfoSerializer.

Field names intentionally match the raw JSON keys exactly (no renaming),
so this is a typing/documentation layer only, never a reshape.
"""

from rest_framework import serializers


class QuestionEvaluationSerializer(serializers.Serializer):
    """One entry of feedback["question_evaluations"] — the full per-question
    breakdown, teacher-facing only (see
    students/serializers.py::StudentSubmissionDetailStudentVersionSerializer
    for the separately-whitelisted student-safe subset of these same
    fields)."""

    # The pipeline's own schema allows number OR string here (extracted
    # assignments sometimes carry string question numbers) — simplified to
    # string for the OpenAPI schema since this is a display/join key, never
    # arithmetic.
    question_number = serializers.CharField()
    question_text = serializers.CharField()
    question_type = serializers.ChoiceField(
        choices=["OBJECTIVE", "ESSAY", "SHORT-ANSWER"]
    )
    max_points = serializers.FloatField()
    student_answer = serializers.CharField(allow_blank=True)
    model_answer = serializers.CharField(allow_blank=True)
    evidence_quotes = serializers.ListField(child=serializers.CharField())
    evaluation_rationale = serializers.CharField(allow_blank=True)
    level_decision = serializers.ChoiceField(choices=["clear", "borderline"])
    level_achieved = serializers.ChoiceField(
        choices=[
            "excellent",
            "good",
            "fair",
            "poor",
            "correct",
            "incorrect",
            "not_attempted",
        ]
    )
    score_awarded = serializers.FloatField()
    strengths = serializers.ListField(child=serializers.CharField())
    weaknesses = serializers.ListField(child=serializers.CharField())
    improvement_suggestions = serializers.ListField(child=serializers.CharField())
    feedback_for_student = serializers.CharField(allow_blank=True)
    flag_for_review = serializers.DictField(allow_null=True)
    # Added by the grading pipeline after the model response, not part of
    # the model's own schema — see AIProcessor._grade_question_batch /
    # _finalize_grading_result.
    graded_by = serializers.CharField(allow_null=True, required=False)
    from_cache = serializers.BooleanField(required=False)
    snapped_from = serializers.FloatField(required=False)


class GraderSideSerializer(serializers.Serializer):
    """One side of a disagreement — matches
    ai_processor/second_opinion.py::_side() exactly."""

    score_awarded = serializers.FloatField()
    level_achieved = serializers.CharField()
    level_decision = serializers.CharField()
    evaluation_rationale = serializers.CharField(allow_blank=True)
    evidence_quotes = serializers.ListField(child=serializers.CharField())


class DisagreementSeveritySerializer(serializers.Serializer):
    """Matches ai_processor/second_opinion.py::_severity()'s return shape."""

    gap_points = serializers.FloatField()
    gap_fraction = serializers.FloatField(allow_null=True)
    levels_apart = serializers.IntegerField(allow_null=True)
    tier = serializers.ChoiceField(choices=["critical", "moderate", "borderline"])


class DisagreementSerializer(serializers.Serializer):
    """One entry of second_opinion.disagreements — matches
    ai_processor/second_opinion.py::compare_evaluations()'s per-disagreement
    shape."""

    # The pipeline's own schema allows number OR string here (extracted
    # assignments sometimes carry string question numbers) — simplified to
    # string for the OpenAPI schema since this is a display/join key, never
    # arithmetic.
    question_number = serializers.CharField()
    a = GraderSideSerializer()
    b = GraderSideSerializer()
    severity = DisagreementSeveritySerializer(required=False)


class SecondOpinionSerializer(serializers.Serializer):
    """The full feedback["second_opinion"] block — see
    AIProcessor._maybe_run_second_opinion. `selected` maps a normalized
    question_number string to the list of trigger reasons
    (ai_processor/second_opinion.py's REASON_* constants) that picked it
    for a second opinion. The skip-path fields (skipped/skipped_reason/
    error) are only present when no second opinion ran at all — e.g. no
    independent model was available, or the pass failed after retries."""

    selected = serializers.DictField(
        child=serializers.ListField(child=serializers.CharField()),
        required=False,
    )
    agreements = serializers.ListField(child=serializers.CharField(), required=False)
    disagreements = serializers.ListField(
        child=DisagreementSerializer(), required=False
    )
    skipped = serializers.CharField(required=False, allow_null=True)
    skipped_reason = serializers.CharField(required=False, allow_null=True)
    error = serializers.CharField(required=False, allow_null=True)
