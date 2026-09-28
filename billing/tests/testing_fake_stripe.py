"""
A fake Stripe API at stripe-python's HTTP-client layer.

Patching `HTTPClient.request_with_retries` (the one method every request
passes through, whether it comes from the legacy global API such as
`stripe.PaymentIntent.retrieve` or from a `StripeClient`) keeps these tests
off the network while still exercising stripe-python's real request
building and response parsing. It also means a test cannot be bypassed by
changing HOW code calls Stripe: any route to the API is seen here.

Every call is recorded with whether the calling thread's database
connection was inside a transaction at that moment, which is the property
P1 is about: no outbound Stripe call may run while a webhook transaction
(and its row locks) is open.
"""

import json
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from unittest import mock
from urllib.parse import parse_qs, urlparse

from django.db import connection
from stripe._http_client import HTTPClient

RECEIPT_LOOKUP_PREFIXES = ("/v1/invoices/", "/v1/charges/", "/v1/payment_intents/")


def invoice_url(invoice_id):
    return f"https://invoice.stripe.test/{invoice_id}"


def charge_receipt_url(charge_id):
    return f"https://pay.stripe.test/receipts/{charge_id}"


@dataclass
class StripeCall:
    method: str
    path: str
    query: dict
    in_transaction: bool
    max_network_retries: object
    timeout: object
    thread: str

    @property
    def is_receipt_lookup(self):
        return self.method == "get" and self.path.startswith(RECEIPT_LOOKUP_PREFIXES)


class FakeStripe:
    def __init__(self, *, receipt_delay_seconds=0.0, receipt_error=None, prices=None):
        self.calls = []
        #: price id -> unit_amount (cents), served by GET /v1/prices/<id>.
        self.prices = dict(prices or {})
        self._lock = threading.Lock()
        self.receipt_delay_seconds = receipt_delay_seconds
        #: (status, body) returned for receipt lookups instead of a success.
        self.receipt_error = receipt_error

    # -- inspection ------------------------------------------------------

    def receipt_lookups(self):
        with self._lock:
            return [c for c in self.calls if c.is_receipt_lookup]

    # -- the fake API ----------------------------------------------------

    def handle(
        self,
        client,
        method,
        url,
        headers,
        post_data=None,
        max_network_retries=None,
        *,
        _usage=None,
    ):
        parsed = urlparse(url)
        call = StripeCall(
            method=method.lower(),
            path=parsed.path,
            query=parse_qs(parsed.query),
            in_transaction=connection.in_atomic_block,
            max_network_retries=max_network_retries,
            timeout=getattr(client, "_timeout", None),
            thread=threading.current_thread().name,
        )
        with self._lock:
            self.calls.append(call)

        if call.is_receipt_lookup:
            if self.receipt_delay_seconds:
                time.sleep(self.receipt_delay_seconds)
            if self.receipt_error is not None:
                status, body = self.receipt_error
                return json.dumps(body), status, {}

        status, body = self._route(call, post_data)
        return json.dumps(body), status, {"request-id": "req_fake"}

    def _route(self, call, post_data):
        parts = [p for p in call.path.split("/") if p]  # ["v1", "invoices", "in_1"]
        resource = parts[1] if len(parts) > 1 else ""
        obj_id = parts[2] if len(parts) > 2 else None
        form = parse_qs(post_data or "") if isinstance(post_data, str) else {}

        if resource == "invoices" and obj_id:
            return 200, {
                "id": obj_id,
                "object": "invoice",
                "hosted_invoice_url": invoice_url(obj_id),
            }
        if resource == "charges" and obj_id:
            return 200, {
                "id": obj_id,
                "object": "charge",
                "receipt_url": charge_receipt_url(obj_id),
            }
        if resource == "payment_intents" and obj_id:
            charge_id = f"ch_for_{obj_id}"
            return 200, {
                "id": obj_id,
                "object": "payment_intent",
                "latest_charge": {
                    "id": charge_id,
                    "object": "charge",
                    "receipt_url": charge_receipt_url(charge_id),
                },
            }
        if resource == "prices" and obj_id in self.prices:
            return 200, {
                "id": obj_id,
                "object": "price",
                "unit_amount": self.prices[obj_id],
                "currency": "usd",
            }
        if resource == "customers":
            return 200, {"id": obj_id or "cus_fake", "object": "customer"}
        if resource == "checkout":
            return 200, {
                "id": "cs_fake_created",
                "object": "checkout.session",
                "url": "https://checkout.stripe.test/cs_fake_created",
            }
        if resource == "subscriptions" and obj_id:
            return 200, {
                "id": obj_id,
                "object": "subscription",
                "status": "active",
                "items": {"object": "list", "data": []},
                "latest_invoice": None,
                "metadata": {
                    k: v[0] for k, v in form.items() if k.startswith("metadata")
                },
            }
        return 404, {
            "error": {
                "type": "invalid_request_error",
                "message": f"fake stripe: no route for {call.method.upper()} {call.path}",
            }
        }


def assert_stripe_untouched(test, fake):
    """
    Assert that the code under test only ever READ from Stripe.

    The Stripe-side half of a Gate 5 failure record: "Stripe is unchanged"
    is an assertion here, not an argument. Any non-GET call is a mutation
    that no database rollback can undo.
    """
    mutating = [(c.method, c.path) for c in fake.calls if c.method != "get"]
    test.assertEqual(
        mutating, [], f"the code under test called Stripe to MUTATE: {mutating}"
    )


def assert_no_call_inside_transaction(test, fake, *, only_receipt_lookups=False):
    """
    Assert that no Stripe call ran while a database transaction was open.

    A call made inside an atomic block holds that block's row locks across
    the network, and on production/beta Postgres
    (idle_in_transaction_session_timeout = 60s, below stripe-python's 80s
    default) a slow answer can have the transaction terminated mid-call,
    rolling back committed-looking work.
    """
    calls = fake.receipt_lookups() if only_receipt_lookups else fake.calls
    inside = [(c.method, c.path) for c in calls if c.in_transaction]
    test.assertEqual(
        inside, [], f"Stripe call(s) ran INSIDE an open transaction: {inside}"
    )


@contextmanager
def fake_stripe(**kwargs):
    fake = FakeStripe(**kwargs)

    def request_with_retries(
        client,
        method,
        url,
        headers,
        post_data=None,
        max_network_retries=None,
        *,
        _usage=None,
    ):
        return fake.handle(
            client, method, url, headers, post_data, max_network_retries, _usage=_usage
        )

    with mock.patch.object(HTTPClient, "request_with_retries", request_with_retries):
        yield fake
