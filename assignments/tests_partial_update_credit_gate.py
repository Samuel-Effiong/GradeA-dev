"""partial_update's AI (raw_input) branch re-extracts an assignment through
a billed AI call, exactly like the other AI-triggering endpoints on this
viewset - but unlike its async sibling `update_async` (which already gates
on HasCreditBalance), it had no credit-balance check at all, so a teacher
with an empty wallet could still trigger a billed AI call through this path.
"""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tests_rigor import RigorFixtureMixin
from billing.models import CreditBucket, CreditBucketType


class PartialUpdateCreditGateTest(RigorFixtureMixin, APITestCase):
    def setUp(self):
        self.course = self.make_course()
        self.teacher = self.course.teacher
        self.assignment = Assignment.objects.create(
            title="MCQ",
            course=self.course,
            status=AssignmentStatus.PUBLISHED,
            questions=[
                {
                    "question_number": 1,
                    "question_text": "Q1",
                    "question_type": "OBJECTIVE",
                    "points": 10,
                    "options": ["one", "two"],
                    "rubric": [],
                    "model_answer": "one",
                }
            ],
        )
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("assignment-detail", kwargs={"pk": self.assignment.id})

    @patch(
        "assignments.views.AssignmentProcessingService.update_assignment_from_extraction"
    )
    def test_raw_input_edit_is_blocked_without_credits(self, mock_extract):
        # A freshly created teacher's CreditWallet has no buckets, so
        # total_remaining_credits() is 0 - no setup needed to simulate the
        # insufficient-balance case.
        response = self.client.patch(
            self.url, {"raw_input": "Some edited assignment text"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        mock_extract.assert_not_called()

    @patch(
        "assignments.views.AssignmentProcessingService.update_assignment_from_extraction"
    )
    def test_raw_input_edit_proceeds_with_sufficient_credits(self, mock_extract):
        mock_extract.return_value = self.assignment
        CreditBucket.objects.create(
            wallet=self.teacher.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
        )

        response = self.client.patch(
            self.url, {"raw_input": "Some edited assignment text"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        mock_extract.assert_called_once()

    def test_metadata_only_edit_is_never_gated_on_credits(self):
        # No raw_input in the payload -> the synchronous, non-AI branch;
        # must not require any credit balance.
        response = self.client.patch(self.url, {"title": "Renamed MCQ"}, format="json")

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assignment.refresh_from_db()
        self.assertEqual(self.assignment.title, "Renamed MCQ")
