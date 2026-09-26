"""
billing/tests/test_webhook_signature_verification.py
====================================================
End-to-end Stripe webhook signature verification, with a REAL HMAC.

WHY THIS FILE EXISTS
--------------------
`test_webhook_idempotency.py` covers the signature branches properly — 400
and no ledger row on both a bad signature and a bad payload — but every one
of those tests patches `stripe.Webhook.construct_event`. Patching the
verifier proves the handler's error handling; it cannot prove that
verification is actually WIRED UP.

Concretely, all of these defects leave the existing suite fully green:
  * `settings.STRIPE_WEBHOOK_SECRET` pointed at the wrong setting name, or
    at an empty string;
  * the `sig_header` read from the wrong META key, so every request
    verifies against "";
  * the `construct_event` call removed entirely and the payload parsed with
    `json.loads`.

Each of those means anyone on the internet can POST a forged
`invoice.payment_succeeded` and be granted credits. So these tests sign
payloads with the real algorithm and the configured secret, and never mock
the verifier.

WHAT IS PINNED
--------------
  * a correctly signed payload is accepted (proves the secret in settings
    is the one actually used to verify);
  * a payload signed with the WRONG secret is rejected 400 and writes no
    StripeEvent row;
  * a valid signature over DIFFERENT bytes than were posted is rejected —
    i.e. the body is covered by the signature, not just the header's shape;
  * a missing signature header is rejected;
  * a stale timestamp outside Stripe's tolerance is rejected (replay
    protection);
  * both the fat and thin endpoints behave identically.
"""

import hashlib
import hmac
import json
import time

from django.test import TestCase, override_settings
from django.urls import reverse

from billing.models import StripeEvent

WEBHOOK_SECRET = (
    "whsec_test_secret_for_signature_verification"  # pragma: allowlist secret
)
WRONG_SECRET = "whsec_a_different_secret_entirely"  # pragma: allowlist secret

EVENT_ID = "evt_signature_check_1"


def event_payload(event_id=EVENT_ID, event_type="invoice.payment_succeeded"):
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "data": {"object": {"id": "in_sig_1"}},
        }
    ).encode()


def stripe_signature(payload: bytes, secret: str, timestamp: int | None = None) -> str:
    """
    Reimplements Stripe's scheme exactly: HMAC-SHA256 over
    "<timestamp>.<raw body>", hex-encoded, in a `t=..,v1=..` header.

    Written out by hand on purpose. Calling a Stripe helper to build the
    header and then letting Stripe verify it would mostly test Stripe
    against itself; this pins the wire format the server must accept.
    """
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed_payload = f"{timestamp}.".encode() + payload
    signature = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
    return f"t={timestamp},v1={signature}"


@override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET)
class RealSignatureVerificationTests(TestCase):
    endpoint = "stripe-webhook"

    def post(self, payload, signature=None):
        kwargs = {}
        if signature is not None:
            kwargs["HTTP_STRIPE_SIGNATURE"] = signature
        return self.client.post(
            reverse(self.endpoint),
            data=payload,
            content_type="application/json",
            **kwargs,
        )

    def test_a_correctly_signed_payload_passes_verification(self):
        """
        Proves the secret in settings is the one actually used to verify.
        Reaching the ledger at all means construct_event accepted it —
        the event type has no registered handler in this test, which the
        dispatcher treats as an unhandled-but-verified event.
        """
        payload = event_payload()
        response = self.post(payload, stripe_signature(payload, WEBHOOK_SECRET))

        self.assertNotEqual(
            response.status_code,
            400,
            "a validly signed payload was rejected — the configured "
            "STRIPE_WEBHOOK_SECRET is not the one used to verify",
        )
        self.assertTrue(
            StripeEvent.objects.filter(stripe_event_id=EVENT_ID).exists(),
            "a verified event should have reached the idempotency ledger",
        )

    def test_a_payload_signed_with_the_wrong_secret_is_rejected(self):
        payload = event_payload()
        response = self.post(payload, stripe_signature(payload, WRONG_SECRET))

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            StripeEvent.objects.count(),
            0,
            "an unverified request must never create a ledger row",
        )

    def test_a_signature_over_different_bytes_is_rejected(self):
        """
        The body is covered by the signature, not merely the header's shape.
        Sign one payload, post another — the classic forgery attempt.
        """
        signed_bytes = event_payload(event_id="evt_the_one_i_signed")
        posted_bytes = event_payload(event_id="evt_the_one_i_sent")

        response = self.post(
            posted_bytes, stripe_signature(signed_bytes, WEBHOOK_SECRET)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_missing_signature_header_is_rejected(self):
        response = self.post(event_payload())

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_an_empty_signature_header_is_rejected(self):
        response = self.post(event_payload(), "")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_garbage_signature_header_is_rejected_not_crashed(self):
        response = self.post(event_payload(), "this is not a signature")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_stale_timestamp_is_rejected_as_a_replay(self):
        """
        Replay protection: the signature is genuine and made with the right
        secret, but the timestamp is far outside Stripe's default tolerance
        (300s), so a captured request cannot be resent later.
        """
        payload = event_payload()
        stale = int(time.time()) - 86_400
        response = self.post(
            payload, stripe_signature(payload, WEBHOOK_SECRET, timestamp=stale)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_body_mutated_by_one_byte_is_rejected(self):
        """Tamper-evidence at the finest granularity."""
        payload = event_payload()
        signature = stripe_signature(payload, WEBHOOK_SECRET)
        tampered = payload.replace(b"in_sig_1", b"in_sig_2")
        self.assertNotEqual(payload, tampered)

        response = self.post(tampered, signature)

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_v0_only_signature_is_rejected_as_a_downgrade_attack(self):
        """
        Stripe's manual-verification spec is explicit: "To prevent downgrade
        attacks, ignore all schemes that aren't v1." `v0` is a deliberately
        FAKE scheme Stripe attaches to test events. An endpoint that accepts
        a v0 signature can be fed forged events by anyone who learns the v0
        construction.

        Verified against the documented scheme, not against a mock.
        """
        payload = event_payload()
        timestamp = int(time.time())
        signed = f"{timestamp}.".encode() + payload
        v0 = hmac.new(WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()

        # A structurally valid header carrying ONLY a v0 scheme.
        response = self.post(payload, f"t={timestamp},v0={v0}")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_rolled_secret_still_verifies_while_both_are_live(self):
        """
        Stripe supports rolling an endpoint secret with the OLD one staying
        valid for up to 24 hours, and during that window it sends ONE
        SIGNATURE PER SECRET in the same header:

            t=...,v1=<sig with secret A>,v1=<sig with secret B>

        If the endpoint only inspected the first v1, rolling the secret
        would cause a silent outage: every event 400s, Stripe retries for
        three days, and credit grants stop landing. The header must be
        accepted when ANY v1 matches.
        """
        payload = event_payload()
        timestamp = int(time.time())
        signed = f"{timestamp}.".encode() + payload
        wrong = hmac.new(WRONG_SECRET.encode(), signed, hashlib.sha256).hexdigest()
        right = hmac.new(WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()

        # Valid signature deliberately SECOND, so passing requires looking
        # past the first one.
        response = self.post(payload, f"t={timestamp},v1={wrong},v1={right}")

        self.assertNotEqual(
            response.status_code,
            400,
            "a rolled-secret header was rejected — rolling the webhook "
            "secret would take the endpoint down for three days",
        )

    def test_a_signature_just_inside_the_tolerance_is_accepted(self):
        """
        Stripe's libraries default to a 5-minute tolerance. Pinned from both
        sides so the window is neither silently widened (replays become
        possible) nor narrowed (legitimate deliveries start failing under
        modest clock skew).
        """
        payload = event_payload()
        recent = int(time.time()) - 60  # comfortably inside 300s
        response = self.post(
            payload, stripe_signature(payload, WEBHOOK_SECRET, timestamp=recent)
        )

        self.assertNotEqual(response.status_code, 400)

    def test_a_signature_just_outside_the_tolerance_is_rejected(self):
        payload = event_payload()
        stale = int(time.time()) - 400  # past the documented 300s default
        response = self.post(
            payload, stripe_signature(payload, WEBHOOK_SECRET, timestamp=stale)
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)

    def test_a_header_with_no_timestamp_is_rejected(self):
        payload = event_payload()
        signed = b"." + payload
        sig = hmac.new(WEBHOOK_SECRET.encode(), signed, hashlib.sha256).hexdigest()

        response = self.post(payload, f"v1={sig}")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(StripeEvent.objects.count(), 0)


@override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET)
class ThinWebhookRealSignatureTests(RealSignatureVerificationTests):
    """
    The thin endpoint duplicates the verification block rather than sharing
    it, so it can drift independently. Same contract, re-run against it.

    `test_a_correctly_signed_payload_passes_verification` is overridden
    because the thin flow calls stripe.Event.retrieve() after verifying,
    which would be a live API call here; the rejection cases all fail
    BEFORE that point and inherit unchanged.
    """

    endpoint = "stripe-webhook-thin"

    def test_a_correctly_signed_payload_passes_verification(self):
        from unittest.mock import patch

        import stripe as real_stripe

        payload = event_payload()
        with patch.object(
            real_stripe.Event,
            "retrieve",
            return_value={
                "id": EVENT_ID,
                "type": "invoice.payment_succeeded",
                "data": {"object": {"id": "in_sig_1"}},
            },
        ) as retrieve:
            response = self.post(payload, stripe_signature(payload, WEBHOOK_SECRET))

        self.assertNotEqual(response.status_code, 400)
        retrieve.assert_called_once()

    def test_retrieve_is_never_reached_when_the_signature_is_bad(self):
        """
        Verification must gate the outbound call, not follow it — otherwise
        a forged request still costs a Stripe API round trip and becomes a
        cheap amplification vector.
        """
        from unittest.mock import patch

        import stripe as real_stripe

        payload = event_payload()
        with patch.object(real_stripe.Event, "retrieve") as retrieve:
            response = self.post(payload, stripe_signature(payload, WRONG_SECRET))

        self.assertEqual(response.status_code, 400)
        retrieve.assert_not_called()
