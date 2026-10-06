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

`student_safe_formatted_grade` is what a student is shown of the formatted
grade, the formatter's own output, which its prompt fills with advice to the
teacher as well.

No Django imports, so any app can use it without an import cycle.
"""

import ast

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

#: --- The formatted grade (the formatter's output; see
#: ai_processor/GRADE_FORMATTER_2.txt for its sections) ---
#: The fields of its overall summary that a student is shown.
STUDENT_FORMATTED_SUMMARY_FIELDS = (
    "score_statement",
    "performance_narrative",
    "grade_tier_context",
)
#: Its sections that are lists of sentences written to the student.
STUDENT_FORMATTED_LIST_SECTIONS = ("strengths", "areas_for_improvement")
#: The fields of one question's entry that a student is shown.
STUDENT_FORMATTED_QUESTION_FIELDS = (
    "question_number",
    "question_text",
    "max_score",
    "score_awarded",
    "narrative",
    "feedback_for_student",
    "strengths",
    "weaknesses",
)
#: Of final_recommendations a student is shown this and nothing else. The
#: prompt also asks for `for_teacher` (and tells the formatter to surface
#: every review flag there) and `follow_up_actions`.
STUDENT_FORMATTED_RECOMMENDATION_FIELDS = ("for_student",)
#: Longer stored text is not read at all. A formatted grade is a few
#: thousand characters a question; this is far above any real one.
MAX_FORMATTED_GRADE_CHARACTERS = 500_000

#: The fields of the grading summary that a student is shown.
STUDENT_SUMMARY_FIELDS = ("total_score", "max_total_points", "percentage")


def _is_plain(value):
    """Text, a number, true or false, or nothing."""
    return value is None or isinstance(value, (str, int, float, bool))


def _is_plain_or_a_list_of_plain(value):
    return _is_plain(value) or (
        isinstance(value, list) and all(_is_plain(item) for item in value)
    )


def _copy_plain(source, names):
    """The named entries of `source` whose value is plain or a list of
    plain values. A name whose value is anything else (a dictionary, a
    list holding a dictionary or a list) is LEFT OUT.

    Copying by name alone is not enough: the value under an allowed name
    comes from an AI's reply, and a dictionary nested where a sentence was
    asked for would be copied with every key in it. So "what is not named
    is not shown" holds at every depth (SM ruling, 2026-10-06, from
    Verifier 1's read)."""
    return {
        name: source[name]
        for name in names
        if name in source and _is_plain_or_a_list_of_plain(source[name])
    }


def student_safe_feedback(feedback):
    """The whitelist projection of a saved grading result for a student.

    Built by copying known keys, never by removing unwanted ones, so a key
    added to the grading result later (as `second_opinion` once was) is
    hidden from students until someone decides otherwise. The second
    opinion is not copied at all: it is a second grader's dissenting score
    and rationale, meant for the teacher's review queue.

    A value that is not a dictionary cannot be projected and is shown as
    nothing (None); the stored value is never returned as it is.

    Under a copied name only plain values pass (`_copy_plain`). The one
    exception is `overall_performance_analysis`, copied whole: it is nested
    by design, students are shown it today, and its real keys are not
    known without reading real rows. A stated limit."""
    if not isinstance(feedback, dict):
        return None

    safe: dict = {}

    summary = feedback.get("grading_summary")
    if isinstance(summary, dict):
        safe["grading_summary"] = _copy_plain(summary, STUDENT_SUMMARY_FIELDS)

    evaluations = feedback.get("question_evaluations")
    if isinstance(evaluations, list):
        safe["question_evaluations"] = [
            _copy_plain(evaluation, STUDENT_EVALUATION_FIELDS)
            for evaluation in evaluations
            if isinstance(evaluation, dict)
        ]

    overall = feedback.get("overall_performance_analysis")
    if isinstance(overall, dict):
        safe["overall_performance_analysis"] = overall

    recommendations = feedback.get("recommendations")
    if isinstance(recommendations, dict):
        for_student = _copy_plain(recommendations, ("for_student",))
        if for_student.get("for_student") is not None:
            safe["recommendations"] = for_student

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


def student_safe_formatted_grade(stored):
    """What a student is shown of `StudentSubmission.formatted_grade`.

    The column is text. The formatting task assigns it a dictionary, so
    the row holds that dictionary in Python's text form, and that text is
    what the student's page has always received. This reads the text back
    as a Python literal (`ast.literal_eval`: read, never run), copies the
    student's sections by name, and returns the result in the same text
    form. Nothing is copied that is not named here: not `for_teacher`, not
    `follow_up_actions`, not a section added later; and under a copied name
    only plain values pass (`_copy_plain`).

    Anything that cannot be taken apart is shown as NOTHING (None): text
    that is not a Python literal (plain words, JSON with null or true),
    a literal that is not a dictionary, text over the size limit, text the
    reader fails on for any reason. Text we cannot take apart cannot be
    shown to be free of what is written for the teacher (SM ruling,
    2026-10-06).

    A limit this cannot remove: a sentence written TO the student may
    itself restate a flag or, in a row formatted before H-127, the second
    opinion. The formatter was sent them."""
    if isinstance(stored, str):
        if len(stored) > MAX_FORMATTED_GRADE_CHARACTERS:
            return None
        try:
            formatted = ast.literal_eval(stored)
        except Exception:  # noqa: BLE001 - every failure to read is "nothing"
            return None
    else:
        # The dictionary itself, on a row object that has not been saved
        # and read back yet.
        formatted = stored
    if not isinstance(formatted, dict):
        return None

    safe: dict = {}

    summary = formatted.get("overall_performance_summary")
    if isinstance(summary, dict):
        safe["overall_performance_summary"] = _copy_plain(
            summary, STUDENT_FORMATTED_SUMMARY_FIELDS
        )

    for section in STUDENT_FORMATTED_LIST_SECTIONS:
        if isinstance(formatted.get(section), list):
            safe.update(_copy_plain(formatted, (section,)))

    breakdown = formatted.get("question_by_question_breakdown")
    if isinstance(breakdown, list):
        safe["question_by_question_breakdown"] = [
            _copy_plain(item, STUDENT_FORMATTED_QUESTION_FIELDS)
            for item in breakdown
            if isinstance(item, dict)
        ]

    recommendations = formatted.get("final_recommendations")
    if isinstance(recommendations, dict):
        safe["final_recommendations"] = _copy_plain(
            recommendations, STUDENT_FORMATTED_RECOMMENDATION_FIELDS
        )

    return str(safe)
