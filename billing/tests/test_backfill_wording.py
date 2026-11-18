"""
S18 (row 138): billing transaction descriptions in plain words.

The backfill command and a data migration used to write "Backfilled from
checkout.session.completed (flow)". Now both use billing.transaction_wording,
which reuses the live wording (H-275 invoice phrases and the checkout texts).
The live overage text is "Credit top-up — N AI credits".
"""

import importlib

from django.apps import apps as global_apps
from django.test import TestCase
from django.utils import timezone

from billing.invoice_wording import invoice_description
from billing.management.commands.backfill_billing_transactions import Command
from billing.models import (
    CONVERSION_FACTOR,
    BillingTransaction,
    BillingTransactionMethod,
    BillingTransactionSource,
    BillingTransactionStatus,
    BillingTransactionType,
    StripeEvent,
    StripeEventStatus,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from billing.tests.test_overage_purchase_integrity import OverageFixture, make_plan
from billing.transaction_wording import (
    checkout_description,
    rewrite_backfilled_description,
)


class WordingModuleTests(TestCase):
    def test_checkout_flows_use_the_live_texts(self):
        cases = [
            ("individual_checkout", "Pro", None, "New subscription — Pro"),
            ("individual_subscribe", "Pro", None, "New subscription — Pro"),
            ("trial_to_paid", "Pro", None, "Trial converted to Pro"),
            ("license_create", "School", None, "License created — School"),
            (
                "overage_block_purchase_checkout",
                "Pro",
                2_000,
                "Credit top-up — 2,000 AI credits",
            ),
            ("overage_block_purchase_checkout", "Pro", None, "Credit top-up"),
            (
                "individual_upgrade_checkout",
                "Pro",
                None,
                "Plan change – payment received",
            ),
        ]
        for flow, plan, credits, expected in cases:
            with self.subTest(flow=flow, credits=credits):
                self.assertEqual(
                    checkout_description(flow, plan_name=plan, credits=credits),
                    expected,
                )

    def test_unknown_plan_falls_back(self):
        self.assertEqual(
            checkout_description("trial_to_paid", plan_name=None, credits=None),
            "Trial converted",
        )
        self.assertEqual(
            checkout_description("individual_checkout", plan_name=None, credits=None),
            invoice_description(billing_reason=None, paid=True, license=False),
        )
        self.assertEqual(
            checkout_description("license_create", plan_name=None, credits=None),
            invoice_description(billing_reason=None, paid=True, license=True),
        )

    def test_rewrite_only_touches_backfilled_text(self):
        self.assertIsNone(
            rewrite_backfilled_description(
                "Subscription renewal – payment received",
                plan_name="Pro",
                license=False,
            )
        )
        self.assertEqual(
            rewrite_backfilled_description(
                "Backfilled from invoice.payment_succeeded (subscription_cycle)",
                plan_name="Pro",
                license=False,
            ),
            "Subscription renewal – payment received",
        )
        self.assertEqual(
            rewrite_backfilled_description(
                "Backfilled from invoice.payment_failed (None)",
                plan_name=None,
                license=True,
            ),
            "License payment failed",
        )
        self.assertEqual(
            rewrite_backfilled_description(
                "Backfilled from checkout.session.completed (trial_to_paid)",
                plan_name="Pro",
                license=False,
            ),
            "Trial converted to Pro",
        )


class CommandWritesPlainTextTests(TestCase, OverageFixture):
    def setUp(self):
        self.plan = make_plan()
        self.plan.overage_block_size = 500 * CONVERSION_FACTOR
        self.plan.save()
        self.user, self.wallet = self.build(email="bf@billing.test")

    def _event(self, event_type, obj):
        return StripeEvent.objects.create(
            stripe_event_id=f"evt_{event_type}_{obj['id']}",
            event_type=event_type,
            payload={"object": obj},
            status=StripeEventStatus.SUCCEEDED,
            completed_at=timezone.now(),
            processed_at=timezone.now(),
        )

    def _checkout(self, flow, **meta):
        session = {
            "id": f"cs_{flow}",
            "amount_total": 1000,
            "currency": "usd",
            "metadata": {"flow": flow, "user_id": str(self.user.id), **meta},
        }
        Command()._backfill_checkout_session(
            session, self._event("checkout.session.completed", session), False
        )
        return BillingTransaction.objects.get(stripe_checkout_session_id=session["id"])

    def test_checkout_flows(self):
        name = self.plan.display_name or self.plan.name
        pid = str(self.plan.id)
        self.assertEqual(
            self._checkout("individual_checkout", plan_id=pid).description,
            f"New subscription — {name}",
        )
        self.assertEqual(
            self._checkout("trial_to_paid", plan_id=pid).description,
            f"Trial converted to {name}",
        )
        self.assertEqual(
            self._checkout("individual_upgrade_checkout").description,
            "Plan change – payment received",
        )
        credits = 3 * self.plan.display_overage_block_size
        self.assertEqual(
            self._checkout(
                "overage_block_purchase_checkout", plan_id=pid, quantity="3"
            ).description,
            f"Credit top-up — {credits:,} AI credits",
        )

    def test_overage_without_quantity(self):
        self.assertEqual(
            self._checkout("overage_block_purchase_checkout").description,
            "Credit top-up",
        )

    def test_invoice_uses_the_h275_phrases(self):
        sub = UserSubscription.objects.get(user=self.user)
        sub.stripe_subscription_id = "sub_bf"
        sub.save()
        for succeeded in (True, False):
            invoice = {
                "id": f"in_{succeeded}",
                "subscription": "sub_bf",
                "billing_reason": "subscription_cycle",
                "amount_paid": 500,
                "amount_due": 500,
                "currency": "usd",
            }
            Command()._backfill_invoice(
                invoice,
                self._event(
                    (
                        "invoice.payment_succeeded"
                        if succeeded
                        else "invoice.payment_failed"
                    ),
                    invoice,
                ),
                False,
                succeeded=succeeded,
            )
            self.assertEqual(
                BillingTransaction.objects.get(
                    stripe_invoice_id=invoice["id"]
                ).description,
                invoice_description(
                    billing_reason="subscription_cycle", paid=succeeded, license=False
                ),
            )


class MigrationRewritesOldRowsTests(TestCase, OverageFixture):
    def setUp(self):
        self.plan = make_plan()
        self.user, _ = self.build(email="mig@billing.test")

    def _row(self, description, **kw):
        return BillingTransaction.objects.create(
            source=BillingTransactionSource.INDIVIDUAL,
            transaction_type=BillingTransactionType.INDIVIDUAL_SUBSCRIPTION_CHARGE,
            status=BillingTransactionStatus.PAID,
            billing_method=BillingTransactionMethod.STRIPE,
            amount_cents=100,
            user=self.user,
            user_subscription=UserSubscription.objects.get(user=self.user),
            description=description,
            occurred_at=timezone.now(),
            **kw,
        )

    def test_old_rows_are_rewritten_and_others_kept(self):
        old = self._row(
            "Backfilled from checkout.session.completed (individual_checkout)"
        )
        inv = self._row(
            "Backfilled from invoice.payment_succeeded (subscription_update)"
        )
        live = self._row("New subscription — Something")
        mod = importlib.import_module(
            "billing.migrations.0089_plain_backfill_descriptions"
        )
        mod.rewrite(global_apps, None)
        for row in (old, inv, live):
            row.refresh_from_db()
        name = self.plan.display_name or self.plan.name
        self.assertEqual(old.description, f"New subscription — {name}")
        self.assertEqual(inv.description, "Plan change – payment received")
        self.assertEqual(live.description, "New subscription — Something")
        self.assertEqual(self._run_reverse(), None)

    def _run_reverse(self):
        mod = importlib.import_module(
            "billing.migrations.0089_plain_backfill_descriptions"
        )
        return mod.noop(global_apps, None)


class LiveOverageTextTests(TestCase, OverageFixture):
    def setUp(self):
        self.stub_receipt_scheduling()
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="live@billing.test")

    def test_individual_checkout_text(self):
        session = self.checkout_session(self.wallet, self.plan, quantity=2)
        StripeWebhookHandler.handle_checkout_completed(session)
        row = BillingTransaction.objects.get(stripe_checkout_session_id=session["id"])
        credits = 2 * self.plan.display_overage_block_size
        self.assertEqual(row.description, f"Credit top-up — {credits:,} AI credits")


class VerificationRefusalTextTests(TestCase):
    def test_wrong_code_uses_the_approved_text(self):
        from django.urls import reverse

        response = self.client.post(
            reverse("auth-verify"),
            {"email": "nobody@example.com", "token": "000000"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn(
            "This verification link is invalid or has expired. Request a new one.",
            response.content.decode(),
        )
        self.assertNotIn("Invalid email or token", response.content.decode())
