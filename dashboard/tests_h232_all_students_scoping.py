"""H-232: all-students' teacher scoping, with the foreign course IN the
requested session.

The route's course set is `reachable_courses(teacher).filter(session_id=...)`
(dashboard/views.py). The existing tests put the foreign course in ANOTHER
teacher's own session while the request names the requester's session, so
the session filter alone hides it and no test would fail if
`reachable_courses(teacher)` became `Course.objects` (Verifier's T1 on
913f248f). Here the foreign course sits in the very session requested:

  (a) a teacher names another teacher's own session;
  (b) a colleague's course in the same school session;
  (c) a teacher removed from the school asks for the school session (H-38);
  (d) a colleague's course named as `course_id` in the shared session.

Tests only: green on the code as it is. Each one is shown able to fail by a
mutant that replaces `reachable_courses(teacher)` with `Course.objects`.
Each test also shows the hidden student IS visible to the right teacher, so
an empty answer is not empty for some other reason.
"""

from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from classrooms.models import School, Session, SessionOwnerType
from dashboard.tests_teacher_all_students import Builder
from users.models import UserTypes

LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def _url(session):
    return reverse("teacher-admin-all-students", kwargs={"session_id": session.id})


@override_settings(CACHES=LOCMEM)
class TheForeignCourseIsInTheRequestedSessionTest(Builder, APITestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="H232 School")
        self.teacher = self.user(UserTypes.TEACHER, first="Ada", school=self.school)
        self.colleague = self.user(UserTypes.TEACHER, first="Bea", school=self.school)
        self.school_term = Session.objects.create(
            name="H232 school term",
            owner_type=SessionOwnerType.SCHOOL,
            school=self.school,
        )
        self.my_course = self.course(self.teacher, self.school_term, name="Mine")
        self.their_course = self.course(self.colleague, self.school_term, name="Theirs")
        self.mine = self.safe_student(self.my_course, first="MyStudent")
        self.theirs = self.safe_student(self.their_course, first="TheirStudent")

    def _ids(self, user, session, **params):
        cache.clear()
        self.client.force_authenticate(user)
        response = self.client.get(_url(session), params)
        self.assertEqual(response.status_code, status.HTTP_200_OK, response.data)
        return {r["student_id"] for r in response.data["results"]}

    def test_a_another_teachers_own_session_shows_none_of_their_students(self):
        other = self.user(UserTypes.TEACHER, first="Cal")
        other_term = self.session(other)
        other_student = self.safe_student(self.course(other, other_term))

        self.assertEqual(self._ids(other, other_term), {str(other_student.id)})
        self.assertEqual(self._ids(self.teacher, other_term), set())

    def test_b_a_colleagues_course_in_the_same_school_session_is_not_mine(self):
        self.assertEqual(
            self._ids(self.colleague, self.school_term), {str(self.theirs.id)}
        )
        self.assertEqual(self._ids(self.teacher, self.school_term), {str(self.mine.id)})

    def test_c_a_teacher_removed_from_the_school_loses_the_school_session(self):
        self.assertEqual(self._ids(self.teacher, self.school_term), {str(self.mine.id)})

        self.teacher.school = None
        self.teacher.save(update_fields=["school"])

        self.assertEqual(self._ids(self.teacher, self.school_term), set())

    def test_d_a_colleagues_course_id_in_the_shared_session_is_404(self):
        cache.clear()
        self.client.force_authenticate(self.colleague)
        response = self.client.get(
            _url(self.school_term), {"course_id": str(self.their_course.id)}
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        cache.clear()
        self.client.force_authenticate(self.teacher)
        response = self.client.get(
            _url(self.school_term), {"course_id": str(self.their_course.id)}
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
