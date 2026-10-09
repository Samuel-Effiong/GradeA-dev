"""
P1 — no checkout.session.completed flow may call Stripe for a receipt link
while its webhook transaction is open.

Every flow handler dispatched from
`StripeWebhookHandler.handle_checkout_completed` (one @transaction.atomic
block) used to resolve the receipt link inline, AFTER writing the paid
grant/activation and while holding its row locks. Production and beta run
Postgres with `idle_in_transaction_session_timeout = 60s`, below
stripe-python's 80s default timeout, so a slow Stripe response terminated
the session and rolled back a PAID grant. The async webhook endpoint had
already answered Stripe 200, so nothing ever retried it.

Each test drives one flow end to end through the real dispatcher on real
Postgres (TransactionTestCase, so commits and on_commit callbacks are real)
against a fake Stripe at the HTTP layer, and asserts:

  1. every receipt lookup ran with NO transaction open;
  2. a receipt lookup still happened, and its URL landed on the
     BillingTransaction (deferred, not dropped);
  3. the Stripe reference the deferred lookup needs is stored on the row;
  4. the flow's business effect is committed.

Prior state is built through the production services (activate_free_trial,
activate_subscription, the license_create webhook itself,
initiate_overage_purchase, handle_subscription_deleted), never by writing
fields production does not write.

Celery is not eager under tests. Background tasks are intercepted: the
receipt-fill task runs synchronously (as a worker would, outside any
transaction), everything else (e.g. MailerLite sync) is recorded and
dropped so nothing reaches the network.
"""

from contextlib import contextmanager
from unittest import mock

from celery.app.task import Task
from django.db import connections
from django.test import TransactionTestCase

from billing.license_service import LicenseSubscriptionService
from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import StripeWebhookHandler
from billing.tests.testing_fake_stripe import (
    assert_no_call_inside_transaction,
    charge_receipt_url,
    fake_stripe,
    invoice_url,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

RECEIPT_TASK_NAME = "billing.tasks.fill_billing_transaction_receipt_url"


@contextmanager
def run_receipt_tasks_inline():
    """Run the receipt-fill task synchronously; drop every other task."""
    dropped = []

    def apply_async(task, args=None, kwargs=None, **options):
        if task.name == RECEIPT_TASK_NAME:
            try:
                return task.run(*(args or ()), **(kwargs or {}))
            finally:
                # A worker runs each task on its own connection.
                connections.close_all()
        dropped.append(task.name)
        return None

    def delay(task, *args, **kwargs):
        return apply_async(task, args, kwargs)

    with mock.patch.object(Task, "apply_async", apply_async), mock.patch.object(
        Task, "delay", delay
    ):
        yield dropped


def pi_receipt(pi_id):
    return charge_receipt_url(f"ch_for_{pi_id}")


def assert_receipts_outside_transaction(test, fake):
    """The P1 invariant: a receipt lookup happened, and none of them ran
    while the webhook transaction (and its row locks) was open."""
    test.assertTrue(
        fake.receipt_lookups(),
        "no receipt lookup happened at all: the link was dropped",
    )
    assert_no_call_inside_transaction(test, fake, only_receipt_lookups=True)


def txn_by(**lookup):
    return BillingTransaction.objects.get(**lookup)


# ---------------------------------------------------------------------------
# Individual flows
# ---------------------------------------------------------------------------


def make_individual_plan(
    name=PlanType.STANDARD,
    tier=PlanTier.STANDARD,
    price_id="price_standard",
    monthly=10_000_000,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=999,
        monthly_credits=monthly,
        stripe_price_id=price_id,
        overage_block_size=500,
        overage_block_price=10,
        max_overage_blocks=10,
        stripe_overage_price_id="price_overage",
        is_active=True,
    )


def make_teacher(email, school=None):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        is_active=True,
        school=school,
    )


class IndividualFlowReceiptTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.plan = make_individual_plan()
        self.pro = make_individual_plan(
            name=PlanType.PRO,
            tier=PlanTier.PRO,
            price_id="price_pro",
            monthly=30_000_000,
        )

    def deliver(self, session):
        with fake_stripe() as fake, run_receipt_tasks_inline():
            StripeWebhookHandler.handle_checkout_completed(session)
        return fake

    # site 2861 -------------------------------------------------------------
    def test_individual_checkout_trial_conversion(self):
        user = make_teacher("trial.convert@p1.test")
        trial_sub = SubscriptionService.activate_free_trial(user, self.plan)

        fake = self.deliver(
            {
                "id": "cs_trial_conv",
                "object": "checkout.session",
                "subscription": "sub_trial_conv",
                "invoice": "in_trial_conv",
                "payment_intent": "pi_trial_conv",
                "amount_total": 999,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "individual_checkout",
                    "user_id": str(user.id),
                    "plan_id": str(self.pro.id),
                    "trial_subscription_id": str(trial_sub.id),
                },
            }
        )

        assert_receipts_outside_transaction(self, fake)
        trial_sub.refresh_from_db()
        self.assertFalse(trial_sub.is_trial)
        self.assertEqual(trial_sub.plan_id, self.pro.id)
        txn = txn_by(stripe_invoice_id="in_trial_conv")
        self.assertEqual(txn.stripe_payment_intent_id, "pi_trial_conv")
        self.assertEqual(txn.receipt_url, invoice_url("in_trial_conv"))

    # site 2912 -------------------------------------------------------------
    def test_individual_checkout_fresh_activation(self):
        user = make_teacher("fresh.activation@p1.test")

        fake = self.deliver(
            {
                "id": "cs_fresh",
                "object": "checkout.session",
                "subscription": "sub_fresh",
                "invoice": "in_fresh",
                "payment_intent": "pi_fresh",
                "amount_total": 999,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "individual_checkout",
                    "user_id": str(user.id),
                    "plan_id": str(self.plan.id),
                },
            }
        )

        assert_receipts_outside_transaction(self, fake)
        self.assertTrue(
            UserSubscription.objects.filter(
                user=user,
                is_active=True,
                plan=self.plan,
                stripe_subscription_id="sub_fresh",
            ).exists()
        )
        txn = txn_by(stripe_invoice_id="in_fresh")
        self.assertEqual(txn.stripe_payment_intent_id, "pi_fresh")
        self.assertEqual(txn.receipt_url, invoice_url("in_fresh"))

    # site 3049 -------------------------------------------------------------
    def test_overage_block_purchase(self):
        user = make_teacher("overage@p1.test")
        SubscriptionService.activate_subscription(user, self.plan)
        wallet = user.credit_wallet

        fake = self.deliver(
            {
                "id": "cs_overage",
                "object": "checkout.session",
                "payment_intent": "pi_overage",
                "invoice": None,
                "amount_total": 1000,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "overage_block_purchase_checkout",
                    "user_id": str(user.id),
                    "wallet_id": str(wallet.id),
                    "plan_id": str(self.plan.id),
                    "quantity": "1",
                },
            }
        )

        assert_receipts_outside_transaction(self, fake)
        self.assertEqual(
            CreditBucket.objects.filter(
                wallet=wallet, bucket_type=CreditBucketType.OVERAGE
            ).count(),
            1,
        )
        txn = txn_by(stripe_payment_intent_id="pi_overage")
        self.assertEqual(txn.receipt_url, pi_receipt("pi_overage"))

    # sites 3380 (subscription changed) and 3442 (applied) -------------------
    def _paid_sub(self, user, plan, stripe_sub_id):
        sub = SubscriptionService.activate_subscription(user, plan)
        # Written exactly as _handle_individual_checkout writes it.
        sub.stripe_subscription_id = stripe_sub_id
        sub.save(update_fields=["stripe_subscription_id", "updated_at"])
        return sub

    def _upgrade_session(self, user, old_sub, new_plan, pi):
        return {
            "id": f"cs_{pi}",
            "object": "checkout.session",
            "payment_intent": pi,
            "invoice": None,
            "amount_total": 2000,
            "currency": "usd",
            "payment_status": "paid",
            "metadata": {
                "flow": "individual_upgrade_checkout",
                "user_id": str(user.id),
                "new_plan_id": str(new_plan.id),
                "user_subscription_id": str(old_sub.id),
                "stripe_subscription_id": old_sub.stripe_subscription_id,
                "stripe_item_id": "si_upgrade",
                "proration_amount": "2000",
            },
        }

    def test_individual_upgrade_applied(self):
        user = make_teacher("upgrade@p1.test")
        old_sub = self._paid_sub(user, self.plan, "sub_upgrade")

        fake = self.deliver(
            self._upgrade_session(user, old_sub, self.pro, "pi_upgrade")
        )

        assert_receipts_outside_transaction(self, fake)
        self.assertEqual(
            UserSubscription.objects.get(user=user, is_active=True).plan_id, self.pro.id
        )
        txn = txn_by(stripe_payment_intent_id="pi_upgrade")
        self.assertEqual(
            txn.transaction_type, BillingTransactionType.INDIVIDUAL_UPGRADE_CHARGE
        )
        self.assertEqual(txn.receipt_url, pi_receipt("pi_upgrade"))

    def test_individual_upgrade_subscription_changed(self):
        user = make_teacher("upgrade.stale@p1.test")
        stale_sub = self._paid_sub(user, self.plan, "sub_stale")
        # A later plan change replaces the subscription the checkout was for.
        self._paid_sub(user, self.pro, "sub_newer")

        fake = self.deliver(
            self._upgrade_session(user, stale_sub, self.pro, "pi_stale")
        )

        assert_receipts_outside_transaction(self, fake)
        txn = txn_by(stripe_payment_intent_id="pi_stale")
        self.assertIn("needs manual review", txn.description)
        self.assertEqual(txn.receipt_url, pi_receipt("pi_stale"))

    # site 3486 (retired flow, replay-only) ----------------------------------
    def test_individual_subscribe_replay(self):
        user = make_teacher("subscribe@p1.test")

        fake = self.deliver(
            {
                "id": "cs_subscribe",
                "object": "checkout.session",
                "subscription": "sub_subscribe",
                "invoice": "in_subscribe",
                "payment_intent": "pi_subscribe",
                "amount_total": 999,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "individual_subscribe",
                    "user_id": str(user.id),
                    "plan_id": str(self.plan.id),
                },
            }
        )

        assert_receipts_outside_transaction(self, fake)
        self.assertTrue(
            UserSubscription.objects.filter(
                user=user, is_active=True, plan=self.plan
            ).exists()
        )
        txn = txn_by(stripe_invoice_id="in_subscribe")
        self.assertEqual(txn.stripe_payment_intent_id, "pi_subscribe")
        self.assertEqual(txn.receipt_url, invoice_url("in_subscribe"))

    # site 3697 (retired flow, replay-only) ----------------------------------
    def test_trial_to_paid_replay(self):
        user = make_teacher("trial.to.paid@p1.test")
        trial_sub = SubscriptionService.activate_free_trial(user, self.plan)

        fake = self.deliver(
            {
                "id": "cs_t2p",
                "object": "checkout.session",
                "subscription": "sub_t2p",
                "invoice": "in_t2p",
                "payment_intent": "pi_t2p",
                "amount_total": 999,
                "currency": "usd",
                "payment_status": "paid",
                "metadata": {
                    "flow": "trial_to_paid",
                    "user_id": str(user.id),
                    "trial_subscription_id": str(trial_sub.id),
                    "new_plan_id": str(self.pro.id),
                },
            }
        )

        assert_receipts_outside_transaction(self, fake)
        trial_sub.refresh_from_db()
        self.assertFalse(trial_sub.is_trial)
        txn = txn_by(stripe_invoice_id="in_t2p")
        self.assertEqual(txn.stripe_payment_intent_id, "pi_t2p")
        self.assertEqual(txn.receipt_url, invoice_url("in_t2p"))


# ---------------------------------------------------------------------------
# License flows
# ---------------------------------------------------------------------------


class LicenseFlowReceiptTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.school = School.objects.create(name="P1 School")
        self.admin = CustomUser.objects.create_user(
            email="admin@p1school.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
            is_active=True,
        )
        self.plan = SubscriptionPlan.objects.create(
            name=PlanType.PRO,
            display_name="License Pro",
            category=PlanCategory.LICENSE,
            tier=PlanTier.PRO,
            monthly_credits=20_000_000,
            overage_block_size=5_000_000,
            overage_block_price=299,
            max_overage_blocks=10,
            stripe_price_id="price_license",
            stripe_overage_price_id="price_license_overage",
            is_active=True,
        )

    def deliver(self, session):
        with fake_stripe() as fake, run_receipt_tasks_inline():
            StripeWebhookHandler.handle_checkout_completed(session)
        return fake

    def license_create_session(self, suffix):
        return {
            "id": f"cs_lic_{suffix}",
            "object": "checkout.session",
            "subscription": f"sub_lic_{suffix}",
            "customer": f"cus_lic_{suffix}",
            "invoice": f"in_lic_{suffix}",
            "payment_intent": f"pi_lic_{suffix}",
            "amount_total": 50_000,
            "currency": "usd",
            "payment_status": "paid",
            "metadata": {
                "flow": "license_create",
                "school_id": str(self.school.id),
                "plan_id": str(self.plan.id),
                "admin_user_id": str(self.admin.id),
                "contract_months": "12",
                "max_seats": "10",
                "teacher_emails": "",
            },
        }

    def create_license(self, suffix="base"):
        with fake_stripe(), run_receipt_tasks_inline():
            StripeWebhookHandler.handle_checkout_completed(
                self.license_create_session(suffix)
            )
        return LicenseSubscription.objects.get(
            stripe_subscription_id=f"sub_lic_{suffix}"
        )

    # site 3571 -------------------------------------------------------------
    def test_license_create(self):
        fake = self.deliver(self.license_create_session("new"))

        assert_receipts_outside_transaction(self, fake)
        license_sub = LicenseSubscription.objects.get(
            stripe_subscription_id="sub_lic_new"
        )
        self.assertTrue(license_sub.is_active)
        txn = txn_by(stripe_invoice_id="in_lic_new")
        self.assertEqual(txn.stripe_payment_intent_id, "pi_lic_new")
        self.assertEqual(txn.receipt_url, invoice_url("in_lic_new"))

    def _overage_intent(self, license_sub, teacher):
        with fake_stripe(
            prices={self.plan.stripe_overage_price_id: self.plan.overage_block_price}
        ), run_receipt_tasks_inline():
            LicenseSubscriptionService.initiate_overage_purchase(
                license_sub,
                self.admin,
                total_blocks=2,
                allocations={str(teacher.id): 2},
                success_url="https://app.test/ok",
                cancel_url="https://app.test/cancel",
            )
        return LicenseOveragePurchaseIntent.objects.get(
            license_subscription=license_sub
        )

    def _overage_session(self, license_sub, intent, pi):
        return {
            "id": f"cs_{pi}",
            "object": "checkout.session",
            "payment_intent": pi,
            "invoice": None,
            "amount_total": intent.amount_cents,
            "currency": "usd",
            "payment_status": "paid",
            "metadata": {
                "flow": "license_overage_purchase_checkout",
                "license_id": str(license_sub.id),
                "intent_id": str(intent.id),
            },
        }

    # site 3282 -------------------------------------------------------------
    def test_license_overage_fulfilled(self):
        license_sub = self.create_license()
        teacher = make_teacher("teacher@p1school.test", school=self.school)
        LicenseSubscriptionService.add_teacher_to_license(license_sub, teacher.email)
        intent = self._overage_intent(license_sub, teacher)

        fake = self.deliver(
            self._overage_session(license_sub, intent, "pi_lic_overage")
        )

        assert_receipts_outside_transaction(self, fake)
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseOveragePurchaseStatus.COMPLETED)
        self.assertTrue(
            CreditBucket.objects.filter(
                wallet__user=teacher, bucket_type=CreditBucketType.OVERAGE
            ).exists()
        )
        txn = txn_by(stripe_payment_intent_id="pi_lic_overage")
        self.assertEqual(txn.receipt_url, pi_receipt("pi_lic_overage"))

    # site 3187 -------------------------------------------------------------
    def test_license_overage_on_inactive_license(self):
        license_sub = self.create_license()
        teacher = make_teacher("teacher2@p1school.test", school=self.school)
        LicenseSubscriptionService.add_teacher_to_license(license_sub, teacher.email)
        intent = self._overage_intent(license_sub, teacher)
        # The license ends (Stripe subscription deleted) before payment lands.
        with run_receipt_tasks_inline():
            StripeWebhookHandler.handle_subscription_deleted(
                {"id": license_sub.stripe_subscription_id, "object": "subscription"}
            )

        fake = self.deliver(
            self._overage_session(license_sub, intent, "pi_lic_inactive")
        )

        assert_receipts_outside_transaction(self, fake)
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseOveragePurchaseStatus.FAILED)
        txn = txn_by(stripe_payment_intent_id="pi_lic_inactive")
        self.assertIn("needs manual refund", txn.description)
        self.assertEqual(txn.receipt_url, pi_receipt("pi_lic_inactive"))
