"""Epic A §6: assignment create/update/delete audit instrumentation.

Each test drives the real HTTP endpoint once and asserts FR-A-01's literal
wording - exactly one AuditEvent, not >= 1 - catching both a missing emit
call and an accidental double-emit (e.g. a retry path calling it twice).
Only the billed AI extraction is stubbed; everything else is the real view,
serializer and permission chain.

Run with:
    python manage.py test assignments.tests_epic_a_crud_audit --settings=settings_worktree
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from audit.enums import AuditAction, AuditOutcome
from audit.models import AuditEvent
from billing.models import CreditBucket, CreditBucketType

from .models import Assignment, AssignmentStatus
from .tests_rigor import RigorFixtureMixin

FAKE_TASK = MagicMock(id="00000000-0000-0000-0000-0000000000aa")


class AssignmentCrudAuditTest(RigorFixtureMixin, APITestCase):
    def setUp(self):
        self.course = self.make_course()
        self.teacher = self.course.teacher
        self.client.force_authenticate(user=self.teacher)

    @patch(
        "assignments.views.AssignmentProcessingService.update_assignment_from_extraction"
    )
    def test_create_emits_exactly_one_event(self, mock_extract):
        mock_extract.side_effect = lambda user, assignment, *a, **k: assignment

        response = self.client.post(
            reverse("assignment-list"),
            {
                "course": str(self.course.id),
                "raw_input": "Q1. What is 2 + 2?",
                "title": "Sync Create",
                "status": AssignmentStatus.DRAFT,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        assignment = Assignment.objects.get(course=self.course, title="Sync Create")
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ASSIGNMENT_CREATE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(assignment.id))
        self.assertEqual(event.metadata["course_id"], str(self.course.id))

    @patch("assignments.views.launch_processing_task")
    @patch("assignments.views.create_processing_task")
    def test_create_async_emits_exactly_one_event(self, mock_create_task, mock_launch):
        mock_create_task.return_value = MagicMock()
        mock_launch.return_value = FAKE_TASK

        response = self.client.post(
            reverse("assignment-create-async"),
            {
                "course": str(self.course.id),
                "raw_input": "Q1. What is 2 + 2?",
                "title": "Async Create",
                "status": AssignmentStatus.DRAFT,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        assignment = Assignment.objects.get(course=self.course, title="Async Create")
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ASSIGNMENT_CREATE)
        self.assertEqual(str(event.target_id), str(assignment.id))

    def test_partial_update_emits_exactly_one_event(self):
        assignment = Assignment.objects.create(
            title="Original",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )

        response = self.client.patch(
            reverse("assignment-detail", kwargs={"pk": assignment.id}),
            {"title": "Renamed"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ASSIGNMENT_UPDATE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(assignment.id))
        self.assertEqual(event.metadata["changed_fields"], ["title"])

    def test_update_async_emits_exactly_one_event(self):
        # update-async is credit-gated at the permission-class level
        # (HasCreditBalance), even for a metadata-only edit.
        CreditBucket.objects.create(
            wallet=self.teacher.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
            expires_at=timezone.now() + timedelta(days=30),
        )
        assignment = Assignment.objects.create(
            title="Original",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
        )

        response = self.client.patch(
            reverse("assignment-update-async", kwargs={"pk": assignment.id}),
            {"title": "Renamed Async"},
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ASSIGNMENT_UPDATE)
        self.assertEqual(str(event.target_id), str(assignment.id))

    def test_destroy_emits_exactly_one_event(self):
        assignment = Assignment.objects.create(
            title="To delete",
            course=self.course,
            status=AssignmentStatus.DRAFT,
        )
        assignment_id = assignment.id

        response = self.client.delete(
            reverse("assignment-detail", kwargs={"pk": assignment.id})
        )

        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Assignment.objects.filter(pk=assignment_id).exists())
        self.assertEqual(AuditEvent.objects.count(), 1)
        event = AuditEvent.objects.get()
        self.assertEqual(event.action, AuditAction.ASSIGNMENT_DELETE)
        self.assertEqual(event.outcome, AuditOutcome.SUCCESS)
        self.assertEqual(str(event.target_id), str(assignment_id))
        self.assertEqual(event.metadata["course_id"], str(self.course.id))
