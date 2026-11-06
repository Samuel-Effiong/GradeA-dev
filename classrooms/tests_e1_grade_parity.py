"""Epic E1a: the legacy grade oracle (plan 09 §4.1, R-2 "no number moves").

!!! FROZEN. DO NOT EDIT THE `legacy_*` FUNCTIONS BELOW. !!!

They are verbatim copies of every grade computation as it stood on staging
2f00c078, before Epic E1a moved them into `classrooms.grade_service`. They
are the definition of "today's numbers": the service must reproduce them
exactly (same Decimal or float, same rounding, same None cases). If a test
here fails, the service changed a number. Fix the service, never the
oracle. The oracle is retired in Epic E2, when the formula changes on
purpose (owner decision D1, 2026-11-06).

Each copy names its source site (file:line on 2f00c078).

Two kinds of test:

* `LiveSitesMatchLegacyTest`: the live entry points that survive E1a
  (`compute_final_grade`, `StudentRiskEvaluator`) against the oracle. Green
  on the base commit by construction, and green after every site move.
* `ServiceMatchesLegacy*Test`: `classrooms.grade_service` against the
  oracle, over seeded random corpora. Red until the service exists.

A failing seed is named in the assertion message; replay it with
`E1_SEED=<n>`.
"""

import importlib
import os
import random
import uuid
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from django.db.models import Avg, Q
from django.db.models.functions import Coalesce
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

# ---------------------------------------------------------------------------
# THE ORACLE: frozen copies of 2f00c078. Do not edit.
# ---------------------------------------------------------------------------


def legacy_final_grade_from(scored):
    """classrooms/final_grade.py:19 `final_grade_from`."""
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


def legacy_weight(submission):
    """The weight ternary, written out at classrooms/final_grade.py:58-62,
    students/serializers.py:1004-1008 and :1093-1097, dashboard/services.py
    :392-396, dashboard/views.py:3324-3328, :3838-3842, :4074-4078."""
    return (
        submission.max_points
        if submission.max_points is not None
        else submission.assignment.total_points
    )


def legacy_compute_final_grade(student_id, course_id):
    """R1: classrooms/signals.py:186 `compute_final_grade`."""
    scored = (
        StudentSubmission.objects.filter(
            student_id=student_id,
            assignment__course_id=course_id,
            graded_at__isnull=False,
            score__isnull=False,
        )
        .annotate(points=Coalesce("max_points", "assignment__total_points"))
        .filter(points__gt=0)
        .values_list("score", "points")
    )
    return legacy_final_grade_from(scored)


def legacy_released_final_grade(submissions, course_id):
    """R2: classrooms/final_grade.py:45 `released_final_grade`."""
    return legacy_final_grade_from(
        (
            submission.score,
            (
                submission.max_points
                if submission.max_points is not None
                else submission.assignment.total_points
            ),
        )
        for submission in submissions
        if submission.assignment.course_id == course_id
        and submission.is_published
        and submission.graded_at is not None
    )


def legacy_cross_course_grade(cross_course_submissions):
    """R3: students/serializers.py:984 `_cross_course_grade`, fed the
    already-filtered `_cross_course_submissions(obj)`."""
    scored = (
        (
            submission.score,
            (
                submission.max_points
                if submission.max_points is not None
                else submission.assignment.total_points
            ),
        )
        for submission in cross_course_submissions
        if submission.graded_at is not None and submission.score is not None
    )
    return legacy_final_grade_from(scored)


def legacy_assignment_average(graded, assignment):
    """R4: dashboard/views.py:3631-3644, without the site's `or 0` (which
    stays at the site). `graded` is already filtered to score and graded_at
    not null. Note the fallback reads the OUTER `assignment`, not
    `submission.assignment`."""
    return legacy_final_grade_from(
        (
            submission.score,
            (
                submission.max_points
                if submission.max_points is not None
                else assignment.total_points
            ),
        )
        for submission in graded
    )


def legacy_risk_points(queryset):
    """R5/R6: dashboard/services.py:1135 and :1518. Annotate only: no
    filter on points (the evaluator skips unusable rows itself)."""
    return queryset.annotate(points=Coalesce("max_points", "assignment__total_points"))


def legacy_dated_score(submission):
    """The RiskInputs row built at dashboard/services.py:388-398,
    dashboard/views.py:3318-3330, :3832-3844, :4068-4080 and
    students/serializers.py:1088-1100 (each over rows with a
    score_percentage)."""
    return (
        submission.submission_date,
        float(submission.score_percentage),
        submission.score,
        (
            submission.max_points
            if submission.max_points is not None
            else submission.assignment.total_points
        ),
    )


def legacy_rounded_mean(values):
    """dashboard/services.py:241, :483, :513, :518, :1245 and
    dashboard/views.py:4617-4619: a plain mean, rounded to 2dp with the
    builtin round() on floats, None when empty."""
    return round(sum(values) / len(values), 2) if values else None


def legacy_overview_course_means(course_subs):
    """dashboard/views.py:4578-4583 (StudentAdminDashboardView.overview),
    for a NON-EMPTY `course_subs` (released, score_percentage not null).
    Both UNROUNDED. `avg_score` divides by every row, scored or not."""
    avg_pct = sum(float(s.score_percentage) for s in course_subs) / len(course_subs)
    avg_score = sum(float(s.score) for s in course_subs if s.score is not None) / len(
        course_subs
    )
    return avg_pct, avg_score


# ---------------------------------------------------------------------------
# Corpora
# ---------------------------------------------------------------------------

SEEDS_IN_MEMORY = 300
SEEDS_DB = 40


def _seeds(count):
    pinned = os.environ.get("E1_SEED")
    if pinned is not None:
        return [int(pinned)]
    return range(count)


def _edge_value(rng, choices):
    return choices[rng.randrange(len(choices))]


def _random_score(rng, points):
    """A score with every edge the plan lists: None, negative, above the
    maximum, and the half-up rounding edge (x.xx5)."""
    roll = rng.random()
    if roll < 0.08:
        return None
    if roll < 0.13:
        return Decimal(str(-rng.randint(1, 5)))
    base = points if points and points > 0 else 10
    if roll < 0.20:
        return Decimal(str(base + rng.randint(1, 10)))
    if roll < 0.26:
        # 1/8 of the maximum: a grade ending in .5 at the third place
        # (e.g. 12.5%), where half-up and half-even differ (M6).
        return (Decimal(str(base)) / 8).quantize(Decimal("0.01"))
    return Decimal(str(round(rng.uniform(0, base), 2)))


def _random_percentage(rng, score, points):
    roll = rng.random()
    if roll < 0.1:
        return None
    if score is None or not points or points <= 0:
        return Decimal(str(round(rng.uniform(0, 100), 2)))
    pct = (Decimal(str(score)) / Decimal(str(points)) * 100).quantize(Decimal("0.01"))
    return max(Decimal("-999.99"), min(Decimal("999.99"), pct))


class _Row:
    """Attribute bag shaped like a submission row with its assignment."""

    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


def in_memory_corpus(seed):
    """Unsaved model instances: 1-4 courses, 1-30 students, 1-20
    assignments, with every edge row from plan §4.1."""
    rng = random.Random(seed)
    now = timezone.now()
    course_ids = [uuid.uuid4() for _ in range(rng.randint(1, 4))]
    assignments = []
    for _ in range(rng.randint(1, 20)):
        assignments.append(
            Assignment(
                id=uuid.uuid4(),
                course_id=_edge_value(rng, course_ids),
                total_points=_edge_value(rng, [None, 0, -5, 1, 5, 10, 20, 100, 37]),
            )
        )
    students = [uuid.uuid4() for _ in range(rng.randint(1, 30))]
    submissions = []
    for student_id in students:
        for assignment in assignments:
            if rng.random() < 0.25:
                continue
            max_points = _edge_value(rng, [None, None, 0, -3, 1, 5, 10, 20, 50, 100, 8])
            weight = max_points if max_points is not None else assignment.total_points
            score = _random_score(rng, weight)
            submission = StudentSubmission(
                id=uuid.uuid4(),
                student_id=student_id,
                score=score,
                max_points=max_points,
                score_percentage=_random_percentage(rng, score, weight),
                graded_at=None if rng.random() < 0.15 else now,
                is_published=rng.random() < 0.7,
            )
            submission.assignment = assignment
            submission.submission_date = now - timedelta(days=rng.randint(0, 60))
            submissions.append(submission)
    return course_ids, assignments, students, submissions


# ---------------------------------------------------------------------------
# Live sites against the oracle (green on the base commit)
# ---------------------------------------------------------------------------


class _DbCorpusMixin:
    """Builds a small saved corpus per seed with bulk_create (no signals),
    so dozens of seeds stay cheap."""

    def build_db_corpus(self, seed):
        rng = random.Random(10_000 + seed)
        tag = f"e1s{seed}x{uuid.uuid4().hex[:6]}"
        teacher = CustomUser.objects.create_user(
            email=f"{tag}-t@e1.test",
            password="e1-parity-pass",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="T",
            last_name=tag,
            is_active=True,
        )
        term = Session.objects.create(name=f"Term {tag}", teacher=teacher)
        courses = [
            Course.objects.create(name=f"C{i} {tag}", teacher=teacher, session=term)
            for i in range(rng.randint(1, 3))
        ]
        assignments = Assignment.objects.bulk_create(
            [
                Assignment(
                    title=f"A{i} {tag}",
                    course=_edge_value(rng, courses),
                    status=AssignmentStatus.PUBLISHED,
                    total_points=_edge_value(rng, [None, 0, 1, 5, 10, 20, 100, 37]),
                )
                for i in range(rng.randint(1, 6))
            ]
        )
        students = CustomUser.objects.bulk_create(
            [
                CustomUser(
                    email=f"{tag}-s{i}@e1.test",
                    user_type=UserTypes.STUDENT,
                    first_name=f"S{i}",
                    last_name=tag,
                    is_active=True,
                )
                for i in range(rng.randint(1, 8))
            ]
        )
        enrollments = []
        for student in students:
            for course in courses:
                if rng.random() < 0.2:
                    continue
                enrollments.append(
                    StudentCourse(
                        student=student,
                        course=course,
                        enrollment_status=_edge_value(
                            rng,
                            [
                                EnrollmentStatusType.ENROLLED,
                                EnrollmentStatusType.ENROLLED,
                                EnrollmentStatusType.WITHDRAWN,
                            ],
                        ),
                        final_grade=_edge_value(
                            rng,
                            [
                                None,
                                Decimal("0.00"),
                                Decimal("100.00"),
                                Decimal(str(round(rng.uniform(0, 100), 2))),
                                Decimal(str(round(rng.uniform(0, 100), 2))),
                            ],
                        ),
                    )
                )
        StudentCourse.objects.bulk_create(enrollments)
        now = timezone.now()
        submissions = []
        for student in students:
            for assignment in assignments:
                if rng.random() < 0.25:
                    continue
                max_points = _edge_value(rng, [None, None, 0, 1, 5, 10, 20, 100, 8])
                weight = (
                    max_points if max_points is not None else assignment.total_points
                )
                score = _random_score(rng, weight)
                submissions.append(
                    StudentSubmission(
                        assignment=assignment,
                        student=student,
                        answers={"q1": "a"},
                        score=score,
                        max_points=max_points,
                        score_percentage=_random_percentage(rng, score, weight),
                        graded_at=None if rng.random() < 0.15 else now,
                        is_published=rng.random() < 0.7,
                    )
                )
        StudentSubmission.objects.bulk_create(submissions)
        return teacher, courses, assignments, students


class LiveSitesMatchLegacyTest(_DbCorpusMixin, TestCase):
    """The entry points that survive E1a, against the oracle."""

    def test_compute_final_grade_matches_legacy_r1(self):
        from classrooms.signals import compute_final_grade

        for seed in _seeds(SEEDS_DB):
            _, courses, _, students = self.build_db_corpus(seed)
            for student in students:
                for course in courses:
                    expected = legacy_compute_final_grade(student.id, course.id)
                    actual = compute_final_grade(student.id, course.id)
                    self.assertEqual(
                        (actual, str(actual)),
                        (expected, str(expected)),
                        f"seed {seed}",
                    )

    def test_risk_evaluator_average_matches_legacy_points_weighted(self):
        from dashboard.risk import RiskInputs, StudentRiskEvaluator

        for seed in _seeds(SEEDS_IN_MEMORY):
            _, _, _, submissions = in_memory_corpus(seed)
            rows = [
                legacy_dated_score(s)
                for s in submissions
                if s.score_percentage is not None
            ]
            result = StudentRiskEvaluator().evaluate(
                RiskInputs(
                    expected_assignment_count=0,
                    submitted_count=0,
                    graded_scores=rows,
                )
            )
            weighted = legacy_final_grade_from(
                (score, points) for _, _, score, points in rows
            )
            expected = float(weighted) if weighted is not None else None
            self.assertEqual(result.average_grade, expected, f"seed {seed}")


# ---------------------------------------------------------------------------
# The service against the oracle (red until classrooms/grade_service.py)
# ---------------------------------------------------------------------------


def _same(test, actual, expected, msg):
    """Equal as values AND as their str (so 80.0 vs 80.00 vs 80 differ),
    and the same type (Decimal stays Decimal, float stays float)."""
    test.assertEqual(type(actual), type(expected), msg)
    test.assertEqual(actual, expected, msg)
    test.assertEqual(str(actual), str(expected), msg)


class ServiceMatchesLegacyInMemoryTest(SimpleTestCase):
    """300 seeded corpora of unsaved rows: every in-memory figure."""

    def setUp(self):
        # importlib, not `from classrooms import grade_service`: the module
        # does not exist on the base commit, and these tests are red there.
        self.gs = importlib.import_module("classrooms.grade_service")

    def test_weight_of_matches_legacy_ternary(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            for s in in_memory_corpus(seed)[3]:
                _same(self, self.gs.weight_of(s), legacy_weight(s), f"seed {seed}")

    def test_points_weighted_matches_legacy_final_grade_from(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            submissions = in_memory_corpus(seed)[3]
            pairs = [(s.score, legacy_weight(s)) for s in submissions]
            _same(
                self,
                self.gs.points_weighted(pairs),
                legacy_final_grade_from(pairs),
                f"seed {seed}",
            )

    def test_points_weighted_edges(self):
        cases = [
            [],
            [(None, 10)],
            [(5, None)],
            [(5, 0)],
            [(5, -1)],
            [(Decimal("1.25"), 10)],  # 12.5 exactly
            [(Decimal("0.125"), 1)],  # 12.5 exactly
            [(1, 8)],  # 12.5 exactly
            [(1, 800)],  # 0.125 -> 0.13 half-up, 0.12 half-even (M6)
            [(150, 100)],  # clamp high (M7)
            [(-5, 10)],  # clamp low
            [(4, 5), (None, 100), (10, 0)],
        ]
        for pairs in cases:
            _same(
                self,
                self.gs.points_weighted(pairs),
                legacy_final_grade_from(pairs),
                repr(pairs),
            )

    def test_released_course_grade_matches_legacy_r2(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            course_ids, _, students, submissions = in_memory_corpus(seed)
            for student_id in students:
                mine = [s for s in submissions if s.student_id == student_id]
                for course_id in course_ids:
                    _same(
                        self,
                        self.gs.released_course_grade(mine, course_id),
                        legacy_released_final_grade(mine, course_id),
                        f"seed {seed}",
                    )

    def test_cross_course_grade_matches_legacy_r3(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            course_ids, _, students, submissions = in_memory_corpus(seed)
            taught = set(course_ids[: max(1, len(course_ids) - 1)])
            for student_id in students:
                mine = [
                    s
                    for s in submissions
                    if s.student_id == student_id and s.assignment.course_id in taught
                ]
                _same(
                    self,
                    self.gs.cross_course_grade(mine),
                    legacy_cross_course_grade(mine),
                    f"seed {seed}",
                )

    def test_assignment_grade_matches_legacy_r4(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            _, assignments, _, submissions = in_memory_corpus(seed)
            for assignment in assignments:
                graded = [
                    s
                    for s in submissions
                    if s.assignment is assignment
                    and s.score is not None
                    and s.graded_at is not None
                ]
                _same(
                    self,
                    self.gs.assignment_grade(graded, assignment),
                    legacy_assignment_average(graded, assignment),
                    f"seed {seed}",
                )

    def test_risk_score_row_matches_legacy_dated_score(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            for s in in_memory_corpus(seed)[3]:
                if s.score_percentage is None:
                    continue
                actual = self.gs.risk_score_row(s)
                expected = legacy_dated_score(s)
                self.assertEqual(len(actual), len(expected), f"seed {seed}")
                for a, e in zip(actual, expected, strict=True):
                    _same(self, a, e, f"seed {seed}")

    def test_counts_toward_grade(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            for s in in_memory_corpus(seed)[3]:
                staff = s.graded_at is not None and s.score is not None
                self.assertIs(self.gs.counts_toward_grade(s), staff, f"seed {seed}")
                self.assertIs(
                    self.gs.counts_toward_grade(s, released_only=True),
                    staff and s.is_published,
                    f"seed {seed}",
                )

    def test_mean_of_values_matches_legacy_rounded_mean(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            submissions = in_memory_corpus(seed)[3]
            values = [
                float(s.score_percentage)
                for s in submissions
                if s.score_percentage is not None
            ]
            for subset in (values, values[:1], values[::3], []):
                _same(
                    self,
                    self.gs.mean_of_values(subset),
                    legacy_rounded_mean(subset),
                    f"seed {seed}",
                )

    def test_unrounded_means_match_legacy_overview(self):
        for seed in _seeds(SEEDS_IN_MEMORY):
            course_ids, _, students, submissions = in_memory_corpus(seed)
            for student_id in students:
                for course_id in course_ids:
                    course_subs = [
                        s
                        for s in submissions
                        if s.student_id == student_id
                        and s.assignment.course_id == course_id
                        and s.is_published
                        and s.score_percentage is not None
                    ]
                    if not course_subs:
                        continue
                    expected_pct, expected_score = legacy_overview_course_means(
                        course_subs
                    )
                    actual_pct = self.gs.mean_of_values(
                        [float(s.score_percentage) for s in course_subs],
                        ndigits=None,
                    )
                    actual_score = self.gs.mean_of_values(
                        [float(s.score) for s in course_subs if s.score is not None],
                        ndigits=None,
                        count=len(course_subs),
                    )
                    _same(self, actual_pct, expected_pct, f"seed {seed}")
                    _same(self, actual_score, expected_score, f"seed {seed}")


class ServiceMatchesLegacyDatabaseTest(_DbCorpusMixin, TestCase):
    """40 seeded saved corpora: every queryset-shaped figure."""

    def setUp(self):
        # importlib, not `from classrooms import grade_service`: the module
        # does not exist on the base commit, and these tests are red there.
        self.gs = importlib.import_module("classrooms.grade_service")

    def test_course_grade_matches_legacy_r1(self):
        for seed in _seeds(SEEDS_DB):
            _, courses, _, students = self.build_db_corpus(seed)
            for student in students:
                for course in courses:
                    _same(
                        self,
                        self.gs.course_grade(student.id, course.id),
                        legacy_compute_final_grade(student.id, course.id),
                        f"seed {seed}",
                    )

    def test_graded_rows_matches_legacy_r1_rows(self):
        for seed in _seeds(SEEDS_DB):
            _, courses, _, students = self.build_db_corpus(seed)
            for course in courses:
                base = StudentSubmission.objects.filter(assignment__course=course)
                legacy = sorted(
                    base.filter(graded_at__isnull=False, score__isnull=False)
                    .annotate(points=Coalesce("max_points", "assignment__total_points"))
                    .filter(points__gt=0)
                    .values_list("id", "score", "points")
                )
                actual = sorted(
                    self.gs.graded_rows(base).values_list("id", "score", "points")
                )
                self.assertEqual(actual, legacy, f"seed {seed}")

    def test_risk_rows_matches_legacy_r5_r6(self):
        for seed in _seeds(SEEDS_DB):
            _, courses, _, _ = self.build_db_corpus(seed)
            base = StudentSubmission.objects.filter(assignment__course__in=courses)
            legacy = sorted(
                legacy_risk_points(base).values_list("id", "score", "points")
            )
            actual = sorted(
                self.gs.risk_rows(base).values_list("id", "score", "points")
            )
            self.assertEqual(actual, legacy, f"seed {seed}")

    def test_mean_percentage_matches_legacy_avg(self):
        """P1-P8: the plain mean, with each site's own filter kept."""
        for seed in _seeds(SEEDS_DB):
            teacher, courses, _, _ = self.build_db_corpus(seed)
            subs = StudentSubmission.objects.filter(assignment__course__in=courses)
            published = Q(graded_at__isnull=False, is_published=True)
            options: list[dict[str, Any]] = [{}, {"filter": published}]
            for kwargs in options:
                _same(
                    self,
                    subs.aggregate(v=self.gs.mean_percentage(**kwargs))["v"],
                    subs.aggregate(v=Avg("score_percentage", **kwargs))["v"],
                    f"seed {seed}",
                )
            path = "assignments__submissions__score_percentage"
            legacy = list(
                Course.objects.filter(teacher=teacher)
                .annotate(v=Avg(path))
                .order_by("id")
                .values_list("v", flat=True)
            )
            actual = list(
                Course.objects.filter(teacher=teacher)
                .annotate(v=self.gs.mean_percentage(path))
                .order_by("id")
                .values_list("v", flat=True)
            )
            self.assertEqual(actual, legacy, f"seed {seed}")

    def test_mean_of_final_grades_matches_legacy_avg(self):
        for seed in _seeds(SEEDS_DB):
            teacher, courses, _, _ = self.build_db_corpus(seed)
            enrollments = StudentCourse.objects.filter(course__in=courses)
            _same(
                self,
                enrollments.aggregate(v=self.gs.mean_of_final_grades())["v"],
                enrollments.aggregate(v=Avg("final_grade"))["v"],
                f"seed {seed}",
            )
            not_withdrawn = ~Q(enrollments__enrollment_status="WITHDRAWN")
            legacy = list(
                Course.objects.filter(teacher=teacher)
                .annotate(v=Avg("enrollments__final_grade", filter=not_withdrawn))
                .order_by("id")
                .values_list("v", flat=True)
            )
            actual = list(
                Course.objects.filter(teacher=teacher)
                .annotate(
                    v=self.gs.mean_of_final_grades(
                        "enrollments__final_grade", filter=not_withdrawn
                    )
                )
                .order_by("id")
                .values_list("v", flat=True)
            )
            self.assertEqual(actual, legacy, f"seed {seed}")
