"""H-274 (b), BUG-009: checkout with a stale Stripe customer id.

A stored customer id that no longer exists at Stripe (a QA test-clock
customer deleted with its clock, a database copied between environments, a
test/live key switch) made every later checkout fail with
``InvalidRequestError("No such customer: ...")``, uncaught, until someone
edited the database. The four ``stripe.checkout.Session.create`` calls in
billing/stripe_service.py now:

- site 1, ``create_individual_checkout_session``, and site 3,
  ``create_license_conversion_session``: on ``resource_missing`` with
  ``param == "customer"``, clear the stale id with a conditional update,
  get a replacement customer under a new idempotency key that names the
  stale id, and retry the session once;
- site 2, ``create_license_session`` (SM ruling B2): clear that licence row's
  stale id the same way and retry once with ``customer_email``, so Stripe
  makes the customer at checkout, as for a school with no billing history;
- site 4, ``create_upgrade_checkout_session`` (SM ruling B1): wrap only. Its
  customer comes from the live Stripe subscription, not the wallet;
- every other StripeError, and a second failure, becomes BillingRequestError
  with the approved text, which never carries Stripe's text.

Stripe is always mocked here. No call leaves the machine.
"""

import hashlib
import threading
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from dateutil.relativedelta import relativedelta
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from AutoGrader.testing.concurrency import run_concurrently
from billing.errors import BillingRequestError
from billing.imports import stripe
from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import (
    StripeCheckoutService,
    StripeSubscriptionMutationService,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

FALLBACK = (
    "We couldn't start checkout for your account. You haven't been charged. "
    "Please contact support."
)
STALE = "cus_h274stale"
URLS = {"success_url": "https://example.com/ok", "cancel_url": "https://example.com/no"}


def missing(param, value):
    return stripe.error.InvalidRequestError(
        f"No such {param}: {value!r}", param, code="resource_missing"
    )


def new_id_for(key):
    return "cus_new_" + hashlib.sha256(key.encode()).hexdigest()[:10]


class FakeStripe:
    """Session.create and Customer.create, recorded.

    `missing_customers` answer "No such customer"; `missing_all` makes every
    customer missing; `error` is raised by every Session.create. Customer
    ids are a function of the idempotency key, as Stripe's replay is.
    `meet` holds the first failure of each thread until `meet` threads have
    failed, so concurrent requests really do see the stale id together.
    """

    def __init__(
        self, missing_customers=(STALE,), missing_all=False, error=None, meet=0
    ):
        self.missing_customers = set(missing_customers)
        self.missing_all = missing_all
        self.error = error
        self.sessions = []
        self.customers = []
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(meet) if meet else None
        self.met = set()

    def session_create(self, **kwargs):
        with self.lock:
            self.sessions.append(kwargs)
        if self.error is not None:
            raise self.error
        customer = kwargs.get("customer")
        if customer is not None and (
            self.missing_all or customer in self.missing_customers
        ):
            me = threading.get_ident()
            if self.barrier is not None and me not in self.met:
                self.met.add(me)
                self.barrier.wait(timeout=30)
            raise missing("customer", customer)
        return SimpleNamespace(id="cs_h274", url="https://checkout.stripe.test/cs_h274")

    def customer_create(self, **kwargs):
        with self.lock:
            self.customers.append(kwargs)
        return SimpleNamespace(id=new_id_for(kwargs["idempotency_key"]))

    def start(self, test):
        for target, fn in (
            (stripe.checkout.Session, self.session_create),
            (stripe.Customer, self.customer_create),
        ):
            p = patch.object(target, "create", side_effect=fn)
            p.start()
            test.addCleanup(p.stop)
        return self


def make_user(email, user_type, **extra):
    return CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        first_name="Test",
        last_name="User",
        user_type=user_type,
        is_active=True,
        **extra,
    )


def make_individual_plan(name, tier, price_cents, price_id, interval=None):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=interval or BillingInterval.MONTHLY,
        price_cents=price_cents,
        monthly_credits=30_000_000,
        stripe_price_id=price_id,
        is_active=True,
    )


def make_license_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="H274 License Plan",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20000,
        overage_block_size=5000,
        overage_block_price=299,
        stripe_price_id="price_h274_license",
    )


class Body(APITestCase):
    """Shared assertion; no tests of its own."""

    def assertFallback(self, response):
        self.assertEqual(
            response.status_code, status.HTTP_400_BAD_REQUEST, response.content
        )
        self.assertEqual(response.json()["message"], FALLBACK)
        self.assertNotIn(b"cus_", response.content)
        self.assertNotIn(b"No such", response.content)


# ----------------------------------------------------------------------
# Site 1: create_individual_checkout_session, over HTTP (select-plan)
# ----------------------------------------------------------------------


class IndividualCheckoutTests(Body):
    def setUp(self):
        cache.clear()
        self.user = make_user("h274-stale@example.com", UserTypes.TEACHER)
        self.plan = make_individual_plan(PlanType.PRO, PlanTier.PRO, 2999, "price_h274")
        CreditWallet.objects.update_or_create(
            user=self.user, defaults={"stripe_customer_id": STALE}
        )
        self.client.force_authenticate(user=self.user)

    def post(self):
        return self.client.post(
            reverse("subscription-select-plan"),
            {"plan_id": str(self.plan.id), **URLS},
            format="json",
        )

    def wallet_id(self):
        return CreditWallet.objects.get(user=self.user).stripe_customer_id

    def test_a_stale_customer_is_replaced_and_checkout_starts(self):
        fake = FakeStripe().start(self)

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        self.assertEqual(response.json()["data"]["action"], "checkout")
        key = f"customer-for-user-{self.user.id}-replacing-{STALE}"
        self.assertEqual([c["idempotency_key"] for c in fake.customers], [key])
        self.assertEqual(self.wallet_id(), new_id_for(key))
        self.assertEqual(
            [s["customer"] for s in fake.sessions], [STALE, new_id_for(key)]
        )
        for s in fake.sessions:
            self.assertNotIn("idempotency_key", s)

    def test_the_replacement_is_logged_with_ids_only(self):
        FakeStripe().start(self)

        with self.assertLogs("billing.stripe_service", level="WARNING") as logs:
            self.post()

        text = "\n".join(logs.output)
        key = f"customer-for-user-{self.user.id}-replacing-{STALE}"
        self.assertIn(STALE, text)
        self.assertIn(new_id_for(key), text)
        self.assertNotIn(self.user.email, text)

    def test_a_second_missing_customer_gives_the_fallback(self):
        fake = FakeStripe(missing_all=True).start(self)

        self.assertFallback(self.post())
        self.assertEqual(len(fake.sessions), 2)
        self.assertEqual(len(fake.customers), 1)

    def test_a_missing_price_is_not_healed(self):
        fake = FakeStripe(error=missing("price", "price_h274")).start(self)

        self.assertFallback(self.post())
        self.assertEqual(self.wallet_id(), STALE)
        self.assertEqual(fake.customers, [])
        self.assertEqual(len(fake.sessions), 1)

    def test_any_other_stripe_error_gives_the_fallback(self):
        fake = FakeStripe(
            error=stripe.error.APIConnectionError(f"boom {STALE} No such thing")
        ).start(self)

        self.assertFallback(self.post())
        self.assertEqual(self.wallet_id(), STALE)
        self.assertEqual(len(fake.sessions), 1)


# ----------------------------------------------------------------------
# Site 3: create_license_conversion_session, over HTTP (convert-to-stripe)
# ----------------------------------------------------------------------


class LicenseConversionTests(Body):
    def setUp(self):
        cache.clear()
        mail = patch("billing.license_service.send_email_task")
        mail.start()
        self.addCleanup(mail.stop)
        self.school = School.objects.create(name="H274 School")
        self.admin = make_user(
            "admin@h274-school.edu", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.license_sub = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=make_license_plan(),
            billing_method=LicenseBillingMethod.OFFLINE,
            max_seats=5,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            stripe_customer_id=STALE,
        )
        root = CustomUser.objects.create_superuser(
            email="root@h274.example",
            password="testpass123",  # pragma: allowlist secret
            first_name="Super",
            last_name="Admin",
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.client.force_authenticate(user=root)

    def post(self):
        return self.client.post(
            reverse(
                "license-subscription-convert-to-stripe",
                kwargs={"pk": self.license_sub.pk},
            ),
            {},
            format="json",
        )

    def license_id(self):
        self.license_sub.refresh_from_db()
        return self.license_sub.stripe_customer_id

    def test_a_stale_customer_is_replaced_and_checkout_starts(self):
        fake = FakeStripe().start(self)

        response = self.post()

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.content)
        key = f"customer-for-license-{self.license_sub.id}-replacing-{STALE}"
        self.assertEqual([c["idempotency_key"] for c in fake.customers], [key])
        self.assertEqual(self.license_id(), new_id_for(key))
        self.assertEqual(
            [s["customer"] for s in fake.sessions], [STALE, new_id_for(key)]
        )

    def test_a_second_missing_customer_gives_the_fallback(self):
        fake = FakeStripe(missing_all=True).start(self)

        self.assertFallback(self.post())
        self.assertEqual(len(fake.sessions), 2)

    def test_a_missing_price_is_not_healed(self):
        fake = FakeStripe(error=missing("price", "price_h274_license")).start(self)

        self.assertFallback(self.post())
        self.assertEqual(self.license_id(), STALE)
        self.assertEqual(fake.customers, [])

    def test_any_other_stripe_error_gives_the_fallback(self):
        FakeStripe(error=stripe.error.APIConnectionError(f"boom {STALE}")).start(self)

        self.assertFallback(self.post())
        self.assertEqual(self.license_id(), STALE)


# ----------------------------------------------------------------------
# Site 2: create_license_session (SM ruling B2)
# ----------------------------------------------------------------------


class LicenseCreateSessionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="H274 New License School")
        self.admin = make_user(
            "admin@h274-new.edu", UserTypes.SCHOOL_ADMIN, school=self.school
        )
        self.plan = make_license_plan()
        # A cancelled earlier licence whose customer is gone at Stripe.
        self.old = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_method=LicenseBillingMethod.STRIPE,
            max_seats=5,
            billing_cycle_start=timezone.now() - timedelta(days=400),
            billing_cycle_end=timezone.now() - timedelta(days=35),
            is_active=False,
            stripe_customer_id=STALE,
        )

    def create(self):
        return StripeCheckoutService.create_license_session(
            school=self.school,
            plan=self.plan,
            admin_user=self.admin,
            contract_months=12,
            max_seats=2,
            teacher_emails=[],
            custom_price_cents=None,
            **URLS,
        )

    def test_a_stale_customer_is_cleared_and_checkout_uses_the_email(self):
        fake = FakeStripe().start(self)

        session = self.create()

        self.assertEqual(session.id, "cs_h274")
        self.old.refresh_from_db()
        self.assertIsNone(self.old.stripe_customer_id)
        self.assertEqual(fake.sessions[0]["customer"], STALE)
        retry = fake.sessions[1]
        self.assertNotIn("customer", retry)
        self.assertEqual(retry["customer_email"], self.admin.email)
        self.assertNotIn("idempotency_key", retry)
        self.assertEqual(fake.customers, [])

    def test_a_missing_price_is_not_healed(self):
        FakeStripe(error=missing("price", "price_h274_license")).start(self)

        with self.assertRaisesMessage(BillingRequestError, FALLBACK):
            self.create()
        self.old.refresh_from_db()
        self.assertEqual(self.old.stripe_customer_id, STALE)

    def test_any_other_stripe_error_gives_the_fallback(self):
        FakeStripe(error=stripe.error.APIConnectionError(f"boom {STALE}")).start(self)

        with self.assertRaises(BillingRequestError) as caught:
            self.create()
        self.assertEqual(str(caught.exception), FALLBACK)


# ----------------------------------------------------------------------
# Site 4: create_upgrade_checkout_session (SM ruling B1: wrap only)
# ----------------------------------------------------------------------


class UpgradeCheckoutTests(TestCase):
    def setUp(self):
        self.user = make_user("h274-upgrade@example.com", UserTypes.TEACHER)
        basic = make_individual_plan(
            PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h274_b"
        )
        self.pro = make_individual_plan(
            PlanType.PRO, PlanTier.PRO, 2999, "price_h274_p"
        )
        CreditWallet.objects.update_or_create(
            user=self.user, defaults={"stripe_customer_id": STALE}
        )
        now = timezone.now()
        self.user_sub = UserSubscription.objects.create(
            user=self.user,
            plan=basic,
            is_active=True,
            auto_renew=True,
            is_trial=False,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
            stripe_subscription_id="sub_h274",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        for target, name, value in (
            (
                stripe.Subscription,
                "retrieve",
                {"items": {"data": [{"id": "si_h274"}]}, "customer": "cus_owner"},
            ),
            (stripe.Invoice, "create_preview", {"total": 2000}),
        ):
            p = patch.object(target, name, return_value=value)
            p.start()
            self.addCleanup(p.stop)

    def upgrade(self):
        return StripeSubscriptionMutationService.create_upgrade_checkout_session(
            self.user_sub, self.pro, **URLS
        )

    def test_a_missing_customer_is_not_healed_here(self):
        fake = FakeStripe(missing_customers=("cus_owner",)).start(self)

        with self.assertRaises(BillingRequestError) as caught:
            self.upgrade()
        self.assertEqual(str(caught.exception), FALLBACK)
        self.assertEqual(len(fake.sessions), 1)
        self.assertEqual(fake.customers, [])
        self.assertEqual(
            CreditWallet.objects.get(user=self.user).stripe_customer_id, STALE
        )

    def test_any_other_stripe_error_gives_the_fallback(self):
        FakeStripe(error=stripe.error.APIConnectionError("boom cus_owner")).start(self)

        with self.assertRaises(BillingRequestError) as caught:
            self.upgrade()
        self.assertEqual(str(caught.exception), FALLBACK)


# ----------------------------------------------------------------------
# Concurrency: two checkouts hit the same stale id at once (sites 1 and 3)
# ----------------------------------------------------------------------


class StaleCustomerConcurrencyTests(TransactionTestCase):
    def assertOneReplacement(self, fake, key, stored, results, errors):
        self.assertEqual(errors, [])
        self.assertEqual([r.id for r in results], ["cs_h274", "cs_h274"])
        # Each request: one failed attempt on the stale id, one retry.
        self.assertEqual(
            sorted(s["customer"] for s in fake.sessions),
            sorted([STALE, STALE, new_id_for(key), new_id_for(key)]),
        )
        # However many Customer.create calls, one key, so one customer.
        self.assertTrue(fake.customers)
        self.assertEqual({c["idempotency_key"] for c in fake.customers}, {key})
        # No lost update: the row ends on that one customer.
        self.assertEqual(stored(), new_id_for(key))

    def test_two_individual_checkouts_replace_the_customer_once(self):
        user = make_user("h274-race@example.com", UserTypes.TEACHER)
        plan = make_individual_plan(PlanType.PRO, PlanTier.PRO, 2999, "price_h274")
        CreditWallet.objects.update_or_create(
            user=user, defaults={"stripe_customer_id": STALE}
        )
        fake = FakeStripe(meet=2).start(self)

        results, errors = run_concurrently(
            lambda i: StripeCheckoutService.create_individual_checkout_session(
                CustomUser.objects.get(pk=user.pk), plan, **URLS
            ),
            2,
            test=self,
        )

        self.assertOneReplacement(
            fake,
            f"customer-for-user-{user.id}-replacing-{STALE}",
            lambda: CreditWallet.objects.get(user=user).stripe_customer_id,
            results,
            errors,
        )

    def test_two_license_conversions_replace_the_customer_once(self):
        school = School.objects.create(name="H274 Race School")
        admin = make_user("admin@h274-race.edu", UserTypes.SCHOOL_ADMIN, school=school)
        license_sub = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=make_license_plan(),
            billing_method=LicenseBillingMethod.OFFLINE,
            max_seats=5,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            is_active=True,
            stripe_customer_id=STALE,
        )
        fake = FakeStripe(meet=2).start(self)

        results, errors = run_concurrently(
            lambda i: StripeCheckoutService.create_license_conversion_session(
                LicenseSubscription.objects.get(pk=license_sub.pk), None, **URLS
            ),
            2,
            test=self,
        )

        self.assertOneReplacement(
            fake,
            f"customer-for-license-{license_sub.id}-replacing-{STALE}",
            lambda: LicenseSubscription.objects.get(
                pk=license_sub.pk
            ).stripe_customer_id,
            results,
            errors,
        )
