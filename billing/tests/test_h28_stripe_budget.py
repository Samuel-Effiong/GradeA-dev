"""
billing/tests/test_h28_stripe_budget.py
=======================================
H-28 Change 1, commit 9: the per-REQUEST Stripe budget
(DESIGN_PROPOSAL.md §9i (2)).

gunicorn kills a request at 100 s, and a killed worker runs no code, so it
sends no alert. stripe-python's default is 80 s per call with two retries,
and a licence operation makes up to four calls. So the Stripe work shares
one deadline (REQUEST_BUDGET_SECONDS, 75 s), each call waits only for what
is left, and the operation reaches its own error branch, and its alert,
in time.

The NAMED GATE-5 ASSERTION (d4's wording): make Stripe slow on EVERY call
in the sequence and assert the handler reaches its own error branch and
completes its alert before 100 s. A single slow call cannot catch the
multiplication. `test_slow_stripe_on_every_call_ends_in_the_error_branch_
with_its_alert_in_time` is that assertion, with the budget scaled down so
the test runs in about a second. The production numbers are pinned
separately.
"""

import ast
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.db import connections, transaction
from django.test import SimpleTestCase, TestCase

from billing import license_stripe_mutation
from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.license_stripe_mutation import LicenceStripe
from billing.models import (
    LicenseStripeMutationIntent,
    LicenseStripeMutationOperation,
    LicenseStripeMutationStatus,
)
from billing.tests.test_h28_cancel_phases import MUTATION_LOGGER, LicencePhaseTestCase
from billing.webhooks import WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS

PRODUCTION_BUDGET = license_stripe_mutation.REQUEST_BUDGET_SECONDS
BUDGET = 1.0
DELAY = 0.6  # every call; four in sequence would take 2.4 s


def join_abandoned_calls():
    """A call the budget gave up on keeps running in the background; let it
    finish before the next test touches the fake."""
    for worker in threading.enumerate():
        if worker.name == "h28-stripe-call":
            worker.join(5)


class StripeBudgetTests(LicencePhaseTestCase):
    SUB_ID = "sub_h28_budget"

    def setUp(self):
        super().setUp()
        self.addCleanup(join_abandoned_calls)
        self.emails = []
        p = patch(
            "AutoGrader.dispatch.safe_delay",
            side_effect=lambda task, **kwargs: self.emails.append(kwargs),
        )
        p.start()
        self.addCleanup(p.stop)

    def slow(self, fn):
        def call(*args, **kwargs):
            time.sleep(DELAY)
            return fn(*args, **kwargs)

        return call

    def slow_stripe_everywhere(self):
        fake = self.stripe
        return [
            patch.object(LicenceStripe, name, side_effect=self.slow(method))
            for name, method in (
                ("retrieve_subscription", fake.subscription_retrieve),
                ("modify_subscription", fake.subscription_modify),
                ("delete_subscription", fake.subscription_delete),
            )
        ] + [
            patch.object(
                LicenceStripe,
                "retrieve_invoice",
                side_effect=self.slow(fake.invoice_retrieve),
            ),
            patch.object(
                LicenceStripe, "void_invoice", side_effect=self.slow(fake.invoice_void)
            ),
            patch.object(
                LicenceStripe, "create_price", side_effect=self.slow(fake.price_create)
            ),
        ]

    # -- the named Gate-5 assertion ------------------------------------------

    def test_slow_stripe_on_every_call_ends_in_the_error_branch_with_its_alert_in_time(
        self,
    ):
        patches = self.slow_stripe_everywhere()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

        started = time.monotonic()
        with patch.object(
            license_stripe_mutation, "REQUEST_BUDGET_SECONDS", BUDGET
        ), self.assertLogs(MUTATION_LOGGER, level="ERROR") as logs:
            with self.assertRaisesRegex(
                ValueError, "Stripe error while updating seats"
            ):
                LicenseSubscriptionService.update_seats(
                    self.licence, self.SEATS + 5, performed_by=self.superadmin
                )
        elapsed = time.monotonic() - started

        # The error branch was reached within the budget, not after the slow
        # sequence: four calls would have taken 4 x DELAY.
        self.assertLess(elapsed, BUDGET + 0.5, f"took {elapsed:.2f} s")
        # ...and the alert completed before the operation returned.
        self.assertIn("MANUAL RECONCILIATION NEEDED", "\n".join(logs.output))
        self.assertEqual(
            [e["recipient_list"] for e in self.emails], [[self.superadmin.email]]
        )
        # The abandoned modify may still land, so it is never classified
        # "not applied": the intent stays PENDING for a human.
        intent = self.only_intent()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        join_abandoned_calls()
        # And it did land, after the caller gave up. The intent still says
        # PENDING (outcome unknown), never FAILED ("not applied").
        self.assertEqual(self.stripe.quantity, self.SEATS + 5)
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.PENDING)
        self.assertEqual(
            [c[0] for c in self.stripe.calls].count("Subscription.retrieve"),
            1,
            "an abandoned call must not be read back",
        )
        self.assertEqual(self.fresh_licence().max_seats, self.SEATS)

    def test_the_production_budget_ends_well_inside_the_request_timeout(self):
        """Scaled down above; these are the real numbers. The Stripe work
        must leave room for the local write, a compensation and the alert
        before gunicorn's kill."""
        self.assertLessEqual(PRODUCTION_BUDGET, 80)
        self.assertLessEqual(
            PRODUCTION_BUDGET + 15, WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS
        )

    # -- the budget's own rules ----------------------------------------------

    def test_a_call_the_budget_never_started_is_failed_not_pending(self):
        intent = LicenseStripeMutationIntent.objects.create(
            license_subscription=self.licence,
            operation=LicenseStripeMutationOperation.CANCEL,
            stripe_subscription_id=self.SUB_ID,
            requested_change={"auto_renew": [True, False]},
        )
        called = []

        with license_stripe_mutation.stripe_budget(0):
            with self.assertRaises(
                license_stripe_mutation.StripeBudgetExhausted
            ) as caught:
                license_stripe_mutation.apply_at_stripe(
                    intent,
                    call=lambda **key: called.append(key),
                    reached=lambda: True,
                )

        self.assertFalse(caught.exception.started)
        self.assertEqual(called, [])
        intent.refresh_from_db()
        self.assertEqual(intent.status, LicenseStripeMutationStatus.FAILED)
        self.assertIn("Not attempted", str(intent.failure_reason))

    def test_nested_budgets_keep_the_earlier_deadline(self):
        with license_stripe_mutation.stripe_budget(0):
            with license_stripe_mutation.stripe_budget(60):
                with self.assertRaises(license_stripe_mutation.StripeBudgetExhausted):
                    license_stripe_mutation.call_stripe(lambda: "never")

    def test_without_a_budget_the_call_runs_inline(self):
        self.assertIs(
            license_stripe_mutation.call_stripe(threading.current_thread),
            threading.current_thread(),
        )

    def test_under_a_budget_the_call_reports_its_callers_transaction(self):
        """The runtime proof that no Stripe call runs inside a transaction
        must survive the worker thread."""
        with license_stripe_mutation.stripe_budget():
            outside = license_stripe_mutation.call_stripe(
                license_stripe_mutation.caller_in_atomic_block
            )
            with transaction.atomic():
                inside = license_stripe_mutation.call_stripe(
                    license_stripe_mutation.caller_in_atomic_block
                )
            worker = license_stripe_mutation.call_stripe(threading.current_thread)

        self.assertFalse(outside)
        self.assertTrue(inside)
        self.assertNotEqual(worker, threading.current_thread())

    def test_the_worker_thread_leaves_no_database_connection_open(self):
        """The worker only calls Stripe; even if something on it touched the
        database, its connection is closed, so none leaks."""
        seen = {}

        def touches_the_database():
            # connections["default"] is this thread's own wrapper (the
            # module-level `connection` is a proxy shared by every thread).
            worker_wrapper = connections["default"]
            with worker_wrapper.cursor() as cursor:
                cursor.execute("SELECT 1")
            seen["wrapper"] = worker_wrapper
            return "done"

        with license_stripe_mutation.stripe_budget():
            self.assertEqual(
                license_stripe_mutation.call_stripe(touches_the_database), "done"
            )

        self.assertIsNot(seen["wrapper"], connections["default"])
        self.assertIsNone(seen["wrapper"].connection, "left open")

    def test_an_error_from_stripe_reaches_the_caller_unchanged(self):
        def refuse():
            raise stripe.error.InvalidRequestError("No such subscription", "id")

        with license_stripe_mutation.stripe_budget():
            with self.assertRaisesRegex(
                stripe.error.InvalidRequestError, "No such subscription"
            ):
                license_stripe_mutation.call_stripe(refuse)


class _RecordingStripeClient:
    """Stands in for stripe.StripeClient: records how each client is built
    and what it is asked, and answers with real StripeObjects."""

    built: list = []
    asked: list = []

    def __init__(self, api_key, **kwargs):
        type(self).built.append((api_key, kwargs))
        answer = stripe.Subscription.construct_from({"id": "sub_x"}, None)

        def ask(name):
            def call(*args, **kw):
                type(self).asked.append((name, args, kw))
                return answer

            return call

        self.v1 = SimpleNamespace(
            subscriptions=SimpleNamespace(
                retrieve=ask("subscriptions.retrieve"),
                update=ask("subscriptions.update"),
                cancel=ask("subscriptions.cancel"),
            ),
            invoices=SimpleNamespace(
                retrieve=ask("invoices.retrieve"),
                void_invoice=ask("invoices.void_invoice"),
            ),
            prices=SimpleNamespace(create=ask("prices.create")),
        )


class _SlowStripeHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        time.sleep(10)

    def log_message(self, *args):
        pass


class LicenceStripeAdapterTests(TestCase):
    """The socket bound (the SM's review of commit 9): each licence Stripe
    call is bounded where it waits, at the socket, not only by the outer
    wait in call_stripe."""

    def setUp(self):
        _RecordingStripeClient.built = []
        _RecordingStripeClient.asked = []

    def recording(self):
        return patch.object(stripe, "StripeClient", _RecordingStripeClient)

    def test_every_call_uses_the_apps_key_and_api_version_and_a_sized_timeout(self):
        with self.recording(), license_stripe_mutation.stripe_budget(20):
            LicenceStripe.modify_subscription(
                "sub_x",
                items=[{"id": "si_x", "quantity": 3}],
                proration_behavior="none",
                idempotency_key="h28-licence-x-apply",
            )

        [(api_key, kwargs)] = _RecordingStripeClient.built
        self.assertEqual(api_key, stripe.api_key)
        self.assertEqual(kwargs["stripe_version"], stripe.api_version)
        self.assertEqual(
            kwargs["max_network_retries"],
            license_stripe_mutation.CALL_MAX_NETWORK_RETRIES,
        )
        timeout = kwargs["http_client"]._timeout
        self.assertLess(timeout * 2 + 1.0, 20)
        [(name, args, kw)] = _RecordingStripeClient.asked
        self.assertEqual((name, args), ("subscriptions.update", ("sub_x",)))
        self.assertEqual(
            kw["params"],
            {"items": [{"id": "si_x", "quantity": 3}], "proration_behavior": "none"},
        )
        self.assertEqual(kw["options"], {"idempotency_key": "h28-licence-x-apply"})

    def test_each_legacy_call_maps_to_its_client_method(self):
        with self.recording():
            LicenceStripe.retrieve_subscription("sub_x")
            LicenceStripe.delete_subscription("sub_x", idempotency_key="k-del")
            LicenceStripe.retrieve_invoice("in_x", expand=["payments"])
            LicenceStripe.void_invoice("in_x", idempotency_key="k-void")
            LicenceStripe.create_price(
                product="prod_x", unit_amount=100, idempotency_key="k-price"
            )

        asked = {name: kw for name, _args, kw in _RecordingStripeClient.asked}
        self.assertEqual(
            asked["subscriptions.cancel"]["options"], {"idempotency_key": "k-del"}
        )
        self.assertEqual(asked["invoices.retrieve"]["params"], {"expand": ["payments"]})
        self.assertEqual(
            asked["invoices.void_invoice"]["options"], {"idempotency_key": "k-void"}
        )
        self.assertEqual(
            asked["prices.create"]["params"], {"product": "prod_x", "unit_amount": 100}
        )
        self.assertEqual(
            asked["prices.create"]["options"], {"idempotency_key": "k-price"}
        )
        self.assertIsNone(asked["subscriptions.retrieve"].get("options"))

    def test_the_timeout_shares_the_time_left_between_the_attempts(self):
        m = license_stripe_mutation
        self.assertEqual(m.call_timeout_seconds(), m.MAX_CALL_TIMEOUT_SECONDS)
        with m.stripe_budget(PRODUCTION_BUDGET):
            self.assertEqual(m.call_timeout_seconds(), m.MAX_CALL_TIMEOUT_SECONDS)
        with m.stripe_budget(10):
            self.assertAlmostEqual(
                m.call_timeout_seconds(), (10 - 1.0) * 0.9 / 2, places=1
            )
        with m.stripe_budget(0.5):
            self.assertEqual(m.call_timeout_seconds(), m.MIN_CALL_TIMEOUT_SECONDS)
        # The production worst case per call fits in the budget.
        self.assertLessEqual(
            m.MAX_CALL_TIMEOUT_SECONDS * (m.CALL_MAX_NETWORK_RETRIES + 1)
            + m.RETRY_SLEEP_ALLOWANCE_SECONDS * m.CALL_MAX_NETWORK_RETRIES,
            PRODUCTION_BUDGET,
        )

    def test_a_hung_stripe_fails_at_the_socket_inside_the_budget(self):
        """A real socket against a local server that never answers: the
        call fails at its own timeout, before the outer wait, and leaves no
        thread behind."""
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowStripeHandler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base = {"api": f"http://127.0.0.1:{server.server_address[1]}"}

        started = time.monotonic()
        with patch.object(
            license_stripe_mutation, "_BASE_ADDRESSES", base
        ), patch.object(
            stripe, "api_key", "sk_test_h28_socket_bound"  # pragma: allowlist secret
        ), license_stripe_mutation.stripe_budget(
            3.0
        ):
            with self.assertRaises(stripe.error.APIConnectionError) as caught:
                license_stripe_mutation.call_stripe(
                    LicenceStripe.retrieve_subscription, "sub_hung"
                )
        elapsed = time.monotonic() - started

        self.assertNotIsInstance(
            caught.exception,
            license_stripe_mutation.StripeBudgetExhausted,
            "the outer wait fired: the socket did not bound the call",
        )
        self.assertLess(elapsed, 3.0)
        join_abandoned_calls()
        self.assertEqual(
            [t for t in threading.enumerate() if t.name == "h28-stripe-call"], []
        )


class NoLegacyStripeCallTests(SimpleTestCase):
    """Every licence-flow Stripe call goes through LicenceStripe, so every
    one is bounded at the socket. A direct stripe.Subscription/Invoice/Price
    call in these flows would bypass that."""

    FLOWS = {
        "billing/license_service.py": {
            "cancel_license_subscription",
            "update_seats",
            "change_license_plan",
            "convert_license_to_offline",
        },
        "billing/stripe_service.py": {"apply_licence_price_at_stripe"},
        "billing/license_stripe_mutation.py": None,  # the whole module...
    }
    ADAPTER = "LicenceStripe"  # ...except the adapter itself

    def legacy_calls(self, node):
        return [
            f"{n.value.value.id}.{n.value.attr}.{n.attr}"
            for n in ast.walk(node)
            if isinstance(n, ast.Attribute)
            and isinstance(n.value, ast.Attribute)
            and isinstance(n.value.value, ast.Name)
            and n.value.value.id == "stripe"
            and n.value.attr in ("Subscription", "Invoice", "Price")
        ]

    def test_no_licence_flow_calls_the_legacy_api(self):
        root = settings.BASE_DIR
        found = {}
        for rel, names in self.FLOWS.items():
            tree = ast.parse((root / rel).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name == self.ADAPTER:
                    continue
                if names is None and isinstance(node, ast.Module):
                    body = [
                        n
                        for n in node.body
                        if not (isinstance(n, ast.ClassDef) and n.name == self.ADAPTER)
                    ]
                    calls = [c for n in body for c in self.legacy_calls(n)]
                    if calls:
                        found[rel] = calls
                if names and isinstance(node, ast.FunctionDef) and node.name in names:
                    calls = self.legacy_calls(node)
                    if calls:
                        found[f"{rel}:{node.name}"] = calls
        self.assertEqual(found, {})

    def test_the_scan_sees_a_legacy_call(self):
        """Guard on the guard."""
        tree = ast.parse("def f():\n    stripe.Subscription.modify('sub_x')\n")
        self.assertEqual(self.legacy_calls(tree), ["stripe.Subscription.modify"])
