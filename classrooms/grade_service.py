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

* Epic E2 (behind GRADE_CATEGORIES_ENABLED; owner Q1-Q4, SM 2026-11-18):
  grade categories, relative weights, excused and missing work. A category
  grade is sum(relative_weight * score) / sum(relative_weight * points)
  * 100, so with every relative weight 1 it IS `points_weighted`; the final
  grade is sum(weight * category grade) / sum(weight) over the categories
  with graded work (Q4). Uncategorized work counts nothing in a course with
  categories (Q3). Excused work counts on neither side; missing work
  (published, a due date set and past, no submission, not excused) counts
  as 0 only when the course says so (Q2). A course with no categories, no
  excuses and missing work not counted takes today's path unchanged, and
  so does every course while the flag is off.

`classrooms/tests_e1_grade_guard.py` fails when grade maths is written
anywhere else. If you need a new grade figure, add it here.
"""

from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any, NamedTuple, Optional

from django.conf import settings
from django.db.models import Avg, Exists, OuterRef
from django.db.models.functions import Coalesce
from django.utils import timezone

HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def categories_enabled():
    """Epic E2's switch: off, every figure is today's."""
    return bool(getattr(settings, "GRADE_CATEGORIES_ENABLED", False))


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
    return _on_scale(raw_grade)


def _on_scale(raw_grade):
    """Clamped to the documented 0-100 scale, rounded half-up to 2dp."""
    clamped = max(Decimal("0"), min(HUNDRED, raw_grade))
    return clamped.quantize(CENT, rounding=ROUND_HALF_UP)


# --- Epic E2: categories, relative weights, excused and missing work ---------


class GradeRow(NamedTuple):
    """One piece of work toward a grade: its category (None: uncategorized),
    relative weight inside it, score and points. Missing work counted as 0
    is a row with score 0 and the assignment's points."""

    category_id: Any
    relative_weight: Any
    score: Any
    points: Any


class CategoryGrade(NamedTuple):
    category_id: Any
    weight_percent: Decimal
    #: None when the category has no graded work (its weight is shared, Q4)
    grade: Optional[Decimal]
    #: Its share of the final grade, in grade points; the contributions add
    #: up to the final grade exactly (FR-E-13).
    contribution: Decimal


class GradeResult(NamedTuple):
    final: Optional[Decimal]
    categories: tuple


def _weight(value):
    return Decimal("1") if value is None else Decimal(str(value))


def _category_sums(rows):
    """sum(relative_weight * score) and sum(relative_weight * points) over
    the rows that can be weighted (as `points_weighted` skips them)."""
    num = Decimal("0")
    den = Decimal("0")
    for row in rows:
        if row.score is None or row.points is None or row.points <= 0:
            continue
        weight = _weight(row.relative_weight)
        if weight <= 0:
            continue
        num += weight * Decimal(str(row.score))
        den += weight * Decimal(str(row.points))
    return num, den


def _split_exactly(parts, total):
    """`parts` (raw Decimals, any precision) rounded to cents so that they
    add up to `total` exactly: floor each, then hand the cents left over to
    the largest remainders (ties: first listed)."""
    cents = [part * 100 for part in parts]
    floors = [c.to_integral_value(rounding=ROUND_DOWN) for c in cents]
    left = int(total * 100 - sum(floors))
    order = sorted(range(len(parts)), key=lambda i: (-(cents[i] - floors[i]), i))
    for i in order[: max(left, 0)]:
        floors[i] += 1
    return [(f / 100).quantize(CENT) for f in floors]


def weighted_grade(rows, category_weights):
    """The grade `rows` imply under a course's categories.

    `category_weights` maps category id to weight percent, in display order,
    or is None for a course with no categories (every row is one bucket).
    Rows whose category is not in it count nothing (Q3). A category with no
    graded work hands its weight to the others (Q4); if no category with a
    weight above 0 has graded work, there is no grade.
    """
    rows = list(rows)
    if category_weights is None:
        num, den = _category_sums(rows)
        if not den:
            return GradeResult(None, ())
        final = _on_scale(num / den * 100)
        return GradeResult(final, (CategoryGrade(None, HUNDRED, final, final),))

    by_category = {cid: [] for cid in category_weights}
    for row in rows:
        if row.category_id in by_category:
            by_category[row.category_id].append(row)

    raw = {}
    for cid, cat_rows in by_category.items():
        num, den = _category_sums(cat_rows)
        if den:
            raw[cid] = num / den * 100
    total_weight = sum(
        (Decimal(str(category_weights[cid])) for cid in raw), Decimal("0")
    )

    if not total_weight:
        return GradeResult(
            None,
            tuple(
                CategoryGrade(
                    cid,
                    Decimal(str(weight)),
                    _on_scale(raw[cid]) if cid in raw else None,
                    Decimal("0.00"),
                )
                for cid, weight in category_weights.items()
            ),
        )

    shares = {
        cid: Decimal(str(category_weights[cid])) * grade / total_weight
        for cid, grade in raw.items()
    }
    raw_final = sum(shares.values(), Decimal("0"))
    final = _on_scale(raw_final)
    # Scale onto the clamped final so the parts add up to what is shown.
    scale = final / raw_final if raw_final else Decimal("0")
    ids = list(shares)
    exact = dict(
        zip(ids, _split_exactly([shares[i] * scale for i in ids], final), strict=True)
    )
    return GradeResult(
        final,
        tuple(
            CategoryGrade(
                cid,
                Decimal(str(weight)),
                _on_scale(raw[cid]) if cid in raw else None,
                exact.get(cid, Decimal("0.00")),
            )
            for cid, weight in category_weights.items()
        ),
    )


class CoursePolicy(NamedTuple):
    """What a course's grade depends on beyond its graded work."""

    course_id: Any
    #: category id -> weight percent, in display order; None: no categories
    category_weights: Optional[dict]
    category_names: dict
    missing_counts_as_zero: bool


def _policy_needed(student_id, course_id):
    """One query: whether the course has categories, counts missing work or
    has excused this student. All False: today's path."""
    from classrooms.models import Course, CourseCategory
    from students.models import ExcusedWork

    return (
        Course.objects.filter(pk=course_id)
        .annotate(
            has_categories=Exists(
                CourseCategory.objects.filter(course_id=OuterRef("pk"))
            ),
            has_excuses=Exists(
                ExcusedWork.objects.filter(
                    assignment__course_id=OuterRef("pk"), student_id=student_id
                )
            ),
        )
        .values("missing_counts_as_zero", "has_categories", "has_excuses")
        .first()
    )


def course_policy(course_id, *, missing_counts_as_zero=None):
    from classrooms.models import Course, CourseCategory

    if missing_counts_as_zero is None:
        missing_counts_as_zero = bool(
            Course.objects.filter(pk=course_id)
            .values_list("missing_counts_as_zero", flat=True)
            .first()
        )
    categories = list(
        CourseCategory.objects.filter(course_id=course_id)
        .order_by("position", "name", "id")
        .values_list("id", "name", "weight_percent")
    )
    return CoursePolicy(
        course_id=course_id,
        category_weights=(
            {cid: weight for cid, _, weight in categories} if categories else None
        ),
        category_names={cid: name for cid, name, _ in categories},
        missing_counts_as_zero=missing_counts_as_zero,
    )


def excused_assignment_ids(student_id, course_ids):
    from students.models import ExcusedWork

    return set(
        ExcusedWork.objects.filter(
            student_id=student_id, assignment__course_id__in=list(course_ids)
        ).values_list("assignment_id", flat=True)
    )


def missing_rows(student_id, course_ids, excused, now=None):
    """Missing work as GradeRows (score 0), keyed by course: published, a
    due date set and past, no submission of the student's at all (handed-in
    work that is not graded yet is not missing), not excused."""
    from assignments.models import Assignment, AssignmentStatus

    now = now or timezone.now()
    found = {}
    for assignment_id, course_id, category_id, relative_weight, points in (
        Assignment.objects.filter(
            course_id__in=list(course_ids),
            status=AssignmentStatus.PUBLISHED,
            due_date__isnull=False,
            due_date__lte=now,
        )
        .exclude(submissions__student_id=student_id)
        .values_list(
            "id", "course_id", "category_id", "relative_weight", "total_points"
        )
    ):
        if assignment_id in excused:
            continue
        found.setdefault(course_id, []).append(
            GradeRow(category_id, relative_weight, Decimal("0"), points)
        )
    return found


def _scored_rows(scored, excused):
    """GradeRows from (assignment_id, category_id, relative_weight, score,
    points) tuples, leaving excused work out."""
    return [
        GradeRow(category_id, relative_weight, score, points)
        for assignment_id, category_id, relative_weight, score, points in scored
        if assignment_id not in excused
    ]


def _policy_result(policy, scored, excused, missing):
    rows = _scored_rows(scored, excused)
    if policy.missing_counts_as_zero:
        rows += missing
    return weighted_grade(rows, policy.category_weights)


def mean_percentage(path="score_percentage", **extra):
    """The average percentage of a set of submission rows, as an aggregate
    expression. Each caller keeps its own row filter, passed as `filter=`.

    D1 (owner Q1): while GRADE_CATEGORIES_ENABLED is off, the PLAIN MEAN of
    percentages (today's figure). On, the points-weighted figure over the
    same rows (those with a percentage), each weighted by its stored
    `max_points`, else its assignment's `total_points`, clamped to 0-100:
    the formula every final grade uses."""
    if not categories_enabled():
        return Avg(path, **extra)
    suffix = "score_percentage"
    if not path.endswith(suffix):
        raise ValueError(f"mean_percentage reads a score_percentage path, not {path!r}")
    return _points_weighted_aggregate(path[: -len(suffix)], extra.pop("filter", None))


def _points_weighted_aggregate(prefix, row_filter=None):
    """sum(score) / sum(points) * 100 as SQL, over the rows of `prefix`
    (a submission relation: "" or "submissions__") that have a percentage
    and can be weighted, and that pass `row_filter`."""
    from django.db.models import (
        Case,
        DecimalField,
        ExpressionWrapper,
        F,
        Q,
        Sum,
        Value,
        When,
    )
    from django.db.models.functions import NullIf
    from django.db.models.lookups import GreaterThan, LessThan

    decimal = DecimalField(max_digits=30, decimal_places=10)
    points = Coalesce(f"{prefix}max_points", f"{prefix}assignment__total_points")
    usable = (
        Q(**{f"{prefix}score_percentage__isnull": False})
        & Q(**{f"{prefix}score__isnull": False})
        & (
            Q(**{f"{prefix}max_points__gt": 0})
            | Q(
                **{
                    f"{prefix}max_points__isnull": True,
                    f"{prefix}assignment__total_points__gt": 0,
                }
            )
        )
    )
    if row_filter is not None:
        usable &= row_filter
    total_score = Sum(
        Case(When(usable, then=F(f"{prefix}score")), output_field=decimal),
        output_field=decimal,
    )
    total_points = Sum(
        Case(When(usable, then=points), output_field=decimal), output_field=decimal
    )
    ratio = ExpressionWrapper(
        total_score * 100 / NullIf(total_points, 0), output_field=decimal
    )
    # A CASE, not GREATEST/LEAST: PostgreSQL's GREATEST ignores NULL, and no
    # rows must stay NULL (as Avg gives), never 0.
    return Case(
        When(GreaterThan(ratio, 100), then=Value(HUNDRED)),
        When(LessThan(ratio, 0), then=Value(Decimal("0"))),
        default=ratio,
        output_field=decimal,
    )


def average_grade(percentages, scored, *, ndigits=2):
    """The average grade of already-chosen rows, in Python.

    D1 (owner Q1): while GRADE_CATEGORIES_ENABLED is off, the PLAIN MEAN of
    `percentages` (today's figure, `mean_of_values`). On, the points-weighted
    figure of `scored`, the (score, points) pairs of the SAME rows, as a
    float rounded to `ndigits` (None: unrounded), or None."""
    if not categories_enabled():
        return mean_of_values(percentages, ndigits=ndigits)
    total_score = Decimal("0")
    total_points = Decimal("0")
    for score, points in scored:
        if score is None or points is None or points <= 0:
            continue
        total_score += Decimal(str(score))
        total_points += Decimal(str(points))
    if not total_points:
        return None
    value = float(max(Decimal("0"), min(HUNDRED, total_score / total_points * 100)))
    return value if ndigits is None else round(value, ndigits)


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

    if categories_enabled():
        needed = _policy_needed(student_id, course_id)
        if needed and any(needed.values()):
            return _staff_result(student_id, course_id, needed).final

    scored = graded_rows(
        StudentSubmission.objects.filter(
            student_id=student_id, assignment__course_id=course_id
        )
    ).values_list("score", "points")
    return points_weighted(scored)


def _staff_result(student_id, course_id, needed=None):
    """The stored (staff) figure under the course's policy, as a
    GradeResult (the breakdown reads its categories)."""
    from students.models import StudentSubmission

    if needed is None:
        needed = _policy_needed(student_id, course_id) or {}
    policy = course_policy(
        course_id, missing_counts_as_zero=needed.get("missing_counts_as_zero", False)
    )
    excused = (
        excused_assignment_ids(student_id, [course_id])
        if needed.get("has_excuses")
        else set()
    )
    scored = graded_rows(
        StudentSubmission.objects.filter(
            student_id=student_id, assignment__course_id=course_id
        )
    ).values_list(
        "assignment_id",
        "assignment__category_id",
        "assignment__relative_weight",
        "score",
        "points",
    )
    missing = (
        missing_rows(student_id, [course_id], excused).get(course_id, [])
        if policy.missing_counts_as_zero
        else []
    )
    return _policy_result(policy, scored, excused, missing)


def released_course_grade(submissions, course_id, *, student_id=None):
    """The final grade a student may see for one course (H-130): the same
    arithmetic over the released submissions only. `submissions` are the
    student's, each with its assignment loaded. `student_id` names the
    student when the list may be empty (E2: missing work can still count)."""
    if categories_enabled():
        submissions = list(submissions)
        if student_id is None and submissions:
            student_id = submissions[0].student_id
        if student_id is not None:
            result = _released_result(submissions, course_id, student_id)
            if result is not None:
                return result.final
    return points_weighted(
        (submission.score, weight_of(submission))
        for submission in submissions
        if submission.assignment.course_id == course_id
        and counts_toward_grade(submission, released_only=True)
    )


def _released_result(submissions, course_id, student_id):
    """The released figure under the course's policy, or None when the
    course takes today's path."""
    needed = _policy_needed(student_id, course_id)
    if not needed or not any(needed.values()):
        return None
    policy = course_policy(
        course_id, missing_counts_as_zero=needed["missing_counts_as_zero"]
    )
    excused = (
        excused_assignment_ids(student_id, [course_id])
        if needed["has_excuses"]
        else set()
    )
    scored = [
        (
            submission.assignment_id,
            submission.assignment.category_id,
            submission.assignment.relative_weight,
            submission.score,
            weight_of(submission),
        )
        for submission in submissions
        if submission.assignment.course_id == course_id
        and counts_toward_grade(submission, released_only=True)
    ]
    missing = (
        missing_rows(student_id, [course_id], excused).get(course_id, [])
        if policy.missing_counts_as_zero
        else []
    )
    return _policy_result(policy, scored, excused, missing)


def cross_course_grade(submissions, *, student_id=None, course_ids=None):
    """One student's points-weighted grade across every submission given
    (the courses one teacher teaches them).

    E2: each course's row rules apply (excused work out, missing work per
    the course, uncategorized work out in a course with categories) and
    relative weights scale the rows; category weights are per course and do
    not combine across courses (SM 2026-11-18, point 7)."""
    if categories_enabled():
        submissions = list(submissions)
        if student_id is None and submissions:
            student_id = submissions[0].student_id
        if course_ids is None:
            course_ids = {s.assignment.course_id for s in submissions}
        if student_id is not None and course_ids:
            rows = _cross_course_rows(submissions, student_id, course_ids)
            if rows is not None:
                return weighted_grade(rows, None).final
    return points_weighted(
        (submission.score, weight_of(submission))
        for submission in submissions
        if counts_toward_grade(submission)
    )


def _cross_course_rows(submissions, student_id, course_ids):
    """The rows under each course's rules, or None when no course needs
    them (today's path)."""
    from classrooms.models import Course, CourseCategory

    course_ids = list(course_ids)
    with_categories = set(
        CourseCategory.objects.filter(course_id__in=course_ids)
        .values_list("course_id", flat=True)
        .distinct()
    )
    counting_missing = set(
        Course.objects.filter(
            pk__in=course_ids, missing_counts_as_zero=True
        ).values_list("pk", flat=True)
    )
    excused = excused_assignment_ids(student_id, course_ids)
    if not (with_categories or counting_missing or excused):
        return None
    rows = [
        GradeRow(
            None,
            submission.assignment.relative_weight,
            submission.score,
            weight_of(submission),
        )
        for submission in submissions
        if counts_toward_grade(submission)
        and submission.assignment_id not in excused
        and not (
            submission.assignment.course_id in with_categories
            and submission.assignment.category_id is None
        )
    ]
    if counting_missing:
        for course_id, course_rows in missing_rows(
            student_id, counting_missing, excused
        ).items():
            rows += [
                row._replace(category_id=None)
                for row in course_rows
                if not (course_id in with_categories and row.category_id is None)
            ]
    return rows


def grade_breakdown(student_id, course_id, *, released_submissions=None):
    """FR-E-13: a student's grade by category. Its contributions add up to
    the final grade the stored figure (or, given `released_submissions`, the
    released one) reports. Values are Decimals; the view renders them."""
    from assignments.models import Assignment

    policy = course_policy(course_id)
    excused = excused_assignment_ids(student_id, [course_id])
    if released_submissions is None:
        result = _staff_result(
            student_id,
            course_id,
            {
                "missing_counts_as_zero": policy.missing_counts_as_zero,
                "has_categories": bool(policy.category_weights),
                "has_excuses": bool(excused),
            },
        )
    else:
        result = _released_result(released_submissions, course_id, student_id)
        if result is None:
            final = released_course_grade(released_submissions, course_id)
            result = GradeResult(
                final,
                (
                    (CategoryGrade(None, HUNDRED, final, final),)
                    if final is not None
                    else ()
                ),
            )
    missing = missing_rows(student_id, [course_id], excused).get(course_id, [])
    has_categories = bool(policy.category_weights)
    uncategorized = (
        list(
            Assignment.objects.filter(course_id=course_id, category__isnull=True)
            .order_by("title", "id")
            .values("id", "title")
        )
        if has_categories
        else []
    )
    return {
        "final_grade": result.final,
        "categories": (
            [
                {
                    "id": str(c.category_id) if c.category_id else None,
                    "name": policy.category_names.get(c.category_id),
                    "weight_percent": c.weight_percent,
                    "grade": c.grade,
                    "contribution": c.contribution,
                    "counted": c.grade is not None,
                }
                for c in result.categories
            ]
            if has_categories
            else []
        ),
        "uncategorized": [
            {"assignment_id": str(a["id"]), "title": a["title"]} for a in uncategorized
        ],
        "excused_count": len(excused),
        "missing_count": len(missing),
        "missing_counts_as_zero": policy.missing_counts_as_zero,
    }


def preview_course_grades(course_id, proposed):
    """FR-E-12: every enrolled student's stored grade now and under
    `proposed` categories (`[{"id"?, "name", "weight_percent"}]`), and the
    class average of each. Writes nothing. An existing category keeps its
    assignments when its id is in `proposed`; one left out leaves them
    uncategorized; a new one has none yet."""
    from classrooms.models import StudentCourse
    from students.models import StudentSubmission

    policy = course_policy(course_id)
    known = {str(cid): cid for cid in policy.category_names}
    proposed_weights = {}
    for index, item in enumerate(proposed):
        key = known.get(str(item.get("id")), f"new-{index}")
        proposed_weights[key] = Decimal(str(item["weight_percent"]))
    proposed_policy = policy._replace(category_weights=proposed_weights or None)

    enrollments = list(
        StudentCourse.objects.filter(course_id=course_id)
        .active()
        .order_by("student_id")
        .values_list("student_id", "final_grade")
    )
    student_ids = [sid for sid, _ in enrollments]
    scored_by_student = {sid: [] for sid in student_ids}
    for sid, *row in graded_rows(
        StudentSubmission.objects.filter(
            student_id__in=student_ids, assignment__course_id=course_id
        )
    ).values_list(
        "student_id",
        "assignment_id",
        "assignment__category_id",
        "assignment__relative_weight",
        "score",
        "points",
    ):
        scored_by_student[sid].append(tuple(row))

    rows = []
    for sid, current in enrollments:
        excused = excused_assignment_ids(sid, [course_id])
        missing = (
            missing_rows(sid, [course_id], excused).get(course_id, [])
            if policy.missing_counts_as_zero
            else []
        )
        rows.append(
            {
                "student_id": str(sid),
                "current": current,
                "proposed": _policy_result(
                    proposed_policy, scored_by_student[sid], excused, missing
                ).final,
            }
        )

    def average(key):
        return mean_of_values(
            [row[key] for row in rows if row[key] is not None], ndigits=None
        )

    return {
        "students": rows,
        "class_average": {
            "current": _rounded(average("current")),
            "proposed": _rounded(average("proposed")),
        },
    }


def _rounded(value):
    if value is None:
        return None
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


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
