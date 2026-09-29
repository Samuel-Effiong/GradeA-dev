# H-50 — webhook cycle tests: no live Stripe PaymentIntent calls

Branch `task/h50-cycle-receipt-mock`: test fix `1235780` off beta `197aa46`, then `task/beta-batch-1` merged in (`8e0b52c`) so the backlog entry sits after H-48/H-49. Lands after the batch. Author: Integration & Release Engineer (grade-automator-plus-0b). Test-only change.

## Finding

The beta-batch-1 full run (a112eda) logged two H-39 `BlockedNetworkCallError`s to `api.stripe.com/v1/payment_intents/pi_test_{1,2}?expand=latest_charge`. Source: `billing/tests/test_subscription_cycle_integrity.py`, `test_webhook_preserves_cycle_for_same_interval` and `test_webhook_resets_cycle_for_interval_crossing` → `StripeWebhookHandler._handle_individual_upgrade_checkout_completed` → `resolve_stripe_receipt_url` → `stripe.PaymentIntent.retrieve`. `stripe.PaymentIntent` was not patched. The lookup swallows `StripeError` by design (a receipt link must never break webhook processing), so the tests passed.

## Change (`billing/tests/test_subscription_cycle_integrity.py` only)

Both tests patch `stripe.PaymentIntent`, return a PaymentIntent with a `latest_charge.receipt_url`, and assert `retrieve("pi_test_N", expand=["latest_charge"])` was called exactly once.

## Results

| Run | Result |
|---|---|
| Fixed file | 10/10 OK, 0 blocked-network lines |
| M0: original file from `197aa46` | 10/10 OK **with 2 blocked Stripe calls**, reproducing the silent pass |
| M1: PaymentIntent lookup removed from `resolve_stripe_receipt_url` | **2 FAIL** (both webhook tests), so the new assertion pins the path |

No production code changed.
