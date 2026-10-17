"""H-18 hardening: classroom ownership validators fail closed without a request.

TopicSerializer.validate_course and CourseSerializer.validate_session used
to return the value unchecked when the serializer had no authenticated
request in its context. Every current data-bound caller passes context, so
this was not exploitable - but assignments' update_async shows how easily a
view builds a serializer without it, and there the same pass-through was a
live bypass (see assignments/tests_course_ownership_idor.py). Both now refuse.

Each check is pinned in both directions: no context -> refused; the owning
teacher with context -> accepted; another teacher (same school and other
school) with context -> refused.
"""

from types import SimpleNamespace

from django.core.cache import cache
from rest_framework.test import APITestCase

from assignments.tests_security import TenancyAttackFixture, make_teacher
from classrooms.models import SessionOwnerType
from classrooms.serializers import CourseSerializer, TopicSerializer


class FailClosedFixture(TenancyAttackFixture, APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()
        self.colleague_a = make_teacher("fc-colleague-a@example.com", self.school_a)

    def ctx(self, user):
        return {"request": SimpleNamespace(user=user)}


class TopicValidateCourseFailClosedTest(FailClosedFixture):
    def serializer(self, context):
        return TopicSerializer(
            data={"name": "Photosynthesis", "course": str(self.course_a.id)},
            context=context,
        )

    def test_no_request_in_context_is_refused(self):
        serializer = self.serializer({})
        self.assertFalse(serializer.is_valid())
        self.assertIn("course", serializer.errors)

    def test_owner_is_accepted(self):
        self.assertTrue(self.serializer(self.ctx(self.teacher_a)).is_valid())

    def test_other_teachers_are_refused(self):
        for user in (self.teacher_b, self.colleague_a):
            with self.subTest(user=user.email):
                serializer = self.serializer(self.ctx(user))
                self.assertFalse(serializer.is_valid())
                self.assertIn("course", serializer.errors)


class CourseValidateSessionFailClosedTest(FailClosedFixture):
    def setUp(self):
        super().setUp()
        # make_course created an INDIVIDUAL session owned by teacher_a.
        self.session_a = self.course_a.session
        self.assertEqual(self.session_a.owner_type, SessionOwnerType.INDIVIDUAL)
        self.assertEqual(self.session_a.teacher_id, self.teacher_a.id)

    def serializer(self, context):
        return CourseSerializer(
            data={"name": "New Course", "session": str(self.session_a.id)},
            context=context,
        )

    def test_unauthenticated_request_in_context_is_refused(self):
        # With no request at all CourseSerializer cannot even run (its
        # teacher HiddenField needs request.user), so the reachable form of
        # "no authenticated requester" is an anonymous request.
        anonymous = SimpleNamespace(is_authenticated=False, id=None, pk=None)
        serializer = self.serializer(self.ctx(anonymous))
        self.assertFalse(serializer.is_valid())
        self.assertIn("session", serializer.errors)

    def test_owner_is_accepted(self):
        serializer = self.serializer(self.ctx(self.teacher_a))
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_other_teachers_are_refused(self):
        for user in (self.teacher_b, self.colleague_a):
            with self.subTest(user=user.email):
                serializer = self.serializer(self.ctx(user))
                self.assertFalse(serializer.is_valid())
                self.assertIn("session", serializer.errors)
