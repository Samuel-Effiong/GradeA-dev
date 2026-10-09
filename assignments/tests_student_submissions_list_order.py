"""H-213: the assignment detail lists its students in the ONE defined order.

`AssignmentDetailSerializer.get_student_submissions` walked the course's
enrolments with no ORDER BY, so the rows came back in whatever order the
database's plan returned (Verifier 2's note on H-196). H-196 defined the order
every list of enrolments uses (`created_at`, then `id`, in
`StudentCourse.objects.in_list_order()`); this list now uses it too.

What does NOT change, and is tested as such: WHICH rows appear. The query
includes every enrolment of the course whatever its status (a withdrawn student
has a row, as before); only the order is new.

How the order is made observable (as in H-196): rows are inserted in one order
and given created_at values (or one shared created_at and chosen ids) so that
the DEFINED order is the REVERSE of the insertion order, which is what the
database returns for a small table. Complete lists are compared and their length
is asserted first.
"""

import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from assignments.models import Assignment
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import UserTypes

User = get_user_model()
# N must stay below 7: the tie test (O2) relies on PostgreSQL sorting fewer
# than 7 rows by insertion sort, which keeps equal keys in input order, so a
# sort on created_at alone returns the rows in insertion order (Verifier 1).
N = 6


def make_user(email, user_type, first_name):
    return User.objects.create_user(
        email=email,
        password="password123",  # nosec  # pragma: allowlist secret
        user_type=user_type,
        is_active=True,
        first_name=first_name,
        last_name=user_type.title(),
        last_login=timezone.now(),
    )


class Fixture(TestCase):
    def setUp(self):
        self.teacher = make_user("h213-t@x.test", UserTypes.TEACHER, "Teacher")
        self.term = Session.objects.create(name="Term", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Course", teacher=self.teacher, session=self.term
        )
        self.assignment = Assignment.objects.create(
            title="Essay",
            course=self.course,
            questions=[{"question_number": 1, "points": 10}],
        )
        self.students = [
            make_user(f"h213-s{i}@x.test", UserTypes.STUDENT, f"Student{i}")
            for i in range(N)
        ]

    def name_of(self, student):
        return student.get_full_name()

    def enrol_in_reverse_created_order(self):
        """Rows inserted 0..N-1; created_at makes N-1..0 the defined order."""
        base = timezone.now() - timedelta(days=1)
        rows = []
        for i, student in enumerate(self.students):
            row = StudentCourse.objects.create(
                student=student,
                course=self.course,
                enrollment_status=EnrollmentStatusType.ENROLLED,
            )
            StudentCourse.objects.filter(pk=row.pk).update(
                created_at=base - timedelta(minutes=i)
            )
            rows.append(row)
        return [self.name_of(row.student) for row in reversed(rows)]

    def enrol_tied_with_descending_ids(self):
        """Rows inserted with ids N..1 and ONE shared created_at; the defined
        order is by id, ascending: the reverse of insertion."""
        shared = timezone.now() - timedelta(days=1)
        inserted = []
        for i, student in enumerate(self.students):
            inserted.append(
                StudentCourse.objects.create(
                    id=uuid.UUID(int=N - i),
                    student=student,
                    course=self.course,
                    enrollment_status=EnrollmentStatusType.ENROLLED,
                )
            )
        StudentCourse.objects.filter(course=self.course).update(created_at=shared)
        return [
            self.name_of(row.student) for row in sorted(inserted, key=lambda r: r.id)
        ]

    def rows(self):
        client = APIClient()
        client.force_authenticate(self.teacher)
        # a fresh query every time: no cached answer can stand in for the order
        with patch.object(cache, "get", lambda *a, **k: None), patch.object(
            cache, "set", lambda *a, **k: True
        ):
            response = client.get(
                reverse("assignment-detail", kwargs={"pk": self.assignment.pk})
            )
        self.assertEqual(response.status_code, 200)
        return response.data["student_submissions"]


class TheListIsInTheDefinedOrder(Fixture):
    def test_o1_students_are_listed_by_enrolment_created_at_oldest_first(self):
        expected = self.enrol_in_reverse_created_order()

        got = [row["name"] for row in self.rows()]

        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)

    def test_o2_a_created_at_tie_is_broken_by_the_enrolment_id(self):
        expected = self.enrol_tied_with_descending_ids()

        got = [row["name"] for row in self.rows()]

        self.assertEqual(len(got), N)
        self.assertEqual(got, expected)


class WhichRowsAppearIsUnchanged(Fixture):
    """Controls: green before and after the change."""

    def test_c1_every_enrolment_has_a_row_whatever_its_status(self):
        expected_names = set()
        statuses = [
            EnrollmentStatusType.ENROLLED,
            EnrollmentStatusType.PENDING,
            EnrollmentStatusType.WITHDRAWN,
        ]
        for i, student in enumerate(self.students[:3]):
            StudentCourse.objects.create(
                student=student, course=self.course, enrollment_status=statuses[i]
            )
            expected_names.add(self.name_of(student))

        got = [row["name"] for row in self.rows()]

        # the withdrawn student's row is there, as before this change
        self.assertEqual(len(got), 3)
        self.assertEqual(set(got), expected_names)

    def test_c2_a_students_row_still_describes_that_students_submission(self):
        self.enrol_in_reverse_created_order()
        submitter = self.students[2]
        submission = StudentSubmission.objects.create(
            assignment=self.assignment,
            student=submitter,
            answers=[{"question_number": 1, "answer_html": "An answer."}],
        )

        rows = self.rows()

        self.assertEqual(len(rows), N)
        by_name = {row["name"]: row for row in rows}
        mine = by_name[self.name_of(submitter)]
        self.assertEqual(mine["submission_status"], "SUBMITTED")
        self.assertEqual(mine["submission_id"], submission.id)
        others = [row for name, row in by_name.items() if name != mine["name"]]
        self.assertEqual(len(others), N - 1)
        self.assertTrue(
            all(row["submission_status"] == "NOT SUBMITTED" for row in others)
        )
        self.assertTrue(all(row["submission_id"] is None for row in others))
