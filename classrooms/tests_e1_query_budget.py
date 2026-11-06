"""Epic E1a: no grade figure costs more queries than before (plan 09 §3.2).

"Query counts may go down, never up." The budgets below were captured on
5d6f246d5 (staging 2f00c078 plus the E1a expectations, before any grade
site moved): the number of queries each snapshot test runs, end to end,
on the snapshot fixture. Every snapshot test is re-run here and must stay
within its budget. Lower a budget when a change saves queries; never
raise one to make this pass.
"""

from django.db import connection
from django.test.utils import CaptureQueriesContext

from classrooms import tests_e1_snapshots as snap

BUDGET = {
    "test_school_admin_at_risk_trend": 3,
    "test_school_admin_course_overview_chart": 3,
    "test_school_admin_course_performance": 4,
    "test_school_admin_students": 5,
    "test_school_admin_summary": 18,
    "test_school_admin_teacher_detail": 15,
    "test_school_admin_teachers": 10,
    "test_school_admin_unit_performance": 14,
    "test_school_admin_weekly_summary_service": 17,
    "test_student_course_summary": 26,
    "test_student_enrollment_list_released_grade": 12,
    "test_student_overview": 24,
    "test_student_weekly_summary_service": 6,
    "test_super_admin_schools": 13,
    "test_super_admin_students": 11,
    "test_super_admin_teachers": 13,
    "test_teacher_ai_context_service": 4,
    "test_teacher_all_students": 7,
    "test_teacher_assignment_analytics": 66,
    "test_teacher_course_analytics": 46,
    "test_teacher_course_students": 16,
    "test_teacher_enrollment_list": 6,
    "test_teacher_my_students": 7,
    "test_teacher_overview": 19,
    "test_teacher_performance_stats_and_rigor": 8,
    "test_weekly_course_summary_service": 6,
}


class GradeFigureQueryBudgetTest(snap.GradeFigureSnapshotTest):
    """The snapshot tests again, each held to its query budget."""

    def _callTestMethod(self, method):
        with CaptureQueriesContext(connection) as ctx:
            method()
        name = self._testMethodName
        self.assertIn(name, BUDGET, f"{name} has no query budget; add one")
        self.assertLessEqual(
            len(ctx.captured_queries),
            BUDGET[name],
            f"{name} ran {len(ctx.captured_queries)} queries, budget "
            f"{BUDGET[name]} (captured before the E1a moves)",
        )
