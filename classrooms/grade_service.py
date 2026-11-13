"""Every grade figure in the product, in one place (FR-E-06, Epic E1a).

Three rules live here and nowhere else:

* WHICH submissions count toward a grade and WHAT EACH ONE WEIGHS: graded
  (`graded_at` set) and scored, weighted by the stored `max_points`, or
  else the assignment's `total_points` (H-33). A row whose weight is NULL
  or not above zero cannot be weighted and is left out. For a figure a
  student may see, the row must also be released (H-130): a student must
  not learn that a grade exists before the teacher releases it.

* The POINTS-WEIGHTED formula (`points_weighted`): sum(score) / sum(points)
  * 100, clamped to 0-100 and rounded half-up to 2dp, so a 100-point exam
  counts more than a 5-point quiz. Every final grade uses it.

* The PLAIN MEAN of percentages (`mean_percentage`, `mean_of_values`): a
  5-point quiz counts as much as a 100-point exam. This is a DIFFERENT
  formula from `points_weighted`, kept on purpose: several dashboard
  "average" figures have always used it, and Epic E1 must not move any
  number (R-2). Owner decision D1 (2026-11-06): the two are unified in
  Epic E2, when weights arrive. Until then, do not "fix" a plain-mean
  figure by switching it to `points_weighted`, and do not add a third.

`classrooms/tests_e1_grade_guard.py` fails when grade maths is written
anywhere else. If you need a new grade figure, add it here.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Avg
from django.db.models.functions import Coalesce

# --- the row and weight rule -------------------------------------------------


def weight_of(submission):
    """The weight of one submission: its stored `max_points`, or else its
    assignment's `total_points` (H-33). Returned as stored: None, zero or
    negative mean "cannot be weighted", and `points_weighted` skips it."""
    return (
        submission.max_points
        if submission.max_points is not None
        else submission.assignment.total_points
    )


def counts_toward_grade(submission, *, released_only=False):
    """Whether a submission counts toward a grade: graded and scored, and
    also released when `released_only` (the figure a student may see)."""
    if submission.graded_at is None or submission.score is None:
        return False
    return submission.is_published if released_only else True


def _with_points(queryset):
    return queryset.annotate(points=Coalesce("max_points", "assignment__total_points"))


def graded_rows(queryset):
    """The weight rule as SQL: the graded, scored rows of a submission
    queryset that can be weighted, each annotated with `points`."""
    return _with_points(
        queryset.filter(graded_at__isnull=False, score__isnull=False)
    ).filter(points__gt=0)


def risk_rows(queryset):
    """A submission queryset annotated with each row's weight as `points`,
    and NOT filtered: the risk inputs keep every row, and
    `points_weighted` skips the ones that cannot be weighted."""
    return _with_points(queryset)


# --- the formulas ------------------------------------------------------------


def points_weighted(scored):
    """The points-weighted grade that `scored` implies, or None.

    `scored` is an iterable of (score, points) pairs. A pair with no score,
    no points, or points not above zero is left out. The result is clamped
    to the documented 0-100 scale (extra credit or a negative adjustment
    must not fall outside every grade band) and rounded half-up to 2dp.
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
    clamped = max(Decimal("0"), min(Decimal("100"), raw_grade))
    return clamped.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def mean_percentage(path="score_percentage", **extra):
    """The PLAIN MEAN of percentages, as an aggregate expression (see the
    module docstring and D1: this is not `points_weighted`). Each caller
    keeps its own row filter, passed through as `filter=`."""
    return Avg(path, **extra)


def mean_of_final_grades(path="final_grade", **extra):
    """The mean of stored final grades, as an aggregate expression."""
    return Avg(path, **extra)


def mean_of_values(values, *, ndigits=2, count=None):
    """The PLAIN MEAN of already-chosen numbers, in Python, or None when
    there is nothing to divide by.

    `ndigits` rounds with the builtin round() (None: unrounded, as the
    student overview's per-course figures are). `count` divides by
    something other than len(values): the student overview's per-course
    score divides by every released row, scored or not.
    """
    values = list(values)
    denominator = len(values) if count is None else count
    if not denominator:
        return None
    mean = sum(values) / denominator
    return mean if ndigits is None else round(mean, ndigits)


# --- the figures -------------------------------------------------------------


def course_grade(student_id, course_id):
    """The stored (staff) final grade: every graded submission of the
    student's in the course, released or not."""
    from students.models import StudentSubmission

    scored = graded_rows(
        StudentSubmission.objects.filter(
            student_id=student_id, assignment__course_id=course_id
        )
    ).values_list("score", "points")
    return points_weighted(scored)


def released_course_grade(submissions, course_id):
    """The final grade a student may see for one course (H-130): the same
    arithmetic over the released submissions only. `submissions` are the
    student's, each with its assignment loaded."""
    return points_weighted(
        (submission.score, weight_of(submission))
        for submission in submissions
        if submission.assignment.course_id == course_id
        and counts_toward_grade(submission, released_only=True)
    )


def cross_course_grade(submissions):
    """One student's points-weighted grade across every submission given
    (the courses one teacher teaches them)."""
    return points_weighted(
        (submission.score, weight_of(submission))
        for submission in submissions
        if counts_toward_grade(submission)
    )


def assignment_grade(submissions, assignment):
    """One assignment's points-weighted average over its graded
    submissions. The fallback weight is `assignment`'s points."""
    return points_weighted(
        (
            submission.score,
            (
                submission.max_points
                if submission.max_points is not None
                else assignment.total_points
            ),
        )
        for submission in submissions
        if counts_toward_grade(submission)
    )


def risk_score_row(submission):
    """One `dashboard.risk.RiskInputs.graded_scores` row for a submission
    with a percentage: (submission_date, percentage, score, weight)."""
    return (
        submission.submission_date,
        float(submission.score_percentage),
        submission.score,
        weight_of(submission),
    )
