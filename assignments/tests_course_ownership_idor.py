"""H-18: a teacher could create or move an assignment into a course they
don't own.

`AssignmentTextSerializer.course` was a plain writable PK field with no
ownership check, and the viewset's get_queryset() only scopes which EXISTING
assignment a teacher can reach - never the course a new or edited one points
at. Knowing another teacher's course UUID was enough to:

  * create an assignment in it (POST assignments/, POST create-async/) -
    published, it appeared straight away to that teacher's students;
  * PATCH one's own assignment into it (PATCH assignments/<pk>/);
  * do the same through PATCH assignments/<pk>/update-async/, which built
    the serializer without a request in context.

Three doors, one serializer. A fourth write path - saving an AI-generated
draft - takes its course from the teacher's own draft session, and is tested
here for its client-supplied `topic`.

Every foreign-course attack runs against two targets: a course in ANOTHER
school, and a colleague's course in the attacker's OWN school (ownership is
per teacher, not per tenant). Every attack test asserts on the database, not
only the status code, and each has a matching legitimate-flow test on the
teacher's own courses so the fix cannot pass by refusing everything.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentGenerationMessage
from assignments.serializers import AssignmentTextSerializer
from assignments.tests_security import (
    REFUSED,
    TenancyAttackFixture,
    enroll,
    make_course,
    make_student,
    make_teacher,
)
from billing.models import CreditBucket, CreditBucketType
from classrooms.models import Topic
from users.models import CustomUser, UserTypes

FAKE_TASK = MagicMock(id="00000000-0000-0000-0000-000000000001")


def _passthrough_extraction(user, assignment, content, **kwargs):
    """Stands in for the billed AI extraction on the synchronous paths."""
    return assignment


class CourseOwnershipFixture(TenancyAttackFixture, APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()
        # A second course of B's own, for the legitimate "move" flows.
        self.course_b2 = make_course(self.teacher_b, "Course B2")
        # A colleague in B's OWN school: same tenant, different teacher.
        # Ownership is per teacher, so sharing a school must grant nothing.
        self.colleague_b = make_teacher("attack-colleague-b@example.com", self.school_b)
        self.course_colleague = make_course(self.colleague_b, "Colleague Course")
        self.student_colleague = make_student(
            "attack-student-colleague@example.com", self.school_b
        )
        enroll(self.student_colleague, self.course_colleague)
        self.foreign_targets = (
            ("other school", self.course_a, self.student_a),
            (
                "same school, other teacher",
                self.course_colleague,
                self.student_colleague,
            ),
        )

    def give_credits(self, teacher):
        CreditBucket.objects.create(
            wallet=teacher.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
        )

    def create_payload(self, course, title):
        return {
            "course": str(course.id),
            "raw_input": f"Q1. {title}",
            "title": title,
            "status": "PUBLISHED",
        }

    def assert_student_does_not_see(self, student, title):
        self.as_user(student)
        self.assertNotIn(title, self.client.get(self.list_url()).content.decode())


@patch(
    "assignments.views.AssignmentProcessingService.update_assignment_from_extraction",
    side_effect=_passthrough_extraction,
)
class CreateIntoForeignCourseTest(CourseOwnershipFixture):
    def test_teacher_b_cannot_create_in_a_foreign_course(self, mock_extract):
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                response = self.client.post(
                    self.list_url(),
                    self.create_payload(course, "PLANTED"),
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn("course", response.content.decode())
                self.assertFalse(Assignment.objects.filter(title="PLANTED").exists())
                mock_extract.assert_not_called()
                self.assert_student_does_not_see(student, "PLANTED")

    def test_teacher_b_cannot_create_async_in_a_foreign_course(self, mock_extract):
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                with patch(
                    "assignments.views.launch_processing_task", return_value=FAKE_TASK
                ) as mock_launch:
                    response = self.client.post(
                        reverse("assignment-create-async"),
                        self.create_payload(course, "PLANTED ASYNC"),
                        format="json",
                    )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertFalse(
                    Assignment.objects.filter(title="PLANTED ASYNC").exists()
                )
                mock_launch.assert_not_called()
                self.assert_student_does_not_see(student, "PLANTED ASYNC")

    def test_a_nonexistent_course_is_refused_the_same_way(self, mock_extract):
        # Not an ownership oracle: someone else's course and no course at
        # all both answer 400 on the same field.
        self.as_user(self.teacher_b)
        foreign = self.client.post(
            self.list_url(), self.create_payload(self.course_a, "X"), format="json"
        )
        payload = self.create_payload(self.course_a, "Y")
        payload["course"] = "00000000-0000-0000-0000-00000000dead"
        missing = self.client.post(self.list_url(), payload, format="json")

        self.assertEqual(foreign.status_code, missing.status_code)
        self.assertIn("course", missing.content.decode())

    # --- legitimate flows ---------------------------------------------------

    def test_teacher_can_create_in_own_course(self, mock_extract):
        self.as_user(self.teacher_b)
        response = self.client.post(
            self.list_url(),
            self.create_payload(self.course_b, "Own Quiz"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        created = Assignment.objects.get(title="Own Quiz")
        self.assertEqual(created.course_id, self.course_b.id)
        mock_extract.assert_called_once()

    def test_teacher_can_create_async_in_own_course(self, mock_extract):
        self.as_user(self.teacher_b)
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client.post(
                reverse("assignment-create-async"),
                self.create_payload(self.course_b, "Own Async Quiz"),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        created = Assignment.objects.get(title="Own Async Quiz")
        self.assertEqual(created.course_id, self.course_b.id)
        mock_launch.assert_called_once()


class MoveIntoForeignCourseTest(CourseOwnershipFixture):
    def patch_url(self, assignment):
        return self.detail_url(assignment)

    def update_async_url(self, assignment):
        return reverse("assignment-update-async", kwargs={"pk": assignment.id})

    def assert_still_in_course_b(self, student):
        self.assignment_b.refresh_from_db()
        self.assertEqual(self.assignment_b.course_id, self.course_b.id)
        self.assert_student_does_not_see(student, "Secret Quiz B")

    # --- PATCH assignments/<pk>/ -------------------------------------------

    def test_teacher_b_cannot_patch_own_assignment_into_a_foreign_course(self):
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                response = self.client.patch(
                    self.patch_url(self.assignment_b),
                    {"course": str(course.id)},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assert_still_in_course_b(student)

    def test_a_foreign_course_is_refused_even_alongside_other_fields(self):
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                response = self.client.patch(
                    self.patch_url(self.assignment_b),
                    {"course": str(course.id), "title": "Renamed"},
                    format="json",
                )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assert_still_in_course_b(student)
                self.assertEqual(self.assignment_b.title, "Secret Quiz B")

    def test_teacher_can_patch_own_assignment_into_own_other_course(self):
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.patch_url(self.assignment_b),
            {"course": str(self.course_b2.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assignment_b.refresh_from_db()
        self.assertEqual(self.assignment_b.course_id, self.course_b2.id)

    def test_patch_without_course_is_unaffected(self):
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.patch_url(self.assignment_b), {"title": "Renamed B"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assignment_b.refresh_from_db()
        self.assertEqual(self.assignment_b.title, "Renamed B")
        self.assertEqual(self.assignment_b.course_id, self.course_b.id)

    # --- PATCH assignments/<pk>/update-async/ (the third door) -------------

    def test_update_async_cannot_move_into_a_foreign_course(self):
        self.give_credits(self.teacher_b)
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                with patch(
                    "assignments.views.launch_processing_task", return_value=FAKE_TASK
                ) as mock_launch:
                    response = self.client.patch(
                        self.update_async_url(self.assignment_b),
                        {"course": str(course.id)},
                        format="json",
                    )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assert_still_in_course_b(student)
                mock_launch.assert_not_called()

    def test_update_async_with_raw_input_cannot_move_into_a_foreign_course(self):
        # The re-extraction branch commits metadata BEFORE queueing the AI
        # task, so validation has to stop it before either happens.
        self.give_credits(self.teacher_b)
        for label, course, student in self.foreign_targets:
            with self.subTest(target=label):
                self.as_user(self.teacher_b)
                with patch(
                    "assignments.views.launch_processing_task", return_value=FAKE_TASK
                ) as mock_launch:
                    response = self.client.patch(
                        self.update_async_url(self.assignment_b),
                        {"course": str(course.id), "raw_input": "Q1. moved"},
                        format="json",
                    )

                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assert_still_in_course_b(student)
                mock_launch.assert_not_called()

    def test_update_async_can_move_into_own_other_course(self):
        self.give_credits(self.teacher_b)
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.update_async_url(self.assignment_b),
            {"course": str(self.course_b2.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assignment_b.refresh_from_db()
        self.assertEqual(self.assignment_b.course_id, self.course_b2.id)

    def test_teacher_b_still_cannot_reach_teacher_a_assignment_at_all(self):
        # The existing object-level scoping is unchanged: A's assignment is
        # a 404 to B on both edit doors, before course is even considered.
        self.give_credits(self.teacher_b)
        self.as_user(self.teacher_b)
        for url in (
            self.patch_url(self.assignment_a),
            self.update_async_url(self.assignment_a),
        ):
            response = self.client.patch(
                url, {"course": str(self.course_b.id)}, format="json"
            )
            self.assertIn(response.status_code, REFUSED, url)
        self.assignment_a.refresh_from_db()
        self.assertEqual(self.assignment_a.course_id, self.course_a.id)


@patch(
    "assignments.views.AssignmentProcessingService.update_assignment_from_extraction",
    side_effect=_passthrough_extraction,
)
class ForeignTopicTest(CourseOwnershipFixture):
    """`topic` is scoped transitively, not by its own check.

    validate() requires topic.course == course, and course is either the
    ownership-checked `course` field or (on partial updates) the instance's
    course, which get_queryset() already scoped to the requester. So a topic
    from someone else's course can only ride in on someone else's course -
    which validate_course refuses. These tests hold that chain in place for
    every entry point, so dropping either link is caught.
    """

    def setUp(self):
        super().setUp()
        self.topic_a = Topic.objects.create(
            name="A's Private Topic", course=self.course_a
        )
        self.topic_b = Topic.objects.create(name="B's Own Topic", course=self.course_b)
        self.give_credits(self.teacher_b)
        self.as_user(self.teacher_b)

    def update_async_url(self, assignment):
        return reverse("assignment-update-async", kwargs={"pk": assignment.id})

    def test_create_in_own_course_with_foreign_topic_is_refused(self, mock_extract):
        payload = self.create_payload(self.course_b, "Topic Smuggle")
        payload["topic"] = str(self.topic_a.id)
        response = self.client.post(self.list_url(), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Assignment.objects.filter(title="Topic Smuggle").exists())
        mock_extract.assert_not_called()

    def test_create_async_in_own_course_with_foreign_topic_is_refused(
        self, mock_extract
    ):
        payload = self.create_payload(self.course_b, "Topic Smuggle Async")
        payload["topic"] = str(self.topic_a.id)
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client.post(
                reverse("assignment-create-async"), payload, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(
            Assignment.objects.filter(title="Topic Smuggle Async").exists()
        )
        mock_launch.assert_not_called()

    def test_foreign_course_and_its_own_topic_together_are_refused(self, mock_extract):
        # Internally consistent (topic belongs to course) - only the course
        # ownership check stands in the way.
        payload = self.create_payload(self.course_a, "Consistent Plant")
        payload["topic"] = str(self.topic_a.id)
        response = self.client.post(self.list_url(), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Assignment.objects.filter(title="Consistent Plant").exists())

    def test_patch_foreign_topic_is_refused(self, mock_extract):
        response = self.client.patch(
            self.detail_url(self.assignment_b),
            {"topic": str(self.topic_a.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assignment_b.refresh_from_db()
        self.assertIsNone(self.assignment_b.topic_id)

    def test_update_async_foreign_topic_is_refused(self, mock_extract):
        for payload in (
            {"topic": str(self.topic_a.id)},
            {"topic": str(self.topic_a.id), "raw_input": "Q1. smuggle"},
        ):
            with self.subTest(payload=sorted(payload)):
                with patch(
                    "assignments.views.launch_processing_task",
                    return_value=FAKE_TASK,
                ) as mock_launch:
                    response = self.client.patch(
                        self.update_async_url(self.assignment_b),
                        payload,
                        format="json",
                    )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                mock_launch.assert_not_called()
                self.assignment_b.refresh_from_db()
                self.assertIsNone(self.assignment_b.topic_id)

    # --- legitimate flows ---------------------------------------------------

    def test_create_with_own_topic_is_accepted(self, mock_extract):
        payload = self.create_payload(self.course_b, "Own Topic Quiz")
        payload["topic"] = str(self.topic_b.id)
        response = self.client.post(self.list_url(), payload, format="json")

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        created = Assignment.objects.get(title="Own Topic Quiz")
        self.assertEqual(created.topic_id, self.topic_b.id)

    def test_topic_only_patch_to_own_topic_is_accepted(self, mock_extract):
        # No `course` in the payload: validate() must fall back to the
        # instance's (owned) course, not refuse every topic-only edit.
        for url in (
            self.detail_url(self.assignment_b),
            self.update_async_url(self.assignment_b),
        ):
            with self.subTest(url=url):
                self.assignment_b.topic = None
                self.assignment_b.save(update_fields=["topic"])
                response = self.client.patch(
                    url, {"topic": str(self.topic_b.id)}, format="json"
                )
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assignment_b.refresh_from_db()
                self.assertEqual(self.assignment_b.topic_id, self.topic_b.id)


@patch("assignments.views.ai_processor.generate_assignment_from_prompt_with_retry")
class GeneratedDraftSaveTopicTest(CourseOwnershipFixture):
    """The fourth assignment write path: saving an AI-generated draft.

    Its course is not client-supplied - it comes from the draft message,
    which the view looks up filtered to session__user and
    session__course__teacher = request.user. Its `topic` IS client-supplied
    (SaveGeneratedAssignmentDraftSerializer.topic is Topic.objects.all()),
    so it must be held to the draft's course. The draft is created through
    the real generate endpoint, only the AI call mocked, so the message,
    session and snapshot are exactly what production writes.
    """

    def setUp(self):
        super().setUp()
        self.topic_a = Topic.objects.create(
            name="A's Private Topic", course=self.course_a
        )
        self.topic_b = Topic.objects.create(name="B's Own Topic", course=self.course_b)

    def generate_draft(self, mock_generate, teacher, course):
        from assignments.tests import generated_assignment_payload

        mock_generate.return_value = generated_assignment_payload()
        self.as_user(teacher)
        response = self.client.post(
            reverse("assignment-generate", kwargs={"course_id": course.id}),
            {"prompt": "Create a one-question biology quiz."},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data["message_id"]

    def save_url(self, message_id):
        return reverse(
            "assignment-save-generated-draft", kwargs={"message_id": message_id}
        )

    def test_saving_a_draft_with_a_foreign_topic_is_refused(self, mock_generate):
        message_id = self.generate_draft(mock_generate, self.teacher_b, self.course_b)
        before = Assignment.objects.count()

        response = self.client.post(
            self.save_url(message_id), {"topic": str(self.topic_a.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(Assignment.objects.count(), before)
        message = AssignmentGenerationMessage.objects.get(id=message_id)
        self.assertIsNone(message.assignment_id)
        self.assertEqual(message.metadata["draft_status"], "AI_DRAFT")

    def test_another_teacher_cannot_save_the_draft_into_its_course(self, mock_generate):
        message_id = self.generate_draft(mock_generate, self.teacher_a, self.course_a)
        before = Assignment.objects.count()

        for teacher in (self.teacher_b, self.colleague_b):
            with self.subTest(teacher=teacher.email):
                self.as_user(teacher)
                response = self.client.post(
                    self.save_url(message_id), {}, format="json"
                )
                self.assertIn(response.status_code, REFUSED)
        self.assertEqual(Assignment.objects.count(), before)

    # --- legitimate flow ----------------------------------------------------

    def test_saving_a_draft_with_own_topic_is_accepted(self, mock_generate):
        message_id = self.generate_draft(mock_generate, self.teacher_b, self.course_b)

        response = self.client.post(
            self.save_url(message_id), {"topic": str(self.topic_b.id)}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        saved = Assignment.objects.get(id=response.data["id"])
        self.assertEqual(saved.course_id, self.course_b.id)
        self.assertEqual(saved.topic_id, self.topic_b.id)


class SerializerBoundaryTest(CourseOwnershipFixture):
    """The rule lives on the serializer, so any view using it inherits it."""

    def serializer(self, user=None, **extra):
        context = {} if user is None else {"request": SimpleNamespace(user=user)}
        return AssignmentTextSerializer(
            data={"course": str(self.course_a.id), "raw_input": "Q1", **extra},
            context=context,
        )

    def test_no_request_in_context_fails_closed(self):
        serializer = self.serializer()
        self.assertFalse(serializer.is_valid())
        self.assertIn("course", serializer.errors)

    def test_foreign_teacher_is_refused(self):
        serializer = self.serializer(self.teacher_b)
        self.assertFalse(serializer.is_valid())
        self.assertIn("course", serializer.errors)

    def test_same_school_colleague_is_refused(self):
        colleague_a = make_teacher("h18-colleague-a@example.com", self.school_a)
        serializer = self.serializer(colleague_a)
        self.assertFalse(serializer.is_valid())
        self.assertIn("course", serializer.errors)

    def test_owner_is_accepted(self):
        self.assertTrue(self.serializer(self.teacher_a).is_valid())

    def test_true_superadmin_is_accepted(self):
        superadmin = CustomUser.objects.create_user(
            email="h18-superadmin@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
        )
        self.assertTrue(self.serializer(superadmin).is_valid())

    def test_django_admin_only_account_is_not_a_superadmin_here(self):
        django_admin = CustomUser.objects.create_superuser(
            email="h18-djadmin@example.com",
            password="password123",  # pragma: allowlist secret
        )
        serializer = self.serializer(django_admin)
        self.assertFalse(serializer.is_valid())
        self.assertIn("course", serializer.errors)
