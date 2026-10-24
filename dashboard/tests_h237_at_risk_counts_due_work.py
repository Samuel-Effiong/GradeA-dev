"""H-237: at_risk counts only submissions to published work that is due.

The user's decision (2026-10-14): the "submitted" figure of the at-risk rate
counts only submissions to work a student could be expected to have done by
now (published, and due or undated), the same work the "expected" figure
counts. An early submission to work not yet due, or a submission to a draft,
can no longer hide a missed assignment. The average, the trend and every
displayed count are unchanged (the SM's ruling).

Every at-risk computation follows the one rule: my-students, all-students,
the per-course students action, the session overview's at_risk_students, the
school's weekly at-risk set (digest, summary count, daily alert), and the
teaching data given to the teacher's AI assistant. The weekly course summary
already counted only due work; s1 pins it.

The cases (dashboard/risk.py: at risk = one critical flag or two moderate):
  user's example  2 due, 1 submitted at 65%, plus 1 early submission.
                  Today 2/2 and 65 (one flag): not at risk. Now 1/2 = 0.5
                  (below 0.7) and 65 (below 70): two flags, at risk.
  critical        3 due, 1 submitted at 90%, plus 2 early submissions.
                  Today 3/3: not at risk. Now 1/3, below 0.5 with 2 or more
                  expected: critical, at risk.
  drafts          3 due, 1 submitted at 90%, plus submissions to 2 drafts.
                  Today 3/3: not at risk. Now 1/3: critical, at risk.
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from AutoGrader.cache_generation import SCOPE_USER, versioned_key
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
)
from dashboard.services import (
    SchoolAdminWeeklySummaryService,
    TeacherAIContextService,
    WeeklyCourseSummaryService,
)
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MY_STUDENTS = reverse("student-course-my-students")


@override_settings(CACHES=LOCMEM)
class AtRiskCountsOnlyDueWorkTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="H237 School")
        self.teacher = self._user(UserTypes.TEACHER, "Tess")
        self.term = Session.objects.create(name="H237 term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="H237 course", teacher=self.teacher, session=self.term
        )
        self.wren = self._user(UserTypes.STUDENT, "Wren")
        StudentCourse.objects.create(
            student=self.wren,
            course=self.course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )
        self.client.force_authenticate(self.teacher)

    # --- fixture ------------------------------------------------------------

    def _user(self, kind, first):
        return CustomUser.objects.create_user(
            email=f"h237-{first.lower()}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name="H237",
            is_active=True,
            school=self.school,
        )

    def _assignment(self, assignment_status=AssignmentStatus.PUBLISHED, days=-1):
        return Assignment.objects.create(
            title=f"{assignment_status} {days}",
            course=self.course,
            status=assignment_status,
            total_points=100,
            due_date=timezone.now() + timedelta(days=days),
        )

    def _submit(self, assignment, pct=None):
        graded = pct is not None
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=self.wren,
            answers={},
            score=pct,
            score_percentage=pct,
            max_points=100 if graded else None,
            submission_date=timezone.now(),
            graded_at=timezone.now() if graded else None,
            is_published=graded,
        )

    def _users_example(self):
        due = self._assignment()
        self._assignment()
        self._submit(due, 65)
        self._submit(self._assignment(days=3))  # early: not due yet

    def _critical(self):
        due = self._assignment()
        self._assignment()
        self._assignment()
        self._submit(due, 90)
        self._submit(self._assignment(days=3))
        self._submit(self._assignment(days=4))

    def _drafts(self):
        due = self._assignment()
        self._assignment()
        self._assignment()
        self._submit(due, 90)
        self._submit(self._assignment(AssignmentStatus.DRAFT))
        self._submit(self._assignment(AssignmentStatus.DRAFT))

    # --- reading each site --------------------------------------------------

    def _get(self, url, **params):
        cache.clear()
        response = self.client.get(url, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response

    def _my_students(self, **params):
        rows = self._get(MY_STUDENTS, **params).data["results"]
        return {r["id"]: r for r in rows}

    def _my_row(self):
        return self._my_students()[str(self.wren.id)]

    def _all_students_url(self):
        return reverse(
            "teacher-admin-all-students", kwargs={"session_id": self.term.id}
        )

    def _all_students(self, **params):
        rows = self._get(self._all_students_url(), **params).data["results"]
        return {r["student_id"]: r for r in rows}

    def _all_row(self):
        return self._all_students()[str(self.wren.id)]

    def _course_row(self):
        url = reverse("teacher-admin-students", kwargs={"course_id": self.course.id})
        rows = self._get(url).data["results"]
        [row] = [r for r in rows if str(r["student_id"]) == str(self.wren.id)]
        return row

    def _overview_ids(self):
        url = reverse("teacher-admin-overview", kwargs={"session_id": self.term.id})
        return {str(r["student_id"]) for r in self._get(url).data["at_risk_students"]}

    def _ai_row(self):
        rows = TeacherAIContextService().build(self.teacher)["students"]
        [row] = [r for r in rows if r["student"] == "Wren H237"]
        return row

    # --- my-students ----------------------------------------------------------

    def test_m1_my_students_the_users_example_is_at_risk(self):
        self._users_example()

        self.assertTrue(self._my_row()["at_risk"])

    def test_m2_my_students_early_submissions_cannot_hide_missed_work(self):
        self._critical()

        self.assertTrue(self._my_row()["at_risk"])

    def test_m3_my_students_submissions_to_drafts_do_not_count(self):
        self._drafts()

        self.assertTrue(self._my_row()["at_risk"])

    def test_m4_my_students_the_at_risk_filter_follows(self):
        self._users_example()

        self.assertEqual(set(self._my_students(at_risk="true")), {str(self.wren.id)})

    # --- all-students -------------------------------------------------------

    def test_a1_all_students_the_users_example_is_at_risk(self):
        self._users_example()

        self.assertTrue(self._all_row()["at_risk"])

    def test_a2_all_students_early_submissions_cannot_hide_missed_work(self):
        self._critical()

        self.assertTrue(self._all_row()["at_risk"])

    def test_a3_all_students_submissions_to_drafts_do_not_count(self):
        self._drafts()

        self.assertTrue(self._all_row()["at_risk"])

    def test_a4_all_students_the_at_risk_filter_follows(self):
        self._users_example()

        self.assertEqual(set(self._all_students(at_risk="true")), {str(self.wren.id)})

    def test_a5_control_all_students_shown_submitted_count_is_unchanged(self):
        """Green before and after: the displayed count keeps every
        submission; only the risk rate changes."""
        self._users_example()

        self.assertEqual(self._all_row()["assignment_submitted"], 2)

    def test_a6_all_students_an_answer_cached_under_the_old_key_is_not_served(self):
        # A page cached before the deploy, under the key the old code used.
        self._users_example()
        old_key = versioned_key(
            f"teacheradmins:user_id__{self.teacher.id}:instance_id__{self.term.id}"
            ":view__all_students:::None:1:",
            [(SCOPE_USER, self.teacher.id)],
        )
        stale = {"count": 1, "next": None, "previous": None, "results": []}
        cache.set(old_key, stale, 60)

        response = self.client.get(self._all_students_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotEqual(response.data["results"], [])

    # --- the per-course students action ---------------------------------------

    def test_c1_course_students_the_users_example_is_at_risk(self):
        self._users_example()

        self.assertTrue(self._course_row()["at_risk"])

    def test_c2_control_course_students_shown_submitted_count_is_unchanged(self):
        """Green before and after."""
        self._users_example()

        self.assertEqual(self._course_row()["assignment_submitted"], 2)

    # --- the session overview ---------------------------------------------------

    def test_o1_overview_the_users_example_is_listed_at_risk(self):
        self._users_example()

        self.assertEqual(self._overview_ids(), {str(self.wren.id)})

    # --- the school's weekly at-risk set -------------------------------------

    def test_w1_weekly_school_set_the_users_example_is_at_risk(self):
        self._users_example()

        ids = {
            s.id
            for s in SchoolAdminWeeklySummaryService()._at_risk_students(self.school)
        }

        self.assertEqual(ids, {self.wren.id})

    # --- the teacher AI assistant's teaching data ------------------------------

    def test_i1_ai_teaching_data_the_users_example_is_at_risk(self):
        self._users_example()

        self.assertTrue(self._ai_row()["at_risk"])

    def test_i2_control_ai_teaching_data_shown_submitted_is_unchanged(self):
        """Green before and after."""
        self._users_example()

        self.assertEqual(self._ai_row()["submitted"], 2)

    # --- the weekly course summary (already right; pinned) --------------------

    def test_s1_control_course_summary_already_counts_only_due_work(self):
        """Green before and after: WeeklyCourseSummaryService counts only
        submissions to published, due work (with its own fallback to every
        published assignment when nothing is due yet, left as it is)."""
        self._users_example()

        summary = WeeklyCourseSummaryService().build_course_summary(self.course)

        self.assertEqual(
            {str(s["student_id"]) for s in summary["at_risk_students"]},
            {str(self.wren.id)},
        )
