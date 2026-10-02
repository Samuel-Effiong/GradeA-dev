"""
billing/tests/test_overage_price_drift.py
=========================================
The quote and the charge must never disagree.

THE DEFECT
----------
Both overage checkout flows build their Stripe session from
`plan.stripe_overage_price_id`, while every local number — the quote, the
purchase intent, the offline request, the log line — comes from
`plan.overage_block_price`. Nothing made the two agree, and against real
Stripe six of nine plans had drifted:

    PRO_LICENSE   quoted 299/block   charged 400/block

A school buying three blocks was quoted 897 and charged 1200.

WHAT IS PINNED HERE
-------------------
Not "the column holds 400". A test asserting today's prices would fail the
day someone legitimately reprices, which teaches people to edit tests
rather than to think. What is pinned is the RELATIONSHIP: whatever the two
numbers are, a purchase cannot proceed while they differ, on either flow,
and the drift is visible to the reconciler before a customer meets it.

WHY REFUSING IS THE RIGHT BEHAVIOUR
-----------------------------------
The alternative is to quote one number and charge another. Between
"nobody can buy overage on this plan until someone fixes the price" and
"customers are billed more than they agreed to", the first is a bad
afternoon and the second is a refund queue and a trust problem.
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SchoolCreditAllocation,
    SubscriptionPlan,
    UserSubscription,
)
from billing.overage_pricing import (
    OveragePriceMismatch,
    OveragePriceUnavailable,
    assert_overage_price_in_sync,
    overage_price_drift,
    stripe_overage_unit_price,
)
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_PRICE_ID = "price_overage_live"


def make_plan(*, local_cents=400, category=PlanCategory.INDIVIDUAL, name=None):
    return SubscriptionPlan.objects.create(
        name=name or PlanType.PRO,
        display_name="Pro",
        category=category,
        tier=PlanTier.PRO,
        interval=BillingInterval.MONTHLY,
        monthly_credits=10_000,
        overage_block_size=500,
        overage_block_price=local_cents,
        max_overage_blocks=10,
        stripe_overage_price_id=STRIPE_PRICE_ID,
        is_active=True,
    )


def stripe_price(unit_amount):
    """Shaped like the Stripe Price object the code reads."""
    return {"id": STRIPE_PRICE_ID, "unit_amount": unit_amount, "currency": "usd"}


class PriceAuthorityTests(TestCase):
    """`billing/overage_pricing.py` on its own."""

    def setUp(self):
        cache.clear()
        self.plan = make_plan(local_cents=400)

    def test_the_stripe_price_is_what_is_reported(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)

            self.assertEqual(stripe_overage_unit_price(self.plan), 400)

    def test_a_matching_pair_is_allowed_and_returns_the_price(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)

            self.assertEqual(assert_overage_price_in_sync(self.plan), 400)

    def test_a_mismatched_pair_is_REFUSED(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(500)

            with self.assertRaises(OveragePriceMismatch) as ctx:
                assert_overage_price_in_sync(self.plan)

        self.assertEqual(ctx.exception.local_cents, 400)
        self.assertEqual(ctx.exception.stripe_cents, 500)

    def test_the_refusal_names_both_numbers_so_it_can_be_acted_on(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(500)

            with self.assertRaises(OveragePriceMismatch) as ctx:
                assert_overage_price_in_sync(self.plan)

        message = str(ctx.exception)
        self.assertIn("400", message)
        self.assertIn("500", message)
        self.assertIn(STRIPE_PRICE_ID, message)

    def test_a_plan_with_no_stripe_overage_price_is_left_alone(self):
        """
        Handled by the existing "this plan does not support overage"
        checks; raising here would replace a clear message with a
        confusing one.
        """
        self.plan.stripe_overage_price_id = ""
        self.plan.save(update_fields=["stripe_overage_price_id"])

        self.assertIsNone(stripe_overage_unit_price(self.plan))
        self.assertEqual(assert_overage_price_in_sync(self.plan), 400)

    def test_an_unreachable_stripe_is_NOT_treated_as_agreement(self):
        """
        The dangerous failure: an outage must not read as "the prices
        match". It has to stop the purchase, same as a mismatch.
        """
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.side_effect = RuntimeError("stripe is down")

            with self.assertRaises(OveragePriceUnavailable):
                assert_overage_price_in_sync(self.plan)

    def test_a_price_with_no_flat_unit_amount_is_refused(self):
        """A tiered or metered price cannot be multiplied by a block count."""
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = {"id": STRIPE_PRICE_ID, "unit_amount": None}

            with self.assertRaises(OveragePriceUnavailable):
                assert_overage_price_in_sync(self.plan)

    def test_the_price_is_cached_so_a_burst_costs_one_lookup(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)

            for _ in range(5):
                stripe_overage_unit_price(self.plan)

            self.assertEqual(retrieve.call_count, 1)

    def test_the_reconciler_can_bypass_the_cache(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)
            stripe_overage_unit_price(self.plan)
            retrieve.return_value = stripe_price(300)

            fresh = stripe_overage_unit_price(self.plan, use_cache=False)

        self.assertEqual(
            fresh,
            300,
            "the reconciler read a stale cached price and would have "
            "reported drift that no longer exists, or missed drift that does",
        )


class DriftReportTests(TestCase):
    def setUp(self):
        cache.clear()
        self.ok = make_plan(local_cents=400, name=PlanType.PRO)
        self.drifted = make_plan(local_cents=299, name=PlanType.STANDARD)

    def test_the_report_separates_synced_from_drifted(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)

            report = {row["name"]: row for row in overage_price_drift()}

        self.assertTrue(report[PlanType.PRO]["in_sync"])
        self.assertFalse(report[PlanType.STANDARD]["in_sync"])
        self.assertEqual(report[PlanType.STANDARD]["stripe_cents"], 400)

    def test_one_unreadable_price_does_not_hide_the_others(self):
        """
        A single bad plan must not abort the sweep — that would let one
        broken row mask drift on every other.
        """
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("stripe hiccup")
            return stripe_price(400)

        with patch("billing.overage_pricing.stripe.Price.retrieve", side_effect=flaky):
            report = overage_price_drift()

        self.assertEqual(len(report), 2)
        self.assertTrue(any(row["error"] for row in report))

    def test_an_unreadable_price_is_never_reported_as_in_sync(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.side_effect = RuntimeError("stripe is down")

            report = overage_price_drift()

        self.assertTrue(all(not row["in_sync"] for row in report))
        self.assertTrue(all(row["error"] for row in report))


class IndividualCheckoutIsRefusedWhenDriftedTests(TestCase):
    """Flow 1 — the individual overage checkout."""

    def setUp(self):
        cache.clear()
        self.plan = make_plan(local_cents=400)
        self.user = CustomUser.objects.create_user(
            email="drift@billing.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
        )
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)
        now = timezone.now()
        UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now,
            billing_cycle_end=now + timezone.timedelta(days=30),
            next_credit_grant_at=now + timezone.timedelta(days=30),
        )

    def _checkout(self):
        from billing.stripe_service import StripeOverageService

        return StripeOverageService.create_overage_checkout_session(
            user=self.user,
            quantity=1,
            success_url="https://example.invalid/ok",
            cancel_url="https://example.invalid/no",
        )

    def test_a_drifted_plan_cannot_open_a_checkout_session(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(500)
            with patch(
                "billing.stripe_service.stripe.checkout.Session.create"
            ) as create:
                with self.assertRaises(OveragePriceMismatch):
                    self._checkout()

        self.assertFalse(
            create.called,
            "a checkout session was created at a price the customer was "
            "never quoted",
        )

    def test_an_in_sync_plan_still_opens_a_checkout_session(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)
            with patch(
                "billing.stripe_service.stripe.checkout.Session.create"
            ) as create:
                with patch(
                    "billing.stripe_service.StripeCustomerService."
                    "get_or_create_customer",
                    return_value="cus_x",
                ):
                    create.return_value = SimpleNamespace(id="cs_x")
                    self._checkout()

        self.assertTrue(create.called)


class SchoolCheckoutIsRefusedWhenDriftedTests(TestCase):
    """Flow 3 — the school-licence overage purchase, quote included."""

    def setUp(self):
        cache.clear()
        self.plan = make_plan(local_cents=400, category=PlanCategory.LICENSE)
        self.school = School.objects.create(name="Drift School")
        self.admin = CustomUser.objects.create_user(
            email="drift.admin@school.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            is_active=True,
            school=self.school,
        )
        self.teacher = CustomUser.objects.create_user(
            email="drift.teacher@school.test",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            school=self.school,
        )
        now = timezone.now()
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            max_seats=5,
            is_active=True,
            billing_cycle_start=now,
            billing_cycle_end=now + timezone.timedelta(days=30),
        )
        # The price gate runs LAST, after every local check, so the teacher
        # has to be genuinely eligible or the request is rejected before it
        # ever gets there.
        SchoolCreditAllocation.objects.create(
            license_subscription=self.license_sub,
            user=self.teacher,
            is_active=True,
            monthly_allocation=1000,
        )

    def _validate(self):
        from billing.license_service import LicenseSubscriptionService

        return LicenseSubscriptionService._validate_overage_purchase_request(
            self.license_sub, total_blocks=3, allocations={str(self.teacher.id): 3}
        )

    def test_a_drifted_plan_is_refused_before_anything_is_quoted(self):
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(500)

            with self.assertRaises(OveragePriceMismatch):
                self._validate()

    def test_the_refusal_covers_the_OFFLINE_quote_too(self):
        """
        The offline path quotes `total_blocks * overage_block_price` and
        sends a human to collect that amount. Drift there means invoicing
        a school for a figure the system will not reconcile against.
        """
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(500)

            with self.assertRaises(OveragePriceMismatch):
                self._validate()

    def test_an_in_sync_plan_gets_past_the_price_gate(self):
        """An eligible request on an in-sync plan validates cleanly."""
        with patch("billing.overage_pricing.stripe.Price.retrieve") as retrieve:
            retrieve.return_value = stripe_price(400)

            self._validate()
