"""
billing/tests/test_h259_stripe_log_text.py
==========================================
H-259: a server log line about a Stripe failure names the error's class and
Stripe's request id, never Stripe's message or its traceback.

H-255 stopped Stripe's text reaching a client. Its survey found the same
text in eighteen server log lines in `billing/` (eight that put the
exception into the line, ten that log its traceback), and in the H-28
reconciliation alert, which is an ERROR log (a Sentry event) and an email to
every super admin. An AuthenticationError's message is "Expired API Key
provided: sk_test_…" with the key's tail; an InvalidRequestError's names ids
and parameters. The rule is H-227's and H-255's: the class and the request
id, which is what finds the call in Stripe's dashboard.

Pinned here:
- each of the eighteen lines, and the alert's log line and email, carry the
  class and request id and no Stripe text; each keeps its level, so a line
  that was `logger.exception` is still an ERROR (and a Sentry event), now
  without the traceback;
- the log scrubber masks a Stripe secret key or webhook secret (`sk_test_`,
  `sk_live_`, `rk_test_`, `rk_live_`, `whsec_`) in a log line, in a
  traceback, and in a Sentry event, keeping the prefix; a publishable key
  and words that merely end in "sk_" are left alone (control).

All Stripe API calls are mocked by patching attributes on the real `stripe`
module, so `stripe.error.*` stay the real exception classes. The scrubber is
off under the test runner, so the site tests read what each line itself
says.
"""

import logging
import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import stripe as real_stripe
from dateutil.relativedelta import relativedelta
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import DatabaseError
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from AutoGrader.tests_log_scrubbing import standalone_logger, stream_handler
from billing import license_stripe_mutation, receipts
from billing.models import (
    BillingInterval,
    CreditWallet,
    LicenseBillingMethod,
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    StripeSubscriptionStatus,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import (
    StripeSubscriptionMutationService,
    StripeSubscriptionScheduleService,
    StripeWebhookHandler,
    SubscriptionReactivationService,
)
from billing.tasks import (
    _find_new_period_paid_invoice,
    process_license_renewals,
    reconcile_subscription_prices,
)
from billing.tests.test_h28_cancel_phases import LicencePhaseTestCase
from billing.tests.test_payment_methods import FakeStripeList, make_card
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

PROBE = "Expired API Key provided: sk_test_h259xxxxxxxxxxxxk7Qp"
REQUEST_ID = "req_h259"
LABEL = f"AuthenticationError (request {REQUEST_ID})"


def expired_key(request_id=REQUEST_ID):
    return real_stripe.error.AuthenticationError(
        PROBE, http_status=401, headers={"request-id": request_id}
    )


def rendered(records):
    """Every record as a handler prints it, traceback included."""
    formatter = logging.Formatter("%(levelname)s %(name)s %(message)s")
    return "\n".join(formatter.format(record) for record in records)


def assert_logged_without_stripe_text(test, logs, fragment, level, label=LABEL):
    """No captured record carries Stripe's text, in its line or a traceback;
    the one line holding `fragment` is at `level`, names `label`, and
    carries no exception."""
    text = rendered(logs.records)
    test.assertNotIn("sk_test", text)
    test.assertNotIn("API Key", text)
    [line] = [r for r in logs.records if fragment in r.getMessage()]
    test.assertEqual(line.levelname, level)
    test.assertIn(label, line.getMessage())
    test.assertIsNone(line.exc_info)


def make_plan(name, tier, price_cents, stripe_price_id, **extra):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=name,
        category=extra.pop("category", PlanCategory.INDIVIDUAL),
        tier=tier,
        interval=BillingInterval.MONTHLY,
        price_cents=price_cents,
        monthly_credits=30_000_000,
        stripe_price_id=stripe_price_id,
        is_active=True,
        **extra,
    )


def make_teacher(email):
    user = CustomUser.objects.create_user(
        email=email,
        password="testpass123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
    )
    CreditWallet.objects.update_or_create(
        user=user, defaults={"stripe_customer_id": "cus_h259"}
    )
    return user


# ---------------------------------------------------------------------------
# billing/receipts.py and billing/tasks.py
# ---------------------------------------------------------------------------


class ReceiptAndTaskLogTests(TestCase):
    def test_a_receipt_lookup_failure_logs_no_stripe_text(self):
        client = MagicMock()
        client.v1.invoices.retrieve.side_effect = expired_key()

        with patch.object(
            receipts, "_receipt_stripe_client", return_value=client
        ), self.assertLogs("billing.receipts", "DEBUG") as logs:
            self.assertIsNone(receipts.lookup_receipt_url(invoice_id="in_h259"))

        assert_logged_without_stripe_text(
            self, logs, "Receipt lookup failed", "WARNING"
        )

    def test_an_invoice_listing_failure_logs_no_stripe_text(self):
        with patch.object(real_stripe, "Invoice") as mock_invoice, self.assertLogs(
            "billing.tasks", "DEBUG"
        ) as logs:
            mock_invoice.list.side_effect = expired_key()
            self.assertIsNone(_find_new_period_paid_invoice("sub_h259", timezone.now()))

        assert_logged_without_stripe_text(
            self, logs, "Could not list invoices", "WARNING"
        )

    def test_a_licence_cancel_failure_in_the_renewal_sweep_logs_no_stripe_text(self):
        school = School.objects.create(name="H-259 Sweep School")
        admin = CustomUser.objects.create_user(
            email="admin@h259-sweep.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        plan = make_plan(
            PlanType.PRO_LICENSE,
            PlanTier.PRO,
            1000,
            "price_h259_licence",
            category=PlanCategory.LICENSE,
        )
        now = timezone.now()
        LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            is_active=True,
            auto_renew=False,
            billing_cycle_start=now - relativedelta(months=12),
            billing_cycle_end=now - timedelta(minutes=5),
            stripe_subscription_id="sub_h259_licence",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
            billing_method=LicenseBillingMethod.STRIPE,
        )

        with patch.object(real_stripe, "Subscription") as mock_sub, patch(
            "billing.tasks.sync_teachers_under_license_to_mailerlite"
        ), self.assertLogs("billing.tasks", "DEBUG") as logs:
            mock_sub.modify.side_effect = expired_key()
            process_license_renewals.apply().get()

        assert_logged_without_stripe_text(
            self, logs, "Failed to cancel Stripe subscription for license", "WARNING"
        )

    def test_a_price_reconciliation_listing_failure_logs_no_stripe_text(self):
        user = make_teacher("h259-reconcile@example.com")
        plan = make_plan(PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h259_std")
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=2),
            billing_cycle_end=now + relativedelta(months=1),
            next_credit_grant_at=now + relativedelta(months=1),
            stripe_subscription_id="sub_h259_reconcile",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        with patch.object(real_stripe, "Subscription") as mock_sub, self.assertLogs(
            "billing.tasks", "DEBUG"
        ) as logs:
            mock_sub.list.side_effect = expired_key()
            with self.assertRaises(real_stripe.error.AuthenticationError):
                reconcile_subscription_prices()

        assert_logged_without_stripe_text(
            self, logs, "NO drift check was performed", "ERROR"
        )


# ---------------------------------------------------------------------------
# billing/stripe_service.py
# ---------------------------------------------------------------------------


class StripeServiceLogTests(TestCase):
    def test_a_failed_compensating_revert_on_reactivation_logs_no_stripe_text(self):
        user = make_teacher("h259-reactivate@example.com")
        plan = make_plan(PlanType.PRO, PlanTier.PRO, 2999, "price_h259_pro")
        now = timezone.now()
        sub = UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            auto_renew=False,
            is_trial=False,
            billing_cycle_start=now,
            billing_cycle_end=now + relativedelta(months=1),
            stripe_subscription_id="sub_h259_reactivate",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )

        with patch.object(real_stripe, "Subscription") as mock_sub, patch.object(
            UserSubscription, "save", side_effect=DatabaseError("h259 local save")
        ), self.assertLogs("billing.stripe_service", "DEBUG") as logs:
            mock_sub.retrieve.return_value = {
                "id": "sub_h259_reactivate",
                "status": "active",
                "cancel_at_period_end": True,
            }
            mock_sub.modify.side_effect = [None, expired_key()]
            with self.assertRaises(DatabaseError):
                SubscriptionReactivationService.reactivate_if_cancelling(sub)

        assert_logged_without_stripe_text(
            self, logs, "compensating Stripe revert ALSO failed", "ERROR"
        )

    def test_a_failed_duplicate_invoice_cleanup_logs_no_stripe_text(self):
        with patch.object(real_stripe, "Subscription") as mock_sub, self.assertLogs(
            "billing.stripe_service", "DEBUG"
        ) as logs:
            mock_sub.retrieve.side_effect = expired_key()
            StripeSubscriptionMutationService._void_or_refund_side_effect_invoice(
                "sub_h259_interval"
            )

        assert_logged_without_stripe_text(
            self, logs, "Failed to void/refund a potential duplicate invoice", "ERROR"
        )

    def test_a_failed_price_revert_logs_no_stripe_text(self):
        with patch.object(real_stripe, "Subscription") as mock_sub, self.assertLogs(
            "billing.stripe_service", "DEBUG"
        ) as logs:
            mock_sub.modify.side_effect = expired_key()
            StripeSubscriptionMutationService._revert_to_previous_price(
                "sub_h259_upgrade", "si_h259", "price_h259_old", None
            )

        assert_logged_without_stripe_text(
            self, logs, "Failed to revert subscription", "ERROR"
        )

    def test_a_failed_unpaid_invoice_void_logs_no_stripe_text(self):
        with patch.object(real_stripe, "Subscription"), patch.object(
            real_stripe, "Invoice"
        ) as mock_invoice, self.assertLogs("billing.stripe_service", "DEBUG") as logs:
            mock_invoice.void_invoice.side_effect = expired_key()
            StripeSubscriptionMutationService._revert_to_previous_price(
                "sub_h259_upgrade",
                "si_h259",
                "price_h259_old",
                {"id": "in_h259_unpaid", "status": "open"},
            )

        assert_logged_without_stripe_text(
            self, logs, "Failed to void unpaid upgrade invoice", "ERROR"
        )

    def test_an_already_released_schedule_logs_no_stripe_text(self):
        error = real_stripe.error.InvalidRequestError(
            PROBE, None, http_status=400, headers={"request-id": REQUEST_ID}
        )
        user_sub = SimpleNamespace(id=4259, stripe_schedule_id="sub_sched_h259")

        with patch.object(
            real_stripe, "SubscriptionSchedule"
        ) as mock_schedule, self.assertLogs("billing.stripe_service", "DEBUG") as logs:
            mock_schedule.release.side_effect = error
            StripeSubscriptionScheduleService.release_schedule(user_sub)

        assert_logged_without_stripe_text(
            self,
            logs,
            "already released or invalid",
            "INFO",
            label=f"InvalidRequestError (request {REQUEST_ID})",
        )

    def test_a_failed_default_card_on_setup_intent_succeeded_logs_no_stripe_text(
        self,
    ):
        setup_intent = {
            "id": "seti_h259",
            "metadata": {"set_as_default": "true"},
            "payment_method": "pm_h259",
            "customer": "cus_h259",
        }

        with patch.object(real_stripe, "Customer") as mock_customer, self.assertLogs(
            "billing.stripe_service", "DEBUG"
        ) as logs:
            mock_customer.modify.side_effect = expired_key()
            StripeWebhookHandler.handle_setup_intent_succeeded(setup_intent)

        assert_logged_without_stripe_text(
            self, logs, "Failed to set default payment method for customer", "ERROR"
        )


# ---------------------------------------------------------------------------
# billing/license_views.py
# ---------------------------------------------------------------------------


class LicenceViewLogTests(APITestCase):
    def setUp(self):
        cache.clear()
        school = School.objects.create(name="H-259 Licence School")
        admin = CustomUser.objects.create_user(
            email="admin@h259-licence.edu",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@h259-licence.gradea.com",
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        plan = make_plan(
            PlanType.PRO_LICENSE,
            PlanTier.PRO,
            1000,
            "price_h259_licence_view",
            category=PlanCategory.LICENSE,
        )
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=admin,
            plan=plan,
            is_active=True,
            auto_renew=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            stripe_subscription_id="sub_h259_licence_view",
        )
        self.client.force_authenticate(user=self.superadmin)

    def test_a_stripe_error_on_licence_overage_purchase_logs_no_stripe_text(self):
        with patch(
            "billing.license_views.LicenseSubscriptionService.initiate_overage_purchase",
            side_effect=expired_key(),
        ), self.assertLogs("billing.license_views", "DEBUG") as logs:
            response = self.client.post(
                reverse(
                    "license-subscription-purchase-overage",
                    kwargs={"pk": self.licence.pk},
                ),
                {"total_blocks": 1, "allocations": {str(uuid.uuid4()): 1}},
                format="json",
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Stripe error during license overage purchase", "ERROR"
        )

    def test_a_stripe_error_on_licence_setup_intent_logs_no_stripe_text(self):
        with patch(
            "billing.license_views.StripeCustomerService.create_license_setup_intent",
            side_effect=expired_key(),
        ), self.assertLogs("billing.license_views", "DEBUG") as logs:
            response = self.client.post(
                reverse(
                    "license-subscription-setup-payment-method",
                    kwargs={"pk": self.licence.pk},
                )
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to create setup intent for license", "ERROR"
        )


# ---------------------------------------------------------------------------
# billing/payment_method_views.py
# ---------------------------------------------------------------------------


class PaymentMethodViewLogTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.user = make_teacher("h259-cards@example.com")
        plan = make_plan(PlanType.STANDARD, PlanTier.STANDARD, 999, "price_h259_pm")
        UserSubscription.objects.create(
            user=self.user,
            plan=plan,
            is_active=True,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            stripe_subscription_id="sub_h259_pm",
            stripe_status=StripeSubscriptionStatus.ACTIVE,
        )
        self.client.force_authenticate(user=self.user)

    def test_a_stripe_error_listing_cards_logs_no_stripe_text(self):
        with patch.object(real_stripe, "PaymentMethod") as mock_pm, patch.object(
            real_stripe, "Customer"
        ) as mock_customer, self.assertLogs(
            "billing.payment_method_views", "DEBUG"
        ) as logs:
            mock_pm.list.side_effect = expired_key()
            mock_customer.retrieve.side_effect = expired_key()
            response = self.client.get(reverse("payment-method-list"))

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to list payment methods", "ERROR"
        )

    def test_a_stripe_error_adding_a_card_logs_no_stripe_text(self):
        with patch.object(
            real_stripe, "SetupIntent"
        ) as mock_setup_intent, self.assertLogs(
            "billing.payment_method_views", "DEBUG"
        ) as logs:
            mock_setup_intent.create.side_effect = expired_key()
            response = self.client.post(
                reverse("payment-method-list"), {}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to create setup intent for user", "ERROR"
        )

    def test_a_stripe_error_opening_the_billing_portal_logs_no_stripe_text(self):
        with patch(
            "billing.payment_method_views.StripeCustomerService."
            "create_billing_portal_session_for_request_user",
            side_effect=expired_key(),
        ), self.assertLogs("billing.payment_method_views", "DEBUG") as logs:
            response = self.client.post(
                reverse("payment-method-portal-session"), {}, format="json"
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to create billing portal session", "ERROR"
        )

    def test_a_stripe_error_removing_a_card_logs_no_stripe_text(self):
        card1 = make_card("pm_h259_1", customer="cus_h259")
        card2 = make_card("pm_h259_2", customer="cus_h259")

        with patch.object(real_stripe, "PaymentMethod") as mock_pm, self.assertLogs(
            "billing.payment_method_views", "DEBUG"
        ) as logs:
            mock_pm.retrieve.return_value = card1
            mock_pm.list.return_value = FakeStripeList([card1, card2])
            mock_pm.detach.side_effect = expired_key()
            response = self.client.delete(
                reverse("payment-method-detail", kwargs={"pk": "pm_h259_1"})
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to detach payment method", "ERROR"
        )

    def test_a_stripe_error_setting_the_default_card_logs_no_stripe_text(self):
        with patch.object(real_stripe, "PaymentMethod") as mock_pm, patch.object(
            real_stripe, "Customer"
        ) as mock_customer, self.assertLogs(
            "billing.payment_method_views", "DEBUG"
        ) as logs:
            mock_pm.retrieve.return_value = make_card("pm_h259_1", customer="cus_h259")
            mock_customer.modify.side_effect = expired_key()
            response = self.client.post(
                reverse("payment-method-set-default", kwargs={"pk": "pm_h259_1"})
            )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        assert_logged_without_stripe_text(
            self, logs, "Failed to set default payment method pm_h259_1", "ERROR"
        )


# ---------------------------------------------------------------------------
# The H-28 licence mutation, in billing/license_stripe_mutation.py
# ---------------------------------------------------------------------------


class LicenceMutationLogTests(LicencePhaseTestCase):
    SUB_ID = "sub_h259_mutation"

    def setUp(self):
        super().setUp()
        self.emails = []
        p = patch(
            "AutoGrader.dispatch.safe_delay",
            side_effect=lambda task, **kwargs: self.emails.append(kwargs),
        )
        p.start()
        self.addCleanup(p.stop)
        self.intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )

    def test_a_lost_response_that_applied_logs_no_stripe_text(self):
        def call(**key):
            raise expired_key()

        with self.assertLogs("billing.license_stripe_mutation", "DEBUG") as logs:
            license_stripe_mutation.apply_at_stripe(
                self.intent,
                call=call,
                reached=lambda: True,
                read_back_on=(real_stripe.error.AuthenticationError,),
            )

        assert_logged_without_stripe_text(self, logs, "Stripe response lost", "WARNING")

    def test_the_reconciliation_alert_carries_no_stripe_text(self):
        def call(**key):
            raise expired_key()

        def reached():
            raise expired_key(request_id="req_h259_read")

        with self.assertLogs("billing.license_stripe_mutation", "DEBUG") as logs:
            with self.assertRaises(real_stripe.error.AuthenticationError):
                license_stripe_mutation.apply_at_stripe(
                    self.intent,
                    call=call,
                    reached=reached,
                    read_back_on=(real_stripe.error.AuthenticationError,),
                )

        assert_logged_without_stripe_text(
            self, logs, "MANUAL RECONCILIATION NEEDED", "ERROR"
        )
        [line] = [r for r in logs.records if "MANUAL RECONCILIATION" in r.getMessage()]
        self.assertIn("AuthenticationError (request req_h259_read)", line.getMessage())
        # The email: Stripe's sentences go; what the operator acts on stays.
        self.assertTrue(self.emails)
        for email in self.emails:
            self.assertNotIn("sk_test", email["message"])
            self.assertNotIn("API Key", email["message"])
            self.assertIn(LABEL, email["message"])
            self.assertIn(
                "AuthenticationError (request req_h259_read)", email["message"]
            )
            self.assertIn(str(self.intent.id), email["message"])
            self.assertIn(str(self.licence.id), email["message"])


# ---------------------------------------------------------------------------
# AutoGrader/log_scrubbing.py: the last line of defence
# ---------------------------------------------------------------------------

#: Built from parts, and short: no line of this file holds a whole key shape
#: a secret scanner would take for a real one.
KEY_BODY = "H259" + "notarealkey" + "0"
PREFIXES = ("sk_test_", "sk_live_", "rk_test_", "rk_live_", "whsec_")


@override_settings(LOG_SCRUB_ADDRESSES=True)
class StripeKeyScrubbingTests(SimpleTestCase):
    def assertMasked(self, output):
        self.assertNotIn(KEY_BODY, output)
        for prefix in PREFIXES:
            self.assertIn(prefix + "[secret]", output)

    def test_a_key_in_a_log_line_is_masked(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h259", handler)
        for prefix in PREFIXES:
            logger.error("Stripe said: Invalid API Key provided: %s", prefix + KEY_BODY)

        self.assertMasked(stream.getvalue())

    def test_a_key_in_a_traceback_is_masked(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h259", handler)
        for prefix in PREFIXES:
            try:
                raise real_stripe.error.AuthenticationError(
                    f"Expired API Key provided: {prefix}{KEY_BODY}"
                )
            except real_stripe.error.AuthenticationError:
                logger.exception("Stripe call failed for licence %s", 4259)

        output = stream.getvalue()
        self.assertMasked(output)
        self.assertIn("Traceback (most recent call last)", output)
        self.assertIn("AuthenticationError", output)

    def test_a_key_in_a_sentry_event_is_masked(self):
        from AutoGrader.sentry_scrubbing import scrub_event

        keys = [prefix + KEY_BODY for prefix in PREFIXES]
        event = {
            "logentry": {
                "message": "Stripe call failed: %s",
                "formatted": f"Stripe call failed: {' '.join(keys)}",
                "params": keys,
            },
            "exception": {
                "values": [
                    {"type": "AuthenticationError", "value": f"Expired: {key}"}
                    for key in keys
                ]
            },
        }

        scrubbed = scrub_event(event, {})

        self.assertMasked(repr(scrubbed))

    def test_a_publishable_key_and_ordinary_words_are_left_alone(self):
        handler, stream = stream_handler()
        logger = standalone_logger("h259", handler)
        text = "pk_live_" + KEY_BODY + " risk_live_report task_test_runner"
        logger.error("Control: %s", text)

        self.assertEqual(stream.getvalue(), f"ERROR Control: {text}\n")
