"""Tests for teacher-level rigor aggregation and its two consumers.

Layers:

  * TeacherRigorAggregationTest - dashboard/rigor.py roll-up correctness
  * TeacherRigorQueryCountTest  - the N+1 regression guard
  * WeeklySummaryRigorTest      - the digest payload
  * WeeklySummaryEmailRigorTest - rendered HTML + plain-text bodies
  * TeacherPerformanceAPIRigorTest - the school-admin endpoints
"""

from django.core.cache import cache
from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from dashboard.rigor import (
    MIN_GRADED_SUBMISSIONS,
    build_rigor_by_teacher,
    build_rigor_for_teacher,
    describe_rigor,
)
from dashboard.services import SchoolAdminWeeklySummaryService
from dashboard.tasks import _build_plaintext_school_admin_summary
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes


def question(
    *, points=10, blooms="Apply", qtype="OBJECTIVE", rubric_levels=0, number=1
):
    payload = {
        "question_number": number,
        "question_text": f"Question {number}",
        "question_type": qtype,
        "points": points,
        "options": [],
        "rubric": [
            {"level": f"L{i}", "description": "d", "points": float(i)}
            for i in range(rubric_levels)
        ],
        "model_answer": "",
    }
    if blooms is not None:
        payload["blooms_level"] = blooms
    return payload


class RigorSchoolFixture:
    """Shared school/teacher/course scaffolding."""

    def build_school(self, name="Rigor High"):
        self.school = School.objects.create(name=name)
        self.admin = CustomUser.objects.create_user(
            email=f"rigor-admin-{name.replace(' ', '')}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            first_name="Rigor",
            last_name="Admin",
            school=self.school,
            is_active=True,
        )

    def make_teacher(self, suffix):
        teacher = CustomUser.objects.create_user(
            email=f"rigor-teacher-{suffix}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Teacher",
            last_name=str(suffix),
            school=self.school,
            is_active=True,
        )
        session = Session.objects.create(name=f"Term {suffix}", teacher=teacher)
        course = Course.objects.create(
            name=f"Course {suffix}", teacher=teacher, session=session
        )
        return teacher, course

    def make_student(self, suffix, course):
        student = CustomUser.objects.create_user(
            email=f"rigor-student-{suffix}@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.STUDENT,
            first_name="Student",
            last_name=str(suffix),
            is_active=True,
        )
        StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        return student

    def grade(self, assignment, student, percentage, when=None):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={"q1": "a"},
            score=percentage,
            score_percentage=percentage,
            graded_at=when or timezone.now(),
        )


class RigorVerdictTest(SimpleTestCase):
    """The plain-language verdict is what the email leads with, so its
    wording and severity matter as much as the arithmetic behind it."""

    def test_no_demand_reads_as_missing_data_not_a_bad_score(self):
        verdict = describe_rigor(None, None, None, None)

        self.assertEqual(verdict["label"], "Not enough data yet")
        self.assertEqual(verdict["tone"], "unknown")

    def test_demanding_work_with_healthy_results_is_the_good_pattern(self):
        verdict = describe_rigor(4.2, 2.1, 4.0, 0.94)

        self.assertEqual(verdict["label"], "Stretching students")
        self.assertEqual(verdict["tone"], "good")

    def test_demanding_work_with_near_perfect_scores_flags_the_marking(self):
        # The case a bare number hides: harder questions than the teacher
        # above, but everyone scores ~94%.
        verdict = describe_rigor(4.4, 0.3, 3.3, 0.88)

        self.assertEqual(verdict["label"], "Check the marking")
        self.assertEqual(verdict["tone"], "watch")

    def test_recall_questions_students_still_fail_is_a_support_problem(self):
        verdict = describe_rigor(1.4, 3.8, 0.0, 0.71)

        self.assertEqual(verdict["label"], "Struggling on basics")
        self.assertEqual(verdict["tone"], "concern")
        self.assertIn("support", verdict["meaning"])

    def test_demanding_work_students_cannot_cope_with_is_a_concern(self):
        verdict = describe_rigor(4.5, 3.6, 4.0, 1.0)

        self.assertEqual(verdict["label"], "Very hard going")
        self.assertEqual(verdict["tone"], "concern")

    def test_easy_questions_with_high_scores_reads_as_too_easy(self):
        verdict = describe_rigor(1.2, 0.4, None, 1.0)

        self.assertEqual(verdict["label"], "Too easy")
        self.assertEqual(verdict["tone"], "watch")

    def test_ungraded_work_is_judged_on_questions_alone(self):
        verdict = describe_rigor(4.0, None, 4.0, 1.0)

        self.assertEqual(verdict["label"], "Demanding questions")
        self.assertIn("graded", verdict["meaning"])

    def test_missing_rubrics_are_called_out_separately(self):
        verdict = describe_rigor(4.2, 2.1, 0.5, 1.0)

        self.assertIsNotNone(verdict["standards_note"])
        self.assertIn("rubric", verdict["standards_note"])
        # ...without changing the headline verdict.
        self.assertEqual(verdict["label"], "Stretching students")

    def test_good_rubric_coverage_produces_no_note(self):
        self.assertIsNone(describe_rigor(4.2, 2.1, 4.5, 1.0)["standards_note"])

    def test_thin_coverage_is_disclosed(self):
        verdict = describe_rigor(4.2, 2.1, 4.0, 0.2)

        self.assertIsNotNone(verdict["coverage_note"])
        self.assertIsNone(describe_rigor(4.2, 2.1, 4.0, 0.9)["coverage_note"])

    def test_every_verdict_carries_a_label_meaning_and_tone(self):
        combinations = [
            (d, e, s, c)
            for d in (None, 0.5, 2.5, 4.5)
            for e in (None, 0.5, 2.0, 4.0)
            for s in (None, 1.0, 4.0)
            for c in (None, 0.3, 0.9)
        ]
        for combo in combinations:
            verdict = describe_rigor(*combo)
            self.assertTrue(verdict["label"], combo)
            self.assertTrue(verdict["meaning"].endswith("."), combo)
            self.assertIn(
                verdict["tone"],
                {"good", "watch", "concern", "neutral", "unknown"},
                combo,
            )


class TeacherRigorAggregationTest(RigorSchoolFixture, TestCase):
    def setUp(self):
        self.build_school()
        self.teacher, self.course = self.make_teacher("agg")

    def test_demand_averages_across_a_teachers_assignments(self):
        Assignment.objects.create(
            title="Recall quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Remember")],  # 0
        )
        Assignment.objects.create(
            title="Design task",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Create")],  # 5
        )

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["demand"], 2.5)
        self.assertEqual(payload["assignments_scored"], 2)
        self.assertEqual(payload["coverage"], 1.0)

    def test_draft_assignments_are_excluded(self):
        # A draft was never given to anyone, so it is not evidence of what a
        # teacher asked of students.
        Assignment.objects.create(
            title="Published",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Create")],
        )
        Assignment.objects.create(
            title="Unfinished draft",
            course=self.course,
            status=AssignmentStatus.DRAFT,
            questions=[question(blooms="Remember")],
        )

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["demand"], 5.0)
        self.assertEqual(payload["assignments_scored"], 1)

    def test_coverage_reports_the_share_of_scoreable_assignments(self):
        Assignment.objects.create(
            title="Scored",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Apply")],
        )
        for i in range(3):
            Assignment.objects.create(
                title=f"Legacy {i}",
                course=self.course,
                status=AssignmentStatus.PUBLISHED,
                questions=[question(blooms=None, number=i)],
            )

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["assignments_scored"], 1)
        self.assertEqual(payload["coverage"], 0.25)

    def test_evidence_requires_a_minimum_sample(self):
        assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Apply")],
        )
        students = [
            self.make_student(f"few-{i}", self.course)
            for i in range(MIN_GRADED_SUBMISSIONS - 1)
        ]
        for student in students:
            self.grade(assignment, student, 50)

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertIsNone(payload["evidence"])
        self.assertEqual(payload["submissions_scored"], MIN_GRADED_SUBMISSIONS - 1)
        # Demand alone still produces a score.
        self.assertEqual(payload["score"], 2.0)

    def test_evidence_appears_once_the_sample_is_large_enough(self):
        assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Apply")],  # demand 2.0
        )
        for i in range(MIN_GRADED_SUBMISSIONS):
            student = self.make_student(f"many-{i}", self.course)
            self.grade(assignment, student, 40)  # evidence = 5 * 0.6 = 3.0

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["evidence"], 3.0)
        self.assertEqual(payload["submissions_scored"], MIN_GRADED_SUBMISSIONS)
        # (0.6*2.0 + 0.25*3.0) / 0.85
        self.assertEqual(payload["score"], round((1.2 + 0.75) / 0.85, 1))

    def test_ungraded_submissions_do_not_drag_evidence_down(self):
        # score defaults to 0.00 and score_percentage is nullable; counting
        # ungraded rows would turn evidence into an engagement metric.
        assignment = Assignment.objects.create(
            title="Quiz",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Apply")],
        )
        for i in range(MIN_GRADED_SUBMISSIONS):
            student = self.make_student(f"graded-{i}", self.course)
            self.grade(assignment, student, 100)

        for i in range(20):
            student = self.make_student(f"ungraded-{i}", self.course)
            StudentSubmission.objects.create(
                assignment=assignment,
                student=student,
                answers={"q1": "a"},
                graded_at=None,
                score_percentage=None,
            )

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["submissions_scored"], MIN_GRADED_SUBMISSIONS)
        self.assertEqual(payload["evidence"], 0.0)

    def test_standards_rolls_up_rubric_coverage(self):
        Assignment.objects.create(
            title="Essay with rubric",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Create", rubric_levels=3)],
        )
        Assignment.objects.create(
            title="Essay without rubric",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Create", rubric_levels=0)],
        )

        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertEqual(payload["standards"], 2.5)

    def test_teacher_with_nothing_gets_a_null_score_not_zero(self):
        payload = build_rigor_for_teacher(self.teacher.id)

        self.assertIsNone(payload["score"])
        self.assertIsNone(payload["demand"])
        self.assertEqual(payload["assignments_scored"], 0)

    def test_every_requested_teacher_is_present_in_the_bulk_result(self):
        other, _ = self.make_teacher("absent")
        result = build_rigor_by_teacher([self.teacher.id, other.id])

        self.assertEqual(set(result), {self.teacher.id, other.id})

    def test_empty_input_returns_empty_mapping(self):
        self.assertEqual(build_rigor_by_teacher([]), {})


class TeacherRigorQueryCountTest(RigorSchoolFixture, TestCase):
    """The old implementation ran two aggregate queries per teacher inside a
    Python loop. Lock the bulk path at a constant two."""

    def setUp(self):
        self.build_school(name="Query School")

    def _seed(self, count):
        teacher_ids = []
        for i in range(count):
            teacher, course = self.make_teacher(f"q{i}")
            assignment = Assignment.objects.create(
                title=f"Quiz {i}",
                course=course,
                status=AssignmentStatus.PUBLISHED,
                questions=[question(blooms="Apply")],
            )
            student = self.make_student(f"q{i}", course)
            self.grade(assignment, student, 70)
            teacher_ids.append(teacher.id)
        return teacher_ids

    def test_bulk_rigor_is_two_queries_for_one_teacher(self):
        teacher_ids = self._seed(1)
        with self.assertNumQueries(2):
            build_rigor_by_teacher(teacher_ids)

    def test_bulk_rigor_is_still_two_queries_for_many_teachers(self):
        teacher_ids = self._seed(6)
        with self.assertNumQueries(2):
            build_rigor_by_teacher(teacher_ids)


class WeeklySummaryRigorTest(RigorSchoolFixture, TestCase):
    def setUp(self):
        self.build_school(name="Digest School")
        self.service = SchoolAdminWeeklySummaryService()
        self.teacher, self.course = self.make_teacher("digest")

        self.assignment = Assignment.objects.create(
            title="Source analysis",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Evaluate", rubric_levels=3)],
        )
        for i in range(MIN_GRADED_SUBMISSIONS):
            student = self.make_student(f"digest-{i}", self.course)
            self.grade(self.assignment, student, 50)

    def test_summary_carries_the_score_and_its_breakdown(self):
        summary = self.service.build_school_summary(self.school)
        row = summary["teacher_activity"][0]

        self.assertEqual(row["rigor"], row["rigor_breakdown"]["score"])
        self.assertEqual(row["rigor_breakdown"]["demand"], 4.0)
        self.assertEqual(row["rigor_breakdown"]["evidence"], 2.5)
        self.assertEqual(row["rigor_breakdown"]["standards"], 5.0)
        self.assertEqual(row["rigor_breakdown"]["coverage"], 1.0)

    def test_teacher_without_scoreable_work_reports_null_rigor(self):
        quiet_teacher, _ = self.make_teacher("quiet")
        summary = self.service.build_school_summary(self.school)

        row = next(
            r for r in summary["teacher_activity"] if r["id"] == quiet_teacher.id
        )
        self.assertIsNone(row["rigor"])
        self.assertEqual(row["rigor_breakdown"]["assignments_scored"], 0)

    def test_summary_query_count_is_independent_of_submission_volume(self):
        """Guards the removal of the dead
        courses__assignments__submissions prefetch, and rigor being an
        aggregate rather than a JSON re-parse: neither should read a row per
        submission, so piling on submissions must not move the query count."""
        baseline = self._count_summary_queries()

        for i in range(30):
            student = self.make_student(f"volume-{i}", self.course)
            self.grade(self.assignment, student, 65)

        self.assertEqual(self._count_summary_queries(), baseline)

    def test_rigor_costs_nothing_per_additional_teacher(self):
        """Rigor's two roll-up queries are paid once for the whole school.

        Adding teachers still costs the surrounding loop's own per-teacher
        metrics, but rigor must contribute nothing to that growth -- it used
        to add two aggregate queries per teacher on top.
        """
        before = self._count_summary_queries()
        for i in range(3):
            self.make_teacher(f"scale-{i}")
        after = self._count_summary_queries()

        per_teacher_cost = (after - before) / 3
        self.assertEqual(per_teacher_cost, self.LOOP_QUERIES_PER_IDLE_TEACHER)

    #: Cost of one *idle* teacher (no assignments, no graded submissions) in
    #: _build_teacher_activity: enrolment count, growth (current + past),
    #: assignment count, first assignment, graded count, AI confidence. The
    #: turnaround Sum is skipped because graded_count is 0. Rigor is
    #: deliberately absent from this list -- if it reappears, this number
    #: jumps by 2 per teacher and the test fails, which is the point.
    LOOP_QUERIES_PER_IDLE_TEACHER = 7

    def _count_summary_queries(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as ctx:
            self.service.build_school_summary(self.school)
        return len(ctx)


class WeeklySummaryEmailRigorTest(RigorSchoolFixture, TestCase):
    def setUp(self):
        self.build_school(name="Email School")
        self.service = SchoolAdminWeeklySummaryService()
        self.teacher, self.course = self.make_teacher("email")

        assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Evaluate", rubric_levels=3)],
        )
        for i in range(MIN_GRADED_SUBMISSIONS):
            student = self.make_student(f"email-{i}", self.course)
            self.grade(assignment, student, 50)

        self.summary = self.service.build_school_summary(self.school)

    def _render_html(self):
        return render_to_string(
            "email/weekly_school_admin_summary.html",
            context={
                "admin": self.admin,
                "school": self.school,
                "summary": self.summary,
                "ai_narrative": None,
                "overall": self.summary["overall"],
                "at_risk_students": self.summary["at_risk_students"],
                "at_risk_student_count": self.summary["at_risk_student_count"],
                "teacher_activity": self.summary["teacher_activity"],
            },
        )

    def test_html_email_leads_with_a_plain_language_verdict(self):
        html = self._render_html()

        # The words carry the meaning; the number is a footnote beneath them.
        self.assertIn("Stretching students", html)
        self.assertIn("scores 3.8/5", html)

    def test_html_email_carries_the_interpretation_guide(self):
        html = self._render_html()

        self.assertIn("What the Rigor column means", html)
        # Every verdict a reader could see is explained in the guide.
        for label in [
            "Stretching students",
            "Demanding questions",
            "Check the marking",
            "Too easy",
            "Very hard going",
            "Struggling on basics",
            "Not enough data yet",
        ]:
            self.assertIn(label, html)
        # ...as is each underlying measurement.
        for measurement in ["Demand", "Evidence", "Standards"]:
            self.assertIn(f"<strong>{measurement}</strong>", html)

    def test_html_email_warns_against_ranking_on_the_number(self):
        html = self._render_html()

        self.assertIn("Use the verdict, not the number", html)

    def test_html_email_still_gives_a_verdict_without_graded_work(self):
        # Questions can be judged even when nothing has been marked yet, so
        # the cell must not fall back to a bare dash.
        thin_teacher, thin_course = self.make_teacher("thin")
        Assignment.objects.create(
            title="Ungraded quiz",
            course=thin_course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(blooms="Create")],
        )
        self.summary = self.service.build_school_summary(self.school)
        html = self._render_html()

        self.assertIn("Demanding questions", html)

    def test_plaintext_email_matches_the_html_verdict(self):
        text = _build_plaintext_school_admin_summary(self.school, self.summary)

        self.assertIn("rigor: Stretching students (3.8/5)", text)

    def test_plaintext_email_says_so_when_rigor_is_unavailable(self):
        quiet_teacher, _ = self.make_teacher("quiet")
        summary = self.service.build_school_summary(self.school)
        text = _build_plaintext_school_admin_summary(self.school, summary)

        self.assertIn("Not enough data yet", text)


class TeacherPerformanceAPIRigorTest(RigorSchoolFixture, APITestCase):
    def setUp(self):
        self.build_school(name="API School")
        self.teacher, self.course = self.make_teacher("api")

        assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[question(qtype="ESSAY", blooms="Evaluate", rubric_levels=3)],
        )
        for i in range(MIN_GRADED_SUBMISSIONS):
            student = self.make_student(f"api-{i}", self.course)
            self.grade(assignment, student, 50)

        # Both endpoints cache for 5 minutes; without this a payload built
        # by another test in this class can satisfy the request under test.
        cache.clear()
        self.client.force_authenticate(user=self.admin)

    def test_list_endpoint_exposes_the_breakdown(self):
        url = reverse("school-admin-teacher-performance")
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        row = response.data["results"][0]

        self.assertEqual(row["rigor"], row["rigor_breakdown"]["score"])
        self.assertEqual(row["rigor_breakdown"]["demand"], 4.0)
        self.assertEqual(row["rigor_breakdown"]["evidence"], 2.5)
        self.assertEqual(row["rigor_breakdown"]["standards"], 5.0)

    def test_detail_endpoint_agrees_with_the_list(self):
        list_row = self.client.get(reverse("school-admin-teacher-performance")).data[
            "results"
        ][0]

        detail = self.client.get(
            reverse(
                "school-admin-teacher-detail",
                kwargs={"teacher_id": str(self.teacher.id)},
            )
        )

        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["rigor"], list_row["rigor"])
        self.assertEqual(detail.data["rigor_breakdown"], list_row["rigor_breakdown"])
