"""H-14: the scalar-column rewrite of _at_risk_students must agree with the
object-based original on every edge case the rule has.

`reference_at_risk_students` is the original body, verbatim from beta 4b902fc.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from unittest import mock

from django.db import connection
from django.db.models import Count, Q
from django.db.models.query import QuerySet
from django.test import TestCase
from django.utils import timezone

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from dashboard.risk import RiskInputs
from dashboard.services import SchoolAdminWeeklySummaryService
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


def reference_at_risk_students(service, school):
    """Verbatim body of SchoolAdminWeeklySummaryService._at_risk_students at
    beta 4b902fc, kept as the oracle the rewrite must agree with."""
    enrollments = StudentCourse.objects.filter(
        course__teacher__school=school,
        enrollment_status=EnrollmentStatusType.ENROLLED,
        student__is_active=True,
        student__user_type=UserTypes.STUDENT,
    ).select_related("student")

    course_ids_by_student = defaultdict(set)
    students_by_id = {}
    for enrollment in enrollments:
        students_by_id[enrollment.student_id] = enrollment.student
        course_ids_by_student[enrollment.student_id].add(enrollment.course_id)

    if not students_by_id:
        return []

    all_course_ids = {
        course_id
        for course_ids in course_ids_by_student.values()
        for course_id in course_ids
    }

    due_assignment_counts = {
        row["course_id"]: row["count"]
        for row in (
            Assignment.objects.filter(
                course_id__in=all_course_ids,
                status=AssignmentStatus.PUBLISHED,
            )
            .filter(Q(due_date__isnull=True) | Q(due_date__lte=timezone.now()))
            .values("course_id")
            .annotate(count=Count("id"))
        )
    }

    submissions_by_student = defaultdict(list)
    submissions = (
        StudentSubmission.objects.filter(
            student_id__in=students_by_id.keys(),
            assignment__course_id__in=all_course_ids,
        )
        .select_related("assignment")
        .order_by("submission_date", "id")
    )
    for submission in submissions:
        submissions_by_student[submission.student_id].append(submission)

    at_risk_students = []
    for student_id, student in students_by_id.items():
        student_course_ids = course_ids_by_student[student_id]
        expected_assignment_count = sum(
            due_assignment_counts.get(course_id, 0) for course_id in student_course_ids
        )
        student_submissions = [
            submission
            for submission in submissions_by_student.get(student_id, [])
            if submission.assignment.course_id in student_course_ids
        ]
        submitted_count = len(
            {submission.assignment_id for submission in student_submissions}
        )
        # Only published, graded submissions count toward the average
        # shown to school admins (matches the prior school-wide behavior).
        graded_scores = [
            (submission.submission_date, float(submission.score_percentage))
            for submission in student_submissions
            if submission.is_published and submission.score_percentage is not None
        ]

        risk_result = service.risk_evaluator.evaluate(
            RiskInputs(
                expected_assignment_count=expected_assignment_count,
                submitted_count=submitted_count,
                graded_scores=graded_scores,
            )
        )
        if risk_result.at_risk:
            student.avg_score = risk_result.average_grade
            at_risk_students.append(student)

    at_risk_students.sort(
        key=lambda student: (
            student.avg_score is None,
            student.avg_score if student.avg_score is not None else 0.0,
        )
    )
    return at_risk_students


def make_user(email, user_type, school=None, first="F", last="L", active=True):
    return CustomUser.objects.create(
        email=email,
        user_type=user_type,
        school=school,
        first_name=first,
        last_name=last,
        is_active=active,
    )


class AtRiskEquivalenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school = School.objects.create(name="H14 School")
        cls.other_school = School.objects.create(name="H14 Other")
        cls.service = SchoolAdminWeeklySummaryService()
        past = timezone.now() - timedelta(days=5)
        future = timezone.now() + timedelta(days=5)

        def course(teacher, name):
            return Course.objects.create(
                name=name,
                teacher=teacher,
                session=Session.objects.create(name=f"S {name}", teacher=teacher),
            )

        t1 = make_user("t1@h14.test", UserTypes.TEACHER, cls.school)
        t2 = make_user("t2@h14.test", UserTypes.TEACHER, cls.other_school)
        cls.c1, cls.c2 = course(t1, "C1"), course(t1, "C2")
        cls.foreign = course(t2, "Foreign")

        def assignment(c, title, due):
            return Assignment.objects.create(
                title=title,
                course=c,
                total_points=10,
                due_date=due,
                status=AssignmentStatus.PUBLISHED,
            )

        cls.a1 = [assignment(cls.c1, f"a1-{i}", past) for i in range(4)]
        cls.a1 += [assignment(cls.c1, "a1-nodue", None)]
        cls.a2 = [assignment(cls.c2, f"a2-{i}", past) for i in range(3)]
        assignment(cls.c2, "a2-future", future)  # not yet due
        cls.af = assignment(cls.foreign, "foreign", past)

        def student(n, **kw):
            return make_user(f"s{n}@h14.test", UserTypes.STUDENT, first=f"S{n}", **kw)

        def enrol(s, c, status=EnrollmentStatusType.ENROLLED):
            StudentCourse.objects.bulk_create(
                [StudentCourse(student=s, course=c, enrollment_status=status)]
            )

        def submit(s, a, pct, published=True):
            StudentSubmission.objects.create(
                student=s,
                assignment=a,
                answers={},
                score=Decimal(pct) / 10 if pct is not None else None,
                score_percentage=Decimal(pct) if pct is not None else None,
                graded_at=timezone.now() if pct is not None else None,
                is_published=published,
            )

        # Missing work only (at risk through missing submissions).
        s_missing = student(1)
        enrol(s_missing, cls.c1)
        # Low published scores.
        s_low = student(2)
        enrol(s_low, cls.c1)
        for a in cls.a1[:4]:
            submit(s_low, a, 30)
        # Fine student: submits everything, high scores.
        s_ok = student(3)
        enrol(s_ok, cls.c1)
        for a in cls.a1:
            submit(s_ok, a, 95)
        # Two courses, mixed.
        s_two = student(4)
        enrol(s_two, cls.c1)
        enrol(s_two, cls.c2)
        for a in cls.a1[:2] + cls.a2[:1]:
            submit(s_two, a, 40)
        # Unpublished and ungraded submissions do not count as scores.
        s_unpub = student(5)
        enrol(s_unpub, cls.c1)
        for a in cls.a1[:4]:
            submit(s_unpub, a, 20, published=False)
        submit(s_unpub, cls.a1[4], None)
        # Withdrawn, inactive, wrong type and foreign-school students drop out.
        s_wd = student(6)
        enrol(s_wd, cls.c1, EnrollmentStatusType.WITHDRAWN)
        s_inactive = student(7, active=False)
        enrol(s_inactive, cls.c1)
        s_teacher_type = make_user("tt@h14.test", UserTypes.TEACHER, cls.school)
        enrol(s_teacher_type, cls.c1)
        s_foreign = student(8)
        enrol(s_foreign, cls.foreign)
        # A submission in a course the student is NOT enrolled in is ignored.
        s_stray = student(9)
        enrol(s_stray, cls.c1)
        for a in cls.a2:
            submit(s_stray, a, 10)
        # Identical scores (ties) for ordering.
        for n in (10, 11, 12):
            s = student(n)
            enrol(s, cls.c1)
            for a in cls.a1[:4]:
                submit(s, a, 50)

    def _ours(self):
        return self.service._at_risk_students(self.school)

    def test_same_students_scores_and_order_as_the_original(self):
        expected = reference_at_risk_students(self.service, self.school)
        actual = self._ours()
        self.assertGreater(len(expected), 3)  # the fixture must flag several
        self.assertEqual(
            [(s.id, s.avg_score) for s in actual],
            [(s.id, s.avg_score) for s in expected],
        )

    def test_a_student_deleted_between_the_scan_and_the_name_lookup_is_skipped(
        self,
    ):
        # The rewrite reads enrollments/submissions first and fetches the
        # students' names afterwards with in_bulk(); a student hard-deleted in
        # between must drop out, not crash the whole rebuild with KeyError.
        baseline = [(s.id, s.avg_score) for s in self._ours()]
        victim_id = baseline[0][0]
        original_in_bulk = QuerySet.in_bulk
        deleted = []

        def delete_then_lookup(queryset, *args, **kwargs):
            if not deleted:
                CustomUser.objects.filter(pk=victim_id).delete()
                deleted.append(victim_id)
            return original_in_bulk(queryset, *args, **kwargs)

        with mock.patch.object(
            QuerySet, "in_bulk", autospec=True, side_effect=delete_then_lookup
        ):
            actual = [(s.id, s.avg_score) for s in self._ours()]

        self.assertEqual(deleted, [victim_id])
        self.assertEqual(actual, [row for row in baseline if row[0] != victim_id])

    def test_names_and_the_built_payload_match(self):
        expected = reference_at_risk_students(self.service, self.school)
        actual = self._ours()
        self.assertEqual(
            [s.get_full_name() for s in actual],
            [s.get_full_name() for s in expected],
        )

    def test_returned_students_cost_no_further_queries_for_the_fields_callers_read(
        self,
    ):
        students = self._ours()
        with self.assertNumQueries(0):
            for s in students:
                _ = (s.id, s.avg_score, s.get_full_name())

    def test_query_count_is_flat_and_below_the_original(self):
        def counting_wrapper(execute, sql, params, many, context):
            calls.append(1)
            return execute(sql, params, many, context)

        def count(fn):
            with connection.execute_wrapper(counting_wrapper):
                fn()

        calls = []
        count(lambda: reference_at_risk_students(self.service, self.school))
        before = len(calls)
        calls.clear()
        count(self._ours)
        after = len(calls)
        self.assertEqual(after, before + 1)  # +1: the at-risk students' names
        # Same query count with far more rows: nothing scales with row count.
        for n in range(20, 40):
            s = make_user(
                f"x{n}@h14.test", UserTypes.STUDENT, self.school, first=f"X{n}"
            )
            StudentCourse.objects.create(student=s, course=self.c1)
        calls.clear()
        count(self._ours)
        self.assertEqual(len(calls), after)
