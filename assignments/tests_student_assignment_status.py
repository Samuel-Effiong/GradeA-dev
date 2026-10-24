"""Tests for `assignments/services.py::get_student_assignment_status` and its
three call sites, previously duplicated (and drifted) across
`assignments/serializers.py`, `classrooms/serializers.py` and
`dashboard/views.py`.

`assignments/serializers.py::AssignmentListStudentSerializer.get_status` had
a real bug this unification fixes: it checked
`submission and not submission.graded_at` for the SUBMITTED case, so a
submission graded but not yet released (`is_published=False`) fell through
to OVERDUE/"NOT SUBMITTED" instead of SUBMITTED. `TestSharedFunction` proves
the fixed logic directly; `TestAssignmentsSerializerCallSite` proves the
same case through the actual endpoint that used to get it wrong.

This is also an intentional, documented API contract change: all three
sites now return "NOT SUBMITTED" (with the space) where two of them used to
return "PENDING". `docs/evidence/unify-assignment-status/EVIDENCE.md` calls
this out explicitly as a breaking response-value change.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.services import get_student_assignment_status
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

PAST = timezone.now() - timedelta(days=2)
FUTURE = timezone.now() + timedelta(days=2)


def make_user(kind, email):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=kind,
        first_name="Status",
        last_name=kind.title(),
        is_active=True,
    )


def make_course(teacher, suffix=""):
    session = Session.objects.create(name=f"Term{suffix}", teacher=teacher)
    return Course.objects.create(
        name=f"Course{suffix}", teacher=teacher, session=session, is_active=True
    )


def enrol(student, course):
    return StudentCourse.objects.create(
        student=student, course=course, enrollment_status=EnrollmentStatusType.ENROLLED
    )


def make_submission(assignment, student, *, graded_at=None, is_published=False):
    return StudentSubmission.objects.create(
        assignment=assignment,
        student=student,
        answers={"q1": "a"},
        score=75,
        score_percentage=75,
        graded_at=graded_at,
        is_published=is_published,
    )


class TestSharedFunction(TestCase):
    """The four states `get_student_assignment_status` must produce."""

    def setUp(self):
        self.teacher = make_user(UserTypes.TEACHER, "status-teacher@example.com")
        self.course = make_course(self.teacher)
        self.student = make_user(UserTypes.STUDENT, "status-student@example.com")

    def _assignment(self, due_date=None):
        return Assignment.objects.create(
            title="A",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=due_date,
        )

    def test_no_submission_and_not_yet_due_is_not_submitted(self):
        assignment = self._assignment(due_date=FUTURE)
        self.assertEqual(
            get_student_assignment_status(assignment, None), "NOT SUBMITTED"
        )

    def test_no_submission_and_no_due_date_is_not_submitted(self):
        assignment = self._assignment(due_date=None)
        self.assertEqual(
            get_student_assignment_status(assignment, None), "NOT SUBMITTED"
        )

    def test_no_submission_past_due_is_overdue(self):
        assignment = self._assignment(due_date=PAST)
        self.assertEqual(get_student_assignment_status(assignment, None), "OVERDUE")

    def test_submitted_not_yet_graded_is_submitted(self):
        assignment = self._assignment(due_date=PAST)
        submission = make_submission(assignment, self.student)
        self.assertEqual(
            get_student_assignment_status(assignment, submission), "SUBMITTED"
        )

    def test_graded_but_not_released_is_submitted_not_graded(self):
        """The bug this unification fixes: a graded-but-unpublished
        submission must read as SUBMITTED, not fall through to
        OVERDUE/NOT SUBMITTED."""
        assignment = self._assignment(due_date=PAST)
        submission = make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=False
        )
        self.assertEqual(
            get_student_assignment_status(assignment, submission), "SUBMITTED"
        )

    def test_graded_and_released_is_graded(self):
        assignment = self._assignment(due_date=PAST)
        submission = make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=True
        )
        self.assertEqual(
            get_student_assignment_status(assignment, submission), "GRADED"
        )

    def test_a_submission_always_beats_an_overdue_due_date(self):
        """Whether the assignment is overdue is irrelevant once a
        submission exists - due_date is only consulted in the no-submission
        branch."""
        assignment = self._assignment(due_date=PAST)
        submission = make_submission(assignment, self.student)
        self.assertEqual(
            get_student_assignment_status(assignment, submission), "SUBMITTED"
        )


class TestAssignmentsSerializerCallSite(APITestCase):
    """assignments/serializers.py::AssignmentListStudentSerializer.get_status
    via GET /assignments/ (assignment-list) as a student."""

    def setUp(self):
        self.teacher = make_user(UserTypes.TEACHER, "as-teacher@example.com")
        self.course = make_course(self.teacher, "-as")
        self.student = make_user(UserTypes.STUDENT, "as-student@example.com")
        enrol(self.student, self.course)
        self.client.force_authenticate(self.student)

    def _list(self):
        response = self.client.get(reverse("assignment-list"))
        return {row["title"]: row for row in response.data["results"]}

    def test_graded_but_not_released_is_submitted_not_overdue(self):
        """The previously-buggy case: a submission graded but not
        published used to report OVERDUE/PENDING here instead of
        SUBMITTED."""
        assignment = Assignment.objects.create(
            title="Held back",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=False
        )

        rows = self._list()
        self.assertEqual(rows["Held back"]["status"], "SUBMITTED")

    def test_no_submission_past_due_is_overdue(self):
        Assignment.objects.create(
            title="Overdue one",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        rows = self._list()
        self.assertEqual(rows["Overdue one"]["status"], "OVERDUE")

    def test_no_submission_not_due_is_not_submitted(self):
        """The new label - this endpoint used to return "PENDING" here."""
        Assignment.objects.create(
            title="Not due yet",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=FUTURE,
        )
        rows = self._list()
        self.assertEqual(rows["Not due yet"]["status"], "NOT SUBMITTED")

    def test_graded_and_released_is_graded(self):
        assignment = Assignment.objects.create(
            title="Released",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=True
        )
        rows = self._list()
        self.assertEqual(rows["Released"]["status"], "GRADED")


class TestClassroomsSerializerCallSite(APITestCase):
    """classrooms/serializers.py::StudentCourseDetailSerializer.get_assignments
    via GET /student-course/<pk>/ (student-course-detail)."""

    def setUp(self):
        self.teacher = make_user(UserTypes.TEACHER, "cs-teacher@example.com")
        self.course = make_course(self.teacher, "-cs")
        self.student = make_user(UserTypes.STUDENT, "cs-student@example.com")
        self.enrollment = enrol(self.student, self.course)
        self.client.force_authenticate(self.student)

    def _assignments(self):
        response = self.client.get(
            reverse("student-course-detail", kwargs={"pk": self.enrollment.pk})
        )
        return {row["title"]: row for row in response.data["assignments"]}

    def test_graded_but_not_released_is_submitted(self):
        assignment = Assignment.objects.create(
            title="Held back",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=False
        )
        rows = self._assignments()
        self.assertEqual(rows["Held back"]["status"], "SUBMITTED")

    def test_no_submission_not_due_is_not_submitted(self):
        """The new label - this endpoint used to return "PENDING" here."""
        Assignment.objects.create(
            title="Not due yet",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=FUTURE,
        )
        rows = self._assignments()
        self.assertEqual(rows["Not due yet"]["status"], "NOT SUBMITTED")

    def test_no_submission_past_due_is_overdue(self):
        Assignment.objects.create(
            title="Overdue one",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        rows = self._assignments()
        self.assertEqual(rows["Overdue one"]["status"], "OVERDUE")

    def test_graded_and_released_is_graded(self):
        assignment = Assignment.objects.create(
            title="Released",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=True
        )
        rows = self._assignments()
        self.assertEqual(rows["Released"]["status"], "GRADED")


class TestDashboardViewCallSite(APITestCase):
    """dashboard/views.py::StudentAdminDashboardView.assignments via
    GET /student-admin/dashboard/assignments/ (student-assignments). This
    site already used "NOT SUBMITTED" and already had the correct
    submission-first condition order, so nothing here changes its observed
    behaviour - it now goes through the shared function instead of its own
    inline copy."""

    def setUp(self):
        self.teacher = make_user(UserTypes.TEACHER, "db-teacher@example.com")
        self.course = make_course(self.teacher, "-db")
        self.student = make_user(UserTypes.STUDENT, "db-student@example.com")
        enrol(self.student, self.course)
        self.client.force_authenticate(self.student)

    def _rows(self):
        response = self.client.get(reverse("student-assignments"))
        return {row["title"]: row for row in response.data["results"]}

    def test_graded_but_not_released_is_submitted(self):
        assignment = Assignment.objects.create(
            title="Held back",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=False
        )
        rows = self._rows()
        self.assertEqual(rows["Held back"]["submission_status"], "SUBMITTED")

    def test_no_submission_not_due_is_not_submitted(self):
        Assignment.objects.create(
            title="Not due yet",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=FUTURE,
        )
        rows = self._rows()
        self.assertEqual(rows["Not due yet"]["submission_status"], "NOT SUBMITTED")

    def test_no_submission_past_due_is_overdue(self):
        Assignment.objects.create(
            title="Overdue one",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        rows = self._rows()
        self.assertEqual(rows["Overdue one"]["submission_status"], "OVERDUE")

    def test_graded_and_released_is_graded(self):
        assignment = Assignment.objects.create(
            title="Released",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            due_date=PAST,
        )
        make_submission(
            assignment, self.student, graded_at=timezone.now(), is_published=True
        )
        rows = self._rows()
        self.assertEqual(rows["Released"]["submission_status"], "GRADED")
