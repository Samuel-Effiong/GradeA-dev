"""
billing/tests/test_price_reconciliation.py
==========================================
The nightly sweep that asks Stripe what every plan actually costs.

WHAT THIS IS FOR
----------------
Layer 2 of three. Layer 1 is the plan row agreeing with its Stripe Price;
layer 3 is `billing/overage_pricing.py` refusing an unsafe purchase at
checkout. This layer exists to find the problem the night before a
customer does.

THE TWO THINGS MOST WORTH GETTING RIGHT
---------------------------------------
1. **An outage is not drift.** A timeout, a rate limit and a bad key all
   produce the same symptom at the call site — we could not confirm the
   amount — and none of them means the price changed. Filing them as drift
   pages someone about a billing incident that is not happening, and
   teaches them that drift alerts are usually noise. Half the operational
   tests below exist to pin that distinction.

2. **It must never fix anything.** A sweep that can write to Stripe or to
   billing state is a sweep that can do damage unattended at 02:30. The
   safety tests assert the absence of writes, which is the kind of
   property that quietly stops being true when someone adds a helpful
   "while we're here" line.

A NOTE ON THE SHAPE
-------------------
The brief describes `plan.overage_plan` with its own `stripe_price_id`.
No such relation exists — a SubscriptionPlan carries BOTH prices on one
row (`stripe_price_id`, `stripe_overage_price_id`). These tests follow the
code.
"""

import threading
from decimal import Decimal
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, TransactionTestCase

from AutoGrader.testing.concurrency import run_concurrently
from billing.models import (
    BillingInterval,
    CreditBucket,
    CreditLedger,
    PlanCategory,
    PlanTier,
    PlanType,
    PriceKind,
    PriceReconciliationResult,
    PriceReconciliationRun,
    PriceReconciliationStatus,
    SubscriptionPlan,
)
from billing.price_reconciliation import EXPECTED_CURRENCY, reconcile_prices

BASE_PRICE_ID = "price_base_1"
OVERAGE_PRICE_ID = "price_overage_1"
PRODUCT_ID = "prod_1"


def make_plan(
    *,
    name=PlanType.STANDARD,
    category=PlanCategory.INDIVIDUAL,
    interval=BillingInterval.MONTHLY,
    base_cents=1499,
    overage_cents=500,
    base_price_id=BASE_PRICE_ID,
    overage_price_id=OVERAGE_PRICE_ID,
    product_id=PRODUCT_ID,
):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=category,
        tier=PlanTier.STANDARD,
        interval=interval,
        monthly_credits=10_000,
        price_cents=Decimal(base_cents),
        overage_block_size=500,
        overage_block_price=overage_cents,
        max_overage_blocks=10,
        stripe_price_id=base_price_id,
        stripe_overage_price_id=overage_price_id,
        product_id=product_id,
        is_active=True,
    )


def price_obj(
    *,
    price_id=BASE_PRICE_ID,
    unit_amount=1499,
    currency="usd",
    active=True,
    product=PRODUCT_ID,
    recurring=("month", 1),
):
    obj = {
        "id": price_id,
        "unit_amount": unit_amount,
        "currency": currency,
        "active": active,
        "product": product,
    }
    obj["recurring"] = (
        {"interval": recurring[0], "interval_count": recurring[1]}
        if recurring
        else None
    )
    return obj


def stripe_returns(mapping):
    """Patch Price.retrieve to serve `mapping`, raising for anything else."""

    def _retrieve(price_id, *args, **kwargs):
        value = mapping.get(price_id)
        if value is None:
            raise _InvalidRequestError(f"No such price: {price_id!r}")
        if isinstance(value, Exception):
            raise value
        return value

    return patch("billing.price_reconciliation.stripe.Price.retrieve", _retrieve)


def account_returns(account_id="acct_test"):
    return patch(
        "billing.price_reconciliation.stripe.Account.retrieve",
        return_value={"id": account_id},
    )


# Stripe's exception classes are matched by NAME in the classifier, so the
# fakes only need the right names — that also keeps these tests from
# depending on the stripe package's internal class layout.
class _InvalidRequestError(Exception):
    code = "resource_missing"


class APIConnectionError(Exception):
    code = "api_connection_error"


class RateLimitError(Exception):
    code = "rate_limit"


class AuthenticationError(Exception):
    code = "authentication_error"


class APIError(Exception):
    code = "api_error"


_InvalidRequestError.__name__ = "InvalidRequestError"


def results_by_kind(run):
    return {r.price_kind: r for r in run.results.all()}


class MatchingTests(TestCase):
    """The quiet path: everything agrees."""

    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run(self, base=None, overage=None):
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: base or price_obj(),
                    OVERAGE_PRICE_ID: overage
                    or price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                return reconcile_prices()

    def test_all_plans_match(self):
        run = self._run()

        self.assertEqual(run.alert_count, 0)
        self.assertEqual(run.matched_count, 2)

    def test_the_base_price_matches(self):
        run = self._run()

        self.assertEqual(
            results_by_kind(run)[PriceKind.BASE].status,
            PriceReconciliationStatus.MATCHED,
        )

    def test_the_overage_price_matches(self):
        run = self._run()

        self.assertEqual(
            results_by_kind(run)[PriceKind.OVERAGE].status,
            PriceReconciliationStatus.MATCHED,
        )

    def test_a_recurring_price_matches_its_plan_interval(self):
        self.plan.interval = BillingInterval.ANNUAL
        self.plan.save(update_fields=["interval"])

        run = self._run(base=price_obj(recurring=("year", 1)))

        self.assertEqual(
            results_by_kind(run)[PriceKind.BASE].status,
            PriceReconciliationStatus.MATCHED,
        )

    def test_a_ONE_TIME_overage_price_is_not_false_drift(self):
        """
        The brief's explicit warning: an overage block is bought outright,
        so its Price has no `recurring` block. Demanding one would report
        drift on a perfectly correct price.
        """
        run = self._run()

        result = results_by_kind(run)[PriceKind.OVERAGE]
        self.assertEqual(result.status, PriceReconciliationStatus.MATCHED)
        self.assertIsNone(result.expected_recurring)

    def test_both_sides_of_every_comparison_are_recorded(self):
        """
        Someone reading this at 3am needs to see what we expected and what
        Stripe said without re-running anything.
        """
        run = self._run()

        result = results_by_kind(run)[PriceKind.BASE]
        self.assertEqual(result.expected_amount, 1499)
        self.assertEqual(result.stripe_amount, 1499)
        self.assertEqual(result.expected_currency, EXPECTED_CURRENCY)
        self.assertEqual(result.stripe_currency, "usd")
        self.assertEqual(result.stripe_product, PRODUCT_ID)


class DriftTests(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run(self, base):
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: base,
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                return reconcile_prices()

    def _base(self, run):
        return results_by_kind(run)[PriceKind.BASE]

    def test_an_amount_mismatch_SYNCHRONISES_from_stripe(self):
        """Stripe is the authority: the local row is what is wrong."""
        result = self._base(self._run(price_obj(unit_amount=1999)))

        self.assertEqual(result.status, PriceReconciliationStatus.SYNCHRONIZED)
        self.assertIn("amount", result.synced_fields)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_cents, Decimal(1999))

    def test_the_synchronisation_records_what_the_price_used_to_be(self):
        """
        The only surviving record of what the application charged before.
        A log line rotates away; this is what someone reconstructing an
        unexpected charge will actually find.
        """
        result = self._base(self._run(price_obj(unit_amount=1999)))

        self.assertEqual(result.previous_local_amount, 1499)
        self.assertEqual(result.stripe_amount, 1999)

    def test_a_currency_mismatch_is_reported_because_no_field_holds_it(self):
        result = self._base(self._run(price_obj(currency="eur")))

        self.assertEqual(result.status, PriceReconciliationStatus.DRIFT_DETECTED)
        self.assertIn("currency", result.mismatched_fields)

    def test_a_product_mismatch_SYNCHRONISES_from_stripe(self):
        result = self._base(self._run(price_obj(product="prod_new")))

        self.assertIn("product", result.synced_fields)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.product_id, "prod_new")

    def test_an_inactive_price_is_its_own_status_not_generic_drift(self):
        """
        An archived price does not charge the wrong amount — it refuses
        the charge. The alert should say so.
        """
        result = self._base(self._run(price_obj(active=False)))

        self.assertEqual(result.status, PriceReconciliationStatus.INACTIVE_PRICE)

    def test_a_recurring_interval_mismatch_is_REPORTED_not_synchronised(self):
        """
        `plan.interval` drives credit cadence and renewal detection.
        Copying it from a Price would change how customers are billed, not
        what they are charged — so it is raised for a human instead.
        """
        result = self._base(self._run(price_obj(recurring=("year", 1))))

        self.assertEqual(result.status, PriceReconciliationStatus.DRIFT_DETECTED)
        self.assertIn("recurring", result.mismatched_fields)

    def test_a_recurring_interval_COUNT_mismatch_is_drift(self):
        result = self._base(self._run(price_obj(recurring=("month", 3))))

        self.assertEqual(result.status, PriceReconciliationStatus.DRIFT_DETECTED)
        self.assertIn("recurring", result.mismatched_fields)

    def test_a_recurring_plan_whose_price_became_one_time_is_drift(self):
        result = self._base(self._run(price_obj(recurring=None)))

        self.assertEqual(result.status, PriceReconciliationStatus.DRIFT_DETECTED)
        self.assertIn("recurring", result.mismatched_fields)

    def test_a_price_that_does_not_resolve_is_INVALID_not_drift(self):
        with account_returns():
            with stripe_returns({OVERAGE_PRICE_ID: price_obj(recurring=None)}):
                run = reconcile_prices()

        result = self._base(run)
        self.assertEqual(result.status, PriceReconciliationStatus.INVALID_PRICE)

    def test_an_unresolvable_price_names_the_account_as_a_possible_cause(self):
        """
        A 404 and 'wrong Stripe account' are indistinguishable from here,
        and account drift was a real finding — so the message says both
        rather than sending someone hunting for a deleted price.
        """
        with account_returns():
            with stripe_returns({OVERAGE_PRICE_ID: price_obj(recurring=None)}):
                run = reconcile_prices()

        self.assertIn("different Stripe account", self._base(run).error_message)

    def test_several_mismatches_are_all_recorded_not_just_the_first(self):
        result = self._base(
            self._run(price_obj(unit_amount=1, currency="gbp", product="prod_x"))
        )

        self.assertEqual(
            set(result.mismatched_fields), {"amount", "currency", "product"}
        )

    def test_an_unsyncable_mismatch_still_reports_even_when_the_price_synced(self):
        """
        The money is now right, but something about HOW it bills is not.
        Reporting only the synchronisation would hide that.
        """
        result = self._base(self._run(price_obj(unit_amount=1999, currency="eur")))

        self.assertEqual(result.status, PriceReconciliationStatus.DRIFT_DETECTED)
        self.assertIn("amount", result.synced_fields)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_cents, Decimal(1999))


# NOTE: the former `UnsetExpectationTests` lived here. An unset local price
# used to be a CONFIGURATION_ERROR — the application had no opinion for
# Stripe to disagree with. Under Stripe-as-source-of-truth it is simply a
# value waiting to be populated, so the scenario moved to
# SynchronisationTests.test_a_missing_base_price_is_POPULATED_from_stripe.


class NotApplicableTests(TestCase):
    """Free and internal plans legitimately have no price."""

    def setUp(self):
        cache.clear()

    def test_a_plan_with_no_price_and_no_expectation_is_not_an_alert(self):
        make_plan(
            name=PlanType.TRIAL,
            base_cents=0,
            overage_cents=0,
            base_price_id="",
            overage_price_id="",
        )

        with account_returns():
            with stripe_returns({}):
                run = reconcile_prices()

        statuses = {r.status for r in run.results.all()}
        self.assertEqual(statuses, {PriceReconciliationStatus.NOT_APPLICABLE})
        self.assertEqual(run.alert_count, 0)

    def test_expecting_to_charge_with_no_price_id_IS_an_alert(self):
        """We intend to bill and have nothing to bill with."""
        make_plan(base_cents=1499, base_price_id="", overage_price_id="")

        with account_returns():
            with stripe_returns({}):
                run = reconcile_prices()

        self.assertEqual(
            results_by_kind(run)[PriceKind.BASE].status,
            PriceReconciliationStatus.MISSING_PRICE_ID,
        )

    def test_a_plan_selling_only_overage_is_fine(self):
        """A beta tier: free base, paid overage. Neither is a problem."""
        make_plan(base_cents=0, base_price_id="", overage_cents=500)

        with account_returns():
            with stripe_returns(
                {
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    )
                }
            ):
                run = reconcile_prices()

        by_kind = results_by_kind(run)
        self.assertEqual(
            by_kind[PriceKind.BASE].status, PriceReconciliationStatus.NOT_APPLICABLE
        )
        self.assertEqual(
            by_kind[PriceKind.OVERAGE].status, PriceReconciliationStatus.MATCHED
        )
        self.assertEqual(run.alert_count, 0)


class OverageIndependenceTests(TestCase):
    """
    The two prices are separate billing surfaces and drift independently.
    A sweep that stopped at the base price would miss half of it.
    """

    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run(self, base_amount, overage_amount):
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(unit_amount=base_amount),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID,
                        unit_amount=overage_amount,
                        recurring=None,
                    ),
                }
            ):
                return reconcile_prices()

    def test_base_matches_but_overage_differs(self):
        by_kind = results_by_kind(self._run(1499, 999))

        self.assertEqual(
            by_kind[PriceKind.BASE].status, PriceReconciliationStatus.MATCHED
        )
        self.assertEqual(
            by_kind[PriceKind.OVERAGE].status,
            PriceReconciliationStatus.SYNCHRONIZED,
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_cents, Decimal(1499))
        self.assertEqual(self.plan.overage_block_price, 999)

    def test_base_differs_but_overage_matches(self):
        by_kind = results_by_kind(self._run(9999, 500))

        self.assertEqual(
            by_kind[PriceKind.BASE].status, PriceReconciliationStatus.SYNCHRONIZED
        )
        self.assertEqual(
            by_kind[PriceKind.OVERAGE].status, PriceReconciliationStatus.MATCHED
        )
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_cents, Decimal(9999))
        self.assertEqual(self.plan.overage_block_price, 500)

    def test_both_differ_and_both_synchronise(self):
        run = self._run(9999, 999)

        self.assertEqual(run.synced_count, 2)
        self.assertEqual(run.alert_count, 0)

    def test_the_overage_price_is_fetched_separately_from_the_base(self):
        seen = []

        def _retrieve(price_id, *a, **k):
            seen.append(price_id)
            return price_obj(
                price_id=price_id,
                unit_amount=1499 if price_id == BASE_PRICE_ID else 500,
                recurring=("month", 1) if price_id == BASE_PRICE_ID else None,
            )

        with account_returns():
            with patch("billing.price_reconciliation.stripe.Price.retrieve", _retrieve):
                reconcile_prices()

        self.assertEqual(set(seen), {BASE_PRICE_ID, OVERAGE_PRICE_ID})


class OperationalFailureTests(TestCase):
    """
    An outage is not drift. Every test here is about that one sentence.
    """

    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run_with(self, error):
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: error,
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                return reconcile_prices()

    def test_a_timeout_is_UNAVAILABLE_not_drift(self):
        result = results_by_kind(self._run_with(APIConnectionError("timed out")))[
            PriceKind.BASE
        ]

        self.assertEqual(result.status, PriceReconciliationStatus.STRIPE_UNAVAILABLE)

    def test_an_api_error_is_UNAVAILABLE_not_drift(self):
        result = results_by_kind(self._run_with(APIError("stripe is down")))[
            PriceKind.BASE
        ]

        self.assertEqual(result.status, PriceReconciliationStatus.STRIPE_UNAVAILABLE)

    def test_an_auth_failure_is_UNAVAILABLE_not_drift(self):
        result = results_by_kind(self._run_with(AuthenticationError("bad key")))[
            PriceKind.BASE
        ]

        self.assertEqual(result.status, PriceReconciliationStatus.STRIPE_UNAVAILABLE)

    def test_a_rate_limit_is_UNAVAILABLE_not_drift(self):
        result = results_by_kind(self._run_with(RateLimitError("slow down")))[
            PriceKind.BASE
        ]

        self.assertEqual(result.status, PriceReconciliationStatus.STRIPE_UNAVAILABLE)

    def test_an_unavailable_price_is_never_counted_as_matched(self):
        run = self._run_with(APIConnectionError("timed out"))

        self.assertEqual(run.unavailable_count, 1)
        self.assertEqual(run.matched_count, 1)  # the overage price only

    def test_an_unavailable_price_is_not_counted_as_an_alert(self):
        run = self._run_with(APIConnectionError("timed out"))

        self.assertEqual(
            run.alert_count,
            0,
            "a Stripe outage was escalated as a billing-drift alert",
        )

    def test_one_failing_plan_does_not_stop_the_others(self):
        make_plan(
            name=PlanType.PRO,
            base_price_id="price_other_base",
            overage_price_id="price_other_overage",
            base_cents=2499,
            overage_cents=400,
        )

        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: APIConnectionError("down"),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                    "price_other_base": price_obj(
                        price_id="price_other_base", unit_amount=2499
                    ),
                    "price_other_overage": price_obj(
                        price_id="price_other_overage",
                        unit_amount=400,
                        recurring=None,
                    ),
                }
            ):
                run = reconcile_prices()

        self.assertEqual(run.prices_checked, 4)
        self.assertEqual(run.matched_count, 3)
        self.assertEqual(run.unavailable_count, 1)

    def test_an_unidentifiable_account_does_not_end_the_run(self):
        with patch(
            "billing.price_reconciliation.stripe.Account.retrieve",
            side_effect=APIConnectionError("down"),
        ):
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                run = reconcile_prices()

        self.assertEqual(run.stripe_account_id, "")
        self.assertEqual(run.matched_count, 2)


class RunRecordTests(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run(self):
        with account_returns("acct_expected"):
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                return reconcile_prices()

    def test_the_run_records_which_stripe_account_it_talked_to(self):
        self.assertEqual(self._run().stripe_account_id, "acct_expected")

    def test_the_run_records_the_api_version_actually_used(self):
        """
        Never pinned by this module — whatever the application uses. The
        last audit verified against a version production does not use and
        missed a removed field because of it.
        """
        run = self._run()

        import stripe as stripe_module

        self.assertEqual(run.stripe_api_version, str(stripe_module.api_version or ""))

    def test_the_run_is_finished_and_summarised(self):
        run = self._run()

        self.assertIsNotNone(run.finished_at)
        self.assertIn("already matching", run.summary)

    def test_the_same_price_is_fetched_once_per_run(self):
        """Plans share prices; re-asking Stripe for the same object wastes
        the call and muddies the timing."""
        make_plan(name=PlanType.PRO, base_cents=1499)  # same price ids
        calls = []

        def _retrieve(price_id, *a, **k):
            calls.append(price_id)
            return price_obj(
                price_id=price_id,
                unit_amount=1499 if price_id == BASE_PRICE_ID else 500,
                recurring=("month", 1) if price_id == BASE_PRICE_ID else None,
            )

        with account_returns():
            with patch("billing.price_reconciliation.stripe.Price.retrieve", _retrieve):
                reconcile_prices()

        self.assertEqual(
            sorted(calls),
            sorted({BASE_PRICE_ID, OVERAGE_PRICE_ID}),
            f"the same Price was retrieved more than once: {calls}",
        )


class SafetyTests(TestCase):
    """
    What the sweep must NOT do. These assert absences, which is the kind of
    property that quietly stops holding when someone adds a helpful line.
    """

    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def _run(self, base_amount=9999):
        """Deliberately drifted — the tempting moment to 'just fix it'."""
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(unit_amount=base_amount),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=999, recurring=None
                    ),
                }
            ):
                return reconcile_prices()

    def test_it_DOES_rewrite_the_local_plan_to_match_stripe(self):
        """
        The contract, inverted from the original design: Stripe is the
        source of truth, so a disagreement is resolved by following it.
        """
        self._run()

        self.plan.refresh_from_db()
        self.assertEqual(self.plan.price_cents, Decimal(9999))
        self.assertEqual(self.plan.overage_block_price, 999)

    def test_it_makes_no_stripe_mutation(self):
        mutators = [
            "billing.price_reconciliation.stripe.Price.modify",
            "billing.price_reconciliation.stripe.Price.create",
        ]
        with patch(mutators[0], create=True) as modify, patch(
            mutators[1], create=True
        ) as create:
            self._run()

        self.assertFalse(modify.called)
        self.assertFalse(create.called)

    def test_it_grants_or_revokes_no_credits(self):
        before_buckets = CreditBucket.objects.count()
        before_ledger = CreditLedger.objects.count()

        self._run()

        self.assertEqual(CreditBucket.objects.count(), before_buckets)
        self.assertEqual(CreditLedger.objects.count(), before_ledger)

    def test_running_it_twice_changes_no_billing_state(self):
        self._run()
        self.plan.refresh_from_db()
        snapshot = (self.plan.price_cents, self.plan.overage_block_price)

        self._run()

        self.plan.refresh_from_db()
        self.assertEqual(
            (self.plan.price_cents, self.plan.overage_block_price), snapshot
        )

    def test_every_run_leaves_its_own_audit_rows(self):
        self._run()
        self._run()

        self.assertEqual(PriceReconciliationRun.objects.count(), 2)
        self.assertEqual(PriceReconciliationResult.objects.count(), 4)

    def test_the_first_run_synchronises_and_the_second_has_nothing_to_do(self):
        first = self._run()
        second = self._run()

        self.assertEqual(first.synced_count, 2)
        self.assertEqual(second.synced_count, 0)


class TaskAndLockTests(TestCase):
    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def tearDown(self):
        cache.clear()

    def test_the_task_runs_the_sweep(self):
        from billing.tasks import reconcile_stripe_prices

        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                summary = reconcile_stripe_prices()

        self.assertIn("already matching", summary)
        self.assertEqual(PriceReconciliationRun.objects.count(), 1)

    def test_a_second_run_is_skipped_while_one_holds_the_lock(self):
        from billing.tasks import PRICE_RECONCILIATION_LOCK_KEY, reconcile_stripe_prices

        cache.add(PRICE_RECONCILIATION_LOCK_KEY, "1", timeout=600)

        summary = reconcile_stripe_prices()

        self.assertIn("already holds the lock", summary)
        self.assertEqual(PriceReconciliationRun.objects.count(), 0)

    def test_the_lock_is_released_when_the_run_finishes(self):
        from billing.tasks import PRICE_RECONCILIATION_LOCK_KEY, reconcile_stripe_prices

        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: price_obj(),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                reconcile_stripe_prices()

        self.assertIsNone(cache.get(PRICE_RECONCILIATION_LOCK_KEY))

    def test_the_lock_is_released_even_when_the_run_raises(self):
        """
        Otherwise one crash disables the nightly sweep until the TTL
        expires — a watchdog that goes quiet is worse than none.
        """
        from billing.tasks import PRICE_RECONCILIATION_LOCK_KEY, reconcile_stripe_prices

        with patch(
            "billing.price_reconciliation.reconcile_prices",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                reconcile_stripe_prices()

        self.assertIsNone(cache.get(PRICE_RECONCILIATION_LOCK_KEY))

    def test_the_lock_carries_a_timeout_so_a_killed_worker_cannot_wedge_it(self):
        from billing.tasks import PRICE_RECONCILIATION_LOCK_TIMEOUT_SECONDS

        self.assertGreater(PRICE_RECONCILIATION_LOCK_TIMEOUT_SECONDS, 0)
        self.assertLess(
            PRICE_RECONCILIATION_LOCK_TIMEOUT_SECONDS,
            24 * 3600,
            "the lock outlives the gap to the next nightly run, so one "
            "killed worker would silently disable the sweep",
        )


class ConcurrentRunTests(TransactionTestCase):
    """Two workers firing at 02:30, real threads."""

    reset_sequences = True

    def setUp(self):
        cache.clear()
        self.plan = make_plan()

    def tearDown(self):
        cache.clear()

    def test_only_one_of_two_simultaneous_tasks_performs_the_sweep(self):
        from billing.tasks import reconcile_stripe_prices

        # The lock is held only for the length of a sweep (released in a
        # `finally`), so "exactly one is skipped" is only true if the two
        # sweeps OVERLAP. With fast stubs the first can finish before the
        # second even asks, and then both legitimately run. So the overlap
        # is made certain rather than hoped for: whichever sweeper holds the
        # lock waits inside its first Stripe call until the other one has
        # tried and returned. If the lock were broken, both would wait here
        # for each other, time out, and both sweep, and the test fails.
        finished = []
        other_finished = threading.Event()
        prices = {
            BASE_PRICE_ID: price_obj(),
            OVERAGE_PRICE_ID: price_obj(
                price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
            ),
        }

        def held_retrieve(price_id, *args, **kwargs):
            other_finished.wait(timeout=10)
            return prices[price_id]

        def sweep(i):
            try:
                return reconcile_stripe_prices()
            finally:
                finished.append(i)
                other_finished.set()

        # Entered ONCE, on the main thread, around the whole race. They
        # used to be entered inside each worker: patch() rebinds a module
        # attribute and is not thread-safe, so the first worker to exit
        # restored the REAL Stripe call while the other was still sweeping.
        with account_returns():
            with patch(
                "billing.price_reconciliation.stripe.Price.retrieve", held_retrieve
            ):
                summaries, errors = run_concurrently(
                    sweep, 2, test=self, name="sweeper"
                )

        self.assertEqual(errors, [], f"a sweeper raised: {errors!r}")
        skipped = [s for s in summaries if "already holds the lock" in s]
        self.assertEqual(
            len(skipped),
            1,
            f"expected exactly one run to be skipped, got {summaries}",
        )
        self.assertEqual(PriceReconciliationRun.objects.count(), 1)


class SynchronisationTests(TestCase):
    """
    Stripe -> application, every case the brief names.

    The pairing that matters: a value is adopted when Stripe could be READ
    and its Price is USABLE, and left strictly alone otherwise. Half of
    these tests exist to pin the second half of that sentence — overwriting
    a working price because Stripe was briefly unreachable would be a
    self-inflicted outage.
    """

    def setUp(self):
        cache.clear()

    def _run(self, *, base=None, overage=None):
        with account_returns():
            with stripe_returns(
                {
                    BASE_PRICE_ID: base if base is not None else price_obj(),
                    OVERAGE_PRICE_ID: (
                        overage
                        if overage is not None
                        else price_obj(
                            price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                        )
                    ),
                }
            ):
                return reconcile_prices()

    # --- base price ------------------------------------------------------

    def test_a_differing_base_price_takes_stripes_value(self):
        plan = make_plan(base_cents=1499)

        self._run(base=price_obj(unit_amount=2499))

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(2499))

    def test_a_missing_base_price_is_POPULATED_from_stripe(self):
        """
        The PRO / PRO_ANNUAL / POWER_ANNUAL case: a live Stripe price and
        nothing stored locally. No longer a finding — just a value waiting
        to be filled in.
        """
        plan = make_plan(base_cents=0)

        run = self._run(base=price_obj(unit_amount=2499))

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(2499))
        self.assertEqual(run.alert_count, 0)

    def test_a_populated_base_price_records_that_it_had_none_before(self):
        make_plan(base_cents=0)

        run = self._run(base=price_obj(unit_amount=2499))

        result = results_by_kind(run)[PriceKind.BASE]
        self.assertIsNone(result.previous_local_amount)
        self.assertEqual(result.stripe_amount, 2499)

    # --- overage price ---------------------------------------------------

    def test_a_differing_overage_price_takes_stripes_value(self):
        plan = make_plan(overage_cents=500)

        self._run(
            overage=price_obj(
                price_id=OVERAGE_PRICE_ID, unit_amount=400, recurring=None
            )
        )

        plan.refresh_from_db()
        self.assertEqual(plan.overage_block_price, 400)

    def test_a_missing_overage_price_is_POPULATED_from_stripe(self):
        plan = make_plan(overage_cents=0)

        self._run(
            overage=price_obj(
                price_id=OVERAGE_PRICE_ID, unit_amount=400, recurring=None
            )
        )

        plan.refresh_from_db()
        self.assertEqual(plan.overage_block_price, 400)

    def test_the_two_prices_synchronise_independently(self):
        plan = make_plan(base_cents=1499, overage_cents=500)

        self._run(
            base=price_obj(unit_amount=2499),
            overage=price_obj(
                price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
            ),
        )

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(2499))
        self.assertEqual(
            plan.overage_block_price, 500, "the overage price was collaterally changed"
        )

    # --- repointing at a new Price --------------------------------------

    def test_repointing_the_price_id_synchronises_to_the_NEW_price(self):
        """
        The workflow the brief is really about: change the price in Stripe,
        point the plan at it, and the application follows — no separate
        manual price edit.
        """
        plan = make_plan(base_cents=1499)
        plan.stripe_price_id = "price_brand_new"
        plan.save(update_fields=["stripe_price_id"])

        with account_returns():
            with stripe_returns(
                {
                    "price_brand_new": price_obj(
                        price_id="price_brand_new", unit_amount=3999
                    ),
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    ),
                }
            ):
                reconcile_prices()

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(3999))

    # --- when the local value must be LEFT ALONE -------------------------

    def test_stripe_unavailable_leaves_the_local_price_untouched(self):
        """
        Overwriting a working price because Stripe was briefly unreachable
        would turn an outage into a billing incident.
        """
        plan = make_plan(base_cents=1499)

        self._run(base=APIConnectionError("timed out"))

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(1499))

    def test_an_unreadable_price_does_not_CLEAR_the_local_price(self):
        plan = make_plan(base_cents=1499)

        self._run(base=APIConnectionError("timed out"))

        plan.refresh_from_db()
        self.assertNotEqual(plan.price_cents, Decimal(0))

    def test_an_invalid_price_id_leaves_the_local_price_untouched(self):
        plan = make_plan(base_cents=1499)
        plan.stripe_price_id = "price_does_not_exist"
        plan.save(update_fields=["stripe_price_id"])

        with account_returns():
            with stripe_returns(
                {
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    )
                }
            ):
                run = reconcile_prices()

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(1499))
        self.assertEqual(
            results_by_kind(run)[PriceKind.BASE].status,
            PriceReconciliationStatus.INVALID_PRICE,
        )

    def test_an_ARCHIVED_price_is_not_adopted(self):
        """
        An archived Price refuses the charge. Treating it as the source of
        truth would replace a working price with an unusable one.
        """
        plan = make_plan(base_cents=1499)

        run = self._run(base=price_obj(unit_amount=9999, active=False))

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(1499))
        self.assertEqual(
            results_by_kind(run)[PriceKind.BASE].status,
            PriceReconciliationStatus.INACTIVE_PRICE,
        )

    def test_a_plan_with_no_price_id_is_not_zeroed(self):
        plan = make_plan(base_cents=1499, base_price_id="")

        with account_returns():
            with stripe_returns(
                {
                    OVERAGE_PRICE_ID: price_obj(
                        price_id=OVERAGE_PRICE_ID, unit_amount=500, recurring=None
                    )
                }
            ):
                reconcile_prices()

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(1499))

    # --- direction ------------------------------------------------------

    def test_synchronising_never_writes_to_stripe(self):
        make_plan(base_cents=1499)
        import stripe as stripe_module

        with patch.object(stripe_module.Price, "modify", create=True) as modify:
            with patch.object(stripe_module.Price, "create", create=True) as create:
                self._run(base=price_obj(unit_amount=2499))

        self.assertFalse(modify.called)
        self.assertFalse(create.called)

    def test_a_synchronisation_touches_no_unrelated_plan_field(self):
        """
        A targeted UPDATE, not a blanket save.

        The edit has to land MID-RUN to be a real test. Editing before the
        sweep starts proves nothing — the reconciler simply loads the new
        value and writes it back. The clobber only happens when someone
        changes the plan in the window between the reconciler loading it
        and saving it, which is exactly what a nightly job racing an admin
        looks like. Injected through the Stripe call, which happens inside
        that window.
        """
        plan = make_plan(base_cents=1499)

        fired = []

        def concurrent_admin_edit(price_id, *a, **k):
            # ONCE, on the first fetch only. Re-applying it on every call
            # would restore the field after a blanket save had clobbered
            # it, hiding the very thing this test is looking for.
            if not fired:
                fired.append(price_id)
                SubscriptionPlan.objects.filter(pk=plan.pk).update(
                    monthly_credits=77_777
                )
            return price_obj(
                price_id=price_id,
                unit_amount=2499 if price_id == BASE_PRICE_ID else 500,
                recurring=("month", 1) if price_id == BASE_PRICE_ID else None,
            )

        with account_returns():
            with patch(
                "billing.price_reconciliation.stripe.Price.retrieve",
                concurrent_admin_edit,
            ):
                reconcile_prices()

        plan.refresh_from_db()
        self.assertEqual(plan.price_cents, Decimal(2499))
        self.assertEqual(
            plan.monthly_credits,
            77_777,
            "a concurrent edit to an unrelated field was clobbered — the "
            "sync wrote the whole row instead of just the price",
        )
