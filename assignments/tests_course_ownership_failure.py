"""H-18 / H-19 failure and recovery (Gate 5).

For each changed path, a dependency fails part-way and the resulting state is
asserted to be an intentional one:

  * AI provider timeout / rejection during extraction whose (would-be)
    output carries forbidden keys: the transaction rolls back - no half-
    created assignment on create; the existing assignment unchanged on
    re-extraction. Nothing from the injected payload is written.
  * Celery redelivery of an extraction task whose provider output carries
    forbidden keys: running the same task twice writes the same filtered
    content twice - the second delivery cannot smuggle what the first
    filtered out.
  * The credit gate when the database errors reading the wallet: the error
    propagates as a server error and the request is never admitted -
    fail closed, never "assume credit".

Not applicable here, with reason: Redis. Neither HasCreditBalance nor the
both-flags branch of execute_graded_task, the AI-output allow-list or
validate_course reads or writes the cache, so a Redis outage cannot change
their decision (the surrounding endpoints' own cache behaviour is H-1's).
"""

from unittest.mock import patch
from uuid import uuid4

from django.core.cache import cache
from django.db import OperationalError
from django.urls import reverse
from rest_framework.test import APITestCase

from assignments.models import Assignment, AssignmentStatus
from assignments.tasks import extract_assignment_background_task
from assignments.tests_extraction_service import extraction_payload
from assignments.tests_security import TenancyAttackFixture
from billing.models import CreditBucket, CreditBucketType, CreditWallet

EXTRACT = "assignments.services.ai_processor.extract_assignment_with_retry"


class ExtractionFailureFixture(TenancyAttackFixture, APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.build_world()
        CreditBucket.objects.create(
            wallet=self.teacher_b.credit_wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=1000,
            used_credits=0,
        )
        self.as_user(self.teacher_b)
        self.client.raise_request_exception = False

    def poisoned_payload(self):
        return {
            **extraction_payload(title="Injected Title"),
            "teacher": str(self.teacher_a.id),
            "status": AssignmentStatus.PUBLISHED,
            "course": str(self.course_a.id),
        }

    def snapshot(self, assignment):
        assignment.refresh_from_db()
        return (
            assignment.title,
            assignment.status,
            assignment.course_id,
            assignment.teacher_id,
            assignment.questions,
            assignment.raw_input,
        )


class ProviderFailureDuringExtractionTest(ExtractionFailureFixture):
    FAILURES = (
        ("timeout", TimeoutError("provider timed out")),
        ("rejection", RuntimeError("provider rejected the request: 400")),
    )

    def test_create_rolls_back_completely_when_the_provider_fails(self):
        for label, failure in self.FAILURES:
            with self.subTest(failure=label):
                before = Assignment.objects.count()
                with patch(EXTRACT, side_effect=failure):
                    response = self.client.post(
                        self.list_url(),
                        {
                            "course": str(self.course_b.id),
                            "raw_input": f"Q1. failing {label}",
                            "title": f"Failing {label}",
                        },
                        format="json",
                    )
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(Assignment.objects.count(), before, "half-created row")
                self.assertFalse(
                    Assignment.objects.filter(course=self.course_a)
                    .exclude(pk__in=[self.assignment_a.pk, self.draft_a.pk])
                    .exists()
                )

    def test_reextraction_leaves_the_assignment_untouched_when_the_provider_fails(self):
        self.assignment_b.status = AssignmentStatus.DRAFT
        self.assignment_b.save(update_fields=["status"])
        for label, failure in self.FAILURES:
            with self.subTest(failure=label):
                before = self.snapshot(self.assignment_b)
                with patch(EXTRACT, side_effect=failure):
                    response = self.client.patch(
                        self.detail_url(self.assignment_b),
                        {"raw_input": f"Q1. edited {label}"},
                        format="json",
                    )
                self.assertGreaterEqual(response.status_code, 400)
                self.assertEqual(self.snapshot(self.assignment_b), before)


class CeleryRedeliveryTest(ExtractionFailureFixture):
    def test_redelivered_extraction_task_never_writes_forbidden_keys(self):
        self.assignment_b.status = AssignmentStatus.DRAFT
        self.assignment_b.save(update_fields=["status"])
        args = [
            str(self.teacher_b.id),
            str(self.assignment_b.id),
            [{"type": "text", "text": "x"}],
        ]

        with patch(EXTRACT, return_value=self.poisoned_payload()):
            first = extract_assignment_background_task.apply(args=args)
            after_first = self.snapshot(self.assignment_b)
            second = extract_assignment_background_task.apply(args=args)  # redelivery
            after_second = self.snapshot(self.assignment_b)

        self.assertTrue(first.successful(), first.result)
        self.assertTrue(second.successful(), second.result)
        self.assertEqual(after_first, after_second)
        _title, status_, course_id, teacher_id, questions, _raw = after_second
        self.assertEqual(status_, AssignmentStatus.DRAFT)
        self.assertEqual(course_id, self.course_b.id)
        self.assertIsNone(teacher_id)
        self.assertEqual(questions[0]["model_answer"], "4")

    def test_a_failed_delivery_then_a_successful_retry_writes_only_filtered_content(
        self,
    ):
        self.assignment_b.status = AssignmentStatus.DRAFT
        self.assignment_b.save(update_fields=["status"])
        before = self.snapshot(self.assignment_b)
        args = [
            str(self.teacher_b.id),
            str(self.assignment_b.id),
            [{"type": "text", "text": "x"}],
        ]

        with patch(EXTRACT, side_effect=TimeoutError("provider timed out")):
            failed = extract_assignment_background_task.apply(args=args)
        self.assertTrue(failed.failed())
        self.assertEqual(self.snapshot(self.assignment_b), before)

        with patch(EXTRACT, return_value=self.poisoned_payload()):
            retried = extract_assignment_background_task.apply(args=args)
        self.assertTrue(retried.successful(), retried.result)
        _title, status_, course_id, teacher_id, _q, _raw = self.snapshot(
            self.assignment_b
        )
        self.assertEqual(
            (status_, course_id, teacher_id),
            (AssignmentStatus.DRAFT, self.course_b.id, None),
        )


class CreditGateDatabaseFailureTest(ExtractionFailureFixture):
    def test_wallet_read_failure_never_admits_the_request(self):
        # PATCH submissions/<pk> is gated only by [IsAuthenticated,
        # HasCreditBalance]. Admission would surface as the queryset's 404.
        with patch.object(
            CreditWallet,
            "total_remaining_credits",
            side_effect=OperationalError("connection to server was lost"),
        ):
            response = self.client.patch(
                reverse("student-submission-detail", kwargs={"pk": uuid4()}),
                {"raw_input": "x"},
                format="json",
            )
        self.assertEqual(response.status_code, 500)
        self.assertNotEqual(response.status_code, 404)
