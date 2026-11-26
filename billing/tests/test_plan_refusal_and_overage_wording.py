"""Two refusals that showed internal text (owner choices 1A and 2A, 2026-11-26).

1. A teacher refused AI access for a plan reason read "AI access denied:
   <can_user_access_ai's reason>", e.g. "AI access denied: Internal Error:
   Could not verify subscription status". They now read the standard plan
   sentence (the AI_FEATURE_NOT_AVAILABLE spec's message); the reason stays
   in the server log.
2. A Stripe failure on a school's overage checkout read "Could not start
   overage checkout: <Stripe's described error>". It now reads the
   checkout sentence the teacher overage checkout uses
   (billing.stripe_service.CHECKOUT_FAILED_MESSAGE). The intent is still
   marked FAILED with its labelled failure_reason.

No new wording: both sentences already exist.
"""

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from AutoGrader.reason_codes import REASON_CODES, ReasonCode
from billing.errors import BillingRequestError
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseOveragePurchaseIntent,
    LicenseOveragePurchaseStatus,
    LicenseSubscription,
)
from billing.stripe_service import CHECKOUT_FAILED_MESSAGE
from billing.tests.test_h261_failure_reason_label import (
    CONTRACT_MONTHS,
    FAKE_KEY,
    PROBE,
    _make_plan,
    assert_no_leak,
    leaking_auth_error,
)
from classrooms.models import Course, School, Session
from users.models import CustomUser, UserTypes

PLAN_SENTENCE = REASON_CODES[ReasonCode.AI_FEATURE_NOT_AVAILABLE].message
INTERNAL = "Internal Error: Could not verify subscription status"


class APlanRefusalReadsTheStandardSentence(TestCase):
    """Through the generate endpoint, as QA reaches it: the real billing
    gate in ai_processor.services, with only the access answer patched."""

    def setUp(self):
        self.teacher = CustomUser.objects.create_user(
            email="plan-wording@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        self.course = Course.objects.create(
            name="Biology",
            teacher=self.teacher,
            session=Session.objects.create(name="Term", teacher=self.teacher),
        )
        self.client = APIClient(raise_request_exception=False)
        self.client.force_authenticate(user=self.teacher)

    def generate(self, reason):
        with patch(
            "ai_processor.services.can_user_access_ai", return_value=(False, reason)
        ):
            with self.assertLogs("ai_processor.services", "WARNING") as logs:
                response = self.client.post(
                    reverse(
                        "assignment-generate", kwargs={"course_id": self.course.id}
                    ),
                    {"prompt": "Create a one-question biology quiz."},
                    format="json",
                )
        return response, "\n".join(logs.output)

    def test_an_internal_reason_is_never_shown(self):
        response, logged = self.generate(INTERNAL)

        self.assertEqual(response.status_code, 403, response.content)
        body = response.json()
        envelope = body["error"]["field_errors"]
        self.assertEqual(envelope["reason_code"], "AI_FEATURE_NOT_AVAILABLE")
        self.assertEqual(envelope["error"], PLAN_SENTENCE)
        self.assertEqual(body["message"], PLAN_SENTENCE)
        raw = response.content.decode()
        self.assertNotIn("Internal Error", raw)
        self.assertNotIn("subscription status", raw)
        self.assertNotIn("AI access denied", raw)
        # The reason is kept for us, in the server log.
        self.assertIn(INTERNAL, logged)
        self.assertIn(str(self.teacher.id), logged)

    def test_a_plain_plan_reason_reads_the_same_sentence(self):
        response, _ = self.generate("No active subscription")

        self.assertEqual(response.status_code, 403, response.content)
        self.assertEqual(response.json()["message"], PLAN_SENTENCE)
        self.assertNotIn("No active subscription", response.content.decode())

    def test_a_balance_reason_is_still_the_credit_refusal(self):
        """Control: unchanged by the fix."""
        from billing.access_control import NO_CREDITS_REMAINING_REASON

        with patch(
            "ai_processor.services.can_user_access_ai",
            return_value=(False, NO_CREDITS_REMAINING_REASON),
        ):
            response = self.client.post(
                reverse("assignment-generate", kwargs={"course_id": self.course.id}),
                {"prompt": "Create a one-question biology quiz."},
                format="json",
            )

        self.assertEqual(response.status_code, 402, response.content)
        self.assertEqual(
            response.json()["error"]["field_errors"]["reason_code"],
            "INSUFFICIENT_CREDITS",
        )


class ALicenseOverageCheckoutFailureReadsTheCheckoutSentence(TransactionTestCase):
    def setUp(self):
        school = School.objects.create(name="Overage Wording School")
        self.admin = CustomUser.objects.create_user(
            email="admin@overage-wording.school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=school,
        )
        self.licence = LicenseSubscription.objects.create(
            school=school,
            admin_user=self.admin,
            plan=_make_plan(),
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            contract_months=CONTRACT_MONTHS,
            max_seats=10,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id="sub_overage_wording",
            stripe_customer_id="cus_overage_wording",
        )

    @patch(
        "billing.stripe_service.StripeCustomerService.get_or_create_license_customer",
        return_value="cus_overage_wording",
    )
    @patch(
        "billing.license_service.stripe.checkout.Session.create",
        side_effect=leaking_auth_error(),
    )
    def test_a_stripe_error_reads_the_checkout_sentence(self, *_mocks):
        with self.assertRaises(BillingRequestError) as caught:
            LicenseSubscriptionService._create_overage_checkout(
                license_sub=self.licence,
                requesting_user=self.admin,
                total_blocks=1,
                allocations={str(self.admin.pk): 1},
                success_url="https://example.com/ok",
                cancel_url="https://example.com/cancel",
            )

        shown = str(caught.exception)
        self.assertEqual(shown, CHECKOUT_FAILED_MESSAGE)
        self.assertNotIn("Could not start overage checkout", shown)
        self.assertNotIn(PROBE, shown)
        self.assertNotIn(FAKE_KEY, shown)
        # Unchanged: the intent is FAILED, its reason labelled for us.
        intent = LicenseOveragePurchaseIntent.objects.get(
            license_subscription=self.licence
        )
        self.assertEqual(intent.status, LicenseOveragePurchaseStatus.FAILED)
        reason = intent.failure_reason
        assert reason is not None
        self.assertIn("Checkout session creation failed:", reason)
        self.assertIn("AuthenticationError", reason)
        assert_no_leak(self, reason)
