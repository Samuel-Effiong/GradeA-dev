"""Teacher-level rigor aggregation for the school-admin dashboard and digest.

assignments/rigor.py scores a single assignment. This module rolls those
scores up to a teacher, adds the `evidence` component (which lives in
submission data, not on the assignment), and blends the three into the
headline number.

Two entry points, one shared implementation:

    build_rigor_by_teacher(teacher_ids)  -> {teacher_id: payload}
    build_rigor_for_teacher(teacher_id)  -> payload

The bulk form answers for every teacher in a school in **two queries total**,
regardless of teacher count. The previous implementation ran two aggregate
queries per teacher inside a Python loop, on top of re-deriving the metric
from raw point values on every request.

Both return the same payload shape::

    {
        "score": 3.4,        # blended headline, 0-5, or None
        "demand": 4.2,       # mean Bloom's level across scored assignments
        "evidence": 2.1,     # inverted achieved score, or None below sample floor
        "standards": 3.8,    # rubric coverage on open-ended questions, or None
        "coverage": 0.94,    # share of assignments carrying usable Bloom's data
        "assignments_scored": 12,
        "submissions_scored": 340,
    }
"""

from django.db.models import Avg, Count, Q

from assignments.models import Assignment, AssignmentStatus
from assignments.rigor import compose_rigor, compute_evidence
from students.models import StudentSubmission

#: `evidence` is a sample statistic, so it needs a sample. Below this many
#: graded submissions across a teacher's whole body of work the average is
#: noise and we report None rather than a number that will swing wildly.
#: `demand` has no equivalent floor: it is measured from the questions
#: themselves, not sampled, so one assignment's demand is genuinely that
#: assignment's demand.
MIN_GRADED_SUBMISSIONS = 5

#: Draft assignments were never given to students, so they are not evidence of
#: anything a teacher asked of anyone. Everything else (published and
#: unpublished, i.e. published-then-withdrawn) counts.
SCOREABLE_STATUSES = [
    status for status in AssignmentStatus.values if status != AssignmentStatus.DRAFT
]


# --- Interpretation thresholds -------------------------------------------
#
# A bare 0-5 number tells a head teacher nothing, and comparing two of them
# actively misleads: a teacher who sets harder questions but marks generously
# can score *below* one who sets easier questions marked honestly. So every
# payload carries a plain-language verdict derived from the same components,
# and that verdict -- not the number -- is what the weekly email leads with.

#: Demand at or above this is Analyze/Evaluate/Create territory.
DEMAND_HIGH = 3.0
#: Demand below this is mostly Remember/Understand -- recall, not thinking.
DEMAND_LOW = 2.0

#: evidence = 5 * (1 - avg%/100), so it runs *opposite* to student scores.
#: Below 1.0 means the class averages over 80% -- almost nobody was stretched.
EVIDENCE_EASY = 1.0
#: Above 3.0 means the class averages under 40% -- students are not coping.
EVIDENCE_STRUGGLING = 3.0

#: Standards below this means fewer than half the open-ended questions carry
#: a usable rubric.
STANDARDS_WEAK = 2.5

#: Below this share of scoreable assignments, say so rather than implying the
#: verdict covers the teacher's whole body of work.
COVERAGE_THIN = 0.5


def describe_rigor(demand, evidence, standards, coverage):
    """Translate the components into words a non-specialist can act on.

    Returns ``label`` (a few words for a table cell), ``meaning`` (one plain
    sentence), ``tone`` (good / watch / concern / unknown, for colour), and
    two optional caveats. Kept beside the scoring so the API, the HTML email
    and the plain-text email all say the same thing.
    """
    standards_note = None
    if standards is not None and standards < STANDARDS_WEAK:
        standards_note = (
            "Most open-ended questions have no marking rubric, so grades on "
            "them are hard to justify."
        )

    coverage_note = None
    if coverage is not None and coverage < COVERAGE_THIN:
        coverage_note = (
            "Based on a minority of this teacher's assignments -- the rest "
            "have no difficulty data recorded."
        )

    def result(label, meaning, tone):
        return {
            "label": label,
            "meaning": meaning,
            "tone": tone,
            "standards_note": standards_note,
            "coverage_note": coverage_note,
        }

    if demand is None:
        return result(
            "Not enough data yet",
            "This teacher has no assignments with difficulty data recorded, "
            "so rigor cannot be scored.",
            "unknown",
        )

    if evidence is None:
        # Questions can be judged, but nothing has been graded yet, so we
        # cannot say whether students were actually stretched.
        if demand >= DEMAND_HIGH:
            return result(
                "Demanding questions",
                "The questions ask students to analyse, judge or create. Not "
                "enough work has been graded yet to see how students cope.",
                "good",
            )
        if demand < DEMAND_LOW:
            return result(
                "Mostly recall",
                "The questions mainly ask students to remember or restate "
                "facts. Not enough work has been graded yet to see how "
                "students cope.",
                "watch",
            )
        return result(
            "Moderate demand",
            "The questions sit between recall and real analysis. Not enough "
            "work has been graded yet to see how students cope.",
            "neutral",
        )

    if evidence > EVIDENCE_STRUGGLING:
        # Low scores dominate the reading whatever the questions look like.
        if demand < DEMAND_LOW:
            return result(
                "Struggling on basics",
                "The questions mainly ask for recall, yet students are still "
                "scoring low. The issue is more likely support than "
                "difficulty.",
                "concern",
            )
        return result(
            "Very hard going",
            "Demanding questions and students are scoring low. They may need "
            "more preparation before working at this level.",
            "concern",
        )

    if demand >= DEMAND_HIGH:
        if evidence < EVIDENCE_EASY:
            return result(
                "Check the marking",
                "The questions are demanding, yet almost every student scores "
                "top marks. Worth checking whether marking is too generous or "
                "answers are circulating.",
                "watch",
            )
        return result(
            "Stretching students",
            "Demanding questions, and results show students are being pushed "
            "without being lost. This is the healthy pattern.",
            "good",
        )

    if demand < DEMAND_LOW:
        return result(
            "Too easy",
            "The questions mainly ask students to remember facts, and they "
            "score highly. There is room to ask for more analysis and "
            "judgement.",
            "watch",
        )

    if evidence < EVIDENCE_EASY:
        return result(
            "Comfortable",
            "Moderate questions that most students find straightforward. "
            "Raising the level of thinking asked for would stretch them more.",
            "watch",
        )

    return result(
        "Balanced",
        "A reasonable mix of question difficulty, with results to match.",
        "neutral",
    )


def empty_rigor_payload():
    """The shape returned for a teacher with nothing scoreable yet. Public so
    callers can fall back to it instead of an ad-hoc {}, keeping every
    consumer's key access total."""
    payload = {
        "score": None,
        "demand": None,
        "evidence": None,
        "standards": None,
        "coverage": None,
        "assignments_scored": 0,
        "submissions_scored": 0,
    }
    payload.update(describe_rigor(None, None, None, None))
    return payload


def _round(value, places=1):
    return round(value, places) if value is not None else None


def _assignment_rollup(teacher_ids):
    """One query: per-teacher means of the denormalized assignment columns.

    Avg() ignores NULLs, so `demand` is the mean over assignments that could
    be scored, while `total` counts every non-draft assignment. Their ratio is
    the coverage figure -- it tells an admin "this score is based on 3 of your
    20 assignments" instead of hiding that behind a confident-looking mean.
    """
    rows = (
        Assignment.objects.filter(
            course__teacher_id__in=teacher_ids,
            status__in=SCOREABLE_STATUSES,
        )
        .values("course__teacher_id")
        .annotate(
            demand=Avg("rigor_demand"),
            standards=Avg("rigor_standards"),
            scored=Count("id", filter=Q(rigor_demand__isnull=False)),
            total=Count("id"),
        )
    )
    return {row["course__teacher_id"]: row for row in rows}


def _submission_rollup(teacher_ids):
    """One query: per-teacher mean achieved percentage over graded work.

    Restricted to submissions that were actually graded and carry a
    percentage. `score_percentage` is nullable and `score` defaults to 0.00,
    so filtering on graded_at alone would drag ungraded zeros into the average
    and turn this into an engagement metric instead of a difficulty one.
    """
    rows = (
        StudentSubmission.objects.filter(
            assignment__course__teacher_id__in=teacher_ids,
            assignment__status__in=SCOREABLE_STATUSES,
            graded_at__isnull=False,
            score_percentage__isnull=False,
        )
        .values("assignment__course__teacher_id")
        .annotate(
            avg_percentage=Avg("score_percentage"),
            graded=Count("id"),
        )
    )
    return {row["assignment__course__teacher_id"]: row for row in rows}


def build_rigor_by_teacher(teacher_ids):
    """Rigor payloads for many teachers in two queries.

    Every requested id is present in the result, so callers never need to
    guard on a missing key -- teachers with no assignments get the empty
    payload rather than being absent.
    """
    teacher_ids = list(teacher_ids)
    if not teacher_ids:
        return {}

    assignments = _assignment_rollup(teacher_ids)
    submissions = _submission_rollup(teacher_ids)

    results = {}
    for teacher_id in teacher_ids:
        assignment_row = assignments.get(teacher_id) or {}
        submission_row = submissions.get(teacher_id) or {}

        demand = assignment_row.get("demand")
        standards = assignment_row.get("standards")
        scored = assignment_row.get("scored") or 0
        total = assignment_row.get("total") or 0

        graded = submission_row.get("graded") or 0
        if graded >= MIN_GRADED_SUBMISSIONS:
            evidence = compute_evidence(submission_row.get("avg_percentage"))
        else:
            evidence = None

        coverage = (scored / total) if total else None

        results[teacher_id] = {
            "score": _round(compose_rigor(demand, evidence, standards)),
            "demand": _round(demand),
            "evidence": _round(evidence),
            "standards": _round(standards),
            "coverage": _round(coverage, 2),
            "assignments_scored": scored,
            "submissions_scored": graded,
            # Verdict from the unrounded components, so the words never
            # disagree with the numbers because of a rounding boundary.
            **describe_rigor(demand, evidence, standards, coverage),
        }

    return results


def build_rigor_for_teacher(teacher_id):
    """Single-teacher wrapper over build_rigor_by_teacher.

    Exists so the teacher_detail view and the teacher_performance list share
    one definition of the metric and cannot drift apart.
    """
    return build_rigor_by_teacher([teacher_id]).get(teacher_id) or empty_rigor_payload()
