"""
Gate 7 (real infrastructure) for H-23: one real Stripe TEST-MODE purchase,
resolved through the new bounded receipt lookup.

Everything else in H-23's evidence fakes Stripe at the HTTP layer. That
proves our logic, not that the request we build is one Stripe accepts or
that the response shape we parse is the one Stripe returns. This does the
real round trip:

  1. create and confirm a real test-mode PaymentIntent (pm_card_visa);
  2. resolve its receipt link through billing.receipts.lookup_receipt_url,
     via the dedicated client (10s timeout, no retries), by payment intent
     and by charge;
  3. fetch each returned URL and require a real receipt page (HTTP 200);
  4. record ids, statuses, amounts and the client settings used.

A fact only the real service could show: Stripe re-mints the receipt URL's
signed token on EVERY retrieval (the same charge fetched twice returns two
different URLs sharing a stable prefix). So two lookups are compared by
whether each opens a working receipt, not by string equality. For H-23
this confirms fill-if-null is the right write rule: a later lookup
returning a different string is not a changed receipt.

Refuses to run unless the configured key is sk_test_. Creates one
test-mode PaymentIntent and nothing else; no live money moves.

    DJANGO_SETTINGS_MODULE=settings_worktree python docs/evidence/p1_overage_lock/g7_real_stripe.py
"""

import os
import sys
import time

import django
import requests

sys.path.insert(0, os.getcwd())
django.setup()

from billing import receipts  # noqa: E402
from billing.imports import stripe  # noqa: E402

if not (stripe.api_key or "").startswith("sk_test_"):
    sys.exit("Refusing: the configured Stripe key is not a test-mode key.")

print(f"stripe-python {stripe.VERSION}; key mode TEST")
print(
    f"bounded client: timeout={receipts.RECEIPT_LOOKUP_TIMEOUT_SECONDS}s, "
    f"max_network_retries=0"
)

intent = stripe.PaymentIntent.create(
    amount=500,
    currency="usd",
    payment_method="pm_card_visa",
    confirm=True,
    automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
    description="H-23 Gate 7 evidence: receipt lookup via bounded client",
    metadata={"purpose": "h23-g7-evidence"},
)
print(
    f"PaymentIntent {intent.id}: status={intent.status} "
    f"amount={intent.amount} {intent.currency} livemode={intent.livemode}"
)
assert intent.livemode is False

started = time.perf_counter()
by_intent = receipts.lookup_receipt_url(payment_intent_id=intent.id)
intent_ms = (time.perf_counter() - started) * 1000

charge_id = intent.latest_charge
started = time.perf_counter()
by_charge = receipts.lookup_receipt_url(charge_id=charge_id)
charge_ms = (time.perf_counter() - started) * 1000


def shape(url):
    # Receipt URLs carry an access token; record the shape, not the token.
    if not url:
        return repr(url)
    return url.split("?")[0].rsplit("/", 1)[0] + "/<token>"


print(f"lookup by payment intent -> {shape(by_intent)} in {intent_ms:.0f} ms")
print(f"lookup by charge {charge_id} -> {shape(by_charge)} in {charge_ms:.0f} ms")
print(f"identical strings: {by_intent == by_charge} (expected False: tokens re-mint)")

statuses: dict[str, int | None] = {}
for label, url in (("payment intent", by_intent), ("charge", by_charge)):
    if not url:
        statuses[label] = None
        continue
    response = requests.get(url, timeout=10)
    statuses[label] = response.status_code
    print(f"GET receipt from {label} lookup -> HTTP {response.status_code}")

prefix = "https://pay.stripe.com/receipts/"
ok = all(url and url.startswith(prefix) for url in (by_intent, by_charge)) and all(
    code == 200 for code in statuses.values()
)
print("RESULT:", "PASS" if ok else "FAIL")
sys.exit(0 if ok else 1)
