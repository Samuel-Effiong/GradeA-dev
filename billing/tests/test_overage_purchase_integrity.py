"""
billing/tests/test_overage_purchase_integrity.py
================================================
Overage purchases: can we make the system grant credits it should not?

THREE OVERAGE FLOWS EXIST, and they are not equally protected:

  1. `overage_block_purchase_checkout`  -> checkout.session.completed
     Individual purchase through Stripe Checkout.
  2. `overage_block_purchase`           -> payment_intent.succeeded
     Individual purchase confirmed off-session.
  3. `license_overage_purchase_checkout`-> checkout.session.completed
     School purchase, fulfilling a LicenseOveragePurchaseIntent.

TWO REAL DEFECTS WERE FOUND HERE AND FIXED
------------------------------------------
Flow 1 had no idempotency guard and no payment confirmation check:

  * A duplicated `checkout.session.completed` DOUBLE-GRANTED. Measured
    before the fix: a customer who paid for one 500-credit block received
    two buckets totalling 1000 credits. Stripe documents this exact case —
    "In some cases, two separate Event objects are generated and sent" —
    and the StripeEvent ledger cannot stop it, because it keys on the
    EVENT id while both events carry the same checkout session.
  * It never checked `payment_status`, so an asynchronous payment method
    (where the event fires before the money settles) would have been
    granted credits for a payment that had not been made.

Flow 3 already had both protections (`intent.status == COMPLETED` under a
row lock, and `payment_status != "paid"` refused). Flow 2 already had
`_overage_already_granted`. The gap was flow 1 alone, and the tests below
hold all three to the same standard.

WHAT IS DELIBERATELY ATTACKED
-----------------------------
Duplicate delivery, out-of-order delivery, concurrent delivery by real
threads, tampered metadata (amount, wallet, plan, user), cross-tenant
wallets, unconfirmed payments, failed and cancelled intents, rollback,
and the cap. Every assertion is on the resulting LEDGER AND WALLET STATE,
not on a return value — a grant that happened is visible in the ledger
whatever the function said.
"""

import threading
import time
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from billing.models import (
    BillingInterval,
    BillingTransaction,
    BillingTransactionStatus,
    BillingTransactionType,
    CreditBucket,
    CreditBucketType,
    CreditLedger,
    CreditLedgerType,
    CreditWallet,
    PlanCategory,
    PlanTier,
    PlanType,
    SubscriptionPlan,
    UserSubscription,
)
from billing.stripe_service import StripeWebhookHandler
from classrooms.models import School
from users.models import UserTypes

CustomUser = get_user_model()

BLOCK = 500
PRICE = 1000  # cents


def make_plan(name=PlanType.STANDARD, tier=PlanTier.STANDARD, max_blocks=10):
    return SubscriptionPlan.objects.create(
        name=name,
        display_name=str(name),
        category=PlanCategory.INDIVIDUAL,
        tier=tier,
        interval=BillingInterval.MONTHLY,
        monthly_credits=10_000,
        overage_block_size=BLOCK,
        overage_block_price=10,
        max_overage_blocks=max_blocks,
        stripe_overage_price_id="price_overage",
        is_active=True,
    )


class OverageFixture:
    """Shared setup: a subscribed teacher with a wallet."""

    #: Set by each TestCase's setUp; declared so the type checker can see it.
    plan: SubscriptionPlan

    def build(self, email="overage@billing.test", plan=None, school=None):
        plan = plan or self.plan
        user = CustomUser.objects.create_user(
            email=email,
            password="testpass123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
            is_active=True,
            school=school,
        )
        wallet, _ = CreditWallet.objects.get_or_create(user=user)
        CreditBucket.objects.filter(wallet=wallet).delete()
        now = timezone.now()
        UserSubscription.objects.create(
            user=user,
            plan=plan,
            is_active=True,
            is_trial=False,
            billing_cycle_start=now - timedelta(days=1),
            billing_cycle_end=now + timedelta(days=29),
            next_credit_grant_at=now + timedelta(days=29),
        )
        return user, wallet

    # -- payload builders (shaped like the real Stripe objects) -----------

    def checkout_session(
        self,
        wallet,
        plan,
        *,
        session_id="cs_ov_1",
        payment_intent="pi_ov_1",
        quantity=1,
        amount_total=PRICE,
        payment_status="paid",
        user=None,
        **metadata_overrides,
    ):
        metadata = {
            "flow": "overage_block_purchase_checkout",
            "user_id": str((user or wallet.user).id),
            "wallet_id": str(wallet.id),
            "plan_id": str(plan.id),
            "quantity": str(quantity),
        }
        metadata.update(metadata_overrides)
        return {
            "id": session_id,
            "object": "checkout.session",
            "amount_total": amount_total,
            "currency": "usd",
            "payment_status": payment_status,
            "payment_intent": payment_intent,
            "invoice": None,
            "metadata": metadata,
        }

    def payment_intent(
        self, wallet, plan, *, pi_id="pi_direct_1", **metadata_overrides
    ):
        metadata = {
            "flow": "overage_block_purchase",
            "user_id": str(wallet.user.id),
            "wallet_id": str(wallet.id),
            "plan_id": str(plan.id),
        }
        metadata.update(metadata_overrides)
        return {"id": pi_id, "object": "payment_intent", "metadata": metadata}

    # -- state readers -----------------------------------------------------

    def overage_buckets(self, wallet):
        return CreditBucket.objects.filter(
            wallet=wallet, bucket_type=CreditBucketType.OVERAGE
        )

    def granted_credits(self, wallet):
        return sum(b.total_credits for b in self.overage_buckets(wallet))

    def purchase_ledger(self, wallet):
        return CreditLedger.objects.filter(
            bucket__wallet=wallet, ledger_type=CreditLedgerType.PURCHASE
        )


class IndividualCheckoutOverageTests(TestCase, OverageFixture):
    """Flow 1 — the one that had the defects."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build()

    def _deliver(self, session):
        StripeWebhookHandler.handle_checkout_completed(session)

    # --- the happy path, asserted on real state ---------------------------

    def test_a_paid_purchase_grants_exactly_one_block(self):
        self._deliver(self.checkout_session(self.wallet, self.plan))

        self.assertEqual(self.overage_buckets(self.wallet).count(), 1)
        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

    def test_the_bucket_is_OVERAGE_and_never_expires(self):
        """A purchased block is a standing balance, not cycle-bound."""
        self._deliver(self.checkout_session(self.wallet, self.plan))

        bucket = self.overage_buckets(self.wallet).get()
        self.assertEqual(bucket.bucket_type, CreditBucketType.OVERAGE)
        self.assertIsNone(bucket.expires_at)
        self.assertEqual(bucket.used_credits, 0)

    def test_the_bucket_belongs_to_the_paying_wallet(self):
        other_user, other_wallet = self.build(email="bystander@billing.test")

        self._deliver(self.checkout_session(self.wallet, self.plan))

        self.assertEqual(self.overage_buckets(self.wallet).count(), 1)
        self.assertEqual(
            self.overage_buckets(other_wallet).count(),
            0,
            "credits landed in the wrong wallet",
        )

    def test_a_ledger_row_records_the_purchase_and_its_payment(self):
        self._deliver(self.checkout_session(self.wallet, self.plan))

        row = self.purchase_ledger(self.wallet).get()
        self.assertEqual(row.amount, BLOCK)
        self.assertEqual(row.user_id, self.user.id)
        self.assertEqual(row.metadata.get("stripe_payment_intent_id"), "pi_ov_1")

    def test_a_billing_transaction_records_the_money(self):
        self._deliver(self.checkout_session(self.wallet, self.plan))

        txn = BillingTransaction.objects.get(user=self.user)
        self.assertEqual(
            txn.transaction_type, BillingTransactionType.INDIVIDUAL_OVERAGE_PURCHASE
        )
        self.assertEqual(txn.status, BillingTransactionStatus.PAID)
        self.assertEqual(txn.amount_cents, PRICE)

    def test_quantity_greater_than_one_grants_proportionally(self):
        self._deliver(
            self.checkout_session(self.wallet, self.plan, quantity=3, amount_total=3000)
        )

        self.assertEqual(self.granted_credits(self.wallet), 3 * BLOCK)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 3)

    # --- duplicate delivery: THE BUG --------------------------------------

    def test_a_duplicated_session_does_not_grant_twice(self):
        """
        THE REGRESSION. Stripe can emit two Event objects for one session;
        the StripeEvent ledger keys on the event id and cannot dedupe that.
        Before the fix this produced 1000 credits for a 500-credit payment.
        """
        session = self.checkout_session(self.wallet, self.plan)

        self._deliver(session)
        self._deliver(session)
        self._deliver(session)

        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            "a duplicated checkout session granted credits more than once",
        )
        self.assertEqual(self.overage_buckets(self.wallet).count(), 1)

    def test_a_duplicate_does_not_inflate_the_block_counter(self):
        """Otherwise the cap is consumed by phantom purchases."""
        session = self.checkout_session(self.wallet, self.plan)
        self._deliver(session)
        self._deliver(session)

        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.overage_blocks_used, 1)

    def test_a_duplicate_writes_only_one_ledger_row(self):
        session = self.checkout_session(self.wallet, self.plan)
        self._deliver(session)
        self._deliver(session)

        self.assertEqual(self.purchase_ledger(self.wallet).count(), 1)

    def test_two_DIFFERENT_sessions_do_both_grant(self):
        """The guard must not block a genuine second purchase."""
        self._deliver(self.checkout_session(self.wallet, self.plan))
        self._deliver(
            self.checkout_session(
                self.wallet, self.plan, session_id="cs_ov_2", payment_intent="pi_ov_2"
            )
        )

        self.assertEqual(self.granted_credits(self.wallet), 2 * BLOCK)

    # --- unconfirmed payment ----------------------------------------------

    def test_an_unpaid_session_grants_nothing(self):
        """
        `checkout.session.completed` fires before async payment methods
        settle. Granting there hands over credits for money not received.
        """
        self._deliver(
            self.checkout_session(self.wallet, self.plan, payment_status="unpaid")
        )

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_a_no_payment_required_session_grants_nothing(self):
        self._deliver(
            self.checkout_session(
                self.wallet, self.plan, payment_status="no_payment_required"
            )
        )

        self.assertEqual(self.granted_credits(self.wallet), 0)

    # --- tampering / bad metadata -----------------------------------------

    def test_a_session_naming_another_users_wallet_credits_that_wallet_only(self):
        """
        The wallet id is the authority, and it is snapshotted by OUR code at
        session creation — a mismatched user_id must not redirect credits.
        """
        victim, victim_wallet = self.build(email="victim@billing.test")

        self._deliver(
            self.checkout_session(
                victim_wallet, self.plan, user=self.user  # mismatched user_id
            )
        )

        self.assertEqual(self.granted_credits(victim_wallet), BLOCK)
        self.assertEqual(
            self.granted_credits(self.wallet),
            0,
            "credits were granted to the user named in metadata rather than "
            "the wallet that was paid for",
        )

    def test_an_unknown_wallet_id_grants_nothing_anywhere(self):
        session = self.checkout_session(self.wallet, self.plan)
        session["metadata"]["wallet_id"] = str(uuid.uuid4())

        with self.assertRaises(CreditWallet.DoesNotExist):
            self._deliver(session)

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_an_unknown_plan_id_grants_nothing(self):
        session = self.checkout_session(self.wallet, self.plan)
        session["metadata"]["plan_id"] = str(uuid.uuid4())

        with self.assertRaises(SubscriptionPlan.DoesNotExist):
            self._deliver(session)

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_the_credit_amount_comes_from_the_PLAN_not_the_payload(self):
        """
        A tampered amount_total must not buy more credits. The block size
        is read from our own plan record.
        """
        self._deliver(
            self.checkout_session(self.wallet, self.plan, amount_total=999_999)
        )

        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            "the granted credits followed the payload amount rather than "
            "the plan's block size",
        )

    def test_a_tampered_quantity_is_still_capped_by_the_plan(self):
        """A forged quantity cannot exceed max_overage_blocks."""
        self._deliver(
            self.checkout_session(
                self.wallet, self.plan, quantity=99, amount_total=PRICE
            )
        )

        self.assertEqual(self.granted_credits(self.wallet), 0, "the cap did not hold")

    # --- cap ---------------------------------------------------------------

    def test_the_cap_is_enforced_at_grant_time(self):
        self.wallet.overage_blocks_used = 10
        self.wallet.save(update_fields=["overage_blocks_used"])

        self._deliver(self.checkout_session(self.wallet, self.plan))

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_a_capped_purchase_is_still_recorded_for_refund(self):
        """Money was taken; it must be visible even though nothing was granted."""
        self.wallet.overage_blocks_used = 10
        self.wallet.save(update_fields=["overage_blocks_used"])

        self._deliver(self.checkout_session(self.wallet, self.plan))

        self.assertTrue(BillingTransaction.objects.filter(user=self.user).exists())

    # --- rollback ----------------------------------------------------------

    def test_a_failure_mid_handler_leaves_no_phantom_credits(self):
        from unittest.mock import patch

        session = self.checkout_session(self.wallet, self.plan)
        with patch(
            "billing.stripe_service.BillingTransactionService.record",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self._deliver(session)

        self.assertEqual(
            self.granted_credits(self.wallet),
            0,
            "the bucket survived a rolled-back transaction",
        )
        self.assertEqual(self.purchase_ledger(self.wallet).count(), 0)

    def test_after_a_rollback_the_retry_grants_exactly_once(self):
        """Stripe redelivers a FAILED event; recovery must not double."""
        from unittest.mock import patch

        session = self.checkout_session(self.wallet, self.plan)
        with patch(
            "billing.stripe_service.BillingTransactionService.record",
            side_effect=RuntimeError("boom"),
        ):
            with self.assertRaises(RuntimeError):
                self._deliver(session)

        self._deliver(session)  # Stripe's retry

        self.assertEqual(self.granted_credits(self.wallet), BLOCK)


class DirectPaymentIntentOverageTests(TestCase, OverageFixture):
    """Flow 2 — the off-session PaymentIntent path."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="pi.overage@billing.test")

    def _deliver(self, pi):
        StripeWebhookHandler.handle_payment_intent_succeeded(pi)

    def test_a_succeeded_intent_grants_one_block(self):
        self._deliver(self.payment_intent(self.wallet, self.plan))

        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

    def test_a_duplicate_intent_does_not_grant_twice(self):
        pi = self.payment_intent(self.wallet, self.plan)
        self._deliver(pi)
        self._deliver(pi)
        self._deliver(pi)

        self.assertEqual(self.granted_credits(self.wallet), BLOCK)

    def test_an_unrelated_payment_intent_is_ignored(self):
        self._deliver({"id": "pi_other", "metadata": {"flow": "something_else"}})
        self._deliver({"id": "pi_none", "metadata": {}})

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_a_missing_wallet_grants_nothing_and_does_not_raise(self):
        pi = self.payment_intent(self.wallet, self.plan)
        pi["metadata"]["wallet_id"] = str(uuid.uuid4())

        self._deliver(pi)  # logged, not raised — money already taken

        self.assertEqual(self.granted_credits(self.wallet), 0)

    def test_another_wallets_grant_cannot_suppress_this_ones(self):
        """
        The idempotency lookup is SCOPED TO THE WALLET. Unscoped, it asks
        "has any wallet anywhere been granted for this PaymentIntent?" —
        and a match on somebody else's ledger row would silently skip a
        legitimate grant, leaving a paying customer with nothing.

        Scoping is usually described as a performance choice (it avoids a
        sequential scan of the fastest-growing table). It is also a
        correctness one, and this is the test that makes that true rather
        than merely asserted.
        """
        other_user, other_wallet = self.build(email="pi.other@billing.test")
        shared_pi = "pi_shared_across_wallets"

        # The other wallet is granted first, writing a ledger row carrying
        # this PaymentIntent id.
        self._deliver(self.payment_intent(other_wallet, self.plan, pi_id=shared_pi))
        self.assertEqual(self.granted_credits(other_wallet), BLOCK)

        # Ours must still be granted — a different wallet entirely.
        self._deliver(self.payment_intent(self.wallet, self.plan, pi_id=shared_pi))

        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            "an unrelated wallet's ledger row suppressed this wallet's grant",
        )

    def test_the_checkout_and_intent_paths_do_not_double_grant_together(self):
        """
        Both events can fire for one purchase. They share the same
        PaymentIntent, which is what the guard keys on.
        """
        session = self.checkout_session(
            self.wallet, self.plan, payment_intent="pi_shared"
        )
        StripeWebhookHandler.handle_checkout_completed(session)
        self._deliver(self.payment_intent(self.wallet, self.plan, pi_id="pi_shared"))

        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            "the two overage flows granted separately for one payment",
        )


class OverageConsumptionOrderingTests(TestCase, OverageFixture):
    """Purchased overage must be spent LAST, after every free bucket."""

    def setUp(self):
        self.plan = make_plan()
        self.user, self.wallet = self.build(email="ordering@billing.test")
        now = timezone.now()
        self.monthly = CreditBucket.objects.create(
            wallet=self.wallet,
            bucket_type=CreditBucketType.MONTHLY,
            total_credits=100,
            used_credits=0,
            expires_at=now + timedelta(days=20),
        )
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(self.wallet, self.plan)
        )
        self.overage = self.overage_buckets(self.wallet).get()

    def test_monthly_credits_are_spent_before_purchased_overage(self):
        self.wallet.consume_credits(
            60, feature="Grading Assignment", task_type="GRADING", task_id="t1"
        )

        self.monthly.refresh_from_db()
        self.overage.refresh_from_db()
        self.assertEqual(self.monthly.used_credits, 60)
        self.assertEqual(
            self.overage.used_credits, 0, "paid overage was spent before free credits"
        )

    def test_overage_is_only_touched_once_the_free_buckets_are_empty(self):
        self.wallet.consume_credits(
            150, feature="Grading Assignment", task_type="GRADING", task_id="t2"
        )

        self.monthly.refresh_from_db()
        self.overage.refresh_from_db()
        self.assertEqual(self.monthly.used_credits, 100)
        self.assertEqual(self.overage.used_credits, 50)

    def test_a_partially_consumed_overage_bucket_is_topped_up_by_a_new_purchase(self):
        self.wallet.consume_credits(
            150, feature="Grading Assignment", task_type="GRADING", task_id="t3"
        )
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                self.wallet, self.plan, session_id="cs_2", payment_intent="pi_2"
            )
        )

        total = sum(
            b.total_credits - b.used_credits for b in self.overage_buckets(self.wallet)
        )
        self.assertEqual(total, (BLOCK - 50) + BLOCK)


class SchoolLicenseOverageIsolationTests(TestCase, OverageFixture):
    """
    A school purchase must never land in the wrong teacher's wallet.

    Several teachers share a school and a licence, so a flow that resolved
    the target by school or by licence rather than by WALLET would be
    indistinguishable in a single-teacher test.
    """

    def setUp(self):
        self.plan = make_plan()
        self.school = School.objects.create(name="Overage School")
        self.other_school = School.objects.create(name="Other School")
        self.teacher_a, self.wallet_a = self.build(
            email="teacher.a@school.test", school=self.school
        )
        self.teacher_b, self.wallet_b = self.build(
            email="teacher.b@school.test", school=self.school
        )
        self.outsider, self.wallet_out = self.build(
            email="outsider@other.test", school=self.other_school
        )

    def test_a_purchase_for_one_teacher_credits_only_that_teacher(self):
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(self.wallet_a, self.plan)
        )

        self.assertEqual(self.granted_credits(self.wallet_a), BLOCK)
        self.assertEqual(
            self.granted_credits(self.wallet_b),
            0,
            "a colleague in the same school received the credits",
        )
        self.assertEqual(self.granted_credits(self.wallet_out), 0)

    def test_two_teachers_in_one_school_purchase_independently(self):
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                self.wallet_a, self.plan, session_id="cs_a", payment_intent="pi_a"
            )
        )
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                self.wallet_b, self.plan, session_id="cs_b", payment_intent="pi_b"
            )
        )

        self.assertEqual(self.granted_credits(self.wallet_a), BLOCK)
        self.assertEqual(self.granted_credits(self.wallet_b), BLOCK)

    def test_the_ledger_attributes_each_purchase_to_its_own_user(self):
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                self.wallet_a, self.plan, session_id="cs_a", payment_intent="pi_a"
            )
        )
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                self.wallet_b, self.plan, session_id="cs_b", payment_intent="pi_b"
            )
        )

        self.assertEqual(
            set(self.purchase_ledger(self.wallet_a).values_list("user_id", flat=True)),
            {self.teacher_a.id},
        )
        self.assertEqual(
            set(self.purchase_ledger(self.wallet_b).values_list("user_id", flat=True)),
            {self.teacher_b.id},
        )

    def test_a_duplicate_for_teacher_a_cannot_leak_into_teacher_b(self):
        session = self.checkout_session(self.wallet_a, self.plan)
        StripeWebhookHandler.handle_checkout_completed(session)
        StripeWebhookHandler.handle_checkout_completed(session)

        self.assertEqual(self.granted_credits(self.wallet_a), BLOCK)
        self.assertEqual(self.granted_credits(self.wallet_b), 0)


class ConcurrentOverageDeliveryTests(TransactionTestCase, OverageFixture):
    """
    REAL threads against Postgres.

    `TransactionTestCase` is required: the standard TestCase wraps each
    test in a transaction the worker threads cannot see, so the row locks
    would never actually contend and the race would not be reproduced.
    """

    reset_sequences = True

    #: Total time _run waits for ALL workers, shared by them as one deadline.
    JOIN_TIMEOUT_SECONDS = 60

    #: Gate 3 load: simultaneous deliveries per round, and rounds per test.
    LOAD_THREADS = 20
    LOAD_ROUNDS = 10

    def setUp(self):
        # The grant path resolves a receipt link with a LIVE Stripe call
        # (resolve_stripe_receipt_url), inside the grant's own transaction.
        # Its latency is not what these tests check, and it made them flaky:
        # a call that outlived the join left the assertions reading a grant
        # that was written but not yet committed ("0 != 500").
        patcher = patch(
            "billing.stripe_service.resolve_stripe_receipt_url", return_value=None
        )
        self.receipt_lookup = patcher.start()
        self.addCleanup(patcher.stop)

        self.plan = make_plan()
        self.user, self.wallet = self.build(email="concurrent.ov@billing.test")

    def _run(self, fn, count, join_timeout=None):
        """
        Start `count` threads, release them together, wait for every one,
        and return the exceptions they raised.

        A thread still running after the timeout FAILS the test. Its
        transaction may not have committed, so asserting on the ledger then
        would be asserting on partial state, which is exactly how this class
        used to fail intermittently.
        """
        timeout = self.JOIN_TIMEOUT_SECONDS if join_timeout is None else join_timeout
        barrier = threading.Barrier(count)
        errors = []

        def worker(i):
            try:
                barrier.wait(timeout=30)
                fn(i)
            except Exception as exc:  # noqa: BLE001 - asserted on below
                errors.append(exc)
            finally:
                # The thread opened its own connection; nobody else can close
                # it, and an open one fails test-database teardown.
                connection.close()

        threads = [
            threading.Thread(target=worker, args=(i,), name=f"overage-worker-{i}")
            for i in range(count)
        ]
        # Whatever happens below, don't leave a live thread (and its open
        # transaction) behind for the table flush to deadlock against.
        self.workers = threads
        self.addCleanup(self._reap, threads)
        for t in threads:
            t.start()

        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))

        still_running = [t.name for t in threads if t.is_alive()]
        if still_running:
            self.fail(
                f"{len(still_running)} of {count} worker thread(s) still running "
                f"after {timeout}s: {still_running}. Their transactions may not "
                f"have committed; refusing to assert on partial state."
            )
        return errors

    @staticmethod
    def _reap(threads, timeout=120):
        deadline = time.monotonic() + timeout
        for t in threads:
            t.join(timeout=max(0.0, deadline - time.monotonic()))

    def _buy_as(self, wallet, tag):
        StripeWebhookHandler.handle_checkout_completed(
            self.checkout_session(
                wallet, self.plan, session_id=f"cs_{tag}", payment_intent=f"pi_{tag}"
            )
        )

    def test_simultaneous_duplicate_deliveries_grant_exactly_once(self):
        """
        Payment succeeds -> the same webhook arrives twice at once.
        Both threads read the wallet before either writes; only the row
        lock and the idempotency guard stand between that and a double
        grant.
        """
        session = self.checkout_session(self.wallet, self.plan)

        errors = self._run(
            lambda i: StripeWebhookHandler.handle_checkout_completed(session), 6
        )

        self.assertEqual(errors, [], f"threads raised: {errors!r}")
        self.assertEqual(
            self.granted_credits(self.wallet),
            BLOCK,
            "concurrent duplicate deliveries granted more than one block",
        )
        self.assertEqual(self.purchase_ledger(self.wallet).count(), 1)

    def test_two_genuinely_different_purchases_both_land(self):
        """Neither may overwrite the other."""

        def buy(i):
            StripeWebhookHandler.handle_checkout_completed(
                self.checkout_session(
                    self.wallet,
                    self.plan,
                    session_id=f"cs_conc_{i}",
                    payment_intent=f"pi_conc_{i}",
                )
            )

        errors = self._run(buy, 4)

        self.assertEqual(errors, [], f"threads raised: {errors!r}")
        self.assertEqual(self.granted_credits(self.wallet), 4 * BLOCK)
        self.wallet.refresh_from_db()
        self.assertEqual(
            self.wallet.overage_blocks_used,
            4,
            "the block counter lost an increment to a race",
        )

    def test_concurrent_purchases_cannot_exceed_the_cap(self):
        """
        Ten threads race for two remaining blocks. The cap is re-checked
        under the row lock, so at most two may win.
        """
        self.wallet.overage_blocks_used = 8
        self.wallet.save(update_fields=["overage_blocks_used"])

        def buy(i):
            StripeWebhookHandler.handle_checkout_completed(
                self.checkout_session(
                    self.wallet,
                    self.plan,
                    session_id=f"cs_cap_{i}",
                    payment_intent=f"pi_cap_{i}",
                )
            )

        errors = self._run(buy, 10)

        self.assertEqual(errors, [], f"threads raised: {errors!r}")
        self.wallet.refresh_from_db()
        self.assertLessEqual(
            self.wallet.overage_blocks_used,
            self.plan.max_overage_blocks,
            f"the cap was breached under concurrency: "
            f"{self.wallet.overage_blocks_used} > {self.plan.max_overage_blocks}",
        )
        self.assertLessEqual(self.granted_credits(self.wallet), 2 * BLOCK)

    def test_concurrent_purchases_by_different_teachers_stay_separate(self):
        """The school case, under contention."""
        school = School.objects.create(name="Concurrent School")
        users = [
            self.build(email=f"conc.t{i}@school.test", school=school) for i in range(4)
        ]

        def buy(i):
            _, wallet = users[i]
            StripeWebhookHandler.handle_checkout_completed(
                self.checkout_session(
                    wallet,
                    self.plan,
                    session_id=f"cs_multi_{i}",
                    payment_intent=f"pi_multi_{i}",
                )
            )

        errors = self._run(buy, 4)

        self.assertEqual(errors, [], f"threads raised: {errors!r}")
        for _, wallet in users:
            self.assertEqual(
                self.granted_credits(wallet),
                BLOCK,
                "a teacher did not receive exactly their own block",
            )

    # --- the harness itself -------------------------------------------------

    def test_a_worker_outliving_the_join_fails_loudly_instead_of_reading_partial_state(
        self,
    ):
        """
        THE FLAKE, made deterministic. One teacher's receipt lookup blocks
        past the join timeout, so that thread's grant is written but not
        committed when the waiting stops. The harness must fail and name
        the thread, never go on to the ledger assertions.
        """
        school = School.objects.create(name="Slow Lookup School")
        wallets = [
            self.build(email=f"slow.t{i}@school.test", school=school)[1]
            for i in range(4)
        ]
        slow_intent = "pi_slow_3"
        entered, release = threading.Event(), threading.Event()

        def lookup(*, invoice_id=None, payment_intent_id=None, **_):
            if payment_intent_id == slow_intent:
                entered.set()
                release.wait(timeout=120)
            return None

        self.receipt_lookup.side_effect = lookup

        try:
            with self.assertRaisesRegex(
                AssertionError, r"still running after 5s: .*'overage-worker-3'"
            ):
                self._run(lambda i: self._buy_as(wallets[i], f"slow_{i}"), 4, 5)
            self.assertTrue(
                entered.is_set(),
                "the slow thread never reached the lookup, so this did not "
                "exercise a written-but-uncommitted grant",
            )
            # What the old harness asserted on: the grant isn't visible yet.
            self.assertEqual(self.granted_credits(wallets[3]), 0)
        finally:
            release.set()

        # Once it is allowed to finish, the grant commits normally.
        self._reap(self.workers)
        self.assertEqual([t.name for t in self.workers if t.is_alive()], [])
        self.assertEqual(
            [self.granted_credits(w) for w in wallets], [BLOCK] * len(wallets)
        )

    # --- Gate 3 load: 20 simultaneous deliveries, 10 rounds ----------------

    def test_twenty_teachers_buying_at_once_each_get_only_their_own_blocks(self):
        """
        Twenty teachers in ONE school buy simultaneously, ten rounds over.
        After every round each wallet holds exactly the blocks it paid for,
        every ledger row names its own teacher, and every payment intent
        in a wallet's ledger is one that teacher paid.
        """
        school = School.objects.create(name="Load School")
        teachers = [
            self.build(email=f"load.t{i}@school.test", school=school)
            for i in range(self.LOAD_THREADS)
        ]

        for rnd in range(1, self.LOAD_ROUNDS + 1):
            errors = self._run(
                lambda i, rnd=rnd: self._buy_as(teachers[i][1], f"load_{rnd}_{i}"),
                self.LOAD_THREADS,
            )

            self.assertEqual(errors, [], f"round {rnd}: threads raised {errors!r}")
            for i, (teacher, wallet) in enumerate(teachers):
                wallet.refresh_from_db()
                ledger = self.purchase_ledger(wallet)
                self.assertEqual(
                    (self.granted_credits(wallet), wallet.overage_blocks_used),
                    (rnd * BLOCK, rnd),
                    f"round {rnd}: teacher {i} does not hold exactly the blocks "
                    f"they paid for",
                )
                self.assertEqual(
                    set(ledger.values_list("user_id", flat=True)), {teacher.id}
                )
                self.assertEqual(
                    {row.metadata["stripe_payment_intent_id"] for row in ledger},
                    {f"pi_load_{r}_{i}" for r in range(1, rnd + 1)},
                    f"round {rnd}: teacher {i}'s ledger holds another "
                    f"teacher's payment",
                )

    def test_twenty_simultaneous_duplicates_grant_once_every_round(self):
        for rnd in range(1, self.LOAD_ROUNDS + 1):
            session = self.checkout_session(
                self.wallet,
                self.plan,
                session_id=f"cs_dup_{rnd}",
                payment_intent=f"pi_dup_{rnd}",
            )

            errors = self._run(
                lambda i, s=session: StripeWebhookHandler.handle_checkout_completed(s),
                self.LOAD_THREADS,
            )

            self.assertEqual(errors, [], f"round {rnd}: threads raised {errors!r}")
            self.wallet.refresh_from_db()
            self.assertEqual(
                (
                    self.granted_credits(self.wallet),
                    self.purchase_ledger(self.wallet).count(),
                    self.wallet.overage_blocks_used,
                ),
                (rnd * BLOCK, rnd, rnd),
                f"round {rnd}: {self.LOAD_THREADS} simultaneous duplicates did "
                f"not grant exactly once",
            )

    def test_twenty_purchases_racing_for_two_blocks_grant_exactly_two_every_round(
        self,
    ):
        """
        Every purchase here is paid and valid, and the cap is re-checked
        under the wallet lock, so exactly two win (not "at most two"), and
        every loser is still recorded for refund.
        """
        for rnd in range(1, self.LOAD_ROUNDS + 1):
            user, wallet = self.build(email=f"cap.r{rnd}@billing.test")
            wallet.overage_blocks_used = self.plan.max_overage_blocks - 2
            wallet.save(update_fields=["overage_blocks_used"])

            errors = self._run(
                lambda i, w=wallet, rnd=rnd: self._buy_as(w, f"cap_{rnd}_{i}"),
                self.LOAD_THREADS,
            )

            self.assertEqual(errors, [], f"round {rnd}: threads raised {errors!r}")
            wallet.refresh_from_db()
            self.assertEqual(
                (
                    self.granted_credits(wallet),
                    wallet.overage_blocks_used,
                    BillingTransaction.objects.filter(user=user).count(),
                ),
                (2 * BLOCK, self.plan.max_overage_blocks, self.LOAD_THREADS),
                f"round {rnd}: the cap race did not end with exactly two grants "
                f"and every payment recorded",
            )
