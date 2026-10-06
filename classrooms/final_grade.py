"""The course final grade's arithmetic, in one place.

A points-weighted average: sum(score) / sum(points) * 100, clamped to 0-100
and rounded to 2dp. Weighted (not a plain mean of percentages) so a
100-point exam counts more than a 5-point quiz.

`classrooms.signals.compute_final_grade` feeds it from the database, for
the stored `StudentCourse.final_grade`. Anything else that shows a final
grade must come through here too, so that two figures can never be two
formulas.
"""

from decimal import ROUND_HALF_UP, Decimal


def final_grade_from(scored):
    """The final grade that `scored` implies, or None.

    `scored` is an iterable of (score, points) pairs, one per graded
    submission. A pair with no points, or none above zero, cannot be
    weighted and is left out.
    """
    total_score = Decimal("0")
    total_points = Decimal("0")
    for score, points in scored:
        if score is None or points is None or points <= 0:
            continue
        total_score += Decimal(str(score))
        total_points += Decimal(str(points))

    if not total_points:
        return None
    raw_grade = (total_score / total_points) * 100
    # Clamp to the documented 0-100 scale so bad upstream data (extra
    # credit pushing a score over 100%, a negative adjustment) can't
    # silently fall outside every grade band in grade-distribution
    # reporting.
    clamped = max(Decimal("0"), min(Decimal("100"), raw_grade))
    return clamped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
