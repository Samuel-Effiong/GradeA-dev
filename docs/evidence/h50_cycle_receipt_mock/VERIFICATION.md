# H-50: Verification (test-only; fix 1235780, tip fb91553)

- Diff: billing/tests/test_subscription_cycle_integrity.py only. Both webhook cycle tests add `@patch("stripe.PaymentIntent")` with a `latest_charge.receipt_url` return value, and assert `retrieve("pi_test_N", expand=["latest_charge"])` is called once.
- The patch target is correct. `resolve_stripe_receipt_url` (billing/stripe_service.py:301) calls `stripe.PaymentIntent.retrieve(...)` through the module attribute at call time, so `patch("stripe.PaymentIntent")` intercepts it.
- Re-ran in a throwaway detached checkout of fb91553 with the H-39 guard active (-v2 output grepped):
  - the fixed module: **10/10, 0 "Blocked real outbound network call" lines**, so nothing else in the module reaches Stripe;
  - the original (197aa46) test file on the same tree: 10/10 with **2 blocked calls to ('198.137.150.221', 443)**. That's the silent leak: the function swallows StripeError by design, so the tests passed anyway;
  - mutant, the PaymentIntent branch disabled (`if False and payment_intent_id:`): both `test_webhook_preserves_cycle_for_same_interval` and `test_webhook_resets_cycle_for_interval_crossing` FAIL. Restore sha-verified.
- On the pin: asserting the call is adequate for this test's purpose. It proves the lookup still happens and is mocked, rather than quietly skipped or live. Optional strengthening (non-blocking): the resolved URL is persisted as `receipt_url` on the billing transaction the webhook creates (billing/models.py:2403; e.g. the call site at stripe_service.py:2912). Asserting that row's `receipt_url == "https://pay.stripe.test/receipt"` would also pin that the value flows through, which the call assertion can't see (a caller dropping the return value would pass today).

Verdict: VERIFIED.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
