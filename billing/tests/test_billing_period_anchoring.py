"""
Local billing periods are anchored on Stripe's timestamps, not our clock.

THE PROBLEM THIS FIXES
-----------------------
`activate_subscription` used to compute the cycle as
`timezone.now() + one interval`, where "now" is whenever OUR server got
round to processing the webhook — not the boundary Stripe actually
billed. So local dates sat one webhook-latency behind Stripe's, and the
offset was re-rolled every cycle.

That turned the renewal idempotency guard (`billing_cycle_end > now`)
into a coin flip. Concretely: cycle N's webhook is slow (a card retry,
a queue backlog), so local billing_cycle_end lands hours after Stripe's
real boundary. Cycle N+1's webhook is fast, arrives BEFORE that inflated
local end, and is silently swallowed as "already renewed" — the customer
paid and gets nothing until the next nightly reconcile sweep, up to a
day later.

`test_local_period_matches_stripe_exactly_across_twelve_renewals` is the
regression: twelve cycles, each webhook processed at a realistically
random lag, and the local dates must equal Stripe's exactly every time.

ARCHITECTURE THIS LOCKS
------------------------
Stripe is the authority; this database is its synchronized projection.
The fix changes WHAT WE STORE, never HOW WE READ IT — no code anywhere
starts calling Stripe to answer "is this subscription current?". The
existing `billing_cycle_end > timezone.now()` checks are untouched, they
simply now compare against a trustworthy value.
"""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditBucketType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.services import SubscriptionService
from billing.stripe_service import (
    StripeWebhookHandler,
    extract_invoice_billing_period,
    extract_subscription_billing_period,
)
from users.models import UserTypes

CustomUser = get_user_model()

STRIPE_SUB_ID = "sub_period_test"


def ts(dt):
    return int(dt.timestamp())


def stripe_now(offset=None):
    """
    A boundary as Stripe would express it: whole seconds, no microseconds.
    Stripe timestamps are Unix ints, so a period round-tripped through the
    API can never carry sub-second precision — test fixtures must not
    pretend otherwise or they compare against a value that cannot exist.
    """
    moment = timezone.now().replace(microsecond=0)
    return moment + offset if offset else moment


def make_plan(name, tier, interval=BillingInterval.MONTHLY, price_id="price_x"):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=interval,
        price_cents=999,
        monthly_credits=10_000_000,
        stripe_price_id=price_id,
        carry_over_percent=0,
        carry_over_expiry_months=1,
        is_active=True,
    )


def renewal_invoice(period_start, period_end, *, invoice_id="in_renew"):
    """A subscription-cycle invoice as Stripe actually shapes it."""
    return {
        "id": invoice_id,
        "status": "paid",
        "billing_reason": "subscription_cycle",
        "amount_paid": 999,
        "currency": "usd",
        "hosted_invoice_url": "https://stripe.test/i",
        "subscription": STRIPE_SUB_ID,
        "lines": {
            "data": [
                {
                    "id": "il_1",
                    "period": {"start": ts(period_start), "end": ts(period_end)},
                }
            ]
        },
    }


class ExtractInvoiceBillingPeriodTests(TestCase):
    """Parsing only — no database, no Stripe calls."""

    def setUp(self):
        self.start = datetime(2030, 3, 1, tzinfo=dt_timezone.utc)
        self.end = datetime(2030, 4, 1, tzinfo=dt_timezone.utc)

    def test_reads_the_subscription_line_period(self):
        start, end = extract_invoice_billing_period(
            renewal_invoice(self.start, self.end)
        )

        self.assertEqual(start, self.start)
        self.assertEqual(end, self.end)

    def test_prefers_the_full_cycle_line_over_a_proration_fragment(self):
        """
        An upgrade proration line sits alongside the renewal line. The
        proration covers days; the renewal covers the cycle. Picking the
        fragment would set billing_cycle_end days away instead of a month.
        """
        invoice = renewal_invoice(self.start, self.end)
        invoice["lines"]["data"].insert(
            0,
            {
                "id": "il_proration",
                "period": {
                    "start": ts(self.start),
                    "end": ts(self.start + timedelta(days=3)),
                },
            },
        )

        start, end = extract_invoice_billing_period(invoice)

        self.assertEqual((start, end), (self.start, self.end))

    def test_falls_back_to_invoice_level_period(self):
        invoice = {
            "id": "in_x",
            "period_start": ts(self.start),
            "period_end": ts(self.end),
        }

        self.assertEqual(
            extract_invoice_billing_period(invoice), (self.start, self.end)
        )

    def test_returns_none_for_unusable_payloads(self):
        for invoice in (
            {},
            {"lines": {"data": []}},
            {"lines": {"data": [{"period": {}}]}},
            {
                "lines": {
                    "data": [{"period": {"start": ts(self.end), "end": ts(self.start)}}]
                }
            },
            {"period_start": 0, "period_end": 0},
            {"period_start": True, "period_end": True},
            {"lines": None},
        ):
            with self.subTest(invoice=invoice):
                self.assertEqual(extract_invoice_billing_period(invoice), (None, None))


class ExtractSubscriptionBillingPeriodTests(TestCase):
    def setUp(self):
        self.start = datetime(2030, 3, 1, tzinfo=dt_timezone.utc)
        self.end = datetime(2031, 3, 1, tzinfo=dt_timezone.utc)

    def test_reads_the_item_period_current_api_shape(self):
        sub = {
            "items": {
                "data": [
                    {
                        "current_period_start": ts(self.start),
                        "current_period_end": ts(self.end),
                    }
                ]
            }
        }

        self.assertEqual(
            extract_subscription_billing_period(sub), (self.start, self.end)
        )

    def test_falls_back_to_legacy_top_level_shape(self):
        sub = {
            "current_period_start": ts(self.start),
            "current_period_end": ts(self.end),
        }

        self.assertEqual(
            extract_subscription_billing_period(sub), (self.start, self.end)
        )

    def test_returns_none_when_absent(self):
        self.assertEqual(extract_subscription_billing_period({}), (None, None))


class ResolveBillingPeriodTests(TestCase):
    """
    The decision layer. Deliberately Stripe-free: it takes datetimes, so
    it is testable without any Stripe object at all.
    """

    def setUp(self):
        self.monthly = make_plan("STANDARD", PlanTier.STANDARD)
        self.annual = make_plan(
            "STANDARD_ANNUAL",
            PlanTier.STANDARD,
            interval=BillingInterval.ANNUAL,
            price_id="price_annual",
        )

    def test_uses_the_stripe_period_verbatim(self):
        start = timezone.now() - timedelta(hours=3)
        end = start + relativedelta(months=1)

        cycle_start, cycle_end, grant_at = SubscriptionService._resolve_billing_period(
            self.monthly, start, end
        )

        self.assertEqual(cycle_start, start)
        self.assertEqual(cycle_end, end)
        self.assertEqual(grant_at, end, "monthly plans grant at the cycle boundary")

    def test_annual_plan_anchors_the_monthly_credit_clock_to_the_period_start(self):
        start = timezone.now() - timedelta(hours=2)
        end = start + relativedelta(years=1)

        _, _, grant_at = SubscriptionService._resolve_billing_period(
            self.annual, start, end
        )

        self.assertEqual(grant_at, start + relativedelta(months=1))

    # -- preserved behaviour -------------------------------------------

    def test_no_period_falls_back_to_the_wall_clock(self):
        before = timezone.now()

        cycle_start, cycle_end, grant_at = SubscriptionService._resolve_billing_period(
            self.monthly, None, None
        )

        self.assertGreaterEqual(cycle_start, before)
        self.assertLessEqual(cycle_start, timezone.now())
        self.assertEqual(cycle_end, cycle_start + relativedelta(months=1))
        self.assertEqual(grant_at, cycle_end)

    def test_partial_period_is_ignored(self):
        start = timezone.now()

        _, cycle_end, _ = SubscriptionService._resolve_billing_period(
            self.monthly, start, None
        )

        self.assertGreater(cycle_end, timezone.now() + timedelta(days=27))

    # -- refusing dangerous periods ------------------------------------

    def test_inverted_period_is_refused(self):
        now = timezone.now()

        cycle_start, cycle_end, _ = SubscriptionService._resolve_billing_period(
            self.monthly, now + relativedelta(months=1), now
        )

        self.assertNotEqual(cycle_end, now)
        self.assertGreater(cycle_end, timezone.now())

    def test_already_elapsed_period_is_refused(self):
        """
        An end date in the past would leave the subscription instantly due
        again — a renewal loop re-granting credits on every sweep. Falling
        back is strictly safer.
        """
        start = timezone.now() - relativedelta(months=2)
        end = timezone.now() - relativedelta(months=1)

        cycle_start, cycle_end, _ = SubscriptionService._resolve_billing_period(
            self.monthly, start, end
        )

        self.assertGreater(
            cycle_end, timezone.now(), "must never store an already-elapsed cycle"
        )
        self.assertNotEqual(cycle_start, start)

    def test_annual_credit_clock_is_never_born_in_the_past(self):
        """
        A badly delayed webhook could otherwise mint a MONTHLY bucket that
        has already expired, leaving the customer with no usable credits.
        """
        start = timezone.now() - relativedelta(months=3)
        end = start + relativedelta(years=1)

        _, _, grant_at = SubscriptionService._resolve_billing_period(
            self.annual, start, end
        )

        self.assertGreater(grant_at, timezone.now())


class RenewalAnchoringTests(TestCase):
    """End-to-end through the real webhook handler."""

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="anchor@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan("STANDARD", PlanTier.STANDARD, price_id="price_standard")
        self.wallet, _ = CreditWallet.objects.get_or_create(user=self.user)

    def _make_sub(self, cycle_start, cycle_end, **kwargs):
        sub = UserSubscription.objects.create(
            user=self.user,
            plan=self.plan,
            is_active=True,
            billing_cycle_start=cycle_start,
            billing_cycle_end=cycle_end,
            next_credit_grant_at=cycle_end,
            stripe_subscription_id=STRIPE_SUB_ID,
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            **kwargs,
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=self.plan.monthly_credits,
            used_credits=0,
            expires_at=cycle_end,
        )
        return sub

    def _renew(self, sub, invoice):
        with patch("stripe.Subscription") as mock_sub:
            mock_sub.retrieve.return_value = {
                "id": STRIPE_SUB_ID,
                "items": {"data": [{"id": "si_1", "price": {"id": "price_standard"}}]},
            }
            StripeWebhookHandler._handle_individual_invoice_succeeded(
                sub, "subscription_cycle", invoice
            )
        return UserSubscription.objects.get(user=self.user, is_active=True)

    def test_renewal_adopts_stripes_period_not_our_processing_time(self):
        stripe_boundary = stripe_now(-timedelta(hours=6))  # webhook arrived late
        next_boundary = stripe_boundary + relativedelta(months=1)
        sub = self._make_sub(stripe_boundary - relativedelta(months=1), stripe_boundary)

        renewed = self._renew(sub, renewal_invoice(stripe_boundary, next_boundary))

        self.assertEqual(renewed.billing_cycle_start, stripe_boundary)
        self.assertEqual(renewed.billing_cycle_end, next_boundary)
        # The old behaviour would have stamped ~now + 1 month, i.e. six
        # hours later than Stripe's real boundary.
        self.assertLess(
            renewed.billing_cycle_end,
            timezone.now() + relativedelta(months=1) - timedelta(hours=5),
        )

    def test_local_period_matches_stripe_exactly_across_twelve_renewals(self):
        """
        THE REGRESSION. Twelve cycles, each webhook processed at a
        different realistic lag. Under the old wall-clock arithmetic every
        lag compounded into the next cycle's dates; anchored on Stripe's
        period they must match exactly, every time, forever.
        """
        anchor = stripe_now(-relativedelta(months=12))
        sub = self._make_sub(anchor - relativedelta(months=1), anchor)

        # Minutes of webhook latency for each cycle — a slow cycle followed
        # by a fast one is exactly what used to swallow a renewal.
        lags = [1, 240, 3, 90, 720, 2, 45, 5, 300, 1, 180, 30]
        boundary = anchor

        for cycle, lag_minutes in enumerate(lags, start=1):
            next_boundary = boundary + relativedelta(months=1)
            processed_at = boundary + timedelta(minutes=lag_minutes)

            with patch("django.utils.timezone.now", return_value=processed_at):
                sub = self._renew(sub, renewal_invoice(boundary, next_boundary))

            self.assertEqual(
                sub.billing_cycle_start,
                boundary,
                f"cycle {cycle}: start drifted from Stripe's boundary",
            )
            self.assertEqual(
                sub.billing_cycle_end,
                next_boundary,
                f"cycle {cycle}: end drifted from Stripe's boundary",
            )
            boundary = next_boundary

        self.assertEqual(
            sub.billing_cycle_end,
            anchor + relativedelta(months=12),
            "twelve renewals must land exactly twelve months after the anchor",
        )

    def test_renewal_without_a_usable_period_still_renews(self):
        """
        Preserved behaviour: a malformed invoice must not break renewal.
        It falls back to the wall clock, exactly as before this change.
        """
        boundary = stripe_now(-timedelta(minutes=5))
        sub = self._make_sub(boundary - relativedelta(months=1), boundary)
        invoice = renewal_invoice(boundary, boundary + relativedelta(months=1))
        del invoice["lines"]

        renewed = self._renew(sub, invoice)

        self.assertGreater(renewed.billing_cycle_end, timezone.now())
        self.assertEqual(
            renewed.billing_cycle_end,
            renewed.billing_cycle_start + relativedelta(months=1),
        )

    def test_trial_conversion_adopts_stripes_period(self):
        boundary = stripe_now(-timedelta(hours=2))
        next_boundary = boundary + relativedelta(months=1)
        sub = self._make_sub(
            boundary - relativedelta(days=14),
            boundary,
            is_trial=True,
            trial_end=boundary,
        )
        CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.TRIAL,
            total_credits=5_000_000,
            used_credits=0,
            expires_at=boundary,
        )

        self._renew(sub, renewal_invoice(boundary, next_boundary))

        sub.refresh_from_db()
        self.assertFalse(sub.is_trial)
        self.assertEqual(sub.billing_cycle_start, boundary)
        self.assertEqual(sub.billing_cycle_end, next_boundary)


class PreservedNonStripeFlowTests(TestCase):
    """
    Flows Stripe does not drive must keep using the wall clock — passing
    no period is the correct call there, not an oversight.
    """

    def setUp(self):
        self.user = CustomUser.objects.create_user(
            email="local@example.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.plan = make_plan("STANDARD", PlanTier.STANDARD)

    def test_direct_activation_still_starts_now(self):
        before = timezone.now()

        sub = SubscriptionService.activate_subscription(self.user, self.plan)

        self.assertGreaterEqual(sub.billing_cycle_start, before)
        self.assertLessEqual(sub.billing_cycle_start, timezone.now())
        self.assertEqual(
            sub.billing_cycle_end,
            sub.billing_cycle_start + relativedelta(months=1),
        )

    def test_free_trial_activation_is_untouched(self):
        trial_plan = SubscriptionPlan.objects.create(
            name="TRIAL",
            display_name="Trial",
            category=PlanCategory.INDIVIDUAL,
            tier=PlanTier.TRIAL,
            interval=BillingInterval.MONTHLY,
            price_cents=0,
            monthly_credits=5_000_000,
            carry_over_percent=0,
            carry_over_expiry_months=1,
            is_active=True,
        )

        sub = SubscriptionService.activate_free_trial(self.user, trial_plan)

        self.assertTrue(sub.is_trial)
        self.assertEqual(sub.billing_cycle_end, sub.trial_end)
        self.assertGreater(sub.trial_end, timezone.now() + timedelta(days=13))
