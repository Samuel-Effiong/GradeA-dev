"""H-300: one school overage purchase is fulfilled at most once.

A purchase where a teacher left before payment is saved FAILED (status) with
its grants made, and the handler's old guard stopped only on COMPLETED. A
second run of the same checkout (the sweeper marks a stale, started claim
FAILED, then `replay_stripe_events --apply`; or a redelivery of a FAILED
row) granted the remaining teachers again. The guard now stops on any sign
the purchase was already fulfilled: `completed_at` (written only with the
grants) or a CreditLedger row carrying the intent's id. A purchase already
recorded as not granted for this PaymentIntent (license inactive, unpaid)
is not recorded again.

Road B, end to end: the worker commits, then dies before SUCCEEDED; the
sweeper marks the claim FAILED; an operator replays it. Nothing reaches
Stripe.
"""

from datetime import timedelta

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    StripeEvent,
    StripeEventStatus,
    SubscriptionPlan,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tasks import sweep_stale_stripe_events
from billing.webhooks import STRIPE_EVENT_CLAIM_STALE_AFTER, _claim_stripe_event
from classrooms.models import School
from users.models import CustomUser, UserTypes

PRICE = 299
BLOCK = 5000
PI = "pi_h300"
EVENT_ID = "evt_h300"


class ReprocessBase(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="H300 School")
        self.admin = CustomUser.objects.create_user(
            email="h300-admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="H300 License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=BLOCK,
            overage_block_price=PRICE,
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            max_seats=3,
        )
        self.teachers = []
        for tag in ("a", "b", "c"):
            teacher = CustomUser.objects.create_user(
                email=f"h300-{tag}@school.edu",
                password="test123",  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
                school=self.school,
            )
            LicenseSubscriptionService.add_teacher_to_license(
                self.license_sub, teacher.email
            )
            self.teachers.append(teacher)
        self.a, self.b, self.c = self.teachers
        self.intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=self.license_sub,
            initiated_by=self.admin,
            total_blocks=3,
            allocations={str(t.id): 1 for t in self.teachers},
            block_size_snapshot=BLOCK,
            unit_price_cents_snapshot=PRICE,
            amount_cents=3 * PRICE,
            status=LicenseOveragePurchaseStatus.PENDING,
            stripe_checkout_session_id="cs_h300",
        )
        self.session = {
            "id": "cs_h300",
            "object": "checkout.session",
            "payment_status": "paid",
            "payment_intent": PI,
            "invoice": None,
            "amount_total": 3 * PRICE,
            "currency": "usd",
            "metadata": {
                "flow": "license_overage_purchase_checkout",
                "license_id": str(self.license_sub.id),
                "intent_id": str(self.intent.id),
            },
        }
        self.event = {
            "id": EVENT_ID,
            "type": "checkout.session.completed",
            "data": {"object": self.session},
        }

    def depart(self, teacher):
        SchoolCreditAllocation.objects.filter(
            license_subscription=self.license_sub, user=teacher
        ).update(is_active=False)

    def credits(self, teacher):
        return sum(
            CreditBucket.objects.filter(
                wallet__user=teacher, bucket_type=CreditBucketType.OVERAGE
            ).values_list("total_credits", flat=True)
        )

    def purchase_records(self):
        return BillingTransaction.objects.filter(stripe_payment_intent_id=PI).count()

    def row(self):
        return StripeEvent.objects.get(stripe_event_id=EVENT_ID)

    def run_then_die(self):
        """The worker commits the handler's work, then dies before SUCCEEDED;
        the sweeper marks the stale, started claim FAILED."""
        _, token = _claim_stripe_event(self.event)
        StripeEvent.objects.filter(stripe_event_id=EVENT_ID).update(
            handler_started_at=timezone.now()
        )
        with self.captureOnCommitCallbacks(execute=True):
            StripeWebhookHandler.handle_checkout_completed(self.session)
        StripeEvent.objects.filter(stripe_event_id=EVENT_ID).update(
            claimed_at=token - STRIPE_EVENT_CLAIM_STALE_AFTER - timedelta(minutes=1)
        )
        sweep_stale_stripe_events.run()
        self.assertEqual(self.row().status, StripeEventStatus.FAILED)

    def replay(self):
        with self.captureOnCommitCallbacks(execute=True):
            call_command("replay_stripe_events", "--event-id", EVENT_ID, "--apply")
        self.assertEqual(self.row().status, StripeEventStatus.SUCCEEDED)


class ReplayDoesNotFulfillTwiceTests(ReprocessBase):
    def test_h300_b1_a_skipped_teacher_purchase_is_not_granted_again(self):
        self.depart(self.c)
        self.run_then_die()
        self.assertEqual(self.credits(self.a), BLOCK)
        self.replay()
        self.assertEqual(
            (self.credits(self.a), self.credits(self.b), self.credits(self.c)),
            (BLOCK, BLOCK, 0),
            "replay granted the remaining teachers a second time",
        )
        self.assertEqual(self.purchase_records(), 1)

    def test_h300_b4_control_a_full_purchase_is_not_granted_again(self):
        self.run_then_die()
        self.replay()
        self.assertEqual(
            [self.credits(t) for t in self.teachers], [BLOCK, BLOCK, BLOCK]
        )
        self.assertEqual(self.purchase_records(), 1)

    def test_h300_c1_an_inactive_license_purchase_is_not_recorded_again(self):
        LicenseSubscription.objects.filter(pk=self.license_sub.pk).update(
            is_active=False
        )
        self.run_then_die()
        self.assertEqual(self.purchase_records(), 1)
        self.replay()
        self.assertEqual(self.purchase_records(), 1)
        self.assertEqual([self.credits(t) for t in self.teachers], [0, 0, 0])

    def test_h300_d1_a_ledger_row_for_the_intent_alone_stops_a_rerun(self):
        # The belt: even with completed_at empty, the grants' own ledger
        # rows say the purchase was fulfilled.
        self.depart(self.c)
        self.run_then_die()
        LicenseOveragePurchaseIntent.objects.filter(pk=self.intent.pk).update(
            completed_at=None
        )
        self.replay()
        self.assertEqual(
            (self.credits(self.a), self.credits(self.b)),
            (BLOCK, BLOCK),
            "replay granted the remaining teachers a second time",
        )
