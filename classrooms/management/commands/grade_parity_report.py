"""Epic E1a parity run (plan 09 §4.2): every grade figure, computed both ways.

For every enrollment, assignment and course in the database, computes each
figure with the frozen legacy oracle (`classrooms/tests_e1_grade_parity.py`,
verbatim copies of the code on staging 2f00c078) and with
`classrooms.grade_service`, and prints every difference. There must be none.

READ-ONLY. It writes nothing: every read runs inside one transaction that is
rolled back at the end, so even a bug here cannot change a row.

    manage.py grade_parity_report               # the whole database
    manage.py grade_parity_report --course <id> # one course
    manage.py grade_parity_report --show 50     # print up to 50 differences per family (default 10)

Exit status 0 when every figure matches, 1 when any differs.

Figures compared:
  R1  stored final grade, per enrollment (course_grade)
  R2  released final grade, per enrollment (released_course_grade)
  R3  cross-course grade, per (teacher, student) (cross_course_grade)
  R4  assignment average, per assignment (assignment_grade)
  RR  risk input row, per submission with a percentage (risk_score_row)
  RS  weight annotation, per submission (risk_rows)
  PM  plain mean of percentages, per course and per assignment (mean_percentage)
  FG  mean of final grades, per course (mean_of_final_grades)

Removed in Epic E2, with the oracle, when the formula changes on purpose.
"""

from collections import defaultdict

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Avg, Prefetch

from assignments.models import Assignment
from classrooms import grade_service
from classrooms.models import Course, StudentCourse
from classrooms.tests_e1_grade_parity import (
    legacy_assignment_average,
    legacy_compute_final_grade,
    legacy_cross_course_grade,
    legacy_dated_score,
    legacy_released_final_grade,
    legacy_risk_points,
)
from students.models import StudentSubmission


class _Rollback(Exception):
    pass


def _same(a, b):
    return type(a) is type(b) and a == b and str(a) == str(b)


class Command(BaseCommand):
    help = (
        "Compare every grade figure computed by the legacy code and by "
        "classrooms.grade_service. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument("--course", help="Only this course id.")
        parser.add_argument(
            "--show",
            type=int,
            default=10,
            help="Differences to print per figure family (default 10).",
        )

    def handle(self, *args, **options):
        self.show = options["show"]
        self.checked = defaultdict(int)
        self.diffs = defaultdict(list)
        try:
            with transaction.atomic():
                self._run(options.get("course"))
                raise _Rollback
        except _Rollback:
            pass
        failed = self._report()
        if failed:
            raise SystemExit(1)

    def _check(self, figure, key, legacy, service):
        self.checked[figure] += 1
        if not _same(legacy, service):
            self.diffs[figure].append((key, legacy, service))

    def _run(self, course_id):
        courses = Course.objects.all()
        if course_id:
            courses = courses.filter(id=course_id)
        course_ids = list(courses.values_list("id", flat=True))
        submissions_qs = StudentSubmission.objects.filter(
            assignment__course_id__in=course_ids
        ).select_related("assignment")

        # R1, R2: per enrollment.
        enrollments = (
            StudentCourse.all_objects.filter(course_id__in=course_ids)
            .select_related("student")
            .prefetch_related(
                Prefetch(
                    "student__submissions",
                    queryset=StudentSubmission.objects.select_related("assignment"),
                )
            )
        )
        for enrollment in enrollments.iterator(chunk_size=500):
            sid, cid = enrollment.student_id, enrollment.course_id
            self._check(
                "R1",
                enrollment.id,
                legacy_compute_final_grade(sid, cid),
                grade_service.course_grade(sid, cid),
            )
            subs = list(enrollment.student.submissions.all())
            self._check(
                "R2",
                enrollment.id,
                legacy_released_final_grade(subs, cid),
                grade_service.released_course_grade(subs, cid),
            )

        # R3: per (teacher, student), over the courses that teacher teaches them.
        by_pair = defaultdict(list)
        for submission in submissions_qs.select_related("assignment__course"):
            teacher_id = submission.assignment.course.teacher_id
            by_pair[(teacher_id, submission.student_id)].append(submission)
        for key, subs in by_pair.items():
            self._check(
                "R3",
                key,
                legacy_cross_course_grade(subs),
                grade_service.cross_course_grade(subs),
            )

        # R4: per assignment, over its graded submissions.
        graded_by_assignment = defaultdict(list)
        for submission in submissions_qs.filter(
            score__isnull=False, graded_at__isnull=False
        ):
            graded_by_assignment[submission.assignment_id].append(submission)
        for assignment in Assignment.objects.filter(course_id__in=course_ids):
            graded = graded_by_assignment.get(assignment.id, [])
            self._check(
                "R4",
                assignment.id,
                legacy_assignment_average(graded, assignment),
                grade_service.assignment_grade(graded, assignment),
            )

        # RR: risk rows, per submission with a percentage.
        for submission in submissions_qs.filter(score_percentage__isnull=False):
            self._check(
                "RR",
                submission.id,
                legacy_dated_score(submission),
                grade_service.risk_score_row(submission),
            )

        # RS: the weight annotation, as SQL.
        base = StudentSubmission.objects.filter(assignment__course_id__in=course_ids)
        legacy_rows = dict(legacy_risk_points(base).values_list("id", "points"))
        for sid, points in grade_service.risk_rows(base).values_list("id", "points"):
            self._check("RS", sid, legacy_rows.pop(sid, "<missing>"), points)
        for sid, points in legacy_rows.items():
            self._check("RS", sid, points, "<missing>")

        # PM, FG: aggregates per course and per assignment.
        path = "assignments__submissions__score_percentage"
        legacy_pm = dict(courses.annotate(v=Avg(path)).values_list("id", "v"))
        for cid, value in courses.annotate(
            v=grade_service.mean_percentage(path)
        ).values_list("id", "v"):
            self._check("PM", ("course", cid), legacy_pm[cid], value)
        assignments = Assignment.objects.filter(course_id__in=course_ids)
        legacy_apm = dict(
            assignments.annotate(v=Avg("submissions__score_percentage")).values_list(
                "id", "v"
            )
        )
        for aid, value in assignments.annotate(
            v=grade_service.mean_percentage("submissions__score_percentage")
        ).values_list("id", "v"):
            self._check("PM", ("assignment", aid), legacy_apm[aid], value)
        legacy_fg = dict(
            courses.annotate(v=Avg("enrollments__final_grade")).values_list("id", "v")
        )
        for cid, value in courses.annotate(
            v=grade_service.mean_of_final_grades("enrollments__final_grade")
        ).values_list("id", "v"):
            self._check("FG", cid, legacy_fg[cid], value)

    FAMILIES = (
        ("R1", "Stored final grade (per enrollment)"),
        ("R2", "Final grade a student sees (per enrollment)"),
        ("R3", "Grade across a teacher's courses (per student)"),
        ("R4", "Assignment average (per assignment)"),
        ("RR", "At-risk inputs (per submission)"),
        ("RS", "Weight of each submission (per submission)"),
        ("PM", "Plain average of percentages (per course and assignment)"),
        ("FG", "Average of final grades (per course)"),
    )

    def _report(self):
        failed = False
        self.stdout.write("Grade parity: old code compared with the grade service\n")
        for figure, label in self.FAMILIES:
            diffs = self.diffs.get(figure, [])
            self.stdout.write(
                f"{figure}  {label}: {self.checked[figure]} compared, "
                f"{len(diffs)} differences"
            )
            if diffs:
                self.stdout.write(f"    first {min(len(diffs), self.show)}:")
            for key, legacy, service in diffs[: self.show]:
                self.stdout.write(f"    id {key}: old={legacy!r}  service={service!r}")
            failed = failed or bool(diffs)
        self.stdout.write(
            "\nRESULT: "
            + (
                "DIFFERENCES FOUND - do not ship; send this output to engineering"
                if failed
                else "every figure matches (0 differences)"
            )
        )
        return failed
