"""H-234: my-students' grade and at_risk follow the page's filters.

The user's decision (M2, 2026-10-14): with `?enrollments__course__session=`
the grade and at_risk use only that session's courses; with
`?enrollments__course=` only that course; with both, their intersection.
Courses the student withdrew from stay out (H-230). min_grade, max_grade,
at_risk and ordering filter and sort on the same scoped figures, and a
filtered row agrees with the all-students route's row for that course.

With NO filter the basis is pending the user's word (SM 2026-10-14): until
then it is the app's precedent, the whole history (every reachable course
not withdrawn from). That one rule lives in one helper
(`classrooms.models.grade_basis_enrollments`, beside `reachable_courses`)
and one constant here, `NO_FILTER_GRADE`: when the user decides, both change
together.

The fixture: one teacher, two of their sessions; the student is enrolled
in C1 and C3 (Term 1) and C2 (Term 2), each with one due, graded,
100-point assignment: C1 90%, C3 60%, C2 30%. Each filter gives a
different grade:
    no filter     (90 + 60 + 30) / 300 = 60.00
    Term 1        (90 + 60) / 200      = 75.00
    Term 2 / C2   30.00
    Term 1 + C1   90.00
    Term 1 + C2   nothing in both: no grade
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

#: The pending decision, in one place (see the module docstring). Today's
#: basis with no filter: every course, (90 + 60 + 30) / 300.
NO_FILTER_GRADE = Decimal("60.00")


@override_settings(CACHES=LOCMEM)
class TheGradeFollowsTheFiltersTest(APITestCase):
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

    def _rows(self, **params):
        cache.clear()
        response = self.client.get(MY_STUDENTS, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response.data["results"]

    def _wren(self, **params):
        rows = [r for r in self._rows(**params) if r["id"] == str(self.wren.id)]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def _percentage(self, **params):
        grade = self._wren(**params)["grade"]
        self.assertIsNotNone(grade)
        return grade["percentage"]

    # --- the grade --------------------------------------------------------

    def test_f1_a_session_filter_grades_that_session_only(self):
        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id)}), Decimal("75.00")
        )

    def test_f2_a_course_filter_grades_that_course_only(self):
        self.assertEqual(
            self._percentage(**{COURSE: str(self.c2.id)}), Decimal("30.00")
        )

    def test_f3_both_filters_grade_their_intersection(self):
        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id), COURSE: str(self.c1.id)}),
            Decimal("90.00"),
        )

    def test_f4_both_filters_with_nothing_in_common_give_no_grade(self):
        # Wren is in C2 and in a Term 1 course, so the roster lists the row
        # (the two filters narrow the student set separately); C2 is not in
        # Term 1, so nothing is graded.
        row = self._wren(**{SESSION: str(self.term1.id), COURSE: str(self.c2.id)})

        self.assertIsNone(row["grade"])
        self.assertFalse(row["at_risk"])

    def test_f5_a_withdrawn_course_stays_out_under_a_filter(self):
        self.enrolment[self.c3.pk].enrollment_status = EnrollmentStatusType.WITHDRAWN
        self.enrolment[self.c3.pk].save(update_fields=["enrollment_status"])

        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id)}), Decimal("90.00")
        )

    def test_f7_an_upper_case_session_id_is_the_same_session(self):
        # The filter accepts any UUID spelling; the grade must follow it too,
        # not only the lower-case form the database prints.
        self.assertEqual(
            self._percentage(**{SESSION: str(self.term1.id).upper()}), Decimal("75.00")
        )

    def test_f6_no_filter_is_the_pending_basis(self):
        """Green before and after, for now: flips with NO_FILTER_GRADE."""
        self.assertEqual(self._percentage(), NO_FILTER_GRADE)

    # --- at_risk ------------------------------------------------------------

    def test_r1_at_risk_follows_the_session(self):
        # Two more due, unsubmitted assignments in C2 (Term 2). Over every
        # course: 3 of 5 submitted (60%, below 70%) and 60.00 (below 70):
        # two moderate flags, at risk. Term 1 alone: 2 of 2, 75.00: not.
        self._assignment(self.c2)
        self._assignment(self.c2)

        self.assertFalse(self._wren(**{SESSION: str(self.term1.id)})["at_risk"])
        self.assertTrue(self._wren(**{SESSION: str(self.term2.id)})["at_risk"])

    def test_r2_the_at_risk_filter_reads_the_scoped_verdict(self):
        self._assignment(self.c2)
        self._assignment(self.c2)

        ids = {
            r["id"]
            for r in self._rows(**{SESSION: str(self.term1.id), "at_risk": "false"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    # --- the grade filters and the sort --------------------------------------

    def test_g1_min_grade_reads_the_scoped_grade(self):
        ids = {
            r["id"]
            for r in self._rows(**{SESSION: str(self.term1.id), "min_grade": "70"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    def test_g2_max_grade_reads_the_scoped_grade(self):
        ids = {
            r["id"] for r in self._rows(**{COURSE: str(self.c2.id), "max_grade": "40"})
        }

        self.assertEqual(ids, {str(self.wren.id)})

    def test_g3_ordering_sorts_on_the_scoped_grade(self):
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

    # --- agreement with all-students ---------------------------------------

    def test_a1_one_course_agrees_with_the_all_students_row(self):
        cache.clear()
        response = self.client.get(
            reverse("teacher-admin-all-students", kwargs={"session_id": self.term1.id}),
            {"course_id": str(self.c1.id)},
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        [theirs] = response.data["results"]

        mine = self._wren(**{SESSION: str(self.term1.id), COURSE: str(self.c1.id)})

        self.assertEqual(float(mine["grade"]["percentage"]), theirs["average_grade"])
        self.assertEqual(mine["at_risk"], theirs["at_risk"])
