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

from unittest.mock import MagicMock, patch

from billing.models import CreditBucketType, CreditWallet, PlanType
from billing.stripe_service import StripeOverageService, StripeWebhookHandler
from billing.tests.test_execute_graded_task import ExecuteGradedTaskTestBase


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
        mock_session_create.return_value = MagicMock(id="cs_test", url="https://x")
        self._set_blocks_used(1)

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

        with self.assertRaises(ValueError) as ctx:
            StripeOverageService.create_overage_checkout_session(
                self.teacher, "https://ok", "https://cancel", quantity=2
            )

        self.assertIn("overage limit", str(ctx.exception))
        mock_session_create.assert_not_called()

    @patch("billing.stripe_service.StripeCustomerService.get_or_create_customer")
    @patch("billing.stripe_service.stripe.checkout.Session.create")
    def test_cap_already_reached_rejects_even_a_single_block(
        self, mock_session_create, mock_customer
    ):
        self._set_blocks_used(3)

        with self.assertRaises(ValueError):
            StripeOverageService.create_overage_checkout_session(
                self.teacher, "https://ok", "https://cancel", quantity=1
            )

        mock_session_create.assert_not_called()


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

    @patch("billing.stripe_service.resolve_stripe_receipt_url", return_value=None)
    @patch("billing.stripe_service.BillingTransactionService.record")
    def test_within_cap_grants_never_expiring_overage_bucket(
        self, mock_record, mock_receipt
    ):
        self._set_blocks_used(1)

        StripeWebhookHandler._handle_overage_checkout_completed(
            self._session(), self._metadata(2)
        )

        bucket = self.wallet.buckets.get(bucket_type=CreditBucketType.OVERAGE)
        self.assertEqual(bucket.total_credits, 200_000)
        self.assertIsNone(bucket.expires_at)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 3)

    @patch("billing.stripe_service.resolve_stripe_receipt_url", return_value=None)
    @patch("billing.stripe_service.BillingTransactionService.record")
    def test_over_cap_at_grant_time_grants_nothing_and_flags_for_refund(
        self, mock_record, mock_receipt
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
