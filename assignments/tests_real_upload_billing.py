"""
ONE real, billed extraction through the teacher batch-upload endpoint,
proving the Section 9 blocker fix against the real provider.

Opt-in: set RUN_REAL_AI=1. It costs money and needs network, so it is
skipped by default, the same as tests_real_extraction.py.

tests_upload_batch_billing.py already proves the per-file outcome, the
refunds and the retry guard with real wallets and a stand-in provider. Per
this project's standing rule a billed path must also be verified against the
real provider: a stand-in returns whatever shape its author imagined, and
cannot show that a genuine response is charged once and then recognised on
replay.

The scenario is the blocker exactly: a batch with one good file and one
unreadable file, then the same batch sent again. The good file must be
extracted and charged once; the bad file never reaches the AI; the replay
must return the existing assignment with no new charge and no new
assignment.
"""

import unittest
from datetime import timedelta

from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from assignments.models import Assignment
from assignments.tests_real_extraction import (
    RUN_REAL_AI,
    SKIP_REASON,
    assignment_image_bytes,
)
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditUsageLog,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from classrooms.models import Course, Session
from users.models import CustomUser, UserTypes


@unittest.skipUnless(RUN_REAL_AI, SKIP_REASON)
class RealProviderBatchUploadBillingTest(TransactionTestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.teacher = CustomUser.objects.create_user(
            email="real-batch-upload@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            first_name="Real",
            last_name="Batch",
            is_active=True,
        )
        # The AI access gate needs an active account, an active subscription
        # context and remaining credits - built for real, not mocked.
        plan = SubscriptionPlan.objects.create(
            name=PlanType.STANDARD,
            display_name="Real Batch Upload",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.STANDARD,
            interval=BillingInterval.MONTHLY,
            monthly_credits=500_000,
            overage_block_size=500,
            overage_block_price=10,
            max_overage_blocks=10,
            is_active=True,
        )
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.teacher,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=self.teacher)
        CreditBucket.objects.filter(wallet=wallet).delete()
        # One real image extraction measured ~33k credits; sized well clear.
        CreditBucket.objects.create(
            wallet=wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=500_000,
            used_credits=0,
            expires_at=now + timedelta(days=25),
        )
        session = Session.objects.create(
            name="Real Batch Session", teacher=self.teacher
        )
        self.course = Course.objects.create(
            name="Real Batch Course", teacher=self.teacher, session=session
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.teacher)

    def _upload_batch(self):
        return self.client.post(
            reverse("assignment-upload"),
            {
                "course": str(self.course.id),
                "assignments": [
                    SimpleUploadedFile(
                        "quiz.png", assignment_image_bytes(), content_type="image/png"
                    ),
                    SimpleUploadedFile(
                        "broken.pdf",
                        b"not really a pdf",
                        content_type="application/pdf",
                    ),
                ],
            },
            format="multipart",
        )

    def _charges(self):
        logs = CreditUsageLog.objects.filter(wallet__user=self.teacher)
        return (
            list(logs.filter(is_refunded=False).values_list("amount", flat=True)),
            list(logs.filter(is_refunded=True).values_list("amount", flat=True)),
        )

    def test_a_real_partly_failed_batch_is_charged_once_and_replays_free(self):
        first = self._upload_batch()

        print("\n=== REAL BATCH UPLOAD: first request ===")
        print("status:", first.status_code)
        self.assertEqual(
            first.status_code, status.HTTP_207_MULTI_STATUS, first.content[:500]
        )
        data = first.json()["data"]
        [created] = data["successful"]
        [refused] = data["failed"]
        self.assertEqual(created["file_name"], "quiz.png")
        self.assertIs(created["already_uploaded"], False)
        self.assertEqual(refused["file_name"], "broken.pdf")

        kept, refunded = self._charges()
        print("charges kept:", kept, "refunded:", refunded)
        self.assertEqual(len(kept), 1, "the good file must be charged exactly once")
        self.assertGreater(kept[0], 0)
        self.assertEqual(refunded, [])
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)

        replay = self._upload_batch()

        print("=== REAL BATCH UPLOAD: identical replay ===")
        print("status:", replay.status_code)
        self.assertEqual(replay.status_code, status.HTTP_207_MULTI_STATUS)
        replay_data = replay.json()["data"]
        [again] = replay_data["successful"]
        self.assertIs(again["already_uploaded"], True)
        self.assertEqual(again["assignment"]["id"], created["assignment"]["id"])
        self.assertEqual(
            [entry["file_name"] for entry in replay_data["failed"]], ["broken.pdf"]
        )

        self.assertEqual(self._charges(), (kept, []), "the replay was charged again")
        self.assertEqual(Assignment.objects.filter(course=self.course).count(), 1)
        print("charges after replay:", self._charges())
