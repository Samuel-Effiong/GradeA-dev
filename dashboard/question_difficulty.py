"""
How a class did on each question of one assignment (H-240, H-241): the
full breakdown, the hardest and easiest questions, and which students
struggled with each.

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

WHEN NOTHING IS NAMED. A question is ranked only once enough students have
been counted on it; below that one student swings the average too far for
a ranking to mean anything. How many is enough scales with the class
(H-245, the user's decision): half the students enrolled, never fewer than
MINIMUM_FLOOR nor more than MINIMUM_CAP (minimum_for_class). A fixed five
left a class of four with empty cards forever, and let a class of forty be
ranked on five papers. MINIMUM_STUDENTS is the minimum when no class size
is given. With fewer than two rankable questions, or with
every question on the same average, nothing is named hardest or easiest.

The two lists never share a question, nor an average: each holds at most
LIST_LIMIT and at most half the rankable questions, a question is
"hardest" only if some question did better and "easiest" only if some
question did worse, and a question tied with one named hardest is never
named easiest. Ties within a list fall to the question's position on the
paper, not to its number as text ("10" would otherwise sort before "2").

THE BREAKDOWN lists every question on the paper, hardest first. A question
short of the minimum is shown, marked `enough_data: false`, after the
trusted ones; a question nobody has a mark on comes last.

WHO STRUGGLED. Per question, the students who earned under half its marks
(blanks included, flagged `left_blank`), lowest first; and, separately,
the students whose answer the AI could not read - their papers need
checking, not reteaching. A paper left out for a hand-changed grade names
nobody: its per-question marks are not the grade the student holds.
"""

import math
import re
from dataclasses import dataclass, field
from html import unescape

from django.utils.html import strip_tags

from ai_processor.extraction_schemas import BLANK, REVIEW_REQUIRED_STATUSES
from ai_processor.services import AIProcessor

MINIMUM_STUDENTS = 5
#: The class-size rule's bounds: ranking never rests on fewer than 3
#: students, and never waits for more than 10.
MINIMUM_FLOOR = 3
MINIMUM_CAP = 10
LIST_LIMIT = 3
# A share of a question's marks below this is "struggled with it".
STRUGGLING_BELOW = 0.5

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


@dataclass
class _Tally:
    fractions: list = field(default_factory=list)
    not_attempted: int = 0
    struggling: list = field(default_factory=list)
    unread: list = field(default_factory=list)


def _student(submission):
    student = submission.student
    return {"student_id": student.id, "full_name": student.get_full_name()}


def minimum_for_class(class_size):
    """How many students a question must be counted on before it is
    ranked: half the class, rounded up, within [MINIMUM_FLOOR, MINIMUM_CAP]."""
    half = math.ceil(max(0, int(class_size or 0)) / 2)
    return min(MINIMUM_CAP, max(MINIMUM_FLOOR, half))


def question_difficulty(
    assignment, submissions, limit=LIST_LIMIT, minimum=MINIMUM_STUDENTS
):
    """
    How the class did on every question of `assignment` across `submissions`.

    `submissions` must already be the reporting basis: graded, and only
    students whose enrollment counts, with `student` loaded. `limit` caps
    each of the hardest/easiest lists; `minimum` is how many students a
    question must be counted on to be ranked. Returns a dict with `status`,
    `students_analysed`, `excluded_manual_grade_change`, `minimum_students`,
    `hardest`, `easiest` and `breakdown`; each list entry describes one
    question, and hardest/easiest entries are the breakdown's own entries.
    """
    paper = _paper(assignment)
    tallies = {key: _Tally() for key in paper}
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
            tally = tallies[key]
            if evaluation.get("answer_status") in REVIEW_REQUIRED_STATUSES:
                tally.unread.append(_student(submission))
                continue
            awarded = _number(evaluation.get("score_awarded"))
            available = _number(evaluation.get("max_points"))
            if awarded is None or available is None or available <= 0:
                continue
            fraction = max(0.0, min(1.0, awarded / available))
            tally.fractions.append(fraction)
            blank = _is_not_attempted(evaluation)
            if blank:
                tally.not_attempted += 1
            if fraction < STRUGGLING_BELOW:
                tally.struggling.append(
                    {
                        **_student(submission),
                        "score_awarded": awarded,
                        "max_points": available,
                        "percentage": round(fraction * 100, 2),
                        "left_blank": blank,
                    }
                )
            contributed = True
        if contributed:
            students_analysed += 1

    breakdown = []
    for key, (position, question) in paper.items():
        tally = tallies[key]
        counted = len(tally.fractions)
        breakdown.append(
            {
                "position": position,
                "question_number": question.get("question_number"),
                "question_text": _plain_text(question.get("question_text")),
                "question_type": question.get("question_type"),
                "points": question.get("points"),
                "average_percentage": (
                    round(sum(tally.fractions) / counted * 100, 2) if counted else None
                ),
                "students_counted": counted,
                "enough_data": counted >= minimum,
                "not_attempted": tally.not_attempted,
                "not_read": len(tally.unread),
                "struggling_students": sorted(
                    tally.struggling,
                    key=lambda s: (s["percentage"], s["full_name"].casefold()),
                ),
                "unread_students": sorted(
                    tally.unread, key=lambda s: s["full_name"].casefold()
                ),
            }
        )

    # Trusted questions hardest first, then those short of the minimum
    # (same order), then questions nobody has a mark on, in paper order.
    breakdown.sort(
        key=lambda q: (
            not q["enough_data"],
            q["average_percentage"] is None,
            q["average_percentage"] or 0,
            q["position"],
        )
    )

    result = {
        "status": STATUS_READY,
        "students_analysed": students_analysed,
        "excluded_manual_grade_change": excluded_manual_grade_change,
        "minimum_students": minimum,
        "hardest": [],
        "easiest": [],
        "breakdown": breakdown,
    }

    ranked = [q for q in breakdown if q["enough_data"]]
    if len(ranked) < 2:
        result["status"] = STATUS_NOT_ENOUGH_DATA
    elif min(q["average_percentage"] for q in ranked) == max(
        q["average_percentage"] for q in ranked
    ):
        result["status"] = STATUS_NO_DIFFERENCE
    else:
        result["hardest"], result["easiest"] = _extremes(ranked, limit)

    for entry in breakdown:
        del entry["position"]
    return result


def _extremes(ranked, limit):
    highest = max(q["average_percentage"] for q in ranked)
    limit = min(limit, len(ranked) // 2)
    ascending = sorted(ranked, key=lambda q: (q["average_percentage"], q["position"]))
    descending = sorted(ranked, key=lambda q: (-q["average_percentage"], q["position"]))
    hardest = [q for q in ascending if q["average_percentage"] < highest][:limit]
    # A question tied with one already named hardest is not "easy". The
    # lowest average is always among them, so this also keeps out every
    # question tied for worst.
    hard_averages = {q["average_percentage"] for q in hardest}
    easiest = [q for q in descending if q["average_percentage"] not in hard_averages][
        :limit
    ]
    return hardest, easiest
