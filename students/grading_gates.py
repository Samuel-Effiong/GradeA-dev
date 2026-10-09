"""
Checks that refuse a grading request before any work starts (Epic A S6d,
FR-A-06 #7).

RUBRIC_MISSING is the founder's definition (08a §6.0, F5): the assignment
has no questions, or any question has no marking guide at all. A marking
guide is a rubric with at least one level, or a model answer; an objective
question's answer key is its model answer. A one-level rubric is a
marking guide, so it is not refused and keeps today's handling (the grader
treats a ladder of fewer than two levels as "no ladder to snap to").

Every grading path runs `ensure_gradable` in grade_engine, before the
claim, the AI call and any charge. The HTTP routes also run it before
queuing anything, so they answer 409 with nothing dispatched, and the
scheduled and automatic paths run it again when they fire, because the
rubric can be removed after the grading was scheduled.
"""

from .exceptions import RubricMissingError


def _has_marking_guide(question) -> bool:
    if not isinstance(question, dict):
        return False
    rubric = question.get("rubric")
    if isinstance(rubric, (list, tuple)) and any(
        isinstance(level, dict) for level in rubric
    ):
        return True
    model_answer = question.get("model_answer")
    return isinstance(model_answer, str) and bool(model_answer.strip())


def rubric_missing(questions) -> bool:
    """True when grading has nothing to grade against (F5)."""
    if not isinstance(questions, (list, tuple)) or not questions:
        return True
    return not all(_has_marking_guide(question) for question in questions)


def ensure_gradable(assignment) -> None:
    """Raise RubricMissingError (409) if `assignment` cannot be graded."""
    if rubric_missing(getattr(assignment, "questions", None)):
        raise RubricMissingError(
            detail=f"assignment {getattr(assignment, 'id', None)} has no marking guide"
        )
