"""
What a student is shown of a saved grading result (H-127).

`StudentSubmission.feedback` is the grading result as the grader produced
it. Much of it is written for the teacher: the second grader's marks and
reasons, which model graded, the review flags, the rationale for the level
chosen, the evidence quotes, the advice to the teacher, the text of a failed
second opinion. Every route that shows a student their graded work goes
through `student_safe_feedback`, and none returns the column itself;
AutoGrader/tests_student_feedback_guard.py holds the repository to that.

`grading_result_for_formatter` is what the feedback formatter (an AI call
whose wording the student reads) is sent of the same result.

No Django imports, so any app can use it without an import cycle.
"""

#: The fields of one question's evaluation that a student is shown.
#: Everything else - flag_for_review, graded_by, snapped_from,
#: evaluation_rationale (a note to the teacher on the level chosen),
#: evidence_quotes (internal verification detail) - is left out.
STUDENT_EVALUATION_FIELDS = (
    "question_number",
    "question_text",
    "question_type",
    "max_points",
    "student_answer",
    "score_awarded",
    "level_achieved",
    "strengths",
    "weaknesses",
    "improvement_suggestions",
    "feedback_for_student",
)

#: The fields of the grading summary that a student is shown.
STUDENT_SUMMARY_FIELDS = ("total_score", "max_total_points", "percentage")


def student_safe_feedback(feedback):
    """The whitelist projection of a saved grading result for a student.

    Built by copying known keys, never by removing unwanted ones, so a key
    added to the grading result later (as `second_opinion` once was) is
    hidden from students until someone decides otherwise. The second
    opinion is not copied at all: it is a second grader's dissenting score
    and rationale, meant for the teacher's review queue.

    A value that is not a dictionary cannot be projected and is shown as
    nothing (None); the stored value is never returned as it is."""
    if not isinstance(feedback, dict):
        return None

    safe: dict = {}

    summary = feedback.get("grading_summary")
    if isinstance(summary, dict):
        safe["grading_summary"] = {
            key: summary.get(key) for key in STUDENT_SUMMARY_FIELDS if key in summary
        }

    evaluations = feedback.get("question_evaluations")
    if isinstance(evaluations, list):
        safe["question_evaluations"] = [
            {
                key: evaluation.get(key)
                for key in STUDENT_EVALUATION_FIELDS
                if key in evaluation
            }
            for evaluation in evaluations
            if isinstance(evaluation, dict)
        ]

    overall = feedback.get("overall_performance_analysis")
    if isinstance(overall, dict):
        safe["overall_performance_analysis"] = overall

    recommendations = feedback.get("recommendations")
    if isinstance(recommendations, dict):
        for_student = recommendations.get("for_student")
        if for_student is not None:
            safe["recommendations"] = {"for_student": for_student}

    return safe


def grading_result_for_formatter(grading):
    """The saved grading result as the feedback formatter is sent it:
    everything except the second-opinion block.

    The formatter is an AI call that words the result for the student. The
    second opinion is the second grader's marks and reasons and, when it
    failed, the text of the failure; it is for the teacher's review queue,
    and what the formatter is sent it can restate. A new dictionary: the
    saved result is not changed. A value that is not a dictionary is
    passed on as it is, as before."""
    if not isinstance(grading, dict):
        return grading
    return {key: value for key, value in grading.items() if key != "second_opinion"}
