"""B1a plan step 8: FR-B-08 / NFR-CRD-05 forced-failure proofs (plan §6.3
F-1 to F-3, NOTES §20).

Overage credits reach teachers' wallets by three paths, each in one
transaction: the Stripe checkout webhook, the super admin's grant, and the
offline-payment approval. A failure forced part way through must leave
nothing behind for anyone: no bucket, ledger row, block count, billing
record, BillingTransaction or credit audit event, and no email or refund.

TransactionTestCase, so each path's own atomic is the only transaction and
on_commit work runs only on a real commit. No Stripe call is live.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TransactionTestCase
from django.utils import timezone

from audit.enums import AuditAction
from audit.models import AuditEvent
from billing import tasks
from billing.billing_transaction_service import BillingTransactionService
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingTransaction,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditWallet,
    LicenseBillingRecord,
    LicenseOverageOfflineRequest,
    LicenseOverageOfflineRequestStatus,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
)
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import CustomUser, UserTypes

PRICE = 299
PI = "pi_ga"
SEND = "AutoGrader.tasks.send_email_task.delay"
CREATE = "stripe.Refund.create"
LIST = "stripe.Refund.list"
RECEIPT = "billing.stripe_service.schedule_receipt_url_fill"
REAL_LEDGER_RECORD = CreditLedger.record
REAL_BT_RECORD = BillingTransactionService.record


class Forced(RuntimeError):
    pass


class GrantAtomicityBase(TransactionTestCase):
    def setUp(self):
        self.school = School.objects.create(name="GA School")
        self.admin = CustomUser.objects.create_user(
            email="ga-admin@school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.super_admin = CustomUser.objects.create_superuser(
            email="ga-root@gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="GA License",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20000,
            overage_block_size=5000,
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
                email=f"ga-{tag}@school.edu",
                password="test123",  # pragma: allowlist secret
                user_type=UserTypes.TEACHER,
                school=self.school,
            )
            LicenseSubscriptionService.add_teacher_to_license(
                self.license_sub, teacher.email
            )
            self.teachers.append(teacher)
        self.a, self.b, self.c = self.teachers
        self.allocations = {str(t.id): 1 for t in self.teachers}
        self.inside = {}

    # --- forcing the failure ------------------------------------------------

    def fail_on_second_ledger_row(self, label):
        """The first teacher's grant goes through; the second's ledger row
        raises, after checking that the first bucket exists in the open
        transaction (so the rollback below is not vacuous)."""
        calls = []

        def record(*args, **kwargs):
            calls.append(kwargs.get("user"))
            if len(calls) == 2:
                self.inside["first_bucket"] = CreditBucket.objects.filter(
                    wallet__user=self.a, bucket_type=CreditBucketType.OVERAGE
                ).exists()
                self.inside["ledger_calls"] = len(calls)
                raise Forced(f"forced {label}")
            return REAL_LEDGER_RECORD(*args, **kwargs)

        return patch.object(CreditLedger, "record", side_effect=record)

    def bt_exists_then_raise(self, label):
        """A side effect for a call made after the BillingTransaction: it
        checks the row is there in the open transaction, then raises."""

        def hook(*args, **kwargs):
            self.inside["bt_written"] = BillingTransaction.objects.filter(
                license_subscription=self.license_sub
            ).exists()
            raise Forced(f"forced {label}")

        return hook

    # --- what must not survive ---------------------------------------------

    def snapshot(self):
        return {
            "ledger": CreditLedger.objects.count(),
            "bt": BillingTransaction.objects.count(),
            "lbr": LicenseBillingRecord.objects.count(),
            "audit": AuditEvent.objects.filter(
                action=AuditAction.CREDIT_TRANSACTION
            ).count(),
            "blocks": self.blocks_used(),
        }

    def blocks_used(self):
        return {
            str(t.id): CreditWallet.objects.filter(user=t)
            .values_list("overage_blocks_used", flat=True)
            .first()
            or 0
            for t in self.teachers
        }

    def assert_nothing_survived(self, before):
        self.assertFalse(
            CreditBucket.objects.filter(
                wallet__user__in=self.teachers, bucket_type=CreditBucketType.OVERAGE
            ).exists(),
            "an overage bucket survived the rollback",
        )
        self.assertEqual(self.snapshot(), before)

    def side_effects(self):
        """Every outward effect, patched so that none can be live."""
        return (
            patch(SEND),
            patch(CREATE),
            patch(LIST),
            patch.object(tasks.refund_departed_teacher_share, "delay"),
        )

    def assert_no_side_effects(self, mocks):
        for mock in mocks:
            mock.assert_not_called()


class WebhookPathTests(GrantAtomicityBase):
    """Path 1: handle_checkout_completed (atomic) → the overage handler."""

    def setUp(self):
        super().setUp()
        self.intent = LicenseOveragePurchaseIntent.objects.create(
            license_subscription=self.license_sub,
            initiated_by=self.admin,
            total_blocks=3,
            allocations=self.allocations,
            block_size_snapshot=self.plan.overage_block_size,
            unit_price_cents_snapshot=PRICE,
            amount_cents=3 * PRICE,
            status=LicenseOveragePurchaseStatus.PENDING,
            stripe_checkout_session_id="cs_ga",
        )
        self.session = {
            "id": "cs_ga",
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

    def deliver(self, forcing, label):
        before = self.snapshot()
        s, c, l, d = self.side_effects()
        with s as send, c as create, l as listing, d as delay, forcing:
            with self.assertRaisesMessage(Forced, f"forced {label}"):
                StripeWebhookHandler.handle_checkout_completed(self.session)
        return before, (send, create, listing, delay)

    def assert_intent_untouched(self):
        self.intent.refresh_from_db()
        self.assertEqual(self.intent.status, LicenseOveragePurchaseStatus.PENDING)
        self.assertFalse(self.intent.stripe_payment_intent_id)
        self.assertIsNone(self.intent.completed_at)
        self.assertIsNone(self.intent.failure_reason)

    def test_f1_failure_on_the_second_teacher_leaves_nothing(self):
        before, mocks = self.deliver(self.fail_on_second_ledger_row("F-1"), "F-1")
        self.assertEqual(self.inside, {"first_bucket": True, "ledger_calls": 2})
        self.assert_nothing_survived(before)
        self.assert_intent_untouched()
        self.assert_no_side_effects(mocks)

    def test_f1b_failure_after_the_billing_transaction_leaves_nothing(self):
        before, mocks = self.deliver(
            patch(RECEIPT, side_effect=self.bt_exists_then_raise("F-1b")), "F-1b"
        )
        self.assertEqual(self.inside, {"bt_written": True})
        self.assert_nothing_survived(before)
        self.assert_intent_untouched()
        self.assert_no_side_effects(mocks)

    def test_f1c_with_a_departed_teacher_no_refund_leaves(self):
        """Real money would move here: C left, so the share refund is due,
        and the failure on B (still active) must stop it."""
        SchoolCreditAllocation.objects.filter(
            license_subscription=self.license_sub, user=self.c
        ).update(is_active=False)
        before, mocks = self.deliver(self.fail_on_second_ledger_row("F-1c"), "F-1c")
        self.assertEqual(self.inside, {"first_bucket": True, "ledger_calls": 2})
        self.assert_nothing_survived(before)
        self.assert_intent_untouched()
        self.assert_no_side_effects(mocks)


class SuperAdminGrantPathTests(GrantAtomicityBase):
    """Path 2: _grant_overage_offline (atomic)."""

    def grant(self, forcing, label):
        before = self.snapshot()
        s, c, l, d = self.side_effects()
        with s as send, c as create, l as listing, d as delay, forcing:
            with self.assertRaisesMessage(Forced, f"forced {label}"):
                LicenseSubscriptionService._grant_overage_offline(
                    self.license_sub, self.super_admin, 3, self.allocations
                )
        return before, (send, create, listing, delay)

    def test_f2_failure_on_the_second_teacher_leaves_nothing(self):
        before, mocks = self.grant(self.fail_on_second_ledger_row("F-2"), "F-2")
        self.assertEqual(self.inside, {"first_bucket": True, "ledger_calls": 2})
        self.assert_nothing_survived(before)
        self.assertFalse(
            LicenseBillingRecord.objects.filter(
                record_type="MANUAL_OVERAGE_GRANT"
            ).exists()
        )
        self.assert_no_side_effects(mocks)

    def test_f2b_failure_after_the_billing_transaction_leaves_nothing(self):
        """Nothing is called after this path's BillingTransaction but a log
        line, so the write itself goes through and then raises (SM, §20)."""

        def record(*args, **kwargs):
            row = REAL_BT_RECORD(*args, **kwargs)
            self.inside["bt_written"] = BillingTransaction.objects.filter(
                pk=row.pk
            ).exists()
            raise Forced("forced F-2b")

        before, mocks = self.grant(
            patch.object(BillingTransactionService, "record", side_effect=record),
            "F-2b",
        )
        self.assertEqual(self.inside, {"bt_written": True})
        self.assert_nothing_survived(before)
        self.assertFalse(
            LicenseBillingRecord.objects.filter(
                record_type="MANUAL_OVERAGE_GRANT"
            ).exists()
        )
        self.assert_no_side_effects(mocks)


class OfflineApprovalPathTests(GrantAtomicityBase):
    """Path 3: approve_overage_offline_request (atomic)."""

    def setUp(self):
        super().setUp()
        with patch("billing.license_service.send_email_task"):
            result = LicenseSubscriptionService.request_overage_offline(
                self.license_sub,
                self.admin,
                total_blocks=3,
                allocations=self.allocations,
            )
        self.request = LicenseOverageOfflineRequest.objects.get(id=result["request_id"])

    def approve(self, forcing, label):
        before = self.snapshot()
        s, c, l, d = self.side_effects()
        with s as send, c as create, l as listing, d as delay, forcing:
            with self.assertRaisesMessage(Forced, f"forced {label}"):
                LicenseSubscriptionService.approve_overage_offline_request(
                    self.request,
                    performed_by=self.super_admin,
                    amount_confirmed_cents=3 * PRICE,
                    payment_reference="WIRE-GA",
                    payment_method_label="Wire transfer",
                )
        return before, (send, create, listing, delay)

    def assert_request_untouched(self):
        self.request.refresh_from_db()
        self.assertEqual(
            self.request.status, LicenseOverageOfflineRequestStatus.PENDING
        )
        self.assertIsNone(self.request.reviewed_by)

    def test_f3_failure_on_the_second_teacher_leaves_nothing(self):
        before, mocks = self.approve(self.fail_on_second_ledger_row("F-3"), "F-3")
        self.assertEqual(self.inside, {"first_bucket": True, "ledger_calls": 2})
        self.assert_nothing_survived(before)
        self.assert_request_untouched()
        self.assert_no_side_effects(mocks)

    def test_f3b_failure_after_the_billing_transaction_leaves_nothing(self):
        before, mocks = self.approve(
            patch.object(
                LicenseSubscriptionService,
                "_notify_school_admin_offline_overage_approved",
                side_effect=self.bt_exists_then_raise("F-3b"),
            ),
            "F-3b",
        )
        self.assertEqual(self.inside, {"bt_written": True})
        self.assert_nothing_survived(before)
        self.assert_request_untouched()
        self.assert_no_side_effects(mocks)
