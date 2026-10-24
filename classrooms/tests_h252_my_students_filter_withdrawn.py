"""H-252: my-students' `enrollments__course` and
`enrollments__course__session` filters leave out a student who is
WITHDRAWN from that course (or that session's), even while they are still
enrolled in another of the teacher's courses. The student set itself was
already picked through `.active()` enrolments; the filters were not, so
the withdrawn student was listed under the class they had left.

Run with:
    python manage.py test classrooms.tests_h252_my_students_filter_withdrawn
"""

from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APITestCase

from classrooms.models import Course, EnrollmentStatusType, Session, StudentCourse
from classrooms.tests_my_students_at_risk_filter import LOCMEM, URL, Builder
from users.models import UserTypes


@override_settings(CACHES=LOCMEM)
class FilterSkipsWithdrawnTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.teacher = self.user(UserTypes.TEACHER)
        self.term_1 = Session.objects.create(name="Term 1", teacher=self.teacher)
        self.term_2 = Session.objects.create(name="Term 2", teacher=self.teacher)
        self.course_1 = Course.objects.create(
            name="Maths T1", teacher=self.teacher, session=self.term_1
        )
        self.course_2 = Course.objects.create(
            name="Maths T2", teacher=self.teacher, session=self.term_2
        )
        # Withdrew from Term 1's course, still enrolled in Term 2's.
        self.student = self.user(UserTypes.STUDENT, first="Left")
        StudentCourse.objects.create(
            student=self.student,
            course=self.course_1,
            enrollment_status=EnrollmentStatusType.WITHDRAWN,
        )
        self.enrol(self.student, self.course_2)
        self.client.force_authenticate(self.teacher)

    def _ids(self, **params):
        cache.clear()
        response = self.client.get(URL, params)
        return [row["id"] for row in response.data["results"]]

    def test_the_session_filter_leaves_out_a_withdrawn_student(self):
        self.assertNotIn(
            str(self.student.id),
            self._ids(enrollments__course__session=str(self.term_1.id)),
        )

    def test_the_course_filter_leaves_out_a_withdrawn_student(self):
        self.assertNotIn(
            str(self.student.id), self._ids(enrollments__course=str(self.course_1.id))
        )

    def test_control_still_listed_in_term_2(self):
        self.assertIn(
            str(self.student.id),
            self._ids(enrollments__course__session=str(self.term_2.id)),
        )
