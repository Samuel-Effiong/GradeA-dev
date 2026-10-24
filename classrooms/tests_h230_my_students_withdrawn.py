"""H-230: my-students' grade and at_risk leave out courses the student has
WITHDRAWN from, as the all-students route does (`.active()`).

The row's student set already excluded a student whose every enrolment
with this teacher is withdrawn; the per-row figures did not. A student who
withdrew from course X and is still enrolled in course Y counted X's
assignments that came due after the withdrawal as expected work and X's
old grades in the grade, so my-students could call them at risk while
all-students, for the same student, did not (Verifier's M1 on 913f248f).

`enrolled_courses` still lists the withdrawn course: that is older
behaviour and not part of this row.
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


@override_settings(CACHES=LOCMEM)
class AWithdrawnCourseDoesNotCountTest(APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self._user(UserTypes.TEACHER, "Tess")
        self.session_obj = Session.objects.create(name="Term", teacher=self.teacher)
        self.course_y = self._course("Course Y")
        self.course_x = self._course("Course X")
        self.student = self._user(UserTypes.STUDENT, "Wren")
        self._enrol(self.course_y, EnrollmentStatusType.ENROLLED)
        self._enrol(self.course_x, EnrollmentStatusType.WITHDRAWN)
        # Course Y: one due assignment, submitted and graded at 90%.
        self._submit(self._assignment(self.course_y), 90)
        self.client.force_authenticate(self.teacher)

    def _user(self, kind, first):
        return CustomUser.objects.create_user(
            email=f"h230-{first.lower()}@audit.test",
            password="remediation-pass-123",  # pragma: allowlist secret
            user_type=kind,
            first_name=first,
            last_name="H230",
            is_active=True,
        )

    def _course(self, name):
        return Course.objects.create(
            name=name, teacher=self.teacher, session=self.session_obj
        )

    def _enrol(self, course, enrollment_status):
        return StudentCourse.objects.create(
            student=self.student, course=course, enrollment_status=enrollment_status
        )

    def _assignment(self, course):
        return Assignment.objects.create(
            title=f"{course.name} work",
            course=course,
            status=AssignmentStatus.PUBLISHED,
            total_points=100,
            due_date=timezone.now() - timedelta(days=1),
        )

    def _submit(self, assignment, pct):
        return StudentSubmission.objects.create(
            assignment=assignment,
            student=self.student,
            answers={},
            score=pct,
            score_percentage=pct,
            submission_date=timezone.now(),
            graded_at=timezone.now(),
        )

    def _my_students(self, **params):
        cache.clear()
        response = self.client.get(MY_STUDENTS, params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return response

    def _my_row(self, **params):
        rows = [
            r
            for r in self._my_students(**params).data["results"]
            if r["id"] == str(self.student.id)
        ]
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_w1_unsubmitted_work_in_the_withdrawn_course_is_not_missing_work(self):
        # X's two due assignments came after the withdrawal and were never
        # submitted. Counted, they make 1 of 3 submitted: critical missing
        # work, at risk.
        self._assignment(self.course_x)
        self._assignment(self.course_x)

        cache.clear()
        all_students = self.client.get(
            reverse(
                "teacher-admin-all-students",
                kwargs={"session_id": self.session_obj.id},
            )
        )
        self.assertEqual(all_students.status_code, status.HTTP_200_OK)
        rows = all_students.data["results"]
        self.assertEqual(
            [(r["course_name"], r["at_risk"]) for r in rows], [("Course Y", False)]
        )

        self.assertFalse(self._my_row()["at_risk"])

    def test_w2_the_grade_leaves_out_the_withdrawn_courses_grades(self):
        # Graded at 30% in X before the withdrawal: counted, the grade is
        # (30 + 90) / 200 = 60.00.
        self._submit(self._assignment(self.course_x), 30)

        grade = self._my_row()["grade"]

        self.assertEqual(grade["percentage"], Decimal("90.00"))

    def test_w3_min_grade_filters_on_the_same_grade(self):
        self._submit(self._assignment(self.course_x), 30)

        ids = {r["id"] for r in self._my_students(min_grade=80).data["results"]}

        self.assertEqual(ids, {str(self.student.id)})

    def test_w4_control_the_withdrawn_course_is_still_listed(self):
        """Green before and after: enrolled_courses is older behaviour."""
        names = [c["name"] for c in self._my_row()["enrolled_courses"]]

        self.assertEqual(sorted(names), ["Course X", "Course Y"])

    def test_w5_control_a_completed_course_still_counts(self):
        """Green before and after: only WITHDRAWN is left out, as
        `.active()` leaves it out."""
        course_z = self._course("Course Z")
        self._enrol(course_z, EnrollmentStatusType.COMPLETED)
        self._submit(self._assignment(course_z), 30)

        grade = self._my_row()["grade"]

        self.assertEqual(grade["percentage"], Decimal("60.00"))
