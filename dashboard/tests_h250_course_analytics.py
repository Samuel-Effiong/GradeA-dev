"""
H-250: the workflow figures on the teacher's course analytics
(`teacher-admin/dashboard/courses/<id>`), the owner's definitions:

  * total_assignments_assigned: published assignments.
  * total_draft_assignments: saved but not published (DRAFT and UNPUBLISHED).
  * total_enrolled_students: students whose enrollment counts (not withdrawn).
  * total_at_risk_students: those students the overview calls at risk, by
    the same rule (H-237: only published work that is due is missing).
  * total_assignments_submitted: every submission to a published
    assignment, by a student whose enrollment counts. It was the number of
    assignments with at least one submission.
  * total_assignments_graded: of those, every one with a grade, released
    to the student or not. It was the number of assignments with at least
    one graded submission.

Run with:
    python manage.py test dashboard.tests_h250_course_analytics
"""

from datetime import timedelta

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import EnrollmentStatusType
from dashboard.tests_teacher_all_students import LOCMEM, Builder
from students.models import StudentSubmission
from users.models import UserTypes

PAST = timezone.now() - timedelta(days=1)
FUTURE = timezone.now() + timedelta(days=7)


@override_settings(CACHES=LOCMEM)
class CourseWorkflowTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.term = self.session(self.teacher)
        self.course_obj = self.course(self.teacher, self.term)
        self.client.force_authenticate(self.teacher)

    def _workflow(self):
        cache.clear()
        response = self.client.get(
            reverse("teacher-admin-courses", kwargs={"course_id": self.course_obj.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["workflow"]

    def _with_status(self, assignment_status, course=None):
        return Assignment.objects.create(
            title=f"Assignment {self.uid()}",
            course=course or self.course_obj,
            status=assignment_status,
            due_date=PAST,
        )

    def _student(self, enrollment_status=None, course=None):
        student = self.user(UserTypes.STUDENT)
        self.enrol(student, course or self.course_obj, enrollment_status)
        return student

    def _class(self, at_risk, safe, course=None):
        """Two past-due assignments; `safe` students hand both in at 100%,
        `at_risk` students hand in nothing (0% submission, critical)."""
        course = course or self.course_obj
        due = [self.assignment(course, due=PAST) for _ in range(2)]
        for _ in range(at_risk):
            self._student(course=course)
        for _ in range(safe):
            student = self._student(course=course)
            for assignment in due:
                self.submit(assignment, student, 100)

    def _ungraded(self, assignment, student):
        return StudentSubmission.objects.create(
            assignment=assignment, student=student, answers={"q1": "a"}
        )

    # -- assignments -------------------------------------------------------

    def test_assigned_counts_published_assignments_only(self):
        self._with_status(AssignmentStatus.PUBLISHED)
        self._with_status(AssignmentStatus.PUBLISHED)
        self._with_status(AssignmentStatus.DRAFT)
        self._with_status(AssignmentStatus.UNPUBLISHED)
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_assigned"], 2)

    def test_drafts_count_draft_and_unpublished(self):
        self._with_status(AssignmentStatus.PUBLISHED)
        self._with_status(AssignmentStatus.DRAFT)
        self._with_status(AssignmentStatus.DRAFT)
        self._with_status(AssignmentStatus.UNPUBLISHED)
        self.assertEqual(self._workflow()["total_draft_assignments"], 3)

    def test_another_courses_assignments_are_not_counted(self):
        other = self.course(self.teacher, self.term)
        self._with_status(AssignmentStatus.PUBLISHED, course=other)
        self._with_status(AssignmentStatus.DRAFT, course=other)
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_assigned"], 0)
        self.assertEqual(workflow["total_draft_assignments"], 0)

    # -- students ----------------------------------------------------------

    def test_enrolled_counts_every_student_but_the_withdrawn(self):
        self._student()
        self._student(EnrollmentStatusType.PENDING)
        self._student(EnrollmentStatusType.COMPLETED)
        self._student(EnrollmentStatusType.WITHDRAWN)
        self._student(course=self.course(self.teacher, self.term))
        self.assertEqual(self._workflow()["total_enrolled_students"], 3)

    def test_at_risk_counts_the_courses_at_risk_students(self):
        self._class(at_risk=2, safe=3)
        self.assertEqual(self._workflow()["total_at_risk_students"], 2)

    def test_a_withdrawn_student_is_not_at_risk(self):
        student = self.at_risk_student(self.course_obj)
        student.enrollments.update(enrollment_status=EnrollmentStatusType.WITHDRAWN)
        self.assertEqual(self._workflow()["total_at_risk_students"], 0)

    def test_work_not_yet_due_or_not_published_is_not_missing(self):
        # H-237: nothing here is expected yet, so nobody is at risk.
        self._student()
        for _ in range(2):
            self.assignment(self.course_obj, due=FUTURE)
            self._with_status(AssignmentStatus.DRAFT)
        self.assertEqual(self._workflow()["total_at_risk_students"], 0)

    def test_another_courses_at_risk_students_are_not_counted(self):
        self._class(at_risk=2, safe=0, course=self.course(self.teacher, self.term))
        self.assertEqual(self._workflow()["total_at_risk_students"], 0)

    def test_at_risk_agrees_with_the_overview(self):
        self._class(at_risk=2, safe=1)
        self._class(at_risk=1, safe=0, course=self.course(self.teacher, self.term))
        response = self.client.get(
            reverse("teacher-admin-overview", kwargs={"session_id": self.term.id})
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        overview = [
            row
            for row in response.data["at_risk_students"]
            if str(row["course_id"]) == str(self.course_obj.id)
        ]
        self.assertEqual(len(overview), 2)
        self.assertEqual(self._workflow()["total_at_risk_students"], len(overview))

    # -- submissions -------------------------------------------------------

    def test_submitted_counts_every_submission_not_assignments(self):
        first = self.assignment(self.course_obj, due=PAST)
        second = self.assignment(self.course_obj, due=PAST)
        students = [self._student() for _ in range(3)]
        for student in students:
            self.submit(first, student, 80)
        self._ungraded(second, students[0])
        self.assertEqual(self._workflow()["total_assignments_submitted"], 4)

    def test_graded_counts_every_graded_submission(self):
        first = self.assignment(self.course_obj, due=PAST)
        second = self.assignment(self.course_obj, due=PAST)
        students = [self._student() for _ in range(3)]
        for student in students:
            self.submit(first, student, 80)
        self.submit(second, students[0], 70)
        self._ungraded(second, students[1])
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_submitted"], 5)
        self.assertEqual(workflow["total_assignments_graded"], 4)

    def test_a_grade_not_yet_released_is_graded(self):
        assignment = self.assignment(self.course_obj, due=PAST)
        submission = self.submit(assignment, self._student(), 60)
        submission.is_published = False
        submission.save(update_fields=["is_published"])
        self.assertEqual(self._workflow()["total_assignments_graded"], 1)

    def test_a_score_without_a_grading_time_is_not_graded(self):
        assignment = self.assignment(self.course_obj, due=PAST)
        submission = self.submit(assignment, self._student(), 60)
        submission.graded_at = None
        submission.save(update_fields=["graded_at"])
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_submitted"], 1)
        self.assertEqual(workflow["total_assignments_graded"], 0)

    def test_work_on_draft_or_unpublished_assignments_is_not_counted(self):
        student = self._student()
        for assignment_status in (AssignmentStatus.DRAFT, AssignmentStatus.UNPUBLISHED):
            self.submit(self._with_status(assignment_status), student, 90)
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_submitted"], 0)
        self.assertEqual(workflow["total_assignments_graded"], 0)

    def test_a_withdrawn_students_work_is_not_counted(self):
        assignment = self.assignment(self.course_obj, due=PAST)
        self.submit(assignment, self._student(), 90)
        self.submit(assignment, self._student(EnrollmentStatusType.WITHDRAWN), 90)
        workflow = self._workflow()
        self.assertEqual(workflow["total_assignments_submitted"], 1)
        self.assertEqual(workflow["total_assignments_graded"], 1)

    def test_work_by_a_student_not_in_the_course_is_not_counted(self):
        # A student enrolled elsewhere, never in this course.
        assignment = self.assignment(self.course_obj, due=PAST)
        stranger = self._student(course=self.course(self.teacher, self.term))
        self.submit(assignment, stranger, 90)
        self.assertEqual(self._workflow()["total_assignments_submitted"], 0)

    def test_another_courses_work_is_not_counted(self):
        other = self.course(self.teacher, self.term)
        student = self._student(course=other)
        self.submit(self.assignment(other, due=PAST), student, 90)
        self.assertEqual(self._workflow()["total_assignments_submitted"], 0)

    # -- the whole block ---------------------------------------------------

    def test_an_empty_course_is_all_zeros(self):
        self.assertEqual(
            self._workflow(),
            {
                "total_assignments_assigned": 0,
                "total_draft_assignments": 0,
                "total_enrolled_students": 0,
                "total_at_risk_students": 0,
                "total_assignments_submitted": 0,
                "total_assignments_graded": 0,
            },
        )
