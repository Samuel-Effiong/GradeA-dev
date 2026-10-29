"""
billing/tests/test_h261_failure_reason_label.py
================================================
H-261: failure_reason stored on intents must never carry Stripe's exception
text — only `stripe_error_label(exc)` (class + request id).

The rule, from H-255 and H-259: Stripe's own message can contain the tail of
an API key ("sk_live_…1234"), customer ids, email addresses and parameters.
`failure_reason` is a server-side column (never sent to a client), but it
appears in admin pages, reconciliation emails and logs that an operator reads.
The operator needs the error class and the Stripe request id, which finds the
call in Stripe's dashboard — not the message text.

Pinned here:
- Site 1 (license_stripe_mutation.py, "Stripe refused: {exc}"): when a
  deterministic Stripe error ends an intent, failure_reason names the class
  and request id, not the error's message.
- Site 2 (license_stripe_mutation.py, "Outcome unknown ({exc}); …"): when an
  outcome-unknown error's read-back shows the change didn't land,
  failure_reason names the class and request id.
- Site 3 (license_service.py, "Checkout session creation failed: {exc}"):
  when the licence overage checkout's Session.create fails, failure_reason
  names the class and request id.
- Site 4 (license_stripe_mutation.py, "the revert at Stripe also failed:
  {revert_exc}"): when the local-write compensate call also fails,
  failure_reason's cause string names the class and request id of the
  revert failure.

All Stripe API calls are mocked so `stripe.error.*` stay the real classes.
"""

from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

import stripe as real_stripe
from django.db import OperationalError
from django.test import TransactionTestCase
from django.utils import timezone

from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import (
    LicenceStripe,
    LicenceStripeChangeNotRecorded,
    apply_at_stripe,
    finalise,
    undo_unpaid_change,
)
from billing.models import (
    LicenseBillingMethod,
    LicenseOveragePurchaseIntent,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from billing.tests.test_h28_cancel_phases import LicencePhaseTestCase
from billing.tests.test_h28_licence_stripe_divergence import (
    _make_plan as _make_typed_plan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

FAKE_KEY = "sk_live_FAKE_key_1234"
FAKE_EMAIL = "leaked@victim.example"
PROBE = f"No such customer: cus_xxx ({FAKE_KEY}); contact {FAKE_EMAIL}"
REQUEST_ID = "req_h261"
CONTRACT_MONTHS = 12


def leaking_auth_error():
    return real_stripe.error.AuthenticationError(
        PROBE, http_status=401, headers={"request-id": REQUEST_ID}
    )


def leaking_connection_error():
    return real_stripe.error.APIConnectionError(PROBE, http_status=None)


def assert_no_leak(test, text):
    test.assertNotIn(FAKE_KEY, text)
    test.assertNotIn("sk_live", text)
    test.assertNotIn(FAKE_EMAIL, text)
    test.assertNotIn("victim.example", text)


def _make_plan():
    return SubscriptionPlan.objects.create(
        name=PlanType.PRO,
        display_name="H-261 Pro",
        category=PlanCategory.LICENSE,
        tier=PlanTier.PRO,
        monthly_credits=20_000,
        overage_block_size=5_000,
        overage_block_price=299,
        price_cents=Decimal("1000.00"),
        stripe_price_id="price_h261_pro",
        stripe_overage_price_id="price_h261_overage",
        product_id="prod_h261",
        max_overage_blocks=10,
    )


class MutationIntentFixture(TransactionTestCase):
    """Shared setup: a LicenseStripeMutationIntent backed by a real DB row."""

    SUB_ID = "sub_h261"

    def setUp(self):
        school = School.objects.create(name="H-261 School")
        self.admin = CustomUser.objects.create_user(
            email="admin@h261.school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@h261.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = _make_plan()
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            contract_months=CONTRACT_MONTHS,
            max_seats=10,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id=self.SUB_ID,
            stripe_customer_id="cus_h261",
        )
        p = patch(
            "AutoGrader.dispatch.safe_delay",
            side_effect=lambda task, **kwargs: None,
        )
        p.start()
        self.addCleanup(p.stop)
        self.intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )


class Site1RefusedTests(MutationIntentFixture):
    """Line 723: failure_reason=f'Stripe refused: {exc}'"""

    def test_failure_reason_has_no_stripe_text(self):
        def call(**key):
            raise leaking_auth_error()

        with self.assertRaises(real_stripe.error.AuthenticationError):
            apply_at_stripe(self.intent, call=call, reached=lambda: None)

        self.intent.refresh_from_db()
        reason = self.intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Stripe refused:", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site2OutcomeUnknownTests(MutationIntentFixture):
    """Line 740: failure_reason=f'Outcome unknown ({exc}); read-back shows
    not applied'"""

    def test_failure_reason_has_no_stripe_text(self):
        def call(**key):
            raise leaking_connection_error()

        def reached():
            return False

        with self.assertRaises(real_stripe.error.APIConnectionError):
            apply_at_stripe(self.intent, call=call, reached=reached)

        self.intent.refresh_from_db()
        reason = self.intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Outcome unknown", reason)
        self.assertIn("APIConnectionError", reason)
        self.assertIn("request None", reason)


class Site4RevertFailureTests(MutationIntentFixture):
    """Line 836: cause += f'; the revert at Stripe also failed: {revert_exc}'
    → escalate(intent, cause) → failure_reason=why"""

    def test_failure_reason_has_no_stripe_text(self):
        self.intent.status = LicenseStripeMutationStatus.STRIPE_APPLIED
        self.intent.save(update_fields=["status"])

        def revalidate(licence):
            raise OperationalError("connection lost")

        def write(licence, result):
            pass  # pragma: no cover

        def compensate(**key):
            raise leaking_auth_error()

        with self.assertRaises(LicenceStripeChangeNotRecorded):
            finalise(
                self.intent,
                revalidate=revalidate,
                write=write,
                compensate=compensate,
            )

        self.intent.refresh_from_db()
        reason = self.intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("the revert at Stripe also failed:", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site3CheckoutTests(TransactionTestCase):
    """Line 3342: intent.failure_reason = f'Checkout session creation
    failed: {exc}'"""

    def setUp(self):
        school = School.objects.create(name="H-261 Checkout School")
        self.admin = CustomUser.objects.create_user(
            email="admin@h261-checkout.school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.plan = _make_plan()
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            contract_months=CONTRACT_MONTHS,
            max_seats=10,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id="sub_h261_checkout",
            stripe_customer_id="cus_h261_checkout",
        )

    @patch(
        "billing.stripe_service.StripeCustomerService.get_or_create_license_customer"
    )
    @patch("billing.license_service.stripe.checkout.Session.create")
    def test_failure_reason_has_no_stripe_text(
        self, mock_session_create, mock_customer
    ):
        mock_customer.return_value = "cus_h261_checkout"
        mock_session_create.side_effect = leaking_auth_error()

        with self.assertRaises(ValueError):
            LicenseSubscriptionService._create_overage_checkout(
                license_sub=self.licence,
                requesting_user=self.admin,
                total_blocks=1,
                allocations={str(self.admin.pk): 1},
                success_url="https://example.com/ok",
                cancel_url="https://example.com/cancel",
            )

        intent = LicenseOveragePurchaseIntent.objects.get(
            license_subscription=self.licence
        )
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Checkout session creation failed:", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


# ---------------------------------------------------------------------------
# Sites 5-6: undo_unpaid_change internal leaks (problems list)
# ---------------------------------------------------------------------------


class Site5UndoRevertTests(MutationIntentFixture):
    """Line 772: problems.append(f"the revert failed: {exc}")
    → escalate(787) → failure_reason"""

    def test_failure_reason_has_no_stripe_text(self):
        def revert(**key):
            raise leaking_auth_error()

        result = undo_unpaid_change(
            self.intent, revert, find_invoice=lambda: None, why="test"
        )
        self.assertFalse(result)
        self.intent.refresh_from_db()
        reason = self.intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("the revert failed", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site6UndoVoidTests(MutationIntentFixture):
    """Line 784: problems.append(f"voiding the change's invoice failed: {exc}")
    → escalate(787) → failure_reason"""

    def test_failure_reason_has_no_stripe_text(self):
        def revert(**key):
            pass

        with patch.object(
            LicenceStripe, "retrieve_invoice", return_value={"status": "open"}
        ), patch.object(
            LicenceStripe, "void_invoice", side_effect=leaking_auth_error()
        ):
            result = undo_unpaid_change(
                self.intent,
                revert,
                find_invoice=lambda: "in_h261_void",
                why="test",
            )
        self.assertFalse(result)
        self.intent.refresh_from_db()
        reason = self.intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("voiding", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


# ---------------------------------------------------------------------------
# Sites 7-13: service-level caller-side leaks
# ---------------------------------------------------------------------------


class ServiceLevelFixture(LicencePhaseTestCase):
    """A licence on the stateful Stripe fake with helpers for the service
    entry points that create and exercise a mutation intent."""

    SUB_ID = "sub_h261_svc"

    def setUp(self):
        super().setUp()
        self.dearer_plan = _make_typed_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h261_svc_power"
        )

    def update_seats(self):
        return LicenseSubscriptionService.update_seats(
            self.licence, self.SEATS + 5, performed_by=self.superadmin
        )

    def change_plan(self):
        return LicenseSubscriptionService.change_license_plan(
            self.licence, self.dearer_plan, performed_by=self.superadmin
        )

    def _card_error_with_probe(self):
        fake = self.stripe.subscription_modify
        self.stripe.card_error_on_modify = True

        def modify(*args, **kwargs):
            try:
                return fake(*args, **kwargs)
            except real_stripe.error.CardError as orig:
                raise real_stripe.error.CardError(
                    PROBE,
                    None,
                    "card_declined",
                    http_status=402,
                    headers={"request-id": REQUEST_ID},
                ) from orig

        return patch.object(LicenceStripe, "modify_subscription", side_effect=modify)


class Site7AbandonSubReadSeatsTests(ServiceLevelFixture):
    """license_service.py:2829 — abandon(intent,
    f"could not read the subscription: {exc}")"""

    def test_failure_reason_has_no_stripe_text(self):
        with patch.object(
            LicenceStripe,
            "retrieve_subscription",
            side_effect=leaking_auth_error(),
        ):
            with self.assertRaises(ValueError):
                self.update_seats()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Not attempted", reason)
        self.assertIn("could not read the subscription", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site8CardErrorSeatsTests(ServiceLevelFixture):
    """license_service.py:2893 — payment_failed(f"card error: {exc}")
    → undo_unpaid_change why → line 792 failure_reason"""

    def test_failure_reason_has_no_stripe_text(self):
        with self._card_error_with_probe():
            with self.assertRaises(ValueError):
                self.update_seats()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Payment not collected", reason)
        self.assertIn("card error", reason)
        self.assertIn("CardError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site9EscalateInvoiceSeatsTests(ServiceLevelFixture):
    """license_service.py:2917 — escalate(intent,
    f"seat increase applied, but its invoice could not be read: {exc}")"""

    def test_failure_reason_has_no_stripe_text(self):
        with patch.object(
            LicenceStripe,
            "retrieve_invoice",
            side_effect=leaking_auth_error(),
        ):
            with self.assertRaises(LicenceStripeChangeNotRecorded):
                self.update_seats()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("seat increase applied", reason)
        self.assertIn("could not be read", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site10AbandonSubReadPlanTests(ServiceLevelFixture):
    """stripe_service.py:1681 — abandon(intent,
    f"could not read the subscription: {exc}")"""

    def test_failure_reason_has_no_stripe_text(self):
        with patch.object(
            LicenceStripe,
            "retrieve_subscription",
            side_effect=leaking_auth_error(),
        ):
            with self.assertRaises(ValueError):
                self.change_plan()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Not attempted", reason)
        self.assertIn("could not read the subscription", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site11AbandonPriceTests(ServiceLevelFixture):
    """stripe_service.py:1723 — abandon(intent,
    f"could not create the new Price: {exc}")"""

    def test_failure_reason_has_no_stripe_text(self):
        with patch.object(
            LicenceStripe, "create_price", side_effect=leaking_auth_error()
        ):
            with self.assertRaises(ValueError):
                self.change_plan()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Not attempted", reason)
        self.assertIn("could not create the new Price", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site12CardErrorPlanTests(ServiceLevelFixture):
    """stripe_service.py:1785 — payment_failed(f"card error: {exc}", …)
    → undo_unpaid_change why → line 792 failure_reason"""

    def test_failure_reason_has_no_stripe_text(self):
        with self._card_error_with_probe():
            with self.assertRaises(ValueError):
                self.change_plan()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("Payment not collected", reason)
        self.assertIn("card error", reason)
        self.assertIn("CardError", reason)
        self.assertIn(REQUEST_ID, reason)


class Site13EscalateInvoicePlanTests(ServiceLevelFixture):
    """stripe_service.py:1815 — escalate(intent,
    f"plan upgrade applied, but its invoice could not be read: {exc}")"""

    def test_failure_reason_has_no_stripe_text(self):
        with patch.object(
            LicenceStripe,
            "retrieve_invoice",
            side_effect=leaking_auth_error(),
        ):
            with self.assertRaises(LicenceStripeChangeNotRecorded):
                self.change_plan()
        intent = self.only_intent()
        reason = intent.failure_reason
        assert reason is not None
        assert_no_leak(self, reason)
        self.assertIn("plan upgrade applied", reason)
        self.assertIn("could not be read", reason)
        self.assertIn("AuthenticationError", reason)
        self.assertIn(REQUEST_ID, reason)
