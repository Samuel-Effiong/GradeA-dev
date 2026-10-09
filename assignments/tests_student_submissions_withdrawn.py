"""H-220: a WITHDRAWN student is listed on the assignment detail only if they
submitted for THIS assignment.

Decision of the founder (2026-10-09): on the teacher's assignment detail, the
`student_submissions` list shows a withdrawn student only when that student has
a submission for the assignment being viewed; otherwise the student is left
out. Pending (and every other status) stays as it was, and the order of H-213
is kept. The detail serializer has no totals beside the list: the number of
"SUBMITTED" rows must equal the number of submissions of the assignment.
"""

from datetime import timedelta

from django.utils import timezone

from assignments.models import Assignment
from assignments.tests_student_submissions_list_order import Fixture
from classrooms.models import EnrollmentStatusType, StudentCourse
from students.models import StudentSubmission

ANSWERS = [{"question_number": 1, "answer_html": "An answer."}]


class WithdrawnFixture(Fixture):
    def enrol(self, index, status, age_minutes=0):
        student = self.students[index]
        row = StudentCourse.objects.create(
            student=student, course=self.course, enrollment_status=status
        )
        # a defined, distinct created_at: later index = older enrolment
        StudentCourse.objects.filter(pk=row.pk).update(
            created_at=timezone.now() - timedelta(days=1, minutes=age_minutes)
        )
        return student

    def submit(self, student, assignment=None):
        return StudentSubmission.objects.create(
            assignment=assignment or self.assignment, student=student, answers=ANSWERS
        )

    def other_assignment(self):
        return Assignment.objects.create(
            title="Other",
            course=self.course,
            questions=[{"question_number": 1, "points": 10}],
        )


class AWithdrawnStudentIsShownOnlyIfTheySubmitted(WithdrawnFixture):
    def test_w1_withdrawn_with_a_submission_for_this_assignment_is_listed(self):
        kept = self.enrol(0, EnrollmentStatusType.WITHDRAWN)
        submission = self.submit(kept)
        self.enrol(1, EnrollmentStatusType.ENROLLED, 1)

        rows = self.rows()

        self.assertEqual(len(rows), 2)
        by_name = {row["name"]: row for row in rows}
        mine = by_name[self.name_of(kept)]
        self.assertEqual(mine["submission_status"], "SUBMITTED")
        self.assertEqual(mine["submission_id"], submission.id)
        # no totals beside the list: its SUBMITTED rows are the submissions
        self.assertEqual(
            sum(r["submission_status"] == "SUBMITTED" for r in rows),
            StudentSubmission.objects.filter(assignment=self.assignment).count(),
        )

    def test_w2_withdrawn_without_a_submission_is_left_out(self):
        gone = self.enrol(0, EnrollmentStatusType.WITHDRAWN)
        present = self.enrol(1, EnrollmentStatusType.ENROLLED, 1)

        rows = self.rows()

        self.assertEqual([row["name"] for row in rows], [self.name_of(present)])
        self.assertNotIn(self.name_of(gone), [row["name"] for row in rows])

    def test_w3_a_submission_for_another_assignment_does_not_count(self):
        gone = self.enrol(0, EnrollmentStatusType.WITHDRAWN)
        self.submit(gone, self.other_assignment())
        present = self.enrol(1, EnrollmentStatusType.ENROLLED, 1)

        rows = self.rows()

        self.assertEqual([row["name"] for row in rows], [self.name_of(present)])


class EveryoneElseIsUnchanged(WithdrawnFixture):
    """Controls: green before and after the change."""

    def test_c1_enrolled_pending_and_completed_are_listed_with_or_without_one(self):
        statuses = [
            EnrollmentStatusType.ENROLLED,
            EnrollmentStatusType.PENDING,
            EnrollmentStatusType.COMPLETED,
        ]
        names = set()
        for i, status in enumerate(statuses):
            names.add(self.name_of(self.enrol(i, status, i)))
        submitted = self.submit(self.students[1])

        rows = self.rows()

        self.assertEqual(len(rows), 3)
        self.assertEqual({row["name"] for row in rows}, names)
        by_name = {row["name"]: row for row in rows}
        self.assertEqual(
            by_name[self.name_of(self.students[1])]["submission_id"], submitted.id
        )
        self.assertIsNone(by_name[self.name_of(self.students[0])]["submission_id"])

    def test_c2_the_defined_order_holds_with_a_withdrawn_student_among_them(self):
        # oldest enrolment first: s0 (withdrawn, submitted), s1, s2 (withdrawn,
        # none: out), s3
        self.enrol(0, EnrollmentStatusType.WITHDRAWN, 0)
        self.submit(self.students[0])
        self.enrol(1, EnrollmentStatusType.ENROLLED, -1)
        self.enrol(2, EnrollmentStatusType.WITHDRAWN, -2)
        self.enrol(3, EnrollmentStatusType.PENDING, -3)
        expected = [self.name_of(self.students[i]) for i in (0, 1, 3)]

        got = [row["name"] for row in self.rows()]

        self.assertEqual(len(got), 3)
        self.assertEqual(got, expected)
