"""
Gate 7 (LOCAL-REAL): real Stripe test mode, both sides recorded.

Proves on the fixed tree that:
  1. every refused free-plan attack leaves BOTH the app rows AND the real
     Stripe objects (subscription, items/price, status, invoices, checkout
     sessions) unchanged, and makes no Stripe write;
  2. the allowed self-service path still reaches Stripe: select-plan for a
     catalog plan opens a real Checkout Session with the right price and
     metadata.

Uses the local test database and the sk_test_ key from settings (refuses any
other key). Every Stripe object it creates is tagged
metadata.free_plan_evidence=<run id> and cleaned up at the end.

    PYTHONPATH=docs/evidence/free_plan_activation \\
        python manage.py test stripe_refusal_real --settings=settings_worktree --noinput
"""

import json
import uuid
from decimal import Decimal

from django.test import override_settings
from django.urls import reverse
from rest_framework.test import APIClient, APITestCase

from billing.imports import stripe
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditLedger,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from users.models import CustomUser, UserTypes

RUN = uuid.uuid4().hex[:10]
TAG = {"free_plan_evidence": RUN}


def emit(name, payload):
    print(f"\nSTRIPE-REAL {name} {json.dumps(payload, default=str, sort_keys=True)}")


def app_state(user):
    return {
        "subscriptions": list(
            UserSubscription.objects.filter(user=user)
            .order_by("created_at", "id")
            .values_list(
                "id",
                "plan__name",
                "is_active",
                "stripe_subscription_id",
                "stripe_status",
            )
        ),
        "buckets": list(
            CreditBucket.objects.filter(wallet__user=user)
            .order_by("created_at", "id")
            .values_list("id", "bucket_type", "total_credits", "used_credits")
        ),
        "ledger_rows": CreditLedger.objects.filter(user_id=user.id).count(),
    }


def stripe_state(customer_id, subscription_id):
    sub = stripe.Subscription.retrieve(subscription_id)
    items = sub["items"]["data"]
    return {
        "subscription_id": sub["id"],
        "status": sub["status"],
        "cancel_at_period_end": sub["cancel_at_period_end"],
        "price_ids": [item["price"]["id"] for item in items],
        "item_ids": [item["id"] for item in items],
        "latest_invoice": sub.get("latest_invoice"),
        "schedule": sub.get("schedule"),
        "customer_subscriptions": sorted(
            s["id"]
            for s in stripe.Subscription.list(customer=customer_id, status="all")
        ),
        "invoices": sorted(i["id"] for i in stripe.Invoice.list(customer=customer_id)),
        "checkout_sessions": sorted(
            s["id"] for s in stripe.checkout.Session.list(customer=customer_id)
        ),
    }


class StripeRefusalReal(APITestCase):
    created_customers: list = []
    created_products: list = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        assert (stripe.api_key or "").startswith("sk_test_"), "test-mode key required"
        cls.product = stripe.Product.create(
            name=f"free-plan-evidence-{RUN}", metadata=TAG
        )
        cls.created_products.append(cls.product["id"])
        cls.price_standard = stripe.Price.create(
            product=cls.product["id"],
            unit_amount=1499,
            currency="usd",
            recurring={"interval": "month"},
            metadata=TAG,
        )

    @classmethod
    def tearDownClass(cls):
        cleanup = {"customers_deleted": [], "products_archived": [], "errors": []}
        for customer_id in cls.created_customers:
            try:
                for sub in stripe.Subscription.list(customer=customer_id, status="all"):
                    if sub["status"] not in ("canceled", "incomplete_expired"):
                        stripe.Subscription.cancel(sub["id"])
                stripe.Customer.delete(customer_id)
                cleanup["customers_deleted"].append(customer_id)
            except Exception as exc:  # recorded, never hidden
                cleanup["errors"].append(f"{customer_id}: {exc}")
        for product_id in cls.created_products:
            try:
                for price in stripe.Price.list(product=product_id):
                    stripe.Price.modify(price["id"], active=False)
                stripe.Product.modify(product_id, active=False)
                cleanup["products_archived"].append(product_id)
            except Exception as exc:
                cleanup["errors"].append(f"{product_id}: {exc}")
        emit("cleanup", cleanup)
        super().tearDownClass()

    def setUp(self):
        def plan(name, credits, price="0.00", **kw):
            fields = {
                "category": PlanCategory.INDIVIDUAL,
                "tier": PlanTier.STANDARD,
                "interval": BillingInterval.MONTHLY,
                "is_active": True,
            }
            fields.update(kw)
            return SubscriptionPlan.objects.create(
                name=name, monthly_credits=credits, price_cents=Decimal(price), **fields
            )

        self.standard = plan(
            PlanType.STANDARD,
            10_000_000,
            "1499.00",
            stripe_price_id=self.price_standard["id"],
        )
        self.beta = plan(PlanType.BETA, 10_000_000, tier=PlanTier.BETA)
        self.trial = plan(
            PlanType.TRIAL,
            5_000_000,
            tier=PlanTier.TRIAL,
            interval=BillingInterval.NONE,
        )
        self.benchmark = plan("Grading Benchmark Plan", 5_000_000)
        self.superadmin = CustomUser.objects.create_user(
            email=f"sa-{RUN}@example.com",
            password="Stripe-real-123!",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_superuser=True,
            is_staff=True,
        )

    def teacher(self, label):
        return CustomUser.objects.create_user(
            email=f"{label}-{RUN}@example.com",
            password="Stripe-real-123!",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )

    def real_paid_subscriber(self):
        teacher = self.teacher("paid")
        customer = stripe.Customer.create(
            email=teacher.email, payment_method="pm_card_visa", metadata=TAG
        )
        self.created_customers.append(customer["id"])
        stripe.Customer.modify(
            customer["id"],
            invoice_settings={
                "default_payment_method": customer["invoice_settings"][
                    "default_payment_method"
                ]
                or stripe.PaymentMethod.list(customer=customer["id"])["data"][0]["id"]
            },
        )
        subscription = stripe.Subscription.create(
            customer=customer["id"],
            items=[{"price": self.price_standard["id"]}],
            metadata=TAG,
        )
        # The app rows, written the way checkout.session.completed and
        # StripeCustomerService write them.
        wallet, _ = CreditWallet.objects.get_or_create(user=teacher)
        wallet.stripe_customer_id = customer["id"]
        wallet.save(update_fields=["stripe_customer_id"])
        local = SubscriptionService.activate_subscription(teacher, self.standard)
        local.stripe_subscription_id = subscription["id"]
        local.stripe_status = StripeSubscriptionStatus.ACTIVE
        local.save(
            update_fields=["stripe_subscription_id", "stripe_status", "updated_at"]
        )
        return teacher, customer["id"], subscription["id"]

    def test_refused_attacks_leave_app_and_stripe_unchanged(self):
        teacher, customer_id, subscription_id = self.real_paid_subscriber()
        app_before = app_state(teacher)
        stripe_before = stripe_state(customer_id, subscription_id)
        emit("paid_subscriber_before", {"app": app_before, "stripe": stripe_before})

        writes = []
        originals = {}
        for resource, method in (
            (stripe.Subscription, "modify"),
            (stripe.Subscription, "cancel"),
            (stripe.Subscription, "create"),
            (stripe.checkout.Session, "create"),
            (stripe.SubscriptionSchedule, "create"),
            (stripe.Customer, "modify"),
        ):
            original = getattr(resource, method)
            originals[(resource, method)] = original

            def spy(*args, _name=f"{resource.__name__}.{method}", _orig=original, **kw):
                writes.append(_name)
                return _orig(*args, **kw)

            setattr(resource, method, spy)

        outcomes = {}
        try:
            teacher_client = APIClient()
            teacher_client.force_authenticate(user=teacher)
            admin_client = APIClient()
            admin_client.force_authenticate(user=self.superadmin)
            for route in ("user-subscription-list", "subscription-list"):
                for plan in (self.beta, self.trial, self.benchmark, self.standard):
                    body = {"user": str(teacher.id), "plan": str(plan.pk)}
                    outcomes[f"teacher {route} {plan.name}"] = teacher_client.post(
                        reverse(route), body, format="json"
                    ).status_code
                    outcomes[f"superadmin {route} {plan.name}"] = admin_client.post(
                        reverse(route), body, format="json"
                    ).status_code
            for plan in (self.beta, self.trial, self.benchmark):
                outcomes[f"teacher select-plan {plan.name}"] = teacher_client.post(
                    reverse("subscription-select-plan"),
                    {
                        "plan_id": str(plan.pk),
                        "success_url": "https://example.test/ok",
                        "cancel_url": "https://example.test/cancel",
                    },
                    format="json",
                ).status_code
        finally:
            for (resource, method), original in originals.items():
                setattr(resource, method, original)

        app_after = app_state(teacher)
        stripe_after = stripe_state(customer_id, subscription_id)
        emit(
            "paid_subscriber_after",
            {
                "app": app_after,
                "stripe": stripe_after,
                "outcomes": outcomes,
                "stripe_write_calls": writes,
                "app_unchanged": app_after == app_before,
                "stripe_unchanged": stripe_after == stripe_before,
            },
        )
        self.assertEqual(app_after, app_before)
        self.assertEqual(stripe_after, stripe_before)
        self.assertEqual(writes, [])
        self.assertTrue(set(outcomes.values()) <= {400, 403}, outcomes)

    def test_allowed_catalog_plan_still_opens_a_real_checkout(self):
        teacher = self.teacher("buyer")  # signup gives the automatic TRIAL
        client = APIClient()
        client.force_authenticate(user=teacher)
        with override_settings(STRIPE_CUSTOMER_METADATA=TAG):
            response = client.post(
                reverse("subscription-select-plan"),
                {
                    "plan_id": str(self.standard.pk),
                    "success_url": "https://example.test/ok",
                    "cancel_url": "https://example.test/cancel",
                },
                format="json",
            )
        wallet = CreditWallet.objects.get(user=teacher)
        if wallet.stripe_customer_id:
            self.created_customers.append(wallet.stripe_customer_id)
        payload = {"status": response.status_code, "response": response.data}
        sessions = (
            list(stripe.checkout.Session.list(customer=wallet.stripe_customer_id))
            if wallet.stripe_customer_id
            else []
        )
        if sessions:
            session = stripe.checkout.Session.retrieve(
                sessions[0]["id"], expand=["line_items"]
            )
            payload["stripe_session"] = {
                "id": session["id"],
                "mode": session["mode"],
                "status": session["status"],
                "metadata": dict(session["metadata"]),
                "line_item_prices": [
                    li["price"]["id"] for li in session["line_items"]["data"]
                ],
            }
        payload["app_after"] = app_state(teacher)
        emit("allowed_checkout", payload)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(
            payload["stripe_session"]["line_item_prices"], [self.price_standard["id"]]
        )
        self.assertEqual(
            payload["stripe_session"]["metadata"]["plan_id"], str(self.standard.pk)
        )
