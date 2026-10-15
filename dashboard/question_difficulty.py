"""
Which questions on one assignment a class found hardest and easiest (H-240).

THE MEASURE. A question's difficulty is the class's average share of that
question's own marks: each student's score_awarded / max_points, clamped
to [0, 1], averaged over the students counted. This is the standard item
difficulty used in exam analysis. Fractions, not raw marks: a 20-point
essay averaging 12 (60%) is harder than a 2-point MCQ averaging 2 (100%),
though raw marks would say the opposite.

WHAT IS LEFT OUT, AND WHY. Each exclusion removes a number that would
describe something other than how hard the question was:

  * A question whose answer the AI could not read or find
    (answer_status ILLEGIBLE / NOT_FOUND_IN_DOCUMENT). It is scored 0 and
    sent to the review queue, but the 0 says the scan failed, not that the
    student found the question hard. Counted separately as `not_read`.
  * A submission whose per-question marks no longer add up to its recorded
    score. The grader's total is always exactly the sum of its per-question
    marks (AIProcessor._finalize_grading_result), so a mismatch means the
    grade was changed afterwards - a teacher's override (update-grade)
    rewrites only the total, leaving the AI's per-question marks behind.
    Those marks are no longer the grade the student holds.
  * An evaluation for a question that is not on the assignment now.

A BLANK answer is kept: a student who skipped a question is part of how
hard it was. It is also counted as `not_attempted`, so a teacher can tell
"got it wrong" from "never reached it".

WHEN NOTHING IS NAMED. A question is ranked only once MINIMUM_STUDENTS have
been counted on it; below that one student swings the average too far for
a ranking to mean anything. With fewer than two rankable questions, or with
every question on the same average, nothing is named hardest or easiest.

The two lists never share a question, nor an average: each holds at most
LIST_LIMIT and at most half the rankable questions, a question is
"hardest" only if some question did better and "easiest" only if some
question did worse, and a question tied with one named hardest is never
named easiest. Ties within a list fall to the question's position on the
paper, not to its number as text ("10" would otherwise sort before "2").
"""

import re
from html import unescape

from django.utils.html import strip_tags

from ai_processor.extraction_schemas import BLANK, REVIEW_REQUIRED_STATUSES
from ai_processor.services import AIProcessor

MINIMUM_STUDENTS = 5
LIST_LIMIT = 2

STATUS_READY = "ready"
STATUS_NOT_ENOUGH_DATA = "not_enough_data"
STATUS_NO_DIFFERENCE = "no_difference"

# Totals are stored to 2dp; anything closer than this is the same mark.
_SUM_TOLERANCE = 0.01

_question_key = AIProcessor._question_number_key


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _plain_text(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", unescape(strip_tags(value))).strip()


def _paper(assignment):
    """key -> (position, the question dict) for the questions on the paper."""
    paper = {}
    for position, question in enumerate(assignment.questions or []):
        if not isinstance(question, dict):
            continue
        key = _question_key(question.get("question_number"))
        paper.setdefault(key, (position, question))
    return paper


def _evaluations(submission):
    feedback = submission.feedback
    if not isinstance(feedback, dict):
        return []
    evaluations = feedback.get("question_evaluations")
    if not isinstance(evaluations, list):
        return []
    return [e for e in evaluations if isinstance(e, dict)]


def _marks_match_score(submission, evaluations):
    """Whether the per-question marks still add up to the recorded score."""
    score = _number(submission.score)
    if score is None or not evaluations:
        return False
    total = 0.0
    for evaluation in evaluations:
        awarded = _number(evaluation.get("score_awarded"))
        if awarded is None:
            return False
        total += awarded
    return abs(total - score) <= _SUM_TOLERANCE


def _is_not_attempted(evaluation):
    status = evaluation.get("answer_status")
    if status is not None:
        return status == BLANK
    return evaluation.get("level_achieved") == "not_attempted"


def question_difficulty(assignment, submissions):
    """
    The hardest and easiest questions on `assignment` across `submissions`.

    `submissions` must already be the reporting basis: graded, and only
    students whose enrollment counts. Returns a dict with `status`,
    `students_analysed`, `excluded_manual_grade_change`, `minimum_students`,
    `hardest` and `easiest`; each list entry describes one question.
    """
    paper = _paper(assignment)
    tallies = {}
    students_analysed = 0
    excluded_manual_grade_change = 0

    for submission in submissions:
        evaluations = _evaluations(submission)
        if not evaluations:
            continue
        if not _marks_match_score(submission, evaluations):
            excluded_manual_grade_change += 1
            continue

        contributed = False
        seen = set()
        for evaluation in evaluations:
            key = _question_key(evaluation.get("question_number"))
            if key not in paper or key in seen:
                continue
            seen.add(key)
            tally = tallies.setdefault(
                key, {"fractions": [], "not_attempted": 0, "not_read": 0}
            )
            if evaluation.get("answer_status") in REVIEW_REQUIRED_STATUSES:
                tally["not_read"] += 1
                continue
            awarded = _number(evaluation.get("score_awarded"))
            available = _number(evaluation.get("max_points"))
            if awarded is None or available is None or available <= 0:
                continue
            tally["fractions"].append(max(0.0, min(1.0, awarded / available)))
            if _is_not_attempted(evaluation):
                tally["not_attempted"] += 1
            contributed = True
        if contributed:
            students_analysed += 1

    ranked = []
    for key, tally in tallies.items():
        counted = len(tally["fractions"])
        if counted < MINIMUM_STUDENTS:
            continue
        position, question = paper[key]
        ranked.append(
            {
                "position": position,
                "question_number": question.get("question_number"),
                "question_text": _plain_text(question.get("question_text")),
                "question_type": question.get("question_type"),
                "points": question.get("points"),
                "average_percentage": round(sum(tally["fractions"]) / counted * 100, 2),
                "students_counted": counted,
                "not_attempted": tally["not_attempted"],
                "not_read": tally["not_read"],
            }
        )

    result = {
        "status": STATUS_READY,
        "students_analysed": students_analysed,
        "excluded_manual_grade_change": excluded_manual_grade_change,
        "minimum_students": MINIMUM_STUDENTS,
        "hardest": [],
        "easiest": [],
    }

    if len(ranked) < 2:
        result["status"] = STATUS_NOT_ENOUGH_DATA
        return result

    lowest = min(q["average_percentage"] for q in ranked)
    highest = max(q["average_percentage"] for q in ranked)
    if lowest == highest:
        result["status"] = STATUS_NO_DIFFERENCE
        return result

    limit = min(LIST_LIMIT, len(ranked) // 2)
    ascending = sorted(ranked, key=lambda q: (q["average_percentage"], q["position"]))
    descending = sorted(ranked, key=lambda q: (-q["average_percentage"], q["position"]))
    hardest = [q for q in ascending if q["average_percentage"] < highest][:limit]
    # A question tied with one already named hardest is not "easy".
    hard_averages = {q["average_percentage"] for q in hardest}
    easiest = [
        q
        for q in descending
        if q["average_percentage"] > lowest
        and q["average_percentage"] not in hard_averages
    ][:limit]

    for entry in hardest + easiest:
        entry.pop("position", None)
    result["hardest"] = hardest
    result["easiest"] = easiest
    return result
