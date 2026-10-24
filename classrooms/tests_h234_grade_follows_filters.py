"""H-234: my-students' numbers follow the selected session, never the course.

The user's final rule (2026-10-14, superseding the earlier one these tests
first encoded): on my-students the grade, at_risk and the three counts
(total_assignments_in_course, total_assignments_submitted,
percentage_of_submission) are all computed over ONE basis: the student's
non-withdrawn enrolments (H-230) in this teacher's reachable courses within
the selected session (`?enrollments__course__session=`). The course filter
(`?enrollments__course=`) changes none of these numbers: it only narrows
which students appear. With no session the basis is the whole history.
min_grade, max_grade, at_risk and ordering filter and sort on the same
figures. "Total" keeps today's meaning, every assignment of the counted
courses (H-235 takes that definition to the user).

The basis is one helper (`classrooms.models.grade_basis_enrollments`); the
no-session basis is pinned by NO_FILTER_GRADE here.

The fixture: one teacher, two of their sessions; the student Wren is
enrolled in C1 and C3 (Term 1) and C2 (Term 2), each with one due, graded,
100-point assignment: C1 90%, C3 60%, C2 30%.
    no session    (90 + 60 + 30) / 300 = 60.00, counts over all three
    Term 1        (90 + 60) / 200      = 75.00, counts over C1 and C3
    Term 2        30.00,                 counts over C2
"""

from datetime import timedelta
from decimal import Decimal

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from students.models import StudentSubmission
from users.models import CustomUser, UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}
MY_STUDENTS = reverse("student-course-my-students")
SESSION = "enrollments__course__session"
COURSE = "enrollments__course"

#: With no session: the whole history, (90 + 60 + 30) / 300.
NO_FILTER_GRADE = Decimal("60.00")


@override_settings(CACHES=LOCMEM)
class TheNumbersFollowTheSessionTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self._user(UserTypes.TEACHER, "Tess")
        self.term1 = Session.objects.create(name="Term 1", teacher=self.teacher)
        self.term2 = Session.objects.create(name="Term 2", teacher=self.teacher)
        self.c1 = self._course("C1", self.term1)
        self.c3 = self._course("C3", self.term1)
        self.c2 = self._course("C2", self.term2)
        self.wren = self._user(UserTypes.STUDENT, "Wren")
        self.enrolment = {}
        for course, pct in ((self.c1, 90), (self.c3, 60), (self.c2, 30)):
            self.enrolment[course.pk] = self._enrol(self.wren, course)
            self._submit(self.wren, self._assignment(course), pct)
        self.client.force_authenticate(self.teacher)

    def _user(self, kind, first):
        return CustomUser.objects.create_user(
            email=f"h234-{first.lower()}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name="H234",
            is_active=True,
        )

    def _course(self, name, session):
        return Course.objects.create(name=name, teacher=self.teacher, session=session)

    def _enrol(self, student, course):
        return StudentCourse.objects.create(
            student=student,
            course=course,
            enrollment_status=EnrollmentStatusType.ENROLLED,
        )

    def _assignment(self, course):
        return Assignment.objects.create(
            title=f"{course.name} work",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            total_points=100,
            due_date=timezone.now() - timedelta(days=1),
        )

    def _submit(self, student, assignment, pct):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=student,
            answers={},
            score=pct,
            score_percentage=pct,
            submission_date=timezone.now(),
            graded_at=timezone.now(),
        )

    def _withdraw(self, course):
        self.enrolment[course.pk].enrollment_status = EnrollmentStatusType.WITHDRAWN
        self.enrolment[course.pk].save(update_fields=["enrollment_status"])

    def _rows(self, **params):
        cache.clear()
        response = self.client.get(MY_STUDENTS, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["results"]

    def _row(self, student, **params):
        rows = [r for r in self._rows(**params) if r["id"] == str(student.id)]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _percentage(self, **params):
        grade = self._row(self.wren, **params)["grade"]
        self.assertIsNotNone(grade)
        return grade["percentage"]

    def _counts(self, **params):
        row = self._row(self.wren, **params)
        return (
            row["total_assignments_in_course"],
            row["total_assignments_submitted"],
            row["percentage_of_submission"],
        )

    def _numbers(self, **params):
        """All five numbers of Wren's row."""
        row = self._row(self.wren, **params)
        return (
            row["grade"]["percentage"],
            row["at_risk"],
            row["total_assignments_in_course"],
            row["total_assignments_submitted"],
            row["percentage_of_submission"],
        )

    # --- the session scopes every number ------------------------------------

    def test_s1_the_session_scopes_the_grade(self):
        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id)}), Decimal("75.00")
        )

    def test_s2_the_session_scopes_the_counts(self):
        self._assignment(self.c2)  # unsubmitted, in Term 2

        self.assertEqual(self._counts(**{SESSION: str(self.term1.id)}), (2, 2, 100.0))

    def test_s3_an_upper_case_session_id_is_the_same_session(self):
        # The filter accepts any UUID spelling; the numbers must follow it too,
        # not only the lower-case form the database prints.
        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id).upper()}), Decimal("75.00")
        )

    # --- the course filter changes no number --------------------------------

    def test_k1_a_course_filter_beside_the_session_changes_no_number(self):
        # C3 gets a second assignment, unsubmitted, so C3 alone would read
        # differently from Term 1.
        self._assignment(self.c3)

        self.assertEqual(
            self._numbers(**{SESSION: str(self.term1.id), COURSE: str(self.c3.id)}),
            self._numbers(**{SESSION: str(self.term1.id)}),
        )

    def test_k2_a_course_filter_alone_changes_no_number(self):
        self._assignment(self.c3)

        self.assertEqual(self._numbers(**{COURSE: str(self.c3.id)}), self._numbers())

    # --- a withdrawn course is out -------------------------------------------

    def test_w1_a_withdrawn_course_is_out_of_the_session_grade(self):
        self._withdraw(self.c3)

        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id)}), Decimal("90.00")
        )

    def test_w2_a_withdrawn_course_is_not_counted(self):
        self._withdraw(self.c1)
        self._assignment(self.c1)  # unsubmitted, in the withdrawn course

        # C3 and C2: one assigned and submitted each.
        self.assertEqual(self._counts(), (2, 2, 100.0))

    # --- no session: the whole history ---------------------------------------

    def test_n1_no_session_grades_the_whole_history(self):
        """Green before and after: the no-session basis is unchanged."""
        self.assertEqual(self._percentage(), NO_FILTER_GRADE)

    def test_n2_no_session_counts_the_whole_history(self):
        self._assignment(self.c2)

        # C1 1 + C3 1 + C2 2 assigned, 3 submitted.
        self.assertEqual(self._counts(), (4, 3, 75.0))

    # --- at_risk and the filters that read the numbers ------------------------

    def test_r1_at_risk_follows_the_session(self):
        # Two more due, unsubmitted assignments in C2 (Term 2). Over every
        # course: 3 of 5 submitted (60%, below 70%) and 60.00 (below 70):
        # two moderate flags, at risk. Term 1 alone: 2 of 2, 75.00: not.
        self._assignment(self.c2)
        self._assignment(self.c2)

        self.assertFalse(
            self._row(self.wren, **{SESSION: str(self.term1.id)})["at_risk"]
        )
        self.assertTrue(
            self._row(self.wren, **{SESSION: str(self.term2.id)})["at_risk"]
        )

    def test_r2_the_at_risk_filter_reads_the_session_verdict(self):
        self._assignment(self.c2)
        self._assignment(self.c2)

        ids = {
            r["id"]
            for r in self._rows(**{SESSION: str(self.term1.id), "at_risk": "false"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    def test_g1_min_grade_reads_the_session_grade(self):
        ids = {
            r["id"]
            for r in self._rows(**{SESSION: str(self.term1.id), "min_grade": "70"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    def test_g2_max_grade_reads_the_session_grade(self):
        ids = {
            r["id"]
            for r in self._rows(**{SESSION: str(self.term2.id), "max_grade": "40"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    def test_g3_ordering_sorts_on_the_session_grade(self):
        # Ada is in C1 only, at 70: 70 in every scope. Wren is 75 in Term 1
        # and 60 over every course.
        ada = self._user(UserTypes.STUDENT, "Ada")
        self._enrol(ada, self.c1)
        self._submit(ada, Assignment.objects.get(course=self.c1), 70)

        ids = [
            r["id"]
            for r in self._rows(
                **{SESSION: str(self.term1.id), "ordering": "-final_grade"}
            )
        ]

        self.assertEqual(ids, [str(self.wren.id), str(ada.id)])

    # --- agreement with all-students -----------------------------------------

    def test_a1_control_a_one_course_student_agrees_with_all_students(self):
        """Green before and after. All-students has one row per course, so its
        rows can only be compared with a my-students row whose session holds
        ONE course of the student's: Ada, in C1 only (Term 1), at 70%."""
        ada = self._user(UserTypes.STUDENT, "Ada")
        self._enrol(ada, self.c1)
        self._submit(ada, Assignment.objects.get(course=self.c1), 70)
        cache.clear()
        response = self.client.get(
            reverse("teacher-admin-all-students", kwargs={"session_id": self.term1.id}),
            {"student_id": str(ada.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [theirs] = response.data["results"]

        mine = self._row(ada, **{SESSION: str(self.term1.id)})

        self.assertEqual(float(mine["grade"]["percentage"]), theirs["average_grade"])
        self.assertEqual(mine["at_risk"], theirs["at_risk"])
