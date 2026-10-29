"""
Coverage for per-cycle overage block cap enforcement.

plan.max_overage_blocks used to be decorative: session creation only
checked that the plan supports overage at all (max_overage_blocks > 0),
the request quantity was client-supplied and unbounded, and the grant-time
re-check in _handle_overage_checkout_completed was commented out entirely.
These tests pin the cap at BOTH ends:

  - create_overage_checkout_session rejects a quantity that would exceed
    the remaining cap (no Stripe session is even created), and
  - _handle_overage_checkout_completed re-validates under the wallet row
    lock at grant time (a session being created doesn't reserve a slot),
    records the paid-but-not-granted transaction for manual refund, and
    grants nothing.

Run with:
    python manage.py test billing.tests.test_overage_cap
"""

from types import SimpleNamespace
from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from audit.enums import ReasonCode
from AutoGrader.reason_codes import CodedError
from billing.models import CreditBucketType, CreditWallet, PlanType
from billing.stripe_service import StripeOverageService, StripeWebhookHandler
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase

#: H-268 (the owner's wording, through the SM 2026-10-27): two sentences,
#: picked from the params by the spec and never at a raise site. The dash
#: is U+2014.
#:
#: H-233's rule still holds, and CAP_REACHED_SENTENCE is what keeps it:
#: with nothing left the teacher is never told to "reduce your quantity to
#: 0 or fewer".
CAP_SENTENCE = (
    "Purchase limit exceeded — max {max_blocks} blocks. Reduce your "
    "quantity to {remaining} or fewer to proceed."
)
CAP_REACHED_SENTENCE = (
    "Purchase limit reached — you have used all {max_blocks} blocks for "
    "this billing cycle. You can buy more when your next cycle starts."
)


class OverageCapTestBase(ExecuteGradedTaskTestBase):
    def setUp(self):
        super().setUp()
        self.plan = self._make_plan(PlanType.PRO)
        self.plan.max_overage_blocks = 3
        self.plan.overage_block_price = 500
        self.plan.overage_block_size = 100_000
        self.plan.stripe_overage_price_id = "price_test_overage"
        self.plan.save()
        self.teacher = self._make_teacher_with_credits(plan=self.plan)
        self.wallet = self.teacher.credit_wallet

    def _set_blocks_used(self, n):
        CreditWallet.objects.filter(pk=self.wallet.pk).update(overage_blocks_used=n)
        self.wallet.refresh_from_db()


class CreateOverageCheckoutCapTests(OverageCapTestBase):
    @patch("billing.stripe_service.StripeCustomerService.get_or_create_customer")
    @patch("billing.stripe_service.stripe.checkout.Session.create")
    def test_quantity_within_remaining_cap_creates_session(
        self, mock_session_create, mock_customer
    ):
        mock_customer.return_value = "cus_test"
        mock_session_create.return_value = SimpleNamespace(
            id="cs_test", url="https://x"
        )
        self._set_blocks_used(1)

        # The checkout now confirms with Stripe that the price it is about
        # to charge is the one this system quotes — see
        # billing/overage_pricing.py. Stubbed to agree so this test stays
        # about the block cap.
        with patch(
            "billing.overage_pricing.stripe.Price.retrieve",
            return_value={"unit_amount": self.plan.overage_block_price},
        ):
            session = StripeOverageService.create_overage_checkout_session(
                self.teacher, "https://ok", "https://cancel", quantity=2
            )

        self.assertEqual(session.id, "cs_test")
        mock_session_create.assert_called_once()

    @patch("billing.stripe_service.StripeCustomerService.get_or_create_customer")
    @patch("billing.stripe_service.stripe.checkout.Session.create")
    def test_quantity_exceeding_remaining_cap_is_rejected_before_stripe(
        self, mock_session_create, mock_customer
    ):
        self._set_blocks_used(2)  # 1 block remaining of 3

        with self.assertRaises(CodedError) as ctx:
            StripeOverageService.create_overage_checkout_session(
                self.teacher, "https://ok", "https://cancel", quantity=2
            )

        self.assertEqual(ctx.exception.reason_code, ReasonCode.OVERAGE_CAP_EXCEEDED)
        self.assertEqual(ctx.exception.params, {"max_blocks": 3, "remaining": 1})
        self.assertEqual(
            str(ctx.exception), CAP_SENTENCE.format(max_blocks=3, remaining=1)
        )
        mock_session_create.assert_not_called()

    @patch("billing.stripe_service.StripeCustomerService.get_or_create_customer")
    @patch("billing.stripe_service.stripe.checkout.Session.create")
    def test_cap_already_reached_rejects_even_a_single_block(
        self, mock_session_create, mock_customer
    ):
        self._set_blocks_used(3)

        with self.assertRaises(CodedError) as ctx:
            StripeOverageService.create_overage_checkout_session(
                self.teacher, "https://ok", "https://cancel", quantity=1
            )

        self.assertEqual(ctx.exception.reason_code, ReasonCode.OVERAGE_CAP_EXCEEDED)
        self.assertEqual(ctx.exception.params, {"max_blocks": 3, "remaining": 0})
        # H-268: with nothing left the other sentence shows, and the first
        # one's "reduce your quantity to ..." never reaches 0 (H-233).
        self.assertEqual(str(ctx.exception), CAP_REACHED_SENTENCE.format(max_blocks=3))
        self.assertNotIn("or fewer", str(ctx.exception))
        mock_session_create.assert_not_called()


class PurchaseOverageViewCapTests(OverageCapTestBase):
    """The envelope `purchase_overage` (billing/views.py) answers when the
    cap check refuses the request: the same coded body every other
    reason-code refusal gets, not a bare `{"detail": ...}`."""

    def setUp(self):
        super().setUp()
        self.client: APIClient = APIClient()
        self.client.force_authenticate(user=self.teacher)
        self.url = reverse("subscription-purchase-overage")

    def test_exceeding_the_cap_answers_the_coded_envelope(self):
        self._set_blocks_used(2)  # 1 block remaining of 3

        response = self.client.post(
            self.url,
            {
                "success_url": "https://example.com/ok",
                "cancel_url": "https://example.com/cancel",
                "quantity": 2,
            },
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["reason_code"], "OVERAGE_CAP_EXCEEDED")
        self.assertEqual(response.data["error_class"], "USER")
        self.assertFalse(response.data["retryable"])
        self.assertEqual(response.data["params"], {"max_blocks": 3, "remaining": 1})
        self.assertEqual(
            response.data["error"], CAP_SENTENCE.format(max_blocks=3, remaining=1)
        )
        self.assertEqual(
            response.data["remediation"],
            "Request fewer blocks, or wait until your next billing cycle, then "
            "try again.",
        )

    def _refusal(self, quantity):
        return self.client.post(
            self.url,
            {
                "success_url": "https://example.com/ok",
                "cancel_url": "https://example.com/cancel",
                "quantity": quantity,
            },
            format="json",
        )

    def test_the_rendered_body_carries_the_sentence_in_both_places(self):
        """F7: the renderer wraps the envelope as `error.field_errors`, so
        the teacher's sentence is in the body twice. The two must never
        drift apart."""
        self._set_blocks_used(2)  # 1 of 3 left

        body = self._refusal(2).json()

        expected = CAP_SENTENCE.format(max_blocks=3, remaining=1)
        self.assertEqual(body["message"], expected)
        self.assertEqual(body["error"]["field_errors"]["error"], expected)
        self.assertIn("—", body["message"])

    def test_at_zero_left_both_places_read_the_other_sentence(self):
        self._set_blocks_used(3)  # none left

        response = self._refusal(1)
        body = response.json()

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        expected = CAP_REACHED_SENTENCE.format(max_blocks=3)
        self.assertEqual(body["message"], expected)
        self.assertEqual(body["error"]["field_errors"]["error"], expected)
        self.assertNotIn("or fewer", body["message"])
        # The sentence formats only max_blocks; both params stay
        # machine-readable either way.
        self.assertEqual(
            body["error"]["field_errors"]["params"],
            {"max_blocks": 3, "remaining": 0},
        )


class GrantTimeOverageCapTests(OverageCapTestBase):
    def _session(self):
        return {
            "id": "cs_grant_test",
            "amount_total": 1_000,
            "currency": "usd",
            "invoice": None,
            "payment_intent": "pi_grant_test",
        }

    def _metadata(self, quantity):
        return {
            "wallet_id": str(self.wallet.id),
            "plan_id": str(self.plan.id),
            "quantity": str(quantity),
        }

    @patch("billing.stripe_service.BillingTransactionService.record")
    def test_within_cap_grants_never_expiring_overage_bucket(self, mock_record):
        self._set_blocks_used(1)

        StripeWebhookHandler._handle_overage_checkout_completed(
            self._session(), self._metadata(2)
        )

        bucket = self.wallet.buckets.get(bucket_type=CreditBucketType.OVERAGE)
        self.assertEqual(bucket.total_credits, 200_000)
        self.assertIsNone(bucket.expires_at)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 3)

    @patch("billing.stripe_service.BillingTransactionService.record")
    def test_over_cap_at_grant_time_grants_nothing_and_flags_for_refund(
        self, mock_record
    ):
        # The session was created when blocks were available, but by
        # payment-confirmation time another purchase filled the cap - the
        # exact race the grant-time re-check exists for.
        self._set_blocks_used(3)

        StripeWebhookHandler._handle_overage_checkout_completed(
            self._session(), self._metadata(1)
        )

        self.assertFalse(
            self.wallet.buckets.filter(bucket_type=CreditBucketType.OVERAGE).exists(),
            "no credits may be granted past the cap",
        )
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 3)
        # The paid-but-ungranted payment is recorded for manual refund.
        mock_record.assert_called_once()
        self.assertIn(
            "NOT granted", mock_record.call_args.kwargs.get("description", "")
        )

    @patch("billing.stripe_service.BillingTransactionService.record")
    def test_control_the_grant_time_recheck_writes_neither_sentence(self, mock_record):
        """H-268: this re-check answers Stripe's webhook, not the teacher.
        It raises no coded error and builds no envelope, so neither of the
        owner's sentences belongs on this path — the log line is ours. It
        still grants nothing and still records the payment for refund."""
        self._set_blocks_used(3)

        with self.assertLogs("billing.stripe_service", level="ERROR") as logs:
            StripeWebhookHandler._handle_overage_checkout_completed(
                self._session(), self._metadata(1)
            )

        self.assertFalse(
            self.wallet.buckets.filter(bucket_type=CreditBucketType.OVERAGE).exists()
        )
        mock_record.assert_called_once()
        written = "\n".join(logs.output) + mock_record.call_args.kwargs.get(
            "description", ""
        )
        for fragment in (
            "Purchase limit exceeded",
            "Purchase limit reached",
            "or fewer to proceed",
            "next cycle starts",
        ):
            self.assertNotIn(fragment, written)
