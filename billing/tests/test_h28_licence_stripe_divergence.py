"""
billing/tests/test_h28_licence_stripe_divergence.py
===================================================
H-28 (P1b): a licence's Stripe subscription and the application's record of
it must never be left disagreeing — whatever the operation does, whether it
returns or raises.

WHY THIS FILE EXISTS
--------------------
Each licence mutation below makes an irreversible Stripe call. Django rolls
back the database half of a failed operation; Stripe does not roll back. So
any path where the Stripe call succeeds but the local write does not commit
leaves the two systems permanently disagreeing, and for a web request
nothing ever retries it. See docs/evidence/h28_p1b/.

Every test here asserts one invariant, `_assert_stripe_and_app_agree`, over
the WHOLE of both records — price, seat quantity, renewal, billing method and
open invoices — rather than over the single field a scenario happens to
touch. A fix that repairs one field while breaking another fails here.

The tests were written to FAIL on b744c9f, before any fix (H2.1). Each
names the defect it reproduces.

THE FAILURE MODELS
------------------
1. The 60 s idle-in-transaction kill. Production and beta run
   `idle_in_transaction_session_timeout = 60s`, shorter than stripe-python's
   80 s request timeout. A Stripe call made while a transaction is open can
   have that transaction terminated underneath it, so the next statement
   fails and everything local rolls back. `_IdleInTransactionKill` models
   exactly that and nothing more: it fires only if a transaction was open
   when Stripe was called. Code that calls Stripe outside a transaction is
   untouched by it — which is the property the fix must earn.
2. Payment failure on an invoiced change (F1-F5): the change is applied at
   Stripe but the proration invoice is not paid — a declined card or 3D
   Secure. Nothing was collected, so both sides must end where they began,
   with no open invoice left for Stripe to collect later.
3. The unknown outcome: the request reached Stripe and was applied, but the
   response was lost (a timeout). A Stripe exception is not proof that the
   mutation failed.

These are TransactionTestCases: `connection.in_atomic_block` is only
meaningful when the test itself is not wrapped in a transaction.
"""

import itertools
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.db import OperationalError, connection
from django.test import TransactionTestCase
from django.utils import timezone

from billing.imports import stripe
from billing.license_service import LicenseSubscriptionService
from billing.models import (
    LicenseBillingMethod,
    LicenseSubscription,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
)
from classrooms.models import School
from users.models import CustomUser, UserTypes

# Production writes contract_months from the school's choice of 9, 10 or 12;
# 12 is the model default. change_license_price() creates a Stripe Price for
# any contract other than 1, so these fixtures exercise that path as
# production does rather than dodging it with a value production never sets.
CONTRACT_MONTHS = 12


class _IdleInTransactionKill:
    """
    Postgres terminating a session left idle inside a transaction.

    Armed by the fake Stripe when a mutation is made while a transaction is
    open; the next SQL statement then fails, as it does in production once
    the 60 s timeout has fired during the network call. Fires once.
    """

    def __init__(self):
        self.armed = False
        self.fired = False

    def arm(self):
        self.armed = True

    def __call__(self, execute, sql, params, many, context):
        if self.armed:
            self.armed = False
            self.fired = True
            raise OperationalError(
                "terminating connection due to idle-in-transaction timeout"
            )
        return execute(sql, params, many, context)


class _FakeLicenceStripe:
    """
    A stateful stand-in for one licence's Stripe subscription.

    Returns real `stripe.StripeObject`s, not dicts, because production code
    reads them both ways (`price.id` and `sub["items"]`). Mutations change
    the state that later retrieves return, so the tests can compare Stripe's
    end state with the database's — which a call-recording mock cannot do.
    """

    def __init__(self, *, sub_id, price_id, unit_amount, quantity):
        self.sub_id = sub_id
        self.status = "active"
        self.cancel_at_period_end = False
        self.item_id = "si_h28"
        self.price_id = price_id
        self.quantity = quantity
        self.latest_invoice = None
        self.prices = {price_id: unit_amount}
        self.invoices = {}
        self.calls = []

        # Scenario switches.
        self.invoice_outcome = "paid"  # or "open"
        self.pi_status = "succeeded"  # or "requires_action" / "requires_payment_method"
        self.card_error_on_modify = False
        self.lost_response_on = set()  # {"modify", "delete"}

        self.kill = _IdleInTransactionKill()
        self._ids = itertools.count(1)

    # -- helpers -------------------------------------------------------------

    def _mutated(self):
        # The CALLER's transaction: under H-28's request budget, Stripe
        # calls run on a worker thread whose own connection never has one.
        from billing.license_stripe_mutation import caller_in_atomic_block

        if caller_in_atomic_block():
            self.kill.arm()

    def _sub_object(self):
        return stripe.Subscription.construct_from(
            {
                "id": self.sub_id,
                "object": "subscription",
                "status": self.status,
                "cancel_at_period_end": self.cancel_at_period_end,
                "latest_invoice": self.latest_invoice,
                "items": {
                    "object": "list",
                    "data": [
                        {
                            "id": self.item_id,
                            "object": "subscription_item",
                            "price": {"id": self.price_id, "object": "price"},
                            "quantity": self.quantity,
                        }
                    ],
                },
            },
            None,
        )

    def unit_amount(self):
        return self.prices[self.price_id]

    def open_invoices(self):
        return [i for i, inv in self.invoices.items() if inv["status"] == "open"]

    # -- the patched API surface --------------------------------------------

    def subscription_retrieve(self, sub_id, *args, **kwargs):
        self.calls.append(("Subscription.retrieve", sub_id, kwargs))
        assert sub_id == self.sub_id, f"unexpected subscription {sub_id}"
        return self._sub_object()

    def subscription_modify(self, sub_id, *args, **kwargs):
        self.calls.append(("Subscription.modify", sub_id, kwargs))
        assert sub_id == self.sub_id, f"unexpected subscription {sub_id}"
        self._mutated()

        old_total = self.unit_amount() * self.quantity
        if "cancel_at_period_end" in kwargs:
            self.cancel_at_period_end = kwargs["cancel_at_period_end"]
        for item in kwargs.get("items") or []:
            if "price" in item:
                self.price_id = item["price"]
            if "quantity" in item:
                self.quantity = item["quantity"]

        new_total = self.unit_amount() * self.quantity
        invoiced = (
            kwargs.get("proration_behavior") == "always_invoice"
            and new_total > old_total
        )
        if invoiced:
            invoice_id = f"in_h28_{next(self._ids)}"
            # A declined card leaves the change's invoice open and unpaid.
            outcome = "open" if self.card_error_on_modify else self.invoice_outcome
            self.invoices[invoice_id] = {
                "status": outcome,
                "amount_paid": new_total - old_total if outcome == "paid" else 0,
                "pi_status": self.pi_status,
            }
            self.latest_invoice = invoice_id

        if self.card_error_on_modify and invoiced:
            # The in-tree individual path records that Stripe applies the
            # item change in the same call that attempts payment, so the swap
            # may be live even though this raised (stripe_service.py ~1157).
            # Only a call that attempts payment can be declined: a revert
            # with proration_behavior="none" charges nothing.
            raise stripe.error.CardError(
                "Your card was declined.", None, "card_declined"
            )
        if "modify" in self.lost_response_on:
            raise stripe.error.APIConnectionError("Request timed out")
        return self._sub_object()

    def subscription_delete(self, sub_id, *args, **kwargs):
        self.calls.append(("Subscription.delete", sub_id, kwargs))
        assert sub_id == self.sub_id, f"unexpected subscription {sub_id}"
        self._mutated()
        self.status = "canceled"
        if "delete" in self.lost_response_on:
            raise stripe.error.APIConnectionError("Request timed out")
        return self._sub_object()

    def price_create(self, *args, **kwargs):
        self.calls.append(("Price.create", None, kwargs))
        self._mutated()
        price_id = f"price_h28_{next(self._ids)}"
        self.prices[price_id] = kwargs["unit_amount"]
        return stripe.Price.construct_from({"id": price_id, "object": "price"}, None)

    def invoice_retrieve(self, invoice_id, *args, **kwargs):
        self.calls.append(("Invoice.retrieve", invoice_id, kwargs))
        inv = self.invoices[invoice_id]
        return stripe.Invoice.construct_from(
            {
                "id": invoice_id,
                "object": "invoice",
                "status": inv["status"],
                "amount_paid": inv["amount_paid"],
                "currency": "usd",
                "hosted_invoice_url": f"https://invoice.example/{invoice_id}",
                "payments": {
                    "object": "list",
                    "data": [
                        {
                            "status": "paid" if inv["status"] == "paid" else "open",
                            "payment": {
                                "payment_intent": {
                                    "id": f"pi_{invoice_id}",
                                    "object": "payment_intent",
                                    "status": inv["pi_status"],
                                }
                            },
                        }
                    ],
                },
            },
            None,
        )

    def invoice_void(self, invoice_id, *args, **kwargs):
        self.calls.append(("Invoice.void_invoice", invoice_id, kwargs))
        self._mutated()
        self.invoices[invoice_id]["status"] = "void"
        return self.invoice_retrieve(invoice_id)

    # -- installation --------------------------------------------------------

    def patches(self):
        return [
            patch.object(
                stripe.Subscription, "retrieve", side_effect=self.subscription_retrieve
            ),
            patch.object(
                stripe.Subscription, "modify", side_effect=self.subscription_modify
            ),
            patch.object(
                stripe.Subscription, "delete", side_effect=self.subscription_delete
            ),
            patch.object(stripe.Price, "create", side_effect=self.price_create),
            patch.object(stripe.Invoice, "retrieve", side_effect=self.invoice_retrieve),
            patch.object(stripe.Invoice, "void_invoice", side_effect=self.invoice_void),
        ]

    def mutations(self):
        return [
            c
            for c in self.calls
            if c[0] not in ("Subscription.retrieve", "Invoice.retrieve")
        ]


def _make_plan(tier, name, price_cents, price_id):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=f"H-28 {name}",
        category=PlanCategory.LICENSE,
        tier=tier,
        monthly_credits=20_000,
        overage_block_size=5_000,
        overage_block_price=299,
        price_cents=Decimal(price_cents),
        stripe_price_id=price_id,
        product_id="prod_h28",
    )


class LicenceStripeDivergenceTests(TransactionTestCase):
    """Reproduce-first (H2.1): every test here FAILS on b744c9f."""

    SUB_ID = "sub_h28_licence"
    SEATS = 10

    def setUp(self):
        self.school = School.objects.create(name="H-28 School")
        self.admin = CustomUser.objects.create_user(
            email="admin@h28.school.edu",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SCHOOL_ADMIN,
            school=self.school,
        )
        self.superadmin = CustomUser.objects.create_superuser(
            email="root@h28.gradea.com",
            password="test123",  # pragma: allowlist secret
            user_type=UserTypes.SUPER_ADMIN,
        )
        self.plan = _make_plan(PlanTier.PRO, PlanType.PRO, "1000.00", "price_h28_pro")
        self.cheaper_plan = _make_plan(
            PlanTier.STANDARD, PlanType.STANDARD, "500.00", "price_h28_std"
        )
        self.dearer_plan = _make_plan(
            PlanTier.POWER, PlanType.POWER, "2000.00", "price_h28_power"
        )
        self.licence = LicenseSubscription.objects.create(
            school=self.school,
            admin_user=self.admin,
            plan=self.plan,
            billing_cycle_start=timezone.now(),
            billing_cycle_end=timezone.now() + timedelta(days=30),
            billing_method=LicenseBillingMethod.STRIPE,
            contract_months=CONTRACT_MONTHS,
            max_seats=self.SEATS,
            is_active=True,
            auto_renew=True,
            stripe_subscription_id=self.SUB_ID,
            stripe_customer_id="cus_h28",
        )
        self.stripe = _FakeLicenceStripe(
            sub_id=self.SUB_ID,
            price_id="price_h28_initial",
            unit_amount=int(self.plan.price_cents) * CONTRACT_MONTHS,
            quantity=self.SEATS,
        )
        for p in self.stripe.patches():
            p.start()
            self.addCleanup(p.stop)

    # -- running and judging -------------------------------------------------

    def _run(self, fn, *args, **kwargs):
        """Run the operation under the idle-in-transaction model. Returns the
        exception it raised, or None — the invariant must hold either way."""
        with connection.execute_wrapper(self.stripe.kill):
            try:
                fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - judged by the invariant
                return exc
        return None

    def _assert_stripe_and_app_agree(self, raised):
        """The whole-record invariant. Stripe is the source of truth for
        billing, so every field the app mirrors must match it."""
        lic = LicenseSubscription.objects.get(pk=self.licence.pk)
        s = self.stripe
        context = (
            f"\n  operation raised: {type(raised).__name__ if raised else 'nothing'}"
            f" {raised or ''}"
            f"\n  idle-in-transaction kill fired: {s.kill.fired}"
            f"\n  Stripe mutations: {[(c[0], c[2]) for c in s.mutations()]}"
        )

        stripe_deleted = s.status == "canceled"
        app_offline = lic.billing_method == LicenseBillingMethod.OFFLINE
        self.assertEqual(
            stripe_deleted,
            app_offline,
            "Stripe subscription deleted must mean the app has converted the "
            f"licence to OFFLINE (Stripe status={s.status}, app "
            f"billing_method={lic.billing_method}, app still holds "
            f"stripe_subscription_id={lic.stripe_subscription_id!r})" + context,
        )
        if stripe_deleted:
            return  # nothing else at Stripe is still billing this licence

        self.assertEqual(
            s.cancel_at_period_end,
            not lic.auto_renew,
            f"renewal disagrees: Stripe cancel_at_period_end={s.cancel_at_period_end}, "
            f"app auto_renew={lic.auto_renew}" + context,
        )
        self.assertEqual(
            s.quantity,
            lic.max_seats,
            f"seat count disagrees: Stripe quantity={s.quantity}, "
            f"app max_seats={lic.max_seats}" + context,
        )
        effective_cents = lic.custom_price_cents or lic.plan.price_cents
        self.assertEqual(
            s.unit_amount(),
            int(effective_cents) * lic.contract_months,
            f"price disagrees: Stripe bills {s.unit_amount()} per "
            f"{lic.contract_months}-month cycle, app says plan {lic.plan.name} "
            f"at {effective_cents}/month" + context,
        )
        self.assertEqual(
            s.open_invoices(),
            [],
            "an open invoice is left for Stripe to collect later, for a change "
            "the app did not keep" + context,
        )

    def _assert_stripe_was_mutated(self, name):
        """Guards against a vacuous pass: the scenario only means something
        if the irreversible call really happened."""
        self.assertIn(
            name,
            [c[0] for c in self.stripe.mutations()],
            f"scenario precondition: {name} must have been called",
        )

    # -- 1. the 60 s idle-in-transaction kill --------------------------------

    def test_cancel_killed_mid_stripe_call_leaves_both_sides_agreeing(self):
        """P1 cancel_license_subscription (license_service.py:1969)."""
        raised = self._run(
            LicenseSubscriptionService.cancel_license_subscription,
            self.licence,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_seat_decrease_killed_mid_stripe_call_leaves_both_sides_agreeing(self):
        """P1 update_seats (license_service.py:2224)."""
        raised = self._run(
            LicenseSubscriptionService.update_seats,
            self.licence,
            self.SEATS - 3,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_plan_downgrade_killed_mid_stripe_call_leaves_both_sides_agreeing(self):
        """P1 change_license_plan -> change_license_price
        (license_service.py:2127 -> stripe_service.py:1680)."""
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.cheaper_plan,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_convert_to_offline_killed_mid_delete_leaves_both_sides_agreeing(self):
        """P0 convert_license_to_offline (license_service.py:3465): the
        delete cannot be undone, so the app must not be left believing the
        school is still billed by a subscription that no longer exists."""
        raised = self._run(
            LicenseSubscriptionService.convert_license_to_offline,
            self.licence,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.delete")
        self._assert_stripe_and_app_agree(raised)

    # -- 2. payment failure on an invoiced change (F1-F5) --------------------

    def test_F1_plan_upgrade_needing_3d_secure_leaves_both_sides_agreeing(self):
        """F1 (stripe_service.py:1707-1711): requires_action raised with no
        revert, so Stripe kept the new price while the plan rolled back."""
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_action"
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.dearer_plan,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_F2_plan_upgrade_card_error_leaves_both_sides_agreeing(self):
        """F2 (stripe_service.py:1685-1686): CardError raised with no revert."""
        self.stripe.card_error_on_modify = True
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.dearer_plan,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_F3_plan_upgrade_declined_leaves_no_open_invoice(self):
        """F3 (stripe_service.py:1713-1717): the price is reverted but the
        unpaid invoice is never voided."""
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_payment_method"
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.dearer_plan,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_F4_seat_increase_declined_leaves_no_open_invoice(self):
        """F4 (license_service.py:2246-2257): quantity reverted, invoice
        never voided."""
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_payment_method"
        raised = self._run(
            LicenseSubscriptionService.update_seats,
            self.licence,
            self.SEATS + 5,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_F4_seat_increase_needing_3d_secure_leaves_no_open_invoice(self):
        """F4, the licence-path requires_action case d4 required: no
        licence-path test covered it before this one."""
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_action"
        raised = self._run(
            LicenseSubscriptionService.update_seats,
            self.licence,
            self.SEATS + 5,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_F5_seat_increase_card_error_leaves_both_sides_agreeing(self):
        """F5 (license_service.py:2273): CardError caught as StripeError,
        raised with no revert."""
        self.stripe.card_error_on_modify = True
        raised = self._run(
            LicenseSubscriptionService.update_seats,
            self.licence,
            self.SEATS + 5,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    # -- 0. F0: a plan change never reaches Stripe at all --------------------

    def test_F0_plan_change_with_no_failure_updates_the_stripe_price(self):
        """F0 (license_service.py:2086 then stripe_service.py:1609/1623).

        No failure is injected. change_license_plan() writes the NEW plan
        onto the licence row, THEN calls change_license_price(), which reads
        the "old" price from that same row — so old equals new, it logs
        "price unchanged, skipping Stripe update", and returns. The app shows
        the new plan; Stripe keeps billing the old price. Nothing else ever
        pushes a licence price to Stripe (no renewal or sync path does), so
        the disagreement is permanent.

        This is also why the change_license_plan scenarios above fail at
        their precondition on b744c9f: the price-change code they target is
        unreachable in production until F0 is fixed.
        """
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.dearer_plan,
            performed_by=self.superadmin,
        )
        self.assertIsNone(raised, "a plan change with no failure must succeed")
        self._assert_stripe_and_app_agree(raised)

    # -- 0b. F1-F3 are latent: shown by calling change_license_price as it
    #        will be called once F0 is fixed (licence row still on the old
    #        plan). They cannot fire through change_license_plan on b744c9f
    #        only because F0 short-circuits them — fixing F0 alone would make
    #        them live, which is why they are fixed together.

    def _call_change_license_price_directly(self, new_plan):
        from billing.stripe_service import StripeSubscriptionMutationService

        return self._run(
            StripeSubscriptionMutationService.change_license_price,
            self.licence,
            new_plan,
            None,
            performed_by=self.superadmin,
        )

    def test_latent_F1_price_change_needing_3d_secure_is_reverted_and_voided(self):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_action"
        raised = self._call_change_license_price_directly(self.dearer_plan)
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_latent_F2_price_change_card_error_is_reverted(self):
        self.stripe.card_error_on_modify = True
        raised = self._call_change_license_price_directly(self.dearer_plan)
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_latent_F3_price_change_declined_leaves_no_open_invoice(self):
        self.stripe.invoice_outcome = "open"
        self.stripe.pi_status = "requires_payment_method"
        raised = self._call_change_license_price_directly(self.dearer_plan)
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    # -- 3. the unknown outcome: applied, response lost ----------------------

    def test_cancel_applied_but_response_lost_leaves_both_sides_agreeing(self):
        """Today every StripeError becomes ValueError with local state left
        untouched — including a timeout whose request actually landed."""
        self.stripe.lost_response_on = {"modify"}
        raised = self._run(
            LicenseSubscriptionService.cancel_license_subscription,
            self.licence,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_seat_decrease_applied_but_response_lost_leaves_both_sides_agreeing(self):
        self.stripe.lost_response_on = {"modify"}
        raised = self._run(
            LicenseSubscriptionService.update_seats,
            self.licence,
            self.SEATS - 3,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_plan_downgrade_applied_but_response_lost_leaves_both_sides_agreeing(self):
        self.stripe.lost_response_on = {"modify"}
        raised = self._run(
            LicenseSubscriptionService.change_license_plan,
            self.licence,
            self.cheaper_plan,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.modify")
        self._assert_stripe_and_app_agree(raised)

    def test_convert_to_offline_delete_applied_but_response_lost(self):
        """P0's most dangerous misreport: the delete happened, the response
        was lost, and today the code reports a harmless failure while the
        school's subscription is gone."""
        self.stripe.lost_response_on = {"delete"}
        raised = self._run(
            LicenseSubscriptionService.convert_license_to_offline,
            self.licence,
            performed_by=self.superadmin,
        )
        self._assert_stripe_was_mutated("Subscription.delete")
        self._assert_stripe_and_app_agree(raised)
