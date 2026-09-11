"""Adversarial access-control tests for the assignments app.

Every other suite in this app asks "does the intended thing work". This
one asks the opposite question: given a valid login for the wrong person,
what can actually be reached? Each test is written as an attack with a
named attacker, a named victim, and an asserted outcome - not as a
description of intended behaviour.

Two rules this file follows deliberately:

  * It asserts on CONTENT, not only on status codes. A queryset that
    leaks another tenant's rows still answers 200, so a test that checks
    only `status_code == 200` passes against a completely open endpoint.
    Where a request should be refused, both 403 and 404 are accepted -
    DRF returns 404 for an object outside `get_queryset()`, which is the
    correct non-disclosing answer - but the response body is checked for
    the victim's data either way.

  * It never reaches past the HTTP boundary. Everything goes through the
    real router, the real permission classes and the real serializers,
    because that is the surface an attacker has.
"""

import threading
import unittest
import uuid
from unittest.mock import patch

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.test import TransactionTestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient, APITestCase

from assignments import pdf_cache
from assignments.models import Assignment, AssignmentStatus
from assignments.tests_download_pdf import objective_question
from classrooms.models import (
    Course,
    EnrollmentStatusType,
    School,
    Session,
    StudentCourse,
    Topic,
)
from users.models import CustomUser, UserTypes

REFUSED = (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND)


def _rss_mb():
    """Resident memory of this process, in MB. 0.0 where unavailable."""
    try:
        import resource

        raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return raw / 1024
    except Exception:  # pragma: no cover - non-POSIX
        return 0.0


def make_teacher(email, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        first_name="T",
        last_name=email.split("@")[0],
        school=school,
    )


def make_student(email, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.STUDENT,
        first_name="S",
        last_name=email.split("@")[0],
        school=school,
    )


def make_course(teacher, name):
    session = Session.objects.create(name=f"Session {name}", teacher=teacher)
    return Course.objects.create(name=name, teacher=teacher, session=session)


def make_assignment(course, title, status_value=AssignmentStatus.PUBLISHED):
    return Assignment.objects.create(
        title=title,
        course=course,
        status=status_value,
        total_points=5,
        questions=[objective_question()],
    )


def enroll(student, course, enrollment_status=EnrollmentStatusType.ENROLLED):
    return StudentCourse.objects.create(
        student=student, course=course, enrollment_status=enrollment_status
    )


class TenancyAttackFixture:
    """Two schools, two teachers, two courses, and students in each."""

    # Supplied by the APITestCase/TransactionTestCase this is mixed into.
    client: APIClient

    def build_world(self):
        self.school_a = School.objects.create(name="School A")
        self.school_b = School.objects.create(name="School B")

        self.teacher_a = make_teacher("attack-teacher-a@example.com", self.school_a)
        self.teacher_b = make_teacher("attack-teacher-b@example.com", self.school_b)

        self.course_a = make_course(self.teacher_a, "Course A")
        self.course_b = make_course(self.teacher_b, "Course B")

        self.assignment_a = make_assignment(self.course_a, "Secret Quiz A")
        self.assignment_b = make_assignment(self.course_b, "Secret Quiz B")
        self.draft_a = make_assignment(
            self.course_a, "Unpublished Draft A", AssignmentStatus.DRAFT
        )

        self.student_a = make_student("attack-student-a@example.com", self.school_a)
        self.student_b = make_student("attack-student-b@example.com", self.school_b)
        enroll(self.student_a, self.course_a)
        enroll(self.student_b, self.course_b)

        self.outsider = make_student("attack-outsider@example.com")

    def detail_url(self, assignment):
        return reverse("assignment-detail", kwargs={"pk": assignment.id})

    def pdf_url(self, assignment):
        return reverse("assignment-download-pdf", kwargs={"pk": assignment.id})

    def list_url(self):
        return reverse("assignment-list")

    def as_user(self, user):
        self.client.force_authenticate(user=user)


class CrossTenantReadTest(TenancyAttackFixture, APITestCase):
    """Reading another teacher's / another school's assignments."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    def test_teacher_cannot_retrieve_another_teachers_assignment(self):
        self.as_user(self.teacher_b)
        response = self.client.get(self.detail_url(self.assignment_a))

        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn("Secret Quiz A", response.content.decode())

    def test_teacher_cannot_see_another_teachers_assignment_in_a_list(self):
        self.as_user(self.teacher_b)
        response = self.client.get(self.list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode()
        self.assertIn("Secret Quiz B", body, "sanity: own assignment must be present")
        self.assertNotIn("Secret Quiz A", body)

    def test_a_course_filter_cannot_widen_a_teachers_own_scope(self):
        """
        `?course=` is a DjangoFilterBackend field, and filters run AFTER
        get_queryset. Passing another teacher's course id must therefore
        narrow to nothing, never reach across.
        """
        self.as_user(self.teacher_b)
        response = self.client.get(self.list_url(), {"course": str(self.course_a.id)})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("Secret Quiz A", response.content.decode())

    def test_a_search_term_cannot_reach_another_tenants_rows(self):
        self.as_user(self.teacher_b)
        response = self.client.get(self.list_url(), {"search": "Secret Quiz A"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("Secret Quiz A", response.content.decode())

    def test_student_cannot_retrieve_an_assignment_from_another_school(self):
        self.as_user(self.student_b)
        response = self.client.get(self.detail_url(self.assignment_a))

        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn("Secret Quiz A", response.content.decode())

    def test_a_student_enrolled_nowhere_sees_no_assignments_at_all(self):
        self.as_user(self.outsider)
        response = self.client.get(self.list_url())

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        body = response.content.decode()
        self.assertNotIn("Secret Quiz A", body)
        self.assertNotIn("Secret Quiz B", body)

    def test_an_unauthenticated_caller_is_refused_everywhere(self):
        self.client.force_authenticate(user=None)

        for url in (
            self.list_url(),
            self.detail_url(self.assignment_a),
            self.pdf_url(self.assignment_a),
        ):
            response = self.client.get(url)
            self.assertIn(
                response.status_code,
                (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
                url,
            )

    def test_a_random_uuid_does_not_distinguish_itself_from_a_real_one(self):
        """
        The 404 for someone else's assignment must look exactly like the
        404 for an id that does not exist, or the endpoint becomes an
        oracle for which assignment ids are real.
        """
        self.as_user(self.teacher_b)
        foreign = self.client.get(self.detail_url(self.assignment_a))
        nonexistent = self.client.get(
            reverse("assignment-detail", kwargs={"pk": uuid.uuid4()})
        )

        self.assertEqual(foreign.status_code, nonexistent.status_code)
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)


class CrossTenantWriteTest(TenancyAttackFixture, APITestCase):
    """Writing to, or through, another tenant's objects."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    def test_teacher_cannot_edit_another_teachers_assignment(self):
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.detail_url(self.assignment_a), {"title": "Defaced"}, format="json"
        )

        self.assertIn(response.status_code, REFUSED)
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.title, "Secret Quiz A")

    def test_teacher_cannot_delete_another_teachers_assignment(self):
        self.as_user(self.teacher_b)
        response = self.client.delete(self.detail_url(self.assignment_a))

        self.assertIn(response.status_code, REFUSED)
        self.assertTrue(Assignment.objects.filter(pk=self.assignment_a.pk).exists())

    def test_a_student_cannot_create_an_assignment(self):
        self.as_user(self.student_a)
        response = self.client.post(
            self.list_url(),
            {"course": str(self.course_a.id), "raw_input": "anything"},
            format="json",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED),
        )

    def test_a_student_cannot_edit_an_assignment_they_can_read(self):
        self.as_user(self.student_a)
        response = self.client.patch(
            self.detail_url(self.assignment_a), {"title": "Defaced"}, format="json"
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED),
        )
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.title, "Secret Quiz A")

    def test_teacher_cannot_move_their_assignment_onto_another_courses_topic(self):
        """
        associate-topic takes a raw topic id from the query string. The
        assignment is scoped, but the topic lookup is not - so the
        same-course check is the only thing standing between a teacher and
        another school's topic being attached to (and named in) their own
        assignment.
        """
        foreign_topic = Topic.objects.create(
            name="Topic B Secret", course=self.course_b
        )
        # topic_id is a QUERY PARAM on this action, not a body field. Sent
        # in the body it never reaches the same-course check at all - the
        # view rejects it as missing, and a test written that way passes
        # while proving nothing.
        url = (
            reverse("assignment-associate-topic", kwargs={"pk": self.assignment_a.id})
            + f"?topic_id={foreign_topic.id}"
        )

        self.as_user(self.teacher_a)
        response = self.client.patch(url)

        self.assertNotEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("Topic B Secret", response.content.decode())
        self.assignment_a.refresh_from_db()
        self.assertIsNone(self.assignment_a.topic)


class ForeignTopicInjectionTest(TenancyAttackFixture, APITestCase):
    """
    The upload actions take a `topic` id straight from the request body
    and, unlike every DRF write path, never run it through
    AssignmentSerializer.validate - which is the only place that checks
    the topic belongs to the same course.

    An unscoped lookup here let a teacher create an assignment in their
    OWN course while attaching another school's topic.

    HONEST SEVERITY: this is a cross-tenant foreign key, not a live data
    leak. The two serializers that would have rendered the foreign topic's
    name were referenced by no view and no URL - and in fact declared
    `course`, `topic` and `generation_mode` fields their model does not
    have, so they could never have been instantiated at all. They have
    since been deleted as dead code.

    The boundary still has to hold: nothing else in the codebase permits
    this (both the DRF write path and associate_topic already check it),
    so the upload actions were the odd ones out, and a foreign FK would
    become readable the moment anything renders it.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()
        self.foreign_topic = Topic.objects.create(
            name="School B Confidential Topic", course=self.course_b
        )
        self.own_topic = Topic.objects.create(name="My Own Topic", course=self.course_a)

    def _allow_credits(self):
        """
        The async upload sits behind HasCreditBalance, which refuses with
        400 before the topic is ever looked at. That is sound
        defence-in-depth, but it would make these tests pass without
        exercising the boundary they are about - so the credit gate is
        neutralised and the topic check left as the only thing standing.
        """
        return patch(
            "users.permissions.HasCreditBalance.has_permission", return_value=True
        )

    def _upload_payload(self, topic_id):
        from django.core.files.uploadedfile import SimpleUploadedFile

        # A 1x1 PNG - enough to get past the content-type branch. The AI
        # call beyond it is mocked out by each test.
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00"
            b"\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        return {
            "course": str(self.course_a.id),
            "topic": topic_id,
            "assignments": SimpleUploadedFile("q.png", png, content_type="image/png"),
        }

    @patch("assignments.views.AssignmentProcessingService.prepare_ai_content")
    def test_sync_upload_refuses_another_courses_topic(self, mock_prepare):
        mock_prepare.side_effect = AssertionError(
            "the request must be refused before any AI work is done"
        )
        self.as_user(self.teacher_a)

        response = self.client.post(
            reverse("assignment-upload"),
            self._upload_payload(str(self.foreign_topic.id)),
            format="multipart",
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("School B Confidential Topic", response.content.decode())
        self.assertFalse(
            Assignment.objects.filter(topic=self.foreign_topic).exists(),
            "no assignment may end up pointing at another course's topic",
        )

    @patch("assignments.views.upload_assignment_async")
    def test_async_upload_refuses_another_courses_topic(self, mock_task):
        self.as_user(self.teacher_a)

        with self._allow_credits():
            response = self.client.post(
                reverse("assignment-upload-async"),
                self._upload_payload(str(self.foreign_topic.id)),
                format="multipart",
            )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertNotIn("School B Confidential Topic", response.content.decode())
        mock_task.delay.assert_not_called()
        self.assertFalse(Assignment.objects.filter(topic=self.foreign_topic).exists())

    @patch("assignments.views.upload_assignment_async")
    def test_the_teachers_own_topic_is_still_accepted(self, mock_task):
        """
        The guard must scope, not simply reject every topic - otherwise
        "secure" would just mean "broken".
        """
        self.as_user(self.teacher_a)

        with self._allow_credits():
            response = self.client.post(
                reverse("assignment-upload-async"),
                self._upload_payload(str(self.own_topic.id)),
                format="multipart",
            )

        self.assertNotEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_a_foreign_topic_cannot_be_attached_through_the_drf_write_path(self):
        """
        The third door into the same field. The upload actions bypass the
        serializer, but PATCH goes through AssignmentSerializer.validate -
        so all three have to agree, or the boundary is only as strong as
        the weakest one.
        """
        self.as_user(self.teacher_a)

        response = self.client.patch(
            self.detail_url(self.assignment_a),
            {"topic": str(self.foreign_topic.id)},
            format="json",
        )

        self.assertNotEqual(response.status_code, status.HTTP_200_OK)
        self.assignment_a.refresh_from_db()
        self.assertIsNone(self.assignment_a.topic)

    def test_no_response_anywhere_reveals_another_schools_topic_name(self):
        """
        Attaching is only half of it - the payoff would be READING the
        name back. This sweeps every endpoint that renders an assignment
        and asserts the foreign topic's name appears in none of them,
        however the attacker got there.

        The serializers that would have exposed the name were dead code
        and have been removed (see the class docstring), so this guards
        the boundary against anything that renders a topic in future
        rather than closing a hole that leaks today.
        """
        self.as_user(self.teacher_a)

        responses = [
            self.client.get(self.list_url()),
            self.client.get(self.detail_url(self.assignment_a)),
            self.client.patch(
                self.detail_url(self.assignment_a),
                {"topic": str(self.foreign_topic.id)},
                format="json",
            ),
            self.client.patch(
                reverse(
                    "assignment-associate-topic",
                    kwargs={"pk": self.assignment_a.id},
                )
                + f"?topic_id={self.foreign_topic.id}"
            ),
        ]

        for index, response in enumerate(responses):
            with self.subTest(response=index):
                self.assertNotIn(
                    "School B Confidential Topic", response.content.decode()
                )

    def test_a_teacher_can_still_attach_and_read_back_their_own_topic(self):
        """
        The other half of "scoped, not just blocked": the legitimate case
        must keep working, and the topic name must still come back.
        """
        url = (
            reverse("assignment-associate-topic", kwargs={"pk": self.assignment_a.id})
            + f"?topic_id={self.own_topic.id}"
        )
        self.as_user(self.teacher_a)

        attach = self.client.patch(url)
        self.assertEqual(attach.status_code, status.HTTP_200_OK)
        self.assertIn("My Own Topic", attach.content.decode())

        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.topic_id, self.own_topic.id)

        detail = self.client.get(self.detail_url(self.assignment_a))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        # AssignmentDetailSerializer renders `topic` as a bare id; the
        # human-readable `topic_name` lives on the generation-history
        # serializers, which is where the foreign-topic read primitive
        # actually surfaced. Assert the id here, and see
        # test_no_response_anywhere_reveals_another_schools_topic_name for
        # the name.
        self.assertIn(str(self.own_topic.id), detail.content.decode())

    def test_a_student_cannot_reach_any_topic_through_an_assignment(self):
        """
        A student in course A must not be able to pull a topic id from
        anywhere else into view either.
        """
        self.as_user(self.student_a)

        response = self.client.patch(
            self.detail_url(self.assignment_a),
            {"topic": str(self.foreign_topic.id)},
            format="json",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_403_FORBIDDEN, status.HTTP_405_METHOD_NOT_ALLOWED),
        )
        self.assertNotIn("School B Confidential Topic", response.content.decode())

    @patch("assignments.views.upload_assignment_async")
    def test_a_nonexistent_topic_is_indistinguishable_from_a_foreign_one(
        self, mock_task
    ):
        """
        Both must 404. A different status for "exists but isn't yours"
        would confirm which topic ids are real across the whole install.
        """
        self.as_user(self.teacher_a)

        with self._allow_credits():
            foreign = self.client.post(
                reverse("assignment-upload-async"),
                self._upload_payload(str(self.foreign_topic.id)),
                format="multipart",
            )
            missing = self.client.post(
                reverse("assignment-upload-async"),
                self._upload_payload(str(uuid.uuid4())),
                format="multipart",
            )

        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertEqual(foreign.status_code, status.HTTP_404_NOT_FOUND)


class TeacherViewLeakTest(TenancyAttackFixture, APITestCase):
    """
    The teacher PDF carries rubrics and model answers. Getting it as a
    student is the highest-value attack on this section.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        self.build_world()

    @patch("assignments.views.render_assignment_pdf")
    def test_student_cannot_request_the_teacher_view(self, mock_render):
        mock_render.return_value = b"%PDF-teacher-with-answers"
        self.as_user(self.student_a)

        response = self.client.get(self.pdf_url(self.assignment_a), {"view": "teacher"})

        self.assertIn(response.status_code, REFUSED)
        mock_render.assert_not_called()

    @patch("assignments.views.render_assignment_pdf")
    def test_a_warm_teacher_entry_cannot_be_pulled_out_by_a_student(self, mock_render):
        """
        The cache is keyed by view type, and permissions run before the
        lookup. Warming the teacher entry first is the setup that would
        expose a mistake in either.
        """
        mock_render.return_value = b"%PDF-teacher-with-answers"
        self.as_user(self.teacher_a)
        self.client.get(self.pdf_url(self.assignment_a), {"view": "teacher"})

        self.as_user(self.student_a)
        response = self.client.get(self.pdf_url(self.assignment_a), {"view": "teacher"})

        self.assertIn(response.status_code, REFUSED)

    @patch("assignments.views.render_assignment_pdf")
    def test_case_and_whitespace_tricks_do_not_reach_the_teacher_view(
        self, mock_render
    ):
        """
        The view param is lowercased and stripped before comparison, so
        these must all resolve to the STUDENT document rather than
        sneaking past the equality check.
        """
        mock_render.return_value = b"%PDF-x"
        self.as_user(self.student_a)

        for probe in ("TEACHER", "Teacher", " teacher ", "\tTeAcHeR\n"):
            with self.subTest(view=probe):
                response = self.client.get(
                    self.pdf_url(self.assignment_a), {"view": probe}
                )
                self.assertIn(response.status_code, REFUSED, probe)

    @patch("assignments.views.render_assignment_pdf")
    def test_an_unknown_view_value_falls_back_to_the_student_document(
        self, mock_render
    ):
        mock_render.return_value = b"%PDF-student"
        self.as_user(self.student_a)

        response = self.client.get(
            self.pdf_url(self.assignment_a), {"view": "administrator"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # include_rubric must be False for anything that isn't "teacher".
        self.assertIs(mock_render.call_args.args[1], False)

    @patch("assignments.views.render_assignment_pdf")
    def test_a_repeated_view_param_cannot_smuggle_teacher_past_the_check(
        self, mock_render
    ):
        """
        `?view=student&view=teacher` - Django's QueryDict.get() returns the
        LAST value, so if the check and the render ever read the parameter
        differently this is where they would disagree. One read, one
        decision: whatever the permission check saw is what gets rendered.
        """
        mock_render.return_value = b"%PDF-x"
        self.as_user(self.student_a)

        response = self.client.get(
            self.pdf_url(self.assignment_a) + "?view=student&view=teacher"
        )

        if response.status_code == status.HTTP_200_OK:
            self.assertIs(
                mock_render.call_args.args[1],
                False,
                "a 200 here must be the student document",
            )
        else:
            self.assertIn(response.status_code, REFUSED)

    @patch("assignments.views.render_assignment_pdf")
    def test_teacher_cannot_download_another_teachers_teacher_view(self, mock_render):
        mock_render.return_value = b"%PDF-teacher-with-answers"
        self.as_user(self.teacher_b)

        response = self.client.get(self.pdf_url(self.assignment_a), {"view": "teacher"})

        self.assertIn(response.status_code, REFUSED)
        mock_render.assert_not_called()


class EnrollmentStateAccessTest(TenancyAttackFixture, APITestCase):
    """
    Access follows the enrollment's CURRENT state, not the mere existence
    of an enrollment row.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()
        self.subject = make_student("attack-state-student@example.com", self.school_a)
        self.enrollment = enroll(self.subject, self.course_a)

    def _set_state(self, state):
        self.enrollment.enrollment_status = state
        self.enrollment.save(update_fields=["enrollment_status"])
        cache.clear()

    @patch("assignments.views.render_assignment_pdf")
    def test_a_withdrawn_student_loses_list_detail_and_pdf_together(self, mock_render):
        """
        The one that mattered: a student a teacher had deliberately
        removed from the course kept full read access to every published
        assignment and its PDF, indefinitely.
        """
        mock_render.return_value = b"%PDF-x"
        self.as_user(self.subject)
        self.assertEqual(
            self.client.get(self.detail_url(self.assignment_a)).status_code,
            status.HTTP_200_OK,
            "sanity: an ENROLLED student can read it",
        )

        self._set_state(EnrollmentStatusType.WITHDRAWN)

        self.assertNotIn(
            "Secret Quiz A",
            self.client.get(self.list_url()).content.decode(),
        )
        self.assertIn(
            self.client.get(self.detail_url(self.assignment_a)).status_code, REFUSED
        )
        self.assertIn(
            self.client.get(self.pdf_url(self.assignment_a)).status_code, REFUSED
        )

    @patch("assignments.views.render_assignment_pdf")
    def test_a_pending_student_cannot_read_the_course_they_were_invited_to(
        self, mock_render
    ):
        """
        PENDING means invited but not yet registered. Such a student is
        never sent the "new assignment posted" notification either, so
        granting them the content would contradict the app's own idea of
        who is in the class.
        """
        mock_render.return_value = b"%PDF-x"
        self._set_state(EnrollmentStatusType.PENDING)
        self.as_user(self.subject)

        self.assertNotIn(
            "Secret Quiz A", self.client.get(self.list_url()).content.decode()
        )
        self.assertIn(
            self.client.get(self.detail_url(self.assignment_a)).status_code, REFUSED
        )
        self.assertIn(
            self.client.get(self.pdf_url(self.assignment_a)).status_code, REFUSED
        )

    @patch("assignments.views.render_assignment_pdf")
    def test_a_completed_student_keeps_access_to_their_own_history(self, mock_render):
        """
        The other side of the rule: finishing a course must not erase it.
        A filter that only allowed ENROLLED would silently delete every
        past student's record of what they were asked to do.
        """
        mock_render.return_value = b"%PDF-x"
        self._set_state(EnrollmentStatusType.COMPLETED)
        self.as_user(self.subject)

        self.assertIn(
            "Secret Quiz A", self.client.get(self.list_url()).content.decode()
        )
        self.assertEqual(
            self.client.get(self.detail_url(self.assignment_a)).status_code,
            status.HTTP_200_OK,
        )

    def test_re_enrolling_a_withdrawn_student_restores_access(self):
        """Withdrawal must be reversible, not a one-way door."""
        self._set_state(EnrollmentStatusType.WITHDRAWN)
        self.as_user(self.subject)
        self.assertIn(
            self.client.get(self.detail_url(self.assignment_a)).status_code, REFUSED
        )

        self._set_state(EnrollmentStatusType.ENROLLED)

        self.assertEqual(
            self.client.get(self.detail_url(self.assignment_a)).status_code,
            status.HTTP_200_OK,
        )

    def test_a_withdrawn_students_enrollment_in_another_course_still_works(self):
        """
        The filter must key off the enrollment row for THAT course, not
        off "this student has some good enrollment somewhere".
        """
        other_course = make_course(self.teacher_a, "Course A2")
        other_assignment = make_assignment(other_course, "Still Mine")
        enroll(self.subject, other_course)

        self._set_state(EnrollmentStatusType.WITHDRAWN)
        self.as_user(self.subject)

        body = self.client.get(self.list_url()).content.decode()
        self.assertIn("Still Mine", body)
        self.assertNotIn("Secret Quiz A", body)
        self.assertEqual(
            self.client.get(self.detail_url(other_assignment)).status_code,
            status.HTTP_200_OK,
        )

    def test_an_enrolled_student_cannot_read_an_unpublished_draft(self):
        self.as_user(self.subject)

        response = self.client.get(self.detail_url(self.draft_a))

        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn("Unpublished Draft A", response.content.decode())

    @patch("assignments.views.render_assignment_pdf")
    def test_an_enrolled_student_cannot_download_a_draft_pdf(self, mock_render):
        mock_render.return_value = b"%PDF-x"
        self.as_user(self.subject)

        response = self.client.get(self.pdf_url(self.draft_a))

        self.assertIn(response.status_code, REFUSED)
        mock_render.assert_not_called()

    @patch("assignments.views.render_assignment_pdf")
    def test_unpublishing_an_assignment_revokes_student_access(self, mock_render):
        mock_render.return_value = b"%PDF-x"
        self.as_user(self.subject)
        self.assertEqual(
            self.client.get(self.pdf_url(self.assignment_a)).status_code,
            status.HTTP_200_OK,
        )

        self.assignment_a.status = AssignmentStatus.UNPUBLISHED
        self.assignment_a.save()
        cache.clear()

        self.assertIn(
            self.client.get(self.pdf_url(self.assignment_a)).status_code, REFUSED
        )


class CachedPdfReuseTest(TenancyAttackFixture, APITestCase):
    """
    "Reuse an old download URL." The PDF URL is stable and carries no
    token, so every request must be re-authorised from scratch and must
    never serve content the assignment no longer has.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        self.build_world()

    @patch("assignments.views.render_assignment_pdf")
    def test_a_url_that_worked_before_withdrawal_stops_working_after(self, mock_render):
        mock_render.return_value = b"%PDF-warm"
        enrollment = enroll(self.outsider, self.course_a)
        self.as_user(self.outsider)
        url = self.pdf_url(self.assignment_a)
        self.assertEqual(self.client.get(url).status_code, status.HTTP_200_OK)

        enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
        enrollment.save(update_fields=["enrollment_status"])

        # Same URL, same warm cache entry, revoked reader.
        self.assertIn(self.client.get(url).status_code, REFUSED)

    @patch("assignments.views.render_assignment_pdf")
    def test_an_edited_assignment_never_serves_the_pre_edit_pdf(self, mock_render):
        """
        Content revocation, not just access revocation: if a teacher
        removes a question, the old document must not still be reachable.
        """
        mock_render.return_value = b"%PDF-with-secret-question"
        self.as_user(self.teacher_a)
        url = self.pdf_url(self.assignment_a)
        self.client.get(url, {"view": "teacher"})

        mock_render.return_value = b"%PDF-redacted"
        self.assignment_a.questions = [objective_question(number=9)]
        self.assignment_a.save()

        response = self.client.get(url, {"view": "teacher"})

        self.assertEqual(b"".join(response.streaming_content), b"%PDF-redacted")

    @patch("assignments.views.render_assignment_pdf")
    def test_a_deleted_assignments_pdf_is_not_still_downloadable(self, mock_render):
        mock_render.return_value = b"%PDF-warm"
        self.as_user(self.teacher_a)
        url = self.pdf_url(self.assignment_a)
        self.client.get(url, {"view": "teacher"})

        self.assignment_a.delete()

        self.assertEqual(
            self.client.get(url, {"view": "teacher"}).status_code,
            status.HTTP_404_NOT_FOUND,
        )

    @patch("assignments.views.render_assignment_pdf")
    def test_two_assignments_never_share_a_cache_entry(self, mock_render):
        """
        A key collision would hand one course's document to another's.
        """
        mock_render.side_effect = lambda a, inc: f"%PDF-{a.title}".encode()
        self.as_user(self.teacher_a)
        second = make_assignment(self.course_a, "Different Quiz")

        first_body = b"".join(
            self.client.get(
                self.pdf_url(self.assignment_a), {"view": "teacher"}
            ).streaming_content
        )
        second_body = b"".join(
            self.client.get(self.pdf_url(second), {"view": "teacher"}).streaming_content
        )

        self.assertEqual(first_body, b"%PDF-Secret Quiz A")
        self.assertEqual(second_body, b"%PDF-Different Quiz")


class MalformedInputTest(TenancyAttackFixture, APITestCase):
    """Parameter manipulation that should be refused, not crash."""

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    def test_a_non_uuid_primary_key_is_not_a_server_error(self):
        self.as_user(self.teacher_a)

        for probe in ("not-a-uuid", "1 OR 1=1", "../../etc/passwd", "%00"):
            with self.subTest(pk=probe):
                response = self.client.get(f"/api/v1/assignments/{probe}/")
                self.assertLess(response.status_code, 500, probe)

    def test_sql_metacharacters_in_search_do_not_reach_the_database(self):
        self.as_user(self.teacher_a)

        response = self.client.get(
            self.list_url(), {"search": "'; DROP TABLE assignments_assignment; --"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # The table is still there and still readable.
        self.assertTrue(Assignment.objects.filter(pk=self.assignment_a.pk).exists())

    def test_an_ordering_value_cannot_name_an_arbitrary_column(self):
        """
        OrderingFilter is allowlisted via `ordering_fields`; a value
        outside it must be ignored rather than ordering by - and thereby
        confirming the existence of - an unlisted column.
        """
        self.as_user(self.teacher_a)

        response = self.client.get(
            self.list_url(), {"ordering": "course__teacher__password"}
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("password", response.content.decode().lower())

    def test_an_oversized_page_size_cannot_exhaust_memory(self):
        self.as_user(self.teacher_a)

        response = self.client.get(self.list_url(), {"page_size": "1000000"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertLessEqual(len(response.data["results"]), 100)

    def test_a_filter_on_an_undeclared_field_is_rejected_or_ignored(self):
        self.as_user(self.teacher_b)

        response = self.client.get(
            self.list_url(), {"course__teacher__id": str(self.teacher_a.id)}
        )

        self.assertLess(response.status_code, 500)
        self.assertNotIn("Secret Quiz A", response.content.decode())


class HostileUploadTest(TenancyAttackFixture, APITestCase):
    """
    The upload endpoints take a file and its Content-Type from the client
    and feed both into a paid AI extraction path. The declared type is a
    claim, not a fact.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    def _upload(self, name, body, content_type):
        from django.core.files.uploadedfile import SimpleUploadedFile

        self.as_user(self.teacher_a)
        return self.client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.course_a.id),
                "assignments": SimpleUploadedFile(
                    name, body, content_type=content_type
                ),
            },
            format="multipart",
        )

    def test_a_non_image_labelled_as_an_image_is_a_client_error_not_a_crash(self):
        """
        Image.open raises UnidentifiedImageError on this, and nothing used
        to catch it - the caller got a 500 and a stack trace for what is an
        ordinary bad request.
        """
        response = self._upload(
            b"payload.png", b"this is not a png at all", "image/png"
        )

        self.assertLess(response.status_code, 500, response.content[:200])
        self.assertGreaterEqual(response.status_code, 400)

    def test_an_executable_renamed_to_png_is_refused(self):
        elf_header = b"\x7fELF\x02\x01\x01\x00" + b"\x00" * 200
        response = self._upload(b"evil.png", elf_header, "image/png")

        self.assertLess(response.status_code, 500)
        self.assertGreaterEqual(response.status_code, 400)

    def test_a_truncated_image_is_refused_rather_than_half_processed(self):
        png = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
        )  # header only, no image data
        response = self._upload(b"truncated.png", png, "image/png")

        self.assertLess(response.status_code, 500)
        self.assertGreaterEqual(response.status_code, 400)

    @staticmethod
    def _png_declaring(width, height):
        """
        A real, structurally valid PNG whose IHDR claims `width` x
        `height` but which carries almost no data - i.e. an actual
        decompression bomb, ~100 bytes on the wire.

        Built by hand rather than with Pillow precisely so the test never
        allocates the raster it is checking we refuse to allocate.
        """
        import struct
        import zlib

        def chunk(tag, payload):
            body = tag + payload
            return (
                struct.pack(">I", len(payload))
                + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
            )

        ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
        return (
            b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(b"\x00" * 32))
            + chunk(b"IEND", b"")
        )

    def test_a_real_decompression_bomb_is_refused_without_allocating_it(self):
        """
        The real thing: a genuine ~100-byte PNG whose header declares 81
        megapixels, run through the real Pillow, with memory measured.

        81 MP is chosen deliberately. It is above our MAX_IMAGE_PIXELS
        (50 MP) but BELOW the threshold at which Pillow raises on its own
        (2x its 89.5 MP default), so it is exactly the band Pillow only
        WARNS about and would happily decode - about 320 MB of raster from
        a file smaller than this docstring. If our check were removed or
        moved after the decode, this test would allocate that.
        """
        from rest_framework.exceptions import ParseError

        from assignments.services import MAX_IMAGE_PIXELS, AssignmentProcessingService

        bomb = self._png_declaring(9000, 9000)  # 81 MP
        self.assertLess(len(bomb), 1024, "the bomb itself must stay tiny")
        self.assertGreater(9000 * 9000, MAX_IMAGE_PIXELS, "sanity: over our cap")

        uploaded = SimpleUploadedFile("bomb.png", bomb, content_type="image/png")
        rss_before = _rss_mb()

        with self.assertRaises(ParseError) as caught:
            AssignmentProcessingService._compress_uploaded_image(uploaded)

        rss_after = _rss_mb()
        self.assertIn("megapixel", str(caught.exception).lower())
        # 81 MP at 3 bytes/pixel is ~243 MB. Anything close to that means
        # the decode happened before the check.
        self.assertLess(
            rss_after - rss_before,
            50,
            f"memory grew {rss_after - rss_before:.0f}MB - the raster was "
            "allocated before it was refused",
        )

    def test_a_bomb_beyond_pillows_own_limit_is_a_400_not_a_500(self):
        """
        Above 2x Pillow's default, Pillow raises DecompressionBombError
        from Image.open itself. That must surface as a client error, not
        an unhandled exception.
        """
        from rest_framework.exceptions import ParseError

        from assignments.services import AssignmentProcessingService

        bomb = self._png_declaring(60000, 60000)  # 3.6 gigapixels
        uploaded = SimpleUploadedFile("huge.png", bomb, content_type="image/png")
        rss_before = _rss_mb()

        with self.assertRaises(ParseError):
            AssignmentProcessingService._compress_uploaded_image(uploaded)

        self.assertLess(_rss_mb() - rss_before, 50)

    def test_a_bomb_uploaded_over_http_is_rejected_cleanly(self):
        """The same payload through the real endpoint, end to end."""
        response = self._upload(
            b"bomb.png", self._png_declaring(9000, 9000), "image/png"
        )

        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)

    def test_an_image_just_under_the_cap_is_still_accepted(self):
        """
        The ceiling must not reject legitimate scans. A 600-dpi A4 page is
        about 35 MP, so this checks a realistic large scan still passes the
        dimension gate.
        """
        from assignments.services import MAX_IMAGE_PIXELS, AssignmentProcessingService

        under = self._png_declaring(6000, 6000)  # 36 MP
        self.assertLess(6000 * 6000, MAX_IMAGE_PIXELS)
        uploaded = SimpleUploadedFile("scan.png", under, content_type="image/png")

        with patch(
            "assignments.services.compress_image_for_upload", return_value=b"jpeg"
        ):
            # load() is what would fail on this truncated body; the point
            # here is that the DIMENSION gate let it through.
            try:
                AssignmentProcessingService._compress_uploaded_image(uploaded)
            except Exception as exc:
                self.assertNotIn(
                    "megapixel",
                    str(exc).lower(),
                    "a legitimate 36 MP scan was refused by the size cap",
                )

    def test_a_decompression_bomb_is_refused_before_its_pixels_are_allocated(self):
        """
        A small file that declares an enormous raster. The 50 MB size cap
        bounds the ENCODED bytes and lets this straight through; Pillow
        only warns below 2x its own default, so without an explicit pixel
        ceiling one request could allocate hundreds of MB.

        Asserted at the service boundary rather than over HTTP so the
        image never has to be built at full size in the test either.
        """
        from unittest.mock import MagicMock

        from assignments.services import MAX_IMAGE_PIXELS, AssignmentProcessingService

        oversized = MagicMock()
        oversized.size = (60_000, 60_000)  # 3.6 gigapixels
        oversized.load.side_effect = AssertionError("the pixels must never be decoded")

        uploaded = MagicMock()
        uploaded.name = "bomb.png"
        uploaded.read.return_value = b"irrelevant"

        with patch("assignments.services.Image.open", return_value=oversized):
            from rest_framework.exceptions import ParseError

            with self.assertRaises(ParseError) as caught:
                AssignmentProcessingService._compress_uploaded_image(uploaded)

        self.assertIn("megapixel", str(caught.exception).lower())
        self.assertGreater(60_000 * 60_000, MAX_IMAGE_PIXELS, "sanity")

    def test_a_normal_sized_image_still_gets_through(self):
        """The guard must scope, not simply reject every upload."""
        from unittest.mock import MagicMock

        from assignments.services import AssignmentProcessingService

        ordinary = MagicMock()
        ordinary.size = (1200, 1600)
        uploaded = MagicMock()
        uploaded.name = "scan.png"
        uploaded.read.return_value = b"irrelevant"

        with patch("assignments.services.Image.open", return_value=ordinary), patch(
            "assignments.services.compress_image_for_upload", return_value=b"jpeg"
        ):
            result = AssignmentProcessingService._compress_uploaded_image(uploaded)

        self.assertEqual(result, b"jpeg")

    def test_an_unsupported_type_is_named_and_refused(self):
        response = self._upload(b"notes.txt", b"hello", "text/plain")

        self.assertLess(response.status_code, 500)
        self.assertGreaterEqual(response.status_code, 400)


class ConcurrentAccessRevocationTest(TenancyAttackFixture, TransactionTestCase):
    """
    Racing a revocation against downloads.

    TransactionTestCase (not TestCase) because these threads need to see
    each other's committed writes - under TestCase everything shares one
    uncommitted transaction and the race cannot happen at all.
    """

    reset_sequences = False

    # H-2 (docs/HARDENING_BACKLOG.md): threads opened by this test get their
    # own DB connection. Any that outlives the test makes Django's final
    # DROP DATABASE fail with "database is being accessed by other users",
    # which exits the whole run non-zero even when every test passed. The
    # workers close their own connections; this closes the main thread's and
    # anything a worker died before releasing.
    def tearDown(self):
        connections.close_all()
        super().tearDown()

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        pdf_cache._inflight.clear()
        self.addCleanup(pdf_cache._inflight.clear)
        self.build_world()
        self.subject = make_student("attack-race-student@example.com", self.school_a)
        self.enrollment = enroll(self.subject, self.course_a)

    def test_no_download_succeeds_after_the_withdrawal_commits(self):
        """
        Twelve threads hammering the PDF endpoint while the student is
        withdrawn mid-flight. Requests that started first may legitimately
        succeed; what must never happen is a success after the revocation
        is visible, or a 500 from the race itself.
        """
        url = self.pdf_url(self.assignment_a)
        results = []
        lock = threading.Lock()
        withdrawn = threading.Event()
        start = threading.Barrier(13)

        def hammer():
            client = APIClient()
            client.force_authenticate(user=self.subject)
            start.wait(timeout=30)
            for _ in range(6):
                with patch(
                    "assignments.views.render_assignment_pdf",
                    return_value=b"%PDF-x",
                ):
                    response = client.get(url)
                with lock:
                    results.append((withdrawn.is_set(), response.status_code))

        def revoke():
            start.wait(timeout=30)
            self.enrollment.enrollment_status = EnrollmentStatusType.WITHDRAWN
            self.enrollment.save(update_fields=["enrollment_status"])
            cache.clear()
            withdrawn.set()

        threads = [threading.Thread(target=hammer) for _ in range(12)]
        threads.append(threading.Thread(target=revoke))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)

        self.assertTrue(results, "no requests completed")
        self.assertFalse(
            [code for _, code in results if code >= 500],
            f"the race produced server errors: {results}",
        )
        # Every request issued strictly after the revocation was visible
        # must have been refused.
        late = [code for after, code in results if after]
        self.assertTrue(
            all(code in REFUSED for code in late),
            f"a download succeeded after withdrawal: {late}",
        )


@unittest.skipUnless(
    hasattr(cache, "delete_pattern"),
    "needs a Redis-backed cache for the per-user list cache",
)
class PerUserCachePoisoningTest(TenancyAttackFixture, APITestCase):
    """
    UserCacheMixin caches list/retrieve responses. Its key includes the
    user id, so one user's cached page must never be served to another -
    which is the difference between a cache and a data leak.
    """

    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()

    def test_one_teachers_cached_list_is_not_served_to_another(self):
        self.as_user(self.teacher_a)
        first = self.client.get(self.list_url())
        self.assertIn("Secret Quiz A", first.content.decode())

        self.as_user(self.teacher_b)
        second = self.client.get(self.list_url())

        self.assertNotIn("Secret Quiz A", second.content.decode())
        self.assertIn("Secret Quiz B", second.content.decode())

    def test_a_students_cached_detail_is_not_served_to_a_different_student(self):
        self.as_user(self.student_a)
        self.client.get(self.detail_url(self.assignment_a))

        self.as_user(self.student_b)
        response = self.client.get(self.detail_url(self.assignment_a))

        self.assertIn(response.status_code, REFUSED)
        self.assertNotIn("Secret Quiz A", response.content.decode())

    def test_identical_query_params_from_different_users_do_not_collide(self):
        """
        The key hashes the query params, so two users issuing the exact
        same query is precisely the case where a user-blind key would
        cross the streams.
        """
        params = {"ordering": "title", "page": "1"}

        self.as_user(self.teacher_a)
        self.client.get(self.list_url(), params)

        self.as_user(self.teacher_b)
        body = self.client.get(self.list_url(), params).content.decode()

        self.assertNotIn("Secret Quiz A", body)
