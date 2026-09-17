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

Three doors, one serializer. Every attack test asserts on the database, not
only the status code, and each has a matching legitimate-flow test on the
teacher's own courses so the fix cannot pass by refusing everything.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.core.cache import cache
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment
from assignments.serializers import AssignmentTextSerializer
from assignments.tests_security import REFUSED, TenancyAttackFixture, make_course
from billing.models import CreditBucket, CreditBucketType
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

    def assert_student_a_does_not_see(self, title):
        self.as_user(self.student_a)
        self.assertNotIn(title, self.client.get(self.list_url()).content.decode())


@patch(
    "assignments.views.AssignmentProcessingService.update_assignment_from_extraction",
    side_effect=_passthrough_extraction,
)
class CreateIntoForeignCourseTest(CourseOwnershipFixture):
    def test_teacher_b_cannot_create_in_teacher_a_course(self, mock_extract):
        self.as_user(self.teacher_b)
        response = self.client.post(
            self.list_url(),
            self.create_payload(self.course_a, "PLANTED"),
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("course", response.content.decode())
        self.assertFalse(Assignment.objects.filter(title="PLANTED").exists())
        mock_extract.assert_not_called()
        self.assert_student_a_does_not_see("PLANTED")

    def test_teacher_b_cannot_create_async_in_teacher_a_course(self, mock_extract):
        self.as_user(self.teacher_b)
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client.post(
                reverse("assignment-create-async"),
                self.create_payload(self.course_a, "PLANTED ASYNC"),
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(Assignment.objects.filter(title="PLANTED ASYNC").exists())
        mock_launch.assert_not_called()
        self.assert_student_a_does_not_see("PLANTED ASYNC")

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

    def assert_still_in_course_b(self):
        self.assignment_b.refresh_from_db()
        self.assertEqual(self.assignment_b.course_id, self.course_b.id)
        self.assert_student_a_does_not_see("Secret Quiz B")

    # --- PATCH assignments/<pk>/ -------------------------------------------

    def test_teacher_b_cannot_patch_own_assignment_into_teacher_a_course(self):
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.patch_url(self.assignment_b),
            {"course": str(self.course_a.id)},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_still_in_course_b()

    def test_a_foreign_course_is_refused_even_alongside_other_fields(self):
        self.as_user(self.teacher_b)
        response = self.client.patch(
            self.patch_url(self.assignment_b),
            {"course": str(self.course_a.id), "title": "Renamed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_still_in_course_b()
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

    def test_update_async_cannot_move_into_teacher_a_course(self):
        self.give_credits(self.teacher_b)
        self.as_user(self.teacher_b)
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client.patch(
                self.update_async_url(self.assignment_b),
                {"course": str(self.course_a.id)},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_still_in_course_b()
        mock_launch.assert_not_called()

    def test_update_async_with_raw_input_cannot_move_into_teacher_a_course(self):
        # The re-extraction branch commits metadata BEFORE queueing the AI
        # task, so validation has to stop it before either happens.
        self.give_credits(self.teacher_b)
        self.as_user(self.teacher_b)
        with patch(
            "assignments.views.launch_processing_task", return_value=FAKE_TASK
        ) as mock_launch:
            response = self.client.patch(
                self.update_async_url(self.assignment_b),
                {"course": str(self.course_a.id), "raw_input": "Q1. moved"},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assert_still_in_course_b()
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
